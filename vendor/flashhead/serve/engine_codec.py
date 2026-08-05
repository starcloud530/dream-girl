"""FlashHead Engine ↔ Gateway 二进制分片协议（本机 IPC）。

FH02：meta + pcm + mp4 + jpeg（不再传 raw 帧，避免每片 ~22MB）。
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

MAGIC = b"FH02"


@dataclass
class EncodedChunk:
    meta: dict[str, Any]
    pcm: bytes
    mp4: bytes
    jpeg: bytes = b""


def pack_encoded_chunks(items: list[EncodedChunk]) -> bytes:
    out = bytearray(MAGIC)
    out += struct.pack("<I", len(items))
    for it in items:
        m = dict(it.meta)
        mb = json.dumps(m, separators=(",", ":")).encode("utf-8")
        pcm_b = it.pcm or b""
        mp4_b = it.mp4 or b""
        jpeg_b = it.jpeg or b""
        out += struct.pack("<IIII", len(mb), len(pcm_b), len(mp4_b), len(jpeg_b))
        out += mb
        out += pcm_b
        out += mp4_b
        out += jpeg_b
    return bytes(out)


def unpack_encoded_chunks(data: bytes) -> list[EncodedChunk]:
    if len(data) < 8 or data[:4] != MAGIC:
        raise ValueError(f"bad engine chunk magic {data[:4]!r}, want {MAGIC!r}")
    (n,) = struct.unpack_from("<I", data, 4)
    off = 8
    out: list[EncodedChunk] = []
    for _ in range(int(n)):
        if off + 16 > len(data):
            raise ValueError("truncated chunk header")
        meta_len, pcm_len, mp4_len, jpeg_len = struct.unpack_from("<IIII", data, off)
        off += 16
        mb = data[off : off + meta_len]
        off += meta_len
        pcm = data[off : off + pcm_len]
        off += pcm_len
        mp4 = data[off : off + mp4_len]
        off += mp4_len
        jpeg = data[off : off + jpeg_len]
        off += jpeg_len
        meta = json.loads(mb.decode("utf-8"))
        out.append(
            EncodedChunk(
                meta=meta, pcm=bytes(pcm), mp4=bytes(mp4), jpeg=bytes(jpeg)
            )
        )
    return out
