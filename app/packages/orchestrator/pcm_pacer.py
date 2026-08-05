"""PCM 匀速缓冲层：吸收 LLM/TTS 突发，按墙钟 1x 实时吐出。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import AsyncIterator, Deque


class PcmPacer:
    """
    入队任意大小的 s16le PCM；出队按 quantum 匀速。

    - preroll：攒够再开始播，避免开头一卡一卡
    - 墙钟对齐：elapsed * sample_rate 决定应吐出多少，超前则 sleep
    - 欠载：等更多数据（不填静音），宁可短暂停顿也不抖
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        preroll_ms: int = 1200,
        quantum_ms: int = 100,
        channels: int = 1,
        sample_width: int = 2,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.bytes_per_sec = self.sample_rate * channels * sample_width
        self.preroll_bytes = max(
            self.bytes_per_sec * int(preroll_ms) // 1000, self.bytes_per_sec // 10
        )
        self.quantum_bytes = max(
            self.bytes_per_sec * int(quantum_ms) // 1000, sample_width * channels * 160
        )
        # 对齐到 sample frame
        align = channels * sample_width
        self.quantum_bytes -= self.quantum_bytes % align
        self.preroll_bytes -= self.preroll_bytes % align

        self._chunks: Deque[bytes] = deque()
        self._buffered = 0
        self._cond = asyncio.Condition()
        self._ended = False
        self._closed = False

    @property
    def buffered_ms(self) -> int:
        if self.bytes_per_sec <= 0:
            return 0
        return int(self._buffered * 1000 / self.bytes_per_sec)

    async def feed(self, pcm: bytes) -> None:
        if not pcm or self._closed:
            return
        async with self._cond:
            self._chunks.append(pcm)
            self._buffered += len(pcm)
            self._cond.notify_all()

    async def end(self) -> None:
        async with self._cond:
            self._ended = True
            self._cond.notify_all()

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._ended = True
            self._chunks.clear()
            self._buffered = 0
            self._cond.notify_all()

    def _take(self, n: int) -> bytes:
        if n <= 0 or self._buffered <= 0:
            return b""
        n = min(n, self._buffered)
        align = 2  # s16le mono
        n -= n % align
        if n <= 0:
            return b""
        out = bytearray()
        while n > 0 and self._chunks:
            head = self._chunks[0]
            if len(head) <= n:
                out.extend(head)
                n -= len(head)
                self._buffered -= len(head)
                self._chunks.popleft()
            else:
                out.extend(head[:n])
                self._chunks[0] = head[n:]
                self._buffered -= n
                n = 0
        return bytes(out)

    async def paced_chunks(
        self, cancel: asyncio.Event | None = None
    ) -> AsyncIterator[bytes]:
        # 等预缓冲
        while True:
            if cancel and cancel.is_set():
                return
            async with self._cond:
                if self._closed:
                    return
                if self._buffered >= self.preroll_bytes or self._ended:
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass

        t0 = time.monotonic()
        sent = 0
        while True:
            if cancel and cancel.is_set():
                return
            if self._closed:
                return

            elapsed = time.monotonic() - t0
            target = int(elapsed * self.bytes_per_sec)
            # 允许略超前一个 quantum，避免调度抖动
            due = target + self.quantum_bytes - sent

            async with self._cond:
                ended = self._ended
                buffered = self._buffered

            if due < self.quantum_bytes and not (ended and buffered > 0):
                await asyncio.sleep(0.01)
                continue

            take_n = self.quantum_bytes
            if ended:
                # 收尾：有多少吐多少
                take_n = max(take_n, buffered) if buffered < self.quantum_bytes else take_n

            async with self._cond:
                if self._buffered <= 0:
                    if self._ended:
                        return
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=0.05)
                    except asyncio.TimeoutError:
                        pass
                    continue
                chunk = self._take(min(take_n, self._buffered))

            if not chunk:
                if ended:
                    return
                await asyncio.sleep(0.01)
                continue

            sent += len(chunk)
            yield chunk

            # 若仍超前墙钟，睡一会
            ahead = sent / self.bytes_per_sec - (time.monotonic() - t0)
            if ahead > 0.015:
                await asyncio.sleep(min(ahead, 0.05))
