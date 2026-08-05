#!/usr/bin/env python3
"""从竖版立绘生成 1:1 图。

默认 mode=top：顶部精确裁切（face-stack 无缝叠层用）。
mode=blur：旧版上半身+模糊边（仅离线素材，勿用于叠层）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLASH_IO = (
    ROOT.parent
    / "模型推理加速工程"
    / "SoulX-FlashHead-1_3B"
    / "lightning-FlashHead"
    / "lightning"
)
sys.path.insert(0, str(FLASH_IO.parent))

from lightning.image_io import crop_top_square, make_upper_body_square  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, default=ROOT / "assets/character/xiaoya-v1.jpg")
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "assets/character/xiaoya-v1-top1x1.jpg",
    )
    p.add_argument("--size", type=int, default=0, help="0=保留裁切原边长")
    p.add_argument(
        "--mode",
        choices=("top", "blur"),
        default="top",
        help="top=顶裁无缝；blur=模糊留白（旧）",
    )
    args = p.parse_args()

    if not args.src.exists():
        alt = ROOT / "assets/character/xiaoya-v1.png"
        if alt.exists():
            args.src = alt
        else:
            raise SystemExit(f"missing source: {args.src}")

    if args.mode == "top":
        out_size = args.size if args.size > 0 else None
        img = crop_top_square(args.src, out_size=out_size)
    else:
        img = make_upper_body_square(
            args.src, out_size=args.size if args.size > 0 else 1024
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out, quality=92, optimize=True)
    print(f"OK {args.out} {img.size} mode={args.mode}")


if __name__ == "__main__":
    main()
