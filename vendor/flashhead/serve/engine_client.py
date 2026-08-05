"""Gateway → FlashHead Engine HTTP 客户端。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from serve.engine_codec import EncodedChunk, unpack_encoded_chunks

logger = logging.getLogger(__name__)


class EngineClient:
    def __init__(self, base_url: str, *, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._meta: dict[str, Any] = {}

    def close(self) -> None:
        self._http.close()

    def refresh_health(self) -> dict[str, Any]:
        r = self._http.get("/v1/health")
        r.raise_for_status()
        self._meta = r.json()
        return self._meta

    @property
    def ready(self) -> bool:
        try:
            d = self.refresh_health()
            return bool(d.get("ready") or d.get("status") == "ok")
        except Exception:
            return False

    @property
    def meta(self) -> dict[str, Any]:
        if not self._meta:
            try:
                self.refresh_health()
            except Exception as exc:
                logger.warning("engine health: %s", exc)
        return self._meta

    def reset_session(self) -> dict[str, Any]:
        r = self._http.post("/v1/session/reset")
        r.raise_for_status()
        return r.json()

    def infer_pcm(
        self, pcm: bytes, *, sample_rate: int = 16000, end: bool = False
    ) -> list[EncodedChunk]:
        r = self._http.post(
            "/v1/infer/pcm",
            params={"end": 1 if end else 0, "sample_rate": int(sample_rate)},
            content=pcm or b"",
            headers={"Content-Type": "application/octet-stream"},
        )
        r.raise_for_status()
        return unpack_encoded_chunks(r.content)
