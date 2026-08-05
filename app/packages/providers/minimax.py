from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx
import websockets

from packages.config import MiniMaxConfig

logger = logging.getLogger(__name__)

# 增量喂字：句末标点优先，否则满 N 字就送（WS 同一会话内保持语调连贯）
_STREAM_MIN = 8
_STREAM_MAX = 36
_DELIMS = "。！？；\n.!?;"


class MiniMaxTTSProvider:
    """MiniMax T2A — WebSocket 实时流式为主，HTTP 仅作连接失败兜底。"""

    def __init__(self, cfg: MiniMaxConfig) -> None:
        self._cfg = cfg

    def _voice_setting(self) -> dict:
        return {
            "voice_id": self._cfg.voice_id,
            "speed": self._cfg.speed,
            "vol": self._cfg.vol,
            "pitch": self._cfg.pitch,
        }

    async def synthesize_text_stream(
        self,
        text_iter: AsyncIterator[str],
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        """LLM 增量文本 → WS 实时合成，边收边吐 PCM。"""
        try:
            async for chunk in self._ws_synthesize_incremental(
                text_iter, sample_rate, cancel_event
            ):
                yield chunk
        except _WsSetupError as exc:
            logger.warning("MiniMax WS unavailable, fallback HTTP: %s", exc)
            async for chunk in self._http_synthesize_text_stream(
                text_iter, sample_rate, cancel_event
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

    async def _ws_synthesize_incremental(
        self,
        text_iter: AsyncIterator[str],
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        headers = {"Authorization": f"Bearer {self._cfg.api_key}"}
        if self._cfg.group_id:
            headers["Group-Id"] = self._cfg.group_id

        try:
            ws_cm = websockets.connect(
                self._cfg.ws_url,
                additional_headers=headers,
                proxy=None,
                max_size=8 * 1024 * 1024,
                open_timeout=15,
                ping_interval=20,
                ping_timeout=20,
            )
        except TypeError:
            # websockets 旧版无 open_timeout / additional_headers 命名差异
            ws_cm = websockets.connect(
                self._cfg.ws_url,
                extra_headers=headers,
                max_size=8 * 1024 * 1024,
            )

        try:
            ws = await ws_cm.__aenter__()
        except Exception as exc:
            raise _WsSetupError(f"connect failed: {exc}") from exc

        try:
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            except Exception as exc:
                raise _WsSetupError(f"handshake failed: {exc}") from exc
            if first.get("event") not in ("connected_success", "task_started"):
                br = first.get("base_resp", {})
                if br.get("status_code", 0) not in (0, None):
                    raise _WsSetupError(f"connect rejected: {first}")

            await ws.send(
                json.dumps(
                    {
                        "event": "task_start",
                        "model": self._cfg.model,
                        "voice_setting": self._voice_setting(),
                        "audio_setting": {
                            "sample_rate": sample_rate,
                            "format": "pcm",
                            "channel": 1,
                        },
                    }
                )
            )
            try:
                started = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            except Exception as exc:
                raise _WsSetupError(f"task_start recv failed: {exc}") from exc
            if started.get("event") not in ("task_started", "connected_success"):
                if started.get("base_resp", {}).get("status_code", 0) not in (0,):
                    raise _WsSetupError(f"task_start failed: {started}")

            # 握手成功后再消费 text_iter，失败时可回退 HTTP
            audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
            err_box: list[BaseException] = []

            async def reader() -> None:
                try:
                    while True:
                        if cancel_event and cancel_event.is_set():
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            err_box.append(RuntimeError("MiniMax WS recv timeout"))
                            break
                        except websockets.exceptions.ConnectionClosed:
                            break
                        msg = json.loads(raw)
                        event = msg.get("event") or ""
                        br = msg.get("base_resp") or {}
                        code = br.get("status_code", 0)
                        # 2203 空文本跳过 / 2204 超限跳过：不中断整段会话
                        if code not in (0, None, 2203, 2204):
                            err_box.append(
                                RuntimeError(f"MiniMax WS error: {br or msg}")
                            )
                            break
                        if event == "task_failed":
                            err_box.append(
                                RuntimeError(f"MiniMax WS task_failed: {br or msg}")
                            )
                            break
                        if event == "task_finished":
                            break
                        data = msg.get("data")
                        if isinstance(data, dict):
                            audio_hex = data.get("audio")
                            if audio_hex:
                                await audio_q.put(bytes.fromhex(audio_hex))
                except Exception as exc:
                    err_box.append(exc)
                finally:
                    await audio_q.put(None)

            reader_task = asyncio.create_task(reader())

            async def drain_available() -> AsyncIterator[bytes]:
                while True:
                    try:
                        item = audio_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        await audio_q.put(None)
                        return
                    yield item

            try:
                buf = ""
                async for piece in text_iter:
                    if cancel_event and cancel_event.is_set():
                        break
                    buf += piece
                    while True:
                        cut = _take_stream_piece(buf)
                        if not cut:
                            break
                        text, buf = cut
                        await ws.send(
                            json.dumps({"event": "task_continue", "text": text})
                        )
                        async for c in drain_available():
                            yield c
                        await asyncio.sleep(0)

                if buf.strip() and not (cancel_event and cancel_event.is_set()):
                    await ws.send(
                        json.dumps(
                            {"event": "task_continue", "text": buf.strip()}
                        )
                    )

                if not (cancel_event and cancel_event.is_set()):
                    await ws.send(json.dumps({"event": "task_finish"}))

                while True:
                    item = await audio_q.get()
                    if item is None:
                        break
                    yield item

                if err_box:
                    raise err_box[0]
            finally:
                if not reader_task.done():
                    reader_task.cancel()
                    try:
                        await reader_task
                    except asyncio.CancelledError:
                        pass
        finally:
            await ws_cm.__aexit__(None, None, None)

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

    async def _http_synthesize(
        self,
        text: str,
        sample_rate: int,
        cancel_event: asyncio.Event | None,
    ) -> AsyncIterator[bytes]:
        url = f"{self._cfg.http_base.rstrip('/')}/t2a_v2"
        if self._cfg.group_id:
            url = f"{url}?GroupId={self._cfg.group_id}"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._cfg.model,
            "text": text,
            "stream": False,
            "voice_setting": self._voice_setting(),
            "audio_setting": {
                "sample_rate": sample_rate,
                "format": "pcm",
                "channel": 1,
            },
        }
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            if cancel_event and cancel_event.is_set():
                return
            msg = resp.json()
            br = msg.get("base_resp") or {}
            if br.get("status_code", 0) not in (0, None):
                raise RuntimeError(f"MiniMax HTTP TTS failed: {br}")
            data = msg.get("data") or {}
            audio_hex = data.get("audio")
            if audio_hex:
                yield bytes.fromhex(audio_hex)


class _WsSetupError(RuntimeError):
    """握手阶段失败，允许回退 HTTP（此时尚未消费 text_iter）。"""


def _take_stream_piece(buf: str) -> tuple[str, str] | None:
    """句末标点优先；否则满 _STREAM_MAX 字切一段。"""
    for i, ch in enumerate(buf):
        if ch in _DELIMS and i + 1 >= _STREAM_MIN:
            return buf[: i + 1], buf[i + 1 :]
    if len(buf) >= _STREAM_MAX:
        return buf[:_STREAM_MAX], buf[_STREAM_MAX:]
    return None
