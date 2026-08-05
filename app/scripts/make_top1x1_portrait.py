#!/usr/bin/env python3
"""从 2:3 立绘顶部截纯 1:1，供 FlashHead 推理 + 前端顶部叠层对齐。"""

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


def crop_top_square(img: Image.Image, out_size: int | None = None) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    crop = img.crop((left, 0, left + side, side))
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
    p.add_argument("--size", type=int, default=0, help="可选缩放边长；0=保持裁切原尺寸")
    p.add_argument(
        "--flash-out",
        type=Path,
        default=FLASH_ASSETS / "xiaoya_top1x1.jpg",
        help="同步一份到 FlashHead assets（默认 512）",
    )
    p.add_argument("--flash-size", type=int, default=512)
    args = p.parse_args()

    if not args.src.exists():
        raise SystemExit(f"missing source: {args.src}")

    img = Image.open(args.src)
    crop = crop_top_square(img, out_size=args.size or None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(args.out, quality=92, optimize=True)
    print(f"OK {args.out} {crop.size}")

    if args.flash_out:
        fh = crop_top_square(img, out_size=args.flash_size)
        args.flash_out.parent.mkdir(parents=True, exist_ok=True)
        fh.save(args.flash_out, quality=92, optimize=True)
        print(f"OK {args.flash_out} {fh.size}")


if __name__ == "__main__":
    main()
