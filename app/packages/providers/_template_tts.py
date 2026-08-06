"""Copy-paste TTS provider stub — not registered in factory.py by default.

See docs/providers.md → «Adding a TTS provider».
Wire-up: implement → register in factory.build_tts() → add yaml under tts:.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator


class TemplateTTSProvider:
    """Minimal TTSProvider-shaped stub.

    Yield 16-bit LE mono PCM at ``sample_rate`` (Avatar ingest expects 16 kHz).
    Replace the body with your cloud / local API calls.
    """

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def synthesize_stream(
        self,
        text: str,
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        if cancel_event and cancel_event.is_set():
            return
        # TODO: call upstream TTS; yield PCM chunks (s16le mono).
        # Example silence frame (~20 ms @ 16 kHz) so the type-check path runs:
        _ = sample_rate
        yield b"\x00\x00" * 320
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
            # Flush on a short buffer; real providers usually flush on sentence delimiters.
            if len(buf) >= 4:
                async for chunk in self.synthesize_stream(
                    buf, sample_rate=sample_rate, cancel_event=cancel_event
                ):
                    yield chunk
                buf = ""
        if buf.strip():
            async for chunk in self.synthesize_stream(
                buf, sample_rate=sample_rate, cancel_event=cancel_event
            ):
                yield chunk
