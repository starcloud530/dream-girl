from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class HttpAvatarClient:
    """纯 HTTP：PCM POST +（可选）健康检查。不再依赖 WebSocket / SSH 隧道。"""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Gateway 已异步推理，PCM POST 应秒回；勿用长 read 拖死 TTS
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0, read=15.0),
                proxy=None,
                trust_env=False,  # 忽略系统代理，直连 AutoDL 公网
            )
        return self._client

    async def create_session(self, avatar_id: str | None = None) -> dict:
        payload = {}
        if avatar_id:
            payload["avatar_id"] = avatar_id
        r = await self._http().post(f"{self._base}/v1/avatar/session", json=payload)
        if r.status_code >= 400:
            raise RuntimeError(
                f"avatar session HTTP {r.status_code}: {r.text[:300]}"
            )
        try:
            return r.json()
        except Exception as exc:
            raise RuntimeError(
                f"avatar session bad JSON: {exc}; body={r.text[:200]!r}"
            ) from exc

    async def push_pcm(self, session_id: str, chunk: bytes) -> None:
        if not chunk:
            return
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Audio-Format": "audio/L16; rate=16000; channels=1",
        }
        r = await self._http().post(
            f"{self._base}/v1/avatar/{session_id}/audio",
            content=chunk,
            headers=headers,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"pcm HTTP {r.status_code}: {r.text[:200]}")

    async def end_pcm(self, session_id: str) -> None:
        """通知 Gateway flush；异步入队后应立刻返回，不阻塞编排器收尾。"""
        try:
            r = await self._http().post(
                f"{self._base}/v1/avatar/{session_id}/audio?end=1",
                content=b"",
                headers={"Content-Type": "application/octet-stream"},
                timeout=5.0,
            )
            if r.status_code >= 400:
                logger.debug("end_pcm HTTP %s", r.status_code)
        except Exception as exc:
            logger.debug("end_pcm: %s", exc)

    async def push_wav(self, session_id: str, wav_bytes: bytes) -> None:
        r = await self._http().post(
            f"{self._base}/v1/avatar/{session_id}/audio/wav",
            content=wav_bytes,
            headers={"Content-Type": "audio/wav"},
        )
        r.raise_for_status()

    async def interrupt(self, session_id: str) -> None:
        r = await self._http().post(f"{self._base}/v1/avatar/{session_id}/interrupt")
        r.raise_for_status()

    async def health(self) -> dict:
        r = await self._http().get(f"{self._base}/v1/health")
        r.raise_for_status()
        return r.json()


class NoOpAvatarClient:
    """Used when avatar service is unreachable (local dev)."""

    async def create_session(self, avatar_id: str | None = None) -> dict:
        return {
            "session_id": "local-noop",
            "livetalking_session_id": "0",
            "webrtc_url": "",
            "livetalking_base_url": "",
        }

    async def push_pcm(self, session_id: str, chunk: bytes) -> None:
        return None

    async def end_pcm(self, session_id: str) -> None:
        return None

    async def push_wav(self, session_id: str, wav_bytes: bytes) -> None:
        return None

    async def interrupt(self, session_id: str) -> None:
        return None

    async def health(self) -> dict:
        return {"status": "noop"}
