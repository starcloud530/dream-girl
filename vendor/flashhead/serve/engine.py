"""FlashHead Engine — 常驻模型进程（本机 :6009）。

Gateway 通过 HTTP 调 /v1/infer/pcm，改 gateway 代码可只重启 Gateway。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from serve.engine_codec import pack_encoded_chunks
from serve.model_stack import ModelStack

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _ROOT / "configs" / "t1_384_x2.yaml"

STACK = ModelStack()
app = FastAPI(title="FlashHead Engine", version="1.0.0")


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    d = STACK.health_dict()
    d["role"] = "engine"
    d["backend"] = "flashhead_engine"
    return d


@app.post("/v1/session/reset")
async def session_reset() -> dict[str, Any]:
    if not STACK.ready:
        raise HTTPException(503, "ENGINE_NOT_READY")
    STACK.reset_session()
    return {
        "ok": True,
        "preset_clip_id": getattr(STACK.preset_pool, "active_id", None),
    }


@app.post("/v1/infer/pcm")
async def infer_pcm(request: Request) -> Response:
    if not STACK.ready:
        raise HTTPException(503, "ENGINE_NOT_READY")
    end = request.query_params.get("end", "0") in ("1", "true", "yes")
    try:
        sr = int(request.query_params.get("sample_rate") or 0) or None
    except ValueError:
        sr = None
    body = await request.body()
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(
        None,
        lambda: STACK.infer_pcm_encoded(body or b"", sample_rate=sr, end=end),
    )
    payload = pack_encoded_chunks(items)
    return Response(
        content=payload, media_type="application/x-flashhead-chunks-v2"
    )


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("FLASHHEAD_CONFIG", _DEFAULT_CONFIG)),
    )
    p.add_argument("--avatar-image", type=Path, default=None)
    p.add_argument(
        "--host",
        default=os.environ.get("FLASHHEAD_ENGINE_HOST", "127.0.0.1"),
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASHHEAD_ENGINE_PORT", "6009")),
    )
    args = p.parse_args(argv)

    import sys

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    STACK.load(args.config, args.avatar_image)
    # 第 1 次 ~45s compile；第 2 次仍可能 >1rtp；至少 3 次才贴近稳态 ~0.62
    warm_runs = int(os.environ.get("FLASHHEAD_WARMUP_RUNS", "3"))
    if warm_runs > 0:
        logger.info("engine warmup runs=%s (compile+sageattn3)", warm_runs)
        STACK.warmup(runs=warm_runs)
    logger.info("FlashHead Engine listening %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
