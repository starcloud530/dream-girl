"""Qwen3-TTS 本地 HTTP 服务 — 供 demo 编排器调用。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _ROOT / "configs" / "qwen_tts.yaml"

app = FastAPI(title="Qwen3-TTS Server", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=1)


class TTSState:
    def __init__(self) -> None:
        self.runtime = None
        self.cfg = None

    def load(self, config_path: Path) -> None:
        from lightning.qwen_tts import Qwen3TTSRuntime
        from lightning.qwen_tts_config import QwenTTSConfig

        self.cfg = QwenTTSConfig.from_yaml(config_path)
        self.runtime = Qwen3TTSRuntime(self.cfg)
        self.runtime.load()


STATE = TTSState()


class SynthesizeRequest(BaseModel):
    text: str
    speaker: str | None = None
    language: str | None = None
    instruct: str | None = None
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    stream: bool = False
    chunk_ms: int = Field(default=200, ge=50, le=1000)


@app.on_event("startup")
async def _startup() -> None:
    cfg_path = Path(os.environ.get("QWEN_TTS_CONFIG", _DEFAULT_CONFIG))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, STATE.load, cfg_path)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    rt = STATE.runtime
    return {
        "status": "ok" if rt and rt.loaded else "loading",
        "backend": "qwen3-tts",
        "speaker": STATE.cfg.speaker if STATE.cfg else None,
        "sample_rate": STATE.cfg.sample_rate if STATE.cfg else 16000,
        "speakers": rt.speakers() if rt else [],
    }


@app.get("/v1/speakers")
def speakers() -> dict[str, Any]:
    if not STATE.runtime:
        raise HTTPException(503, "TTS not ready")
    return {"speakers": STATE.runtime.speakers()}


@app.post("/v1/tts/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    if not STATE.runtime:
        raise HTTPException(503, "TTS not ready")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "empty text")

    loop = asyncio.get_event_loop()

    if not req.stream:
        pcm, meta = await loop.run_in_executor(
            _executor,
            lambda: STATE.runtime.synthesize_pcm(
                text,
                speaker=req.speaker,
                language=req.language,
                instruct=req.instruct,
                sample_rate=req.sample_rate,
            ),
        )
        return Response(
            content=pcm,
            media_type="audio/L16;rate=16000;channels=1",
            headers={
                "X-Audio-Ms": str(meta.get("audio_ms", 0)),
                "X-Elapsed-Ms": str(meta.get("elapsed_ms", 0)),
                "X-RTF": str(meta.get("rtf", 0)),
            },
        )

    async def _gen():
        chunks = await loop.run_in_executor(
            _executor,
            lambda: list(
                STATE.runtime.synthesize_pcm_chunks(
                    text,
                    chunk_ms=req.chunk_ms,
                    speaker=req.speaker,
                    language=req.language,
                    instruct=req.instruct,
                    sample_rate=req.sample_rate,
                )
            ),
        )
        for pcm, _meta in chunks:
            yield pcm

    return StreamingResponse(_gen(), media_type="application/octet-stream")


@app.websocket("/v1/tts/ws")
async def tts_ws(ws: WebSocket) -> None:
    """增量文本 → PCM hex（兼容 MiniMax 风格，便于编排器对接）。"""
    await ws.accept()
    if not STATE.runtime:
        await ws.send_json({"event": "task_failed", "message": "TTS not ready"})
        await ws.close()
        return

    speaker: str | None = None
    language: str | None = None
    instruct: str | None = None
    sample_rate = 16000
    loop = asyncio.get_event_loop()

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            event = msg.get("event", "")

            if event == "task_start":
                vs = msg.get("voice_setting") or {}
                speaker = vs.get("speaker") or msg.get("speaker")
                language = vs.get("language") or msg.get("language")
                instruct = vs.get("instruct") or msg.get("instruct")
                aset = msg.get("audio_setting") or {}
                sample_rate = int(aset.get("sample_rate", sample_rate))
                await ws.send_json({"event": "task_started"})
                continue

            if event == "task_continue":
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                pcm, meta = await loop.run_in_executor(
                    _executor,
                    lambda t=text: STATE.runtime.synthesize_pcm(
                        t,
                        speaker=speaker,
                        language=language,
                        instruct=instruct,
                        sample_rate=sample_rate,
                    ),
                )
                if pcm:
                    await ws.send_json(
                        {
                            "event": "task_continue",
                            "data": {"audio": pcm.hex()},
                            "meta": meta,
                        }
                    )
                continue

            if event == "task_finish":
                await ws.send_json({"event": "task_finished"})
                break

            if event == "ping":
                await ws.send_json({"event": "pong"})
                continue

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("tts ws error: %s", exc)
        try:
            await ws.send_json({"event": "task_failed", "message": str(exc)})
        except Exception:
            pass


def main() -> None:
    import argparse
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("QWEN_TTS_CONFIG", _DEFAULT_CONFIG)),
    )
    p.add_argument("--host", default=os.environ.get("QWEN_TTS_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("QWEN_TTS_PORT", "6010")))
    args = p.parse_args()
    os.environ["QWEN_TTS_CONFIG"] = str(args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
