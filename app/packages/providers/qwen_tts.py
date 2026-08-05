from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx
import websockets

from packages.config import QwenTTSConfig

logger = logging.getLogger(__name__)

# 按句切片：WS 与 HTTP fallback 共用（远端 Omni 需 input.done 才开生成）
# 中文短句常见「好的。」「嗯嗯。」，min 过大会拖到下一句才 flush
_STREAM_MIN = 4
_STREAM_MAX = 36
_DELIMS = "。！？；\n.!?;"

# Qwen3-TTS / vLLM-Omni 默认输出
_NATIVE_SR = 24000


class QwenTTSProvider:
    """vLLM-Omni Qwen3-TTS（OpenAI speech + /v1/audio/speech/stream）。"""

    def __init__(self, cfg: QwenTTSConfig) -> None:
        self._cfg = cfg
        self._native_sr = int(getattr(cfg, "native_sample_rate", None) or _NATIVE_SR)

    async def synthesize_text_stream(
        self,
        text_iter: AsyncIterator[str],
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        captured: list[str] = []
        yielded_any = False

        async def _capturing() -> AsyncIterator[str]:
            async for piece in text_iter:
                captured.append(piece)
                yield piece

        try:
            async for chunk in self._ws_synthesize_incremental(
                _capturing(), sample_rate, cancel_event
            ):
                yielded_any = True
                yield chunk
        except Exception as exc:
            logger.warning(
                "Qwen TTS WS failed, fallback HTTP: %s (yielded_pcm=%s chars=%s)",
                exc,
                yielded_any,
                sum(len(p) for p in captured),
            )
            if yielded_any:
                return
            text = "".join(captured)
            if not text.strip():
                return

            async def _buffered() -> AsyncIterator[str]:
                yield text

            async for chunk in self._http_synthesize_text_stream(
                _buffered(), sample_rate, cancel_event
            ):
                yield chunk

    async def synthesize_stream(
        self,
        text: str,
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return

        async def _once() -> AsyncIterator[str]:
            yield text

        async for chunk in self.synthesize_text_stream(
            _once(), sample_rate=sample_rate, cancel_event=cancel_event
        ):
            yield chunk

    def _session_config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "type": "session.config",
            "task_type": getattr(self._cfg, "task_type", None) or "CustomVoice",
            "speaker": self._cfg.speaker,
            "language": self._cfg.language,
            "response_format": "pcm",
            "stream_audio": True,
        }
        if self._cfg.instruct:
            cfg["instructions"] = self._cfg.instruct
        return cfg

    def _extract_pcm(self, message: Any) -> bytes:
        if isinstance(message, (bytes, bytearray)):
            return bytes(message)
        if not isinstance(message, dict):
            return b""
        if message.get("type") == "audio.chunk":
            b64 = message.get("audio_b64") or message.get("audio") or ""
            if b64:
                return base64.b64decode(b64)
        return b""

    async def _ws_connect(self):
        try:
            return websockets.connect(
                self._cfg.ws_url,
                max_size=16 * 1024 * 1024,
                open_timeout=30,
                ping_interval=20,
                ping_timeout=20,
            )
        except TypeError:
            return websockets.connect(
                self._cfg.ws_url,
                max_size=16 * 1024 * 1024,
            )

    def _pcm_converter(self, sample_rate: int):
        rate_state = None
        need_resample = sample_rate != self._native_sr

        def _convert(pcm: bytes) -> bytes:
            nonlocal rate_state
            if not pcm:
                return b""
            if not need_resample:
                return pcm
            out, rate_state = audioop.ratecv(
                pcm, 2, 1, self._native_sr, sample_rate, rate_state
            )
            return out

        return _convert

    async def _drain_utterance(
        self,
        ws: Any,
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        """收一句：stream_audio 多块 PCM，直到 session.done。"""
        convert = self._pcm_converter(sample_rate)

        while True:
            if cancel_event and cancel_event.is_set():
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
            except asyncio.TimeoutError:
                raise RuntimeError("Qwen TTS WS recv timeout") from None
            except websockets.exceptions.ConnectionClosed:
                return

            if isinstance(raw, (bytes, bytearray)):
                pcm = convert(bytes(raw))
                if pcm:
                    yield pcm
                continue

            msg = json.loads(raw)
            typ = msg.get("type")
            if typ == "error":
                raise RuntimeError(msg.get("message") or str(msg))
            if typ == "audio.start":
                logger.debug(
                    "Qwen TTS audio.start text=%r",
                    (msg.get("sentence_text") or "")[:40],
                )
                continue
            if typ == "audio.done":
                if msg.get("error"):
                    raise RuntimeError(
                        msg.get("message") or "audio.done error"
                    )
                continue

            pcm = convert(self._extract_pcm(msg))
            if pcm:
                yield pcm

            if typ == "session.done":
                return

    async def _ws_synthesize_incremental(
        self,
        text_iter: AsyncIterator[str],
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        """并发拉 LLM token + 按句合成。

        说明：当前 AutoDL 上的 vLLM-Omni 需 input.done 才开生成（不会在句中标点处
        提前出声）。因此客户端按句切片，每句 session.config → text → input.done；
        与旧实现的关键差异是 token 读取与 TTS drain 并行，首句合成时 LLM 继续推进。
        """
        t0 = time.monotonic()
        first_pcm_ms: int | None = None
        sentence_q: asyncio.Queue[str | None] = asyncio.Queue()

        async def _read_tokens() -> None:
            buf = ""
            try:
                async for piece in text_iter:
                    if cancel_event and cancel_event.is_set():
                        break
                    if not piece:
                        continue
                    buf += piece
                    while True:
                        cut = _take_stream_piece(buf)
                        if not cut:
                            break
                        sentence, buf = cut
                        await sentence_q.put(sentence)
                if buf.strip() and not (cancel_event and cancel_event.is_set()):
                    await sentence_q.put(buf.strip())
            finally:
                await sentence_q.put(None)

        ws_cm = await self._ws_connect()
        async with ws_cm as ws:
            logger.info("Qwen TTS WS connected url=%s", self._cfg.ws_url)
            reader = asyncio.create_task(_read_tokens())
            uttered = 0
            try:
                while True:
                    if cancel_event and cancel_event.is_set():
                        break
                    sentence = await sentence_q.get()
                    if sentence is None:
                        break
                    await ws.send(json.dumps(self._session_config()))
                    # 句内可增量喂字；本机 Omni 仍等 input.done 才出声
                    for i in range(0, len(sentence), 4):
                        if cancel_event and cancel_event.is_set():
                            break
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "input.text",
                                    "text": sentence[i : i + 4],
                                }
                            )
                        )
                    if cancel_event and cancel_event.is_set():
                        break
                    await ws.send(json.dumps({"type": "input.done"}))
                    async for pcm in self._drain_utterance(
                        ws, sample_rate, cancel_event
                    ):
                        if first_pcm_ms is None:
                            first_pcm_ms = int((time.monotonic() - t0) * 1000)
                            logger.info(
                                "Qwen TTS WS first_pcm_ms=%s", first_pcm_ms
                            )
                        yield pcm
                    uttered += 1
                logger.info("Qwen TTS WS uttered_sentences=%s", uttered)
                if reader.done() and not reader.cancelled():
                    exc = reader.exception()
                    if exc is not None:
                        raise exc
            finally:
                if not reader.done():
                    reader.cancel()
                    try:
                        await reader
                    except (asyncio.CancelledError, Exception):
                        pass
                try:
                    await ws.send(json.dumps({"type": "session.close"}))
                except Exception:
                    pass

    async def _http_synthesize(
        self,
        text: str,
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        payload: dict[str, Any] = {
            "input": text,
            "voice": self._cfg.speaker,
            "response_format": "pcm",
            "language": self._cfg.language,
            "task_type": getattr(self._cfg, "task_type", None) or "CustomVoice",
        }
        if self._cfg.model:
            payload["model"] = self._cfg.model
        if self._cfg.instruct:
            payload["instructions"] = self._cfg.instruct

        url = f"{self._cfg.base_url.rstrip('/')}/v1/audio/speech"
        async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            if cancel_event and cancel_event.is_set():
                return
            body = resp.content
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                data = resp.json()
                b64 = data.get("audio") or data.get("data")
                if isinstance(b64, str):
                    body = base64.b64decode(b64)
            if body[:4] == b"RIFF":
                body = body[44:]
            if sample_rate != self._native_sr and body:
                body, _ = audioop.ratecv(
                    body, 2, 1, self._native_sr, sample_rate, None
                )
            if body:
                yield body

    async def _http_synthesize_text_stream(
        self,
        text_iter: AsyncIterator[str],
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        buf = ""
        async for piece in text_iter:
            if cancel_event and cancel_event.is_set():
                return
            buf += piece
            while True:
                cut = _take_stream_piece(buf)
                if not cut:
                    break
                sentence, buf = cut
                async for chunk in self._http_synthesize(
                    sentence, sample_rate, cancel_event
                ):
                    yield chunk
        if buf.strip() and not (cancel_event and cancel_event.is_set()):
            async for chunk in self._http_synthesize(
                buf.strip(), sample_rate, cancel_event
            ):
                yield chunk


def _take_stream_piece(buf: str) -> tuple[str, str] | None:
    for i, ch in enumerate(buf):
        if ch in _DELIMS and i + 1 >= _STREAM_MIN:
            return buf[: i + 1], buf[i + 1 :]
    if len(buf) >= _STREAM_MAX:
        return buf[:_STREAM_MAX], buf[_STREAM_MAX:]
    return None
