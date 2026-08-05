from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class ChatMessage:
    role: str
    content: str


class LLMProvider(Protocol):
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        ...


class TTSProvider(Protocol):
    async def synthesize_stream(
        self,
        text: str,
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        ...

    async def synthesize_text_stream(
        self,
        text_iter: AsyncIterator[str],
        *,
        sample_rate: int = 16000,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        ...


class AvatarClient(Protocol):
    async def create_session(self, avatar_id: str | None = None) -> dict:
        ...

    async def push_pcm(self, session_id: str, chunk: bytes) -> None:
        ...

    async def end_pcm(self, session_id: str) -> None:
        ...

    async def push_wav(self, session_id: str, wav_bytes: bytes) -> None:
        ...

    async def interrupt(self, session_id: str) -> None:
        ...

    async def health(self) -> dict:
        ...
