from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

from packages.interfaces import ChatMessage
from packages.orchestrator.pcm_pacer import PcmPacer

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    avatar_session_id: str
    livetalking_session_id: str | int = 0
    messages: list[ChatMessage] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    turn_task: asyncio.Task | None = None


class SentenceBuffer:
    def __init__(self, min_chars: int, max_chars: int, delimiters: str) -> None:
        self._min = min_chars
        self._max = max_chars
        self._delims = delimiters
        self._buf = ""

    def push(self, token: str) -> list[str]:
        self._buf += token
        out: list[str] = []
        while True:
            sentence = self._pop_one()
            if not sentence:
                break
            out.append(sentence)
        return out

    def flush(self) -> str | None:
        s = self._buf.strip()
        self._buf = ""
        return s or None

    def _pop_one(self) -> str | None:
        for i, ch in enumerate(self._buf):
            if ch in self._delims and i + 1 >= self._min:
                s = self._buf[: i + 1].strip()
                self._buf = self._buf[i + 1 :]
                return s if s else None
        if len(self._buf) >= self._max:
            s = self._buf[: self._max].strip()
            self._buf = self._buf[self._max :]
            return s if s else None
        return None


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def create(self, avatar_session_id: str, livetalking_session_id: str | int = 0) -> SessionState:
        sid = uuid.uuid4().hex
        st = SessionState(
            session_id=sid,
            avatar_session_id=avatar_session_id,
            livetalking_session_id=livetalking_session_id,
        )
        self._sessions[sid] = st
        return st

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)


class DialoguePipeline:
    def __init__(
        self,
        *,
        llm,
        tts,
        avatar_client,
        system_prompt: str,
        sentence_min: int,
        sentence_max: int,
        sentence_delims: str,
        avatar_mode: str = "browser",
        tts_format: str = "audio/mpeg",
        pcm_pacer_enabled: bool = True,
        pcm_pacer_preroll_ms: int = 1200,
        pcm_pacer_quantum_ms: int = 100,
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._avatar = avatar_client
        self._system = system_prompt
        self._sentence_min = sentence_min
        self._sentence_max = sentence_max
        self._sentence_delims = sentence_delims
        self._avatar_mode = avatar_mode
        self._tts_format = tts_format
        self._pcm_pacer_enabled = pcm_pacer_enabled
        self._pcm_pacer_preroll_ms = pcm_pacer_preroll_ms
        self._pcm_pacer_quantum_ms = pcm_pacer_quantum_ms

    async def run_turn(
        self,
        session: SessionState,
        user_text: str,
        emit: EventEmitter,
    ) -> None:
        await self._interrupt_turn(session)
        # 每轮开场：清空 Gateway/Engine 缓冲与 chunk 序号，前端 MSE 同步重建
        if self._avatar_mode == "gpu" and session.avatar_session_id not in (
            "",
            "local-noop",
            "noop",
        ):
            try:
                await self._avatar.interrupt(session.avatar_session_id)
            except Exception:
                logger.warning(
                    "avatar turn_reset failed session=%s",
                    session.session_id[:8],
                    exc_info=True,
                )
        session.cancel_event = asyncio.Event()
        cancel = session.cancel_event

        session.messages.append(ChatMessage("user", user_text))
        await emit(_event(session.session_id, "state", {"state": "thinking"}))

        history = [ChatMessage("system", self._system), *session.messages]
        assistant_parts: list[str] = []

        await emit(_event(session.session_id, "state", {"state": "speaking"}))
        t0 = time.monotonic()
        # 管线 RTF 监控：LLM / TTS 墙钟 vs 音频时长
        llm_first_ms: int | None = None
        llm_done_ms: int | None = None
        tts_first_ms: int | None = None
        tts_last_ms: int | None = None
        pcm_bytes = 0
        sample_rate = 16000

        async def token_iter() -> AsyncIterator[str]:
            nonlocal llm_first_ms, llm_done_ms
            # 直接把 LLM token 喂给 TTS（MiniMax WS 实时流，按句/标点切片）
            async for token in self._llm.chat_stream(history, cancel_event=cancel):
                if llm_first_ms is None:
                    llm_first_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "RTF llm_ttft_ms=%s session=%s",
                        llm_first_ms,
                        session.session_id[:8],
                    )
                assistant_parts.append(token)
                await emit(_event(session.session_id, "assistant_delta", {"text": token}))
                yield token
            llm_done_ms = int((time.monotonic() - t0) * 1000)

        push_gpu = self._avatar_mode == "gpu"
        if session.avatar_session_id in ("", "local-noop", "noop"):
            push_gpu = False
        audio_chunks = 0
        first_audio = True
        use_pacer = self._pcm_pacer_enabled
        pacer = (
            PcmPacer(
                sample_rate=sample_rate,
                preroll_ms=self._pcm_pacer_preroll_ms,
                quantum_ms=self._pcm_pacer_quantum_ms,
            )
            if use_pacer
            else None
        )

        async def _deliver(chunk: bytes) -> None:
            nonlocal push_gpu, audio_chunks, first_audio, pcm_bytes, tts_first_ms, tts_last_ms
            if not chunk or cancel.is_set():
                return
            audio_chunks += 1
            pcm_bytes += len(chunk)
            now_ms = int((time.monotonic() - t0) * 1000)
            if tts_first_ms is None:
                tts_first_ms = now_ms
            tts_last_ms = now_ms
            if not push_gpu:
                await emit(
                    _event(
                        session.session_id,
                        "assistant_audio",
                        {
                            "format": self._tts_format,
                            "data": base64.b64encode(chunk).decode("ascii"),
                        },
                    )
                )
            else:
                try:
                    await self._avatar.push_pcm(session.avatar_session_id, chunk)
                except Exception as exc:
                    if first_audio:
                        await emit(
                            _event(
                                session.session_id,
                                "error",
                                {
                                    "code": "AVATAR_PUSH_FAILED",
                                    "message": f"嘴型推流失败（浏览器仍播声音）: {exc}",
                                },
                            )
                        )
                    push_gpu = False
                    await emit(
                        _event(
                            session.session_id,
                            "assistant_audio",
                            {
                                "format": self._tts_format,
                                "data": base64.b64encode(chunk).decode("ascii"),
                            },
                        )
                    )
            if first_audio:
                first_audio = False
                await emit(
                    _event(
                        session.session_id,
                        "metrics",
                        {
                            "first_audio_ms": tts_first_ms,
                            "llm_ttft_ms": llm_first_ms,
                            "pcm_pacer": use_pacer,
                            "pcm_buffer_ms": pacer.buffered_ms if pacer else 0,
                        },
                    )
                )

        if pacer is None:
            async for chunk in self._tts.synthesize_text_stream(
                token_iter(), sample_rate=sample_rate, cancel_event=cancel
            ):
                if cancel.is_set():
                    break
                await _deliver(chunk)
        else:
            # 生产者：TTS 突发入队；消费者：墙钟匀速出队
            async def _produce() -> None:
                try:
                    async for chunk in self._tts.synthesize_text_stream(
                        token_iter(), sample_rate=sample_rate, cancel_event=cancel
                    ):
                        if cancel.is_set():
                            break
                        if chunk:
                            await pacer.feed(chunk)
                finally:
                    await pacer.end()

            prod = asyncio.create_task(_produce())
            try:
                async for chunk in pacer.paced_chunks(cancel):
                    await _deliver(chunk)
            finally:
                await pacer.close()
                if not prod.done():
                    prod.cancel()
                    try:
                        await prod
                    except (asyncio.CancelledError, Exception):
                        pass

        end_pcm_ms: int | None = None
        if push_gpu:
            t_end = time.monotonic()
            try:
                end = getattr(self._avatar, "end_pcm", None)
                if end:
                    # Gateway 异步 flush：只等入队
                    await asyncio.wait_for(end(session.avatar_session_id), timeout=1.0)
            except Exception as exc:
                logger.debug("end_pcm skip: %s", exc)
            end_pcm_ms = int((time.monotonic() - t_end) * 1000)

        turn_ms = int((time.monotonic() - t0) * 1000)
        audio_ms = int(pcm_bytes / (sample_rate * 2) * 1000) if pcm_bytes else 0
        text = "".join(assistant_parts)
        chars = len(text)
        # RTF>1 = 墙钟慢于音频时长（跟不上实时）
        llm_wall = llm_done_ms if llm_done_ms is not None else turn_ms
        tts_span = (
            (tts_last_ms - tts_first_ms)
            if tts_first_ms is not None and tts_last_ms is not None
            else 0
        )
        tail_ms = (
            (turn_ms - tts_last_ms) if tts_last_ms is not None else turn_ms
        )
        llm_rtf = (llm_wall / audio_ms) if audio_ms > 0 else None
        tts_rtf = (tts_span / audio_ms) if audio_ms > 0 else None
        pipe_rtf = (turn_ms / audio_ms) if audio_ms > 0 else None
        logger.info(
            "RTF turn session=%s chars=%s audio_ms=%s llm_ttft_ms=%s llm_done_ms=%s "
            "llm_rtf=%s tts_first_ms=%s tts_span_ms=%s tts_rtf=%s tail_ms=%s end_pcm_ms=%s "
            "pipe_rtf=%s chunks=%s pacer=%s",
            session.session_id[:8],
            chars,
            audio_ms,
            llm_first_ms,
            llm_done_ms,
            f"{llm_rtf:.3f}" if llm_rtf is not None else "n/a",
            tts_first_ms,
            tts_span,
            f"{tts_rtf:.3f}" if tts_rtf is not None else "n/a",
            tail_ms,
            end_pcm_ms,
            f"{pipe_rtf:.3f}" if pipe_rtf is not None else "n/a",
            audio_chunks,
            use_pacer,
        )
        if llm_rtf is not None and llm_rtf > 1.0:
            logger.warning(
                "RTF llm>1 session=%s llm_rtf=%.3f (LLM 墙钟 > 音频时长)",
                session.session_id[:8],
                llm_rtf,
            )
        if tts_rtf is not None and tts_rtf > 1.0:
            logger.warning(
                "RTF tts>1 session=%s tts_rtf=%.3f (TTS 出声跨度 > 音频时长)",
                session.session_id[:8],
                tts_rtf,
            )
        if tail_ms > 1500:
            logger.warning(
                "RTF tail session=%s tail_ms=%s end_pcm_ms=%s (收尾/FlashHead flush 偏慢)",
                session.session_id[:8],
                tail_ms,
                end_pcm_ms,
            )

        if assistant_parts and not cancel.is_set():
            session.messages.append(ChatMessage("assistant", text))

        await emit(
            _event(
                session.session_id,
                "assistant_done",
                {
                    "audio_chunks": audio_chunks,
                    "audio_ms": audio_ms,
                    "llm_ttft_ms": llm_first_ms,
                    "llm_done_ms": llm_done_ms,
                    "llm_rtf": round(llm_rtf, 3) if llm_rtf is not None else None,
                    "tts_first_ms": tts_first_ms,
                    "tts_rtf": round(tts_rtf, 3) if tts_rtf is not None else None,
                    "tail_ms": tail_ms,
                    "end_pcm_ms": end_pcm_ms,
                    "pipe_rtf": round(pipe_rtf, 3) if pipe_rtf is not None else None,
                },
            )
        )
        if audio_chunks == 0:
            await emit(
                _event(
                    session.session_id,
                    "error",
                    {
                        "code": "TTS_EMPTY",
                        "message": "TTS 未产出音频，请检查 MiniMax / Edge TTS",
                    },
                )
            )
        await emit(_event(session.session_id, "state", {"state": "idle"}))

    async def interrupt(self, session: SessionState) -> None:
        await self._interrupt_turn(session)
        await self._avatar.interrupt(session.avatar_session_id)

    async def _interrupt_turn(self, session: SessionState) -> None:
        session.cancel_event.set()
        if session.turn_task and not session.turn_task.done():
            session.turn_task.cancel()
            try:
                await session.turn_task
            except asyncio.CancelledError:
                pass
        session.turn_task = None


def _event(session_id: str, typ: str, payload: dict) -> dict:
    return {
        "type": typ,
        "session_id": session_id,
        "ts": time.time(),
        "payload": payload,
    }
