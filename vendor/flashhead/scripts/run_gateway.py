#!/usr/bin/env python3
"""Stable gateway launcher for AutoDL.

``python -m serve.gateway`` / calling ``main()`` via some entrypoints has
segfaulted on this host; load + uvicorn.run here is the known-good path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def _parse_args(argv: list[str]):
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("FLASHHEAD_CONFIG", "configs/t1_compile.yaml")),
    )
    p.add_argument("--avatar-image", type=Path, default=None)
    p.add_argument("--host", default=os.environ.get("FLASHHEAD_GATEWAY_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("FLASHHEAD_GATEWAY_PORT", "6008")))
    p.add_argument("--assets-dir", type=Path, default=None)
    p.add_argument(
        "--engine-url",
        default=os.environ.get("FLASHHEAD_ENGINE_URL", ""),
        help="双进程：连常驻 Engine（不加载模型）",
    )
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    import uvicorn

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    import serve.gateway as g

    if args.engine_url:
        g.STATE.attach_engine(args.engine_url)
    else:
        g.STATE.load(args.config, args.avatar_image)
    g._mount_assets(args.assets_dir)
    uvicorn.run(g.app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
