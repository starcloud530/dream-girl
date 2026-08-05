from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from packages.avatar_client.client import HttpAvatarClient, NoOpAvatarClient
from packages.config import load_config
from packages.orchestrator.pipeline import DialoguePipeline, SessionManager
from packages.providers.factory import build_llm, build_tts

logger = logging.getLogger(__name__)

sessions = SessionManager()
ws_subscribers: dict[str, list[WebSocket]] = {}
pipeline: DialoguePipeline | None = None
tts_backend = "unknown"
cfg = load_config()


def _use_mock() -> bool:
    return os.environ.get("CYBER_GF_USE_MOCK", "").lower() in ("1", "true", "yes")


def _avatar_client():
    if _use_mock() or cfg.avatar_mode == "browser":
        return NoOpAvatarClient()
    return HttpAvatarClient(cfg.avatar_public_url)


async def _build_pipeline() -> DialoguePipeline:
    global tts_backend
    use_mock = _use_mock()
    llm = build_llm(cfg, use_mock=use_mock)
    tts, tts_backend = await build_tts(cfg, use_mock=use_mock)
    return DialoguePipeline(
        llm=llm,
        tts=tts,
        avatar_client=_avatar_client(),
        system_prompt=cfg.system_prompt,
        sentence_min=cfg.sentence_min_chars,
        sentence_max=cfg.sentence_max_chars,
        sentence_delims=cfg.sentence_delimiters,
        avatar_mode="browser" if cfg.avatar_mode == "browser" or use_mock else "gpu",
        # MiniMax streams raw PCM; Edge TTS streams mp3
        tts_format="audio/mpeg" if tts_backend == "edge" else "audio/L16;rate=16000;channels=1",
        pcm_pacer_enabled=cfg.pcm_pacer_enabled,
        pcm_pacer_preroll_ms=cfg.pcm_pacer_preroll_ms,
        pcm_pacer_quantum_ms=cfg.pcm_pacer_quantum_ms,
    )


async def _broadcast(session_id: str, event: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for ws in ws_subscribers.get(session_id, []):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_subscribers.get(session_id, []).remove(ws)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pipeline
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    pipeline = await _build_pipeline()
    logger.info(
        "pipeline ready llm=%s tts=%s avatar_mode=%s tts_format=%s",
        "mock" if _use_mock() else "deepseek",
        tts_backend,
        cfg.avatar_mode,
        "audio/mpeg" if tts_backend == "edge" else "pcm",
    )
    yield


app = FastAPI(title="Cyber GF Orchestrator", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache_web_shell(request, call_next):
    """避免浏览器 304 卡住旧 index / session.js，导致 frames_ws 永远不连。"""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.startswith("/ui/"):
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


class CreateSessionBody(BaseModel):
    avatar_id: str | None = None


class MessageBody(BaseModel):
    text: str


@app.get("/v1/health")
async def health() -> dict:
    avatar_status = "browser"
    if cfg.avatar_mode == "gpu":
        try:
            client = HttpAvatarClient(cfg.avatar_public_url)
            avatar_status = (await client.health()).get("status", "ok")
        except Exception as exc:
            avatar_status = f"down:{exc}"
    portrait = pathlib.Path(__file__).resolve().parents[2] / cfg.portrait_local_path
    return {
        "status": "ok",
        "avatar_mode": cfg.avatar_mode,
        "avatar_service": cfg.avatar_public_url,
        "avatar_status": avatar_status,
        "llm": "mock" if _use_mock() else ("deepseek" if cfg.deepseek else "mock"),
        "tts": tts_backend,
        "portrait_ready": portrait.exists(),
        "portrait_url": cfg.portrait_cache_url
        or f"/{cfg.portrait_local_path.lstrip('/')}",
    }


@app.post("/v1/session")
async def create_session(body: CreateSessionBody | None = None) -> dict:
    avatar_id = (body.avatar_id if body else None) or cfg.livetalking_avatar_id
    client = _avatar_client()
    av: dict[str, Any] | None = None
    last_exc: BaseException | None = None
    for attempt in range(1, 4):
        try:
            av = await client.create_session(avatar_id)
            if attempt > 1:
                logger.info("avatar create ok on attempt %s", attempt)
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "avatar create attempt %s/3 failed: %r",
                attempt,
                exc,
            )
            await asyncio.sleep(0.6 * attempt)
    if av is None:
        logger.warning("avatar create exhausted retries, using noop: %r", last_exc)
        av = await NoOpAvatarClient().create_session(avatar_id)

    st = sessions.create(
        avatar_session_id=av["session_id"],
        livetalking_session_id=av.get("livetalking_session_id", 0),
    )
    # noop 会话强制浏览器模式，避免往真 Gateway 推 local-noop
    mode = cfg.avatar_mode
    if st.avatar_session_id in ("local-noop", "noop"):
        mode = "browser"
        logger.warning("session %s using browser mode (avatar noop)", st.session_id[:8])
    return {
        "session_id": st.session_id,
        "avatar_session_id": st.avatar_session_id,
        "livetalking_session_id": st.livetalking_session_id,
        "avatar_mode": mode,
        "portrait_url": cfg.portrait_cache_url
        or f"/{cfg.portrait_local_path.lstrip('/')}",
        "events_url": f"ws://{cfg.orchestrator_host}:{cfg.orchestrator_port}/v1/session/{st.session_id}/events",
        "webrtc_url": av.get("webrtc_url", ""),
        "livetalking_base_url": av.get("livetalking_base_url", cfg.livetalking_base_url),
        # FlashHead：backend_composite=已贴回 2:3；frontend_stack=顶 1:1 叠层
        "layout": av.get("layout") or "frontend_stack",
        "aspect_ratio": av.get("aspect_ratio") or "1:1",
        "composite": bool(av.get("composite")),
    }


@app.post("/v1/session/{session_id}/message")
async def send_message(session_id: str, body: MessageBody) -> dict:
    st = sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if not pipeline:
        raise HTTPException(503, "PIPELINE_NOT_READY")

    async def emit(event: dict) -> None:
        await _broadcast(session_id, event)

    async def _run() -> None:
        try:
            await pipeline.run_turn(st, body.text.strip(), emit)
        except Exception as exc:
            logger.exception("turn failed")
            await emit(
                {
                    "type": "error",
                    "session_id": session_id,
                    "ts": 0,
                    "payload": {"code": "PIPELINE_ERROR", "message": str(exc)},
                }
            )

    st.turn_task = asyncio.create_task(_run())
    return {"accepted": True}


@app.post("/v1/session/{session_id}/interrupt", status_code=204)
async def interrupt(session_id: str) -> None:
    st = sessions.get(session_id)
    if not st or not pipeline:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    await pipeline.interrupt(st)


def _avatar_ws_base(avatar_session_id: str) -> str:
    gw = cfg.avatar_public_url.rstrip("/")
    if gw.startswith("https://"):
        upstream = "wss://" + gw[len("https://") :]
    elif gw.startswith("http://"):
        upstream = "ws://" + gw[len("http://") :]
    else:
        upstream = gw
    return f"{upstream}/v1/avatar/{avatar_session_id}"


async def _proxy_avatar_ws(
    websocket: WebSocket,
    session_id: str,
    *,
    path_suffix: str,
    log_name: str,
) -> None:
    """Browser → Mac orchestrator → AutoDL gateway WS (text+binary pump)."""
    import websockets
    from starlette.websockets import WebSocketState

    st = sessions.get(session_id)
    if not st:
        await websocket.close(code=4404)
        return
    if st.avatar_session_id in ("local-noop", "noop", ""):
        await websocket.close(code=4403)
        return

    await websocket.accept()
    upstream = f"{_avatar_ws_base(st.avatar_session_id)}/{path_suffix}"
    logger.info(
        "%s OPEN orch=%s avatar=%s → %s",
        log_name,
        session_id[:8],
        st.avatar_session_id[:8],
        upstream,
    )

    sent = 0
    try:
        # FlashHead 首包（compile/暖机）可达数十秒；默认 ping 会误杀代理 → 浏览器 sent=0
        async with websockets.connect(
            upstream,
            proxy=None,
            max_size=16 * 1024 * 1024,
            open_timeout=30,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        ) as up:

            async def pump_up() -> None:
                nonlocal sent
                async for msg in up:
                    if websocket.client_state != WebSocketState.CONNECTED:
                        break
                    if isinstance(msg, (bytes, bytearray)):
                        await websocket.send_bytes(msg)
                        sent += 1
                        if sent == 1 or sent % 5 == 0:
                            logger.info(
                                "%s PROG orch=%s sent=%s bytes=%s",
                                log_name,
                                session_id[:8],
                                sent,
                                len(msg),
                            )
                    else:
                        await websocket.send_text(msg)
                        if "av_mp4" in msg or "av_chunk" in msg:
                            logger.info(
                                "%s META orch=%s %s",
                                log_name,
                                session_id[:8],
                                msg[:160],
                            )

            async def watch_client() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                except WebSocketDisconnect:
                    return

            t_up = asyncio.create_task(pump_up())
            t_cli = asyncio.create_task(watch_client())
            _done, pending = await asyncio.wait(
                {t_up, t_cli}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("%s FAIL orch=%s: %s", log_name, session_id[:8], exc)
        try:
            await websocket.send_json(
                {"type": "status", "event": "error", "message": str(exc)}
            )
        except Exception:
            pass
    finally:
        logger.info("%s CLOSE orch=%s sent=%s", log_name, session_id[:8], sent)


@app.get("/v1/session/{session_id}/av/sse")
async def proxy_av_sse(session_id: str):
    """代理 AutoDL SSE（av_mp4 元数据）。浏览器只连本机编排器即可。"""
    from fastapi.responses import StreamingResponse

    st = sessions.get(session_id)
    if not st:
        # 返回 200 + 错误事件，避免 EventSource 对 404 无限重连刷日志
        async def expired_gen():
            err = {
                "type": "status",
                "event": "session_expired",
                "message": "SESSION_NOT_FOUND",
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

        logger.info("av_sse EXPIRED orch=%s (no retry)", session_id[:8])
        return StreamingResponse(
            expired_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "close",
                "X-Accel-Buffering": "no",
            },
        )
    if st.avatar_session_id in ("local-noop", "noop", ""):
        raise HTTPException(403, "AVATAR_NOOP")

    upstream = (
        f"{cfg.avatar_public_url.rstrip('/')}/v1/avatar/{st.avatar_session_id}/av/sse"
    )
    logger.info(
        "av_sse OPEN orch=%s avatar=%s → %s",
        session_id[:8],
        st.avatar_session_id[:8],
        upstream,
    )

    async def event_gen():
        sent = 0
        try:
            async with httpx.AsyncClient(
                timeout=None, proxy=None, trust_env=False
            ) as client:
                async with client.stream("GET", upstream) as resp:
                    if resp.status_code >= 400:
                        err = {
                            "type": "status",
                            "event": "error",
                            "message": f"upstream SSE HTTP {resp.status_code}",
                        }
                        yield f"data: {json.dumps(err)}\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        # 改写 gateway 的绝对 mp4 url → 走编排器代理
                        text = chunk.decode("utf-8", errors="ignore")
                        if '"url"' in text and "/v1/avatar/" in text:
                            text = text.replace(
                                f"/v1/avatar/{st.avatar_session_id}/mp4/",
                                f"/v1/session/{session_id}/mp4/",
                            )
                            chunk = text.encode("utf-8")
                        sent += 1
                        yield chunk
        except Exception as exc:
            logger.warning("av_sse FAIL orch=%s: %s", session_id[:8], exc)
            err = {"type": "status", "event": "error", "message": str(exc)}
            yield f"data: {json.dumps(err)}\n\n"
        finally:
            logger.info("av_sse CLOSE orch=%s chunks=%s", session_id[:8], sent)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/v1/session/{session_id}/mp4/{chunk_index}")
async def proxy_mp4(session_id: str, chunk_index: int):
    """代理短 MP4 段（HTTP GET，无需隧道）。"""
    from fastapi.responses import Response

    st = sessions.get(session_id)
    if not st:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    url = (
        f"{cfg.avatar_public_url.rstrip('/')}/v1/avatar/"
        f"{st.avatar_session_id}/mp4/{int(chunk_index)}"
    )
    async with httpx.AsyncClient(
        timeout=60.0, proxy=None, trust_env=False
    ) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:200])
        return Response(
            content=r.content,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store"},
        )


@app.websocket("/v1/session/{session_id}/av/ws")
async def proxy_av_ws(websocket: WebSocket, session_id: str) -> None:
    """兼容旧 WS；推荐改用 /av/sse。"""
    await _proxy_avatar_ws(
        websocket, session_id, path_suffix="av/ws", log_name="av_proxy"
    )


@app.websocket("/v1/session/{session_id}/frames/ws")
async def proxy_frames_ws(websocket: WebSocket, session_id: str) -> None:
    """Browser → Mac orchestrator → AutoDL gateway frames_ws (JPEG binary)。"""
    fps = 20
    try:
        q = websocket.query_params.get("fps")
        if q:
            fps = max(5, min(25, int(q)))
    except Exception:
        pass
    await _proxy_avatar_ws(
        websocket,
        session_id,
        path_suffix=f"frames/ws?fps={fps}",
        log_name="frames_proxy",
    )


@app.websocket("/v1/session/{session_id}/events")
async def session_events(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    ws_subscribers.setdefault(session_id, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if session_id in ws_subscribers and websocket in ws_subscribers[session_id]:
            ws_subscribers[session_id].remove(websocket)


_demo = pathlib.Path(__file__).resolve().parents[2]
_assets = _demo / "assets"
_web = _demo / "web"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
if _web.exists():
    app.mount("/", StaticFiles(directory=str(_web), html=True), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "services.orchestrator.main:app",
        host=cfg.orchestrator_host,
        port=cfg.orchestrator_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
