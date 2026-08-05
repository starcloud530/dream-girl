from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import pathlib
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from packages.config import load_config

logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
cfg = load_config()

_LT_INTERNAL = os.environ.get("CYBER_GF_LT_BASE", "http://127.0.0.1:6006").rstrip("/")
_LT_PUBLIC = os.environ.get(
    "CYBER_GF_LT_PUBLIC",
    cfg.livetalking_base_url if "127.0.0.1" not in cfg.livetalking_base_url else _LT_INTERNAL,
).rstrip("/")

_ASSETS_DIR = pathlib.Path(__file__).resolve().parents[2] / "assets"
_PCM_FRAME_BYTES = 640  # 20ms @ 16kHz mono s16le = 320 * 2


@dataclass
class AvatarSession:
    session_id: str
    livetalking_session_id: str
    avatar_id: str
    pcm_buffer: bytearray = field(default_factory=bytearray)
    flush_task: asyncio.Task | None = None
    pcm_started: bool = False


sessions: dict[str, AvatarSession] = {}


class CreateSessionRequest(BaseModel):
    avatar_id: str = "xiaoya_v1"


app = FastAPI(title="Avatar Gateway", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def cache_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers.setdefault(
            "Cache-Control", "public, max-age=86400, immutable"
        )
    return response


async def _livetalking_health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{_LT_INTERNAL}/index.html")
            return r.status_code < 500
    except Exception:
        return False


async def _lt_create_session(prefer_id: str | None = None) -> str:
    """Create LT session so PCM and WebRTC share the same sessionid.

    If LT hits max_session, reuse an existing idle session instead of failing.
    """
    payload: dict[str, Any] = {}
    if prefer_id:
        payload["sessionid"] = prefer_id
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{_LT_INTERNAL}/session/create", json=payload)
        text = r.text[:300]
        if r.status_code >= 400:
            # try reuse existing
            reused = await _lt_reuse_session(client)
            if reused:
                logger.warning("LT create HTTP %s — reused session %s", r.status_code, reused)
                return reused
            logger.warning("LT session/create HTTP %s: %s", r.status_code, text)
            raise HTTPException(503, f"LT_SESSION_CREATE_HTTP_{r.status_code}")
        try:
            data = r.json()
        except Exception as exc:
            raise HTTPException(503, f"LT_SESSION_CREATE_BAD_JSON: {exc}") from exc
        if isinstance(data, dict) and data.get("code", 0) not in (0, None):
            reused = await _lt_reuse_session(client)
            if reused:
                logger.warning("LT create biz fail (%s) — reused %s", data.get("msg"), reused)
                return reused
            logger.warning("LT session/create biz fail: %s", text)
            raise HTTPException(503, f"LT_SESSION_FULL: {data.get('msg')}")
        sid = (data.get("data") or {}).get("sessionid") or data.get("sessionid")
        if not sid:
            raise HTTPException(503, "LT_SESSION_CREATE_NO_ID")
        return str(sid)


async def _lt_reuse_session(client: httpx.AsyncClient) -> str | None:
    """Pick an existing LT session when max_session is hit."""
    try:
        r = await client.get(f"{_LT_INTERNAL}/api/admin/sessions")
        if r.status_code >= 400:
            return None
        data = r.json()
        items = (data.get("data") or {}).get("sessions") or []
        if not items:
            return None
        # prefer idle (not speaking)
        for it in items:
            if not it.get("speaking"):
                return str(it.get("sessionid") or "")
        return str(items[0].get("sessionid") or "") or None
    except Exception as exc:
        logger.debug("lt reuse failed: %s", exc)
        return None


async def _livetalking_pcm(
    lt_session_id: str, pcm: bytes, *, end: bool = False
) -> None:
    """Forward raw s16le to LiveTalking /humanaudio_pcm (20ms framing inside LT)."""
    url = f"{_LT_INTERNAL}/humanaudio_pcm"
    params = {"sessionid": str(lt_session_id), "end": "1" if end else "0"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, params=params, content=pcm or b"")
        if r.status_code >= 400:
            # fallback: wrap WAV for old humanaudio
            if end and not pcm:
                return
            logger.warning("humanaudio_pcm %s: %s — fallback wav", r.status_code, r.text[:160])
            wav = _pcm_to_wav(pcm) if pcm else None
            if wav:
                await _livetalking_human_audio(lt_session_id, wav)


async def _livetalking_human_audio(session_id: str, wav_bytes: bytes) -> None:
    url = f"{_LT_INTERNAL}/humanaudio"
    files = {"file": ("chunk.wav", wav_bytes, "audio/wav")}
    data = {"sessionid": str(session_id)}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, data=data, files=files)
        if r.status_code >= 400:
            logger.warning("humanaudio %s: %s", r.status_code, r.text[:200])


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _forward_pcm(session: AvatarSession, chunk: bytes, *, end: bool = False) -> None:
    """Align to 20ms and push; end flushes residual."""
    if chunk:
        session.pcm_buffer.extend(chunk)
    # push whole buffer regularly — LT does frame split; keep gateway latency low
    if len(session.pcm_buffer) >= _PCM_FRAME_BYTES or (end and session.pcm_buffer):
        data = bytes(session.pcm_buffer)
        session.pcm_buffer.clear()
        await _livetalking_pcm(session.livetalking_session_id, data, end=False)
    if end:
        await _livetalking_pcm(session.livetalking_session_id, b"", end=True)


@app.get("/v1/health")
async def health() -> dict:
    ok = await _livetalking_health()
    return {
        "status": "ok" if ok else "degraded",
        "livetalking_ready": ok,
        "avatar_id": cfg.livetalking_avatar_id,
        "gpu": "autodl",
        "livetalking_base_url": _LT_PUBLIC,
        "pcm_ws": True,
    }


@app.post("/v1/avatar/session")
async def create_session(body: CreateSessionRequest | None = None) -> dict:
    sid = uuid.uuid4().hex
    avatar_id = (body.avatar_id if body else None) or cfg.livetalking_avatar_id
    lt_sid = await _lt_create_session()
    sessions[sid] = AvatarSession(
        session_id=sid,
        livetalking_session_id=str(lt_sid),
        avatar_id=avatar_id,
    )
    return {
        "session_id": sid,
        "livetalking_session_id": str(lt_sid),
        "webrtc_url": f"{_LT_PUBLIC}/webrtcapi.html",
        "livetalking_base_url": _LT_PUBLIC,
        "audio_ws_path": f"/v1/avatar/{sid}/audio/ws",
        "frames_ws_path": f"/v1/avatar/{sid}/frames/ws",
    }


@app.websocket("/v1/avatar/{session_id}/audio/ws")
async def audio_ws(websocket: WebSocket, session_id: str) -> None:
    st = sessions.get(session_id)
    if not st:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await _forward_pcm(st, msg["bytes"], end=False)
            elif msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if data.get("end") or data.get("type") == "end":
                    await _forward_pcm(st, b"", end=True)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await _forward_pcm(st, b"", end=True)
        except Exception as exc:
            logger.debug("pcm ws end flush: %s", exc)


@app.websocket("/v1/avatar/{session_id}/frames/ws")
async def frames_ws(websocket: WebSocket, session_id: str) -> None:
    """Pull LT /frame.jpg and push JPEG binary to browser; log every ~2s."""
    from starlette.websockets import WebSocketState

    st = sessions.get(session_id)
    if not st:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    fps = 15
    try:
        q = websocket.query_params.get("fps")
        if q:
            fps = max(5, min(25, int(q)))
    except Exception:
        pass
    interval = 1.0 / fps
    sent = 0
    empty = 0
    logger.info(
        "frames_ws OPEN session=%s lt=%s fps=%s",
        session_id[:8],
        st.livetalking_session_id,
        fps,
    )

    async def _watch_client() -> None:
        """Drain client messages so we notice disconnect promptly."""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            return
        except Exception:
            return

    watcher = asyncio.create_task(_watch_client())
    try:
        await websocket.send_json(
            {
                "type": "status",
                "event": "open",
                "lt_session_id": st.livetalking_session_id,
                "fps": fps,
            }
        )
        async with httpx.AsyncClient(timeout=5) as client:
            while not watcher.done():
                if websocket.client_state != WebSocketState.CONNECTED:
                    break
                try:
                    r = await client.get(
                        f"{_LT_INTERNAL}/frame.jpg",
                        params={"sessionid": st.livetalking_session_id},
                    )
                    if websocket.client_state != WebSocketState.CONNECTED or watcher.done():
                        break
                    if r.status_code == 200 and len(r.content) > 100:
                        await websocket.send_bytes(r.content)
                        sent += 1
                        empty = 0
                        if sent == 1 or sent % 30 == 0:
                            logger.info(
                                "frames_ws SEND session=%s n=%s bytes=%s",
                                session_id[:8],
                                sent,
                                len(r.content),
                            )
                    else:
                        empty += 1
                        if empty == 1 or empty % 30 == 0:
                            logger.info(
                                "frames_ws EMPTY session=%s status=%s empty=%s",
                                session_id[:8],
                                r.status_code,
                                empty,
                            )
                except WebSocketDisconnect:
                    break
                except RuntimeError as exc:
                    # "Cannot call send once a close message has been sent"
                    logger.info("frames_ws stop (closed): %s", exc)
                    break
                except Exception as exc:
                    logger.warning("frames_ws pull/send error: %s", exc)
                    break
                await asyncio.sleep(interval)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("frames_ws abort: %s", exc)
    finally:
        if not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except Exception:
                pass
        logger.info(
            "frames_ws CLOSE session=%s sent=%s empty=%s",
            session_id[:8],
            sent,
            empty,
        )


@app.post("/v1/avatar/{session_id}/audio", status_code=204)
async def push_audio(session_id: str, request: Request) -> None:
    """HTTP 兜底（兼容旧客户端）；优先走 /audio/ws。"""
    st = sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    chunk = await request.body()
    if chunk:
        await _forward_pcm(st, chunk, end=False)


@app.post("/v1/avatar/{session_id}/audio/wav", status_code=204)
async def push_wav(session_id: str, request: Request) -> None:
    st = sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    wav = await request.body()
    await _livetalking_human_audio(st.livetalking_session_id, wav)


@app.post("/v1/avatar/{session_id}/interrupt", status_code=204)
async def interrupt(session_id: str) -> None:
    st = sessions.get(session_id)
    if not st:
        return
    st.pcm_buffer.clear()
    try:
        await _livetalking_pcm(st.livetalking_session_id, b"", end=True)
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_LT_INTERNAL}/interrupt_talk",
                json={"sessionid": st.livetalking_session_id},
            )
    except Exception as exc:
        logger.debug("interrupt forward: %s", exc)


@app.post("/v1/avatar/{session_id}/webrtc/offer")
async def webrtc_offer(session_id: str, body: dict[str, Any]) -> dict:
    st = sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_LT_INTERNAL}/offer",
            json={**body, "sessionid": st.livetalking_session_id},
        )
        r.raise_for_status()
        return r.json()


if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


def main() -> None:
    import uvicorn
    import yaml

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    host = "0.0.0.0"
    port = int(os.environ.get("AVATAR_GATEWAY_PORT", "6008"))
    app_yaml = pathlib.Path(__file__).resolve().parents[2] / "config" / "app.yaml"
    if app_yaml.exists():
        raw = yaml.safe_load(app_yaml.read_text(encoding="utf-8")) or {}
        port = int((raw.get("avatar_gateway") or {}).get("port", port))
    uvicorn.run("services.avatar_gateway.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
