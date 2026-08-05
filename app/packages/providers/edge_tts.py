from __future__ import annotations

import asyncio
from typing import AsyncIterator

import edge_tts

from packages.config import EdgeTTSConfig


class EdgeTTSProvider:
    """Microsoft Edge TTS — reliable local fallback when MiniMax unavailable."""

    def __init__(self, cfg: EdgeTTSConfig) -> None:
        self._cfg = cfg

    async def synthesize_stream(
        self,
        text: str,
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        communicate = edge_tts.Communicate(text, voice=self._cfg.voice, rate=self._cfg.rate)
        async for chunk in communicate.stream():
            if cancel_event and cancel_event.is_set():
                return
            if chunk["type"] == "audio":
                yield chunk["data"]

    async def synthesize_text_stream(
        self,
        text_iter: AsyncIterator[str],
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        buf = ""
        async for piece in text_iter:
            if cancel_event and cancel_event.is_set():
                return
            buf += piece
            while True:
                cut = _take_sentence(buf)
                if not cut:
                    break
                sentence, buf = cut
                async for chunk in self.synthesize_stream(
                    sentence, sample_rate=sample_rate, cancel_event=cancel_event
                ):
                    yield chunk
        if buf.strip() and not (cancel_event and cancel_event.is_set()):
            async for chunk in self.synthesize_stream(
                buf.strip(), sample_rate=sample_rate, cancel_event=cancel_event
            ):
                yield chunk


def _take_sentence(buf: str) -> tuple[str, str] | None:
    for i, ch in enumerate(buf):
        if ch in "。！？；\n" and i >= 3:
            return buf[: i + 1], buf[i + 1 :]
    if len(buf) >= 48:
        return buf[:48], buf[48:]
    return None
