from __future__ import annotations

import asyncio
import math
import struct
from typing import AsyncIterator


class MockLLMProvider:
    def __init__(self, reply: str = "你好呀，我是小雅。今天想聊点什么？") -> None:
        self._reply = reply

    async def chat_stream(
        self,
        messages: list,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        for ch in self._reply:
            if cancel_event and cancel_event.is_set():
                return
            yield ch
            await asyncio.sleep(0.02)


class MockTTSProvider:
    """Generate a simple sine tone per character chunk (16kHz mono s16le)."""

    def __init__(self, duration_per_char: float = 0.06) -> None:
        self._dur = duration_per_char

    async def synthesize_stream(
        self,
        text: str,
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        frames = int(sample_rate * self._dur * max(len(text), 1))
        freq = 440.0
        for i in range(0, frames, 320):
            if cancel_event and cancel_event.is_set():
                return
            chunk = []
            for j in range(i, min(i + 320, frames)):
                val = int(8000 * math.sin(2 * math.pi * freq * j / sample_rate))
                chunk.append(val)
            yield struct.pack(f"<{len(chunk)}h", *chunk)
            await asyncio.sleep(0)

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
            if len(buf) >= 4:
                async for c in self.synthesize_stream(
                    buf, sample_rate=sample_rate, cancel_event=cancel_event
                ):
                    yield c
                buf = ""
        if buf.strip():
            async for c in self.synthesize_stream(
                buf, sample_rate=sample_rate, cancel_event=cancel_event
            ):
                yield c
