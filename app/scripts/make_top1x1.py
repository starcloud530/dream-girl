#!/usr/bin/env python3
"""从 2:3 立绘顶部截纯 1:1，供 FlashHead 输入 + 前端顶区叠层对齐。"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FLASH_ASSETS = (
    ROOT.parent
    / "模型推理加速工程"
    / "SoulX-FlashHead-1_3B"
    / "lightning-FlashHead"
    / "assets"
    / "preset_clips"
)


def crop_top_square(src: Path, *, out_size: int | None = None) -> Image.Image:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    side = min(w, h)
    crop = img.crop((0, 0, side, side))
    if out_size and out_size > 0 and crop.size != (out_size, out_size):
        crop = crop.resize((out_size, out_size), Image.Resampling.LANCZOS)
    return crop


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--src",
        type=Path,
        default=ROOT / "assets/character/xiaoya-v1.jpg",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "assets/character/xiaoya-v1-top1x1.jpg",
    )
    p.add_argument("--size", type=int, default=0, help="可选缩放边长；0=保持裁切原边")
    p.add_argument(
        "--sync-flashhead",
        action="store_true",
        help="同步写一份到 lightning-FlashHead/assets/preset_clips/xiaoya_top1x1.jpg",
    )
    args = p.parse_args()

    if not args.src.exists():
        raise SystemExit(f"missing source: {args.src}")

    img = crop_top_square(args.src, out_size=args.size or None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out, quality=92, optimize=True)
    print(f"OK {args.out} {img.size}")

    if args.sync_flashhead:
        FLASH_ASSETS.mkdir(parents=True, exist_ok=True)
        fh = FLASH_ASSETS / "xiaoya_top1x1.jpg"
        # Gateway 侧用 512 即可
        crop_top_square(args.src, out_size=512).save(fh, quality=92, optimize=True)
        print(f"OK {fh} (512)")


if __name__ == "__main__":
    main()
