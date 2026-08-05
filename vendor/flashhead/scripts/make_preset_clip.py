#!/usr/bin/env python3
"""Build a short 512x768 looping preset mp4 from a 2:3 still (or any image)."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image


def _fit_23(img: Image.Image, width: int, height: int) -> Image.Image:
    """Cover-fit into WxH then center-crop (preserve face-ish upper body)."""
    img = img.convert("RGB")
    tw, th = width, height
    scale = max(tw / img.width, th / img.height)
    nw, nh = max(1, int(round(img.width * scale))), max(1, int(round(img.height * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = max(0, (nh - th) // 5)  # bias upward for half-body
    if top + th > nh:
        top = nh - th
    return resized.crop((left, top, left + tw, top + th))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument(
        "--breathe",
        action="store_true",
        help="tiny scale pulse so idle loop is not a frozen still",
    )
    args = ap.parse_args()

    base = _fit_23(Image.open(args.image), args.width, args.height)
    n = max(1, int(round(args.seconds * args.fps)))
    args.out.parent.mkdir(parents=True, exist_ok=True)

    import imageio.v2 as imageio

    writer = imageio.get_writer(
        str(args.out),
        fps=args.fps,
        codec="libx264",
        ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "18", "-bf", "0"],
    )
    try:
        for i in range(n):
            frame = base
            if args.breathe:
                # ±1.5% pulse
                amp = 1.0 + 0.015 * math.sin(2 * math.pi * i / max(1, n))
                sw = max(args.width, int(round(args.width * amp)))
                sh = max(args.height, int(round(args.height * amp)))
                zoomed = base.resize((sw, sh), Image.Resampling.LANCZOS)
                left = (sw - args.width) // 2
                top = (sh - args.height) // 2
                frame = zoomed.crop((left, top, left + args.width, top + args.height))
            writer.append_data(np.asarray(frame, dtype=np.uint8))
    finally:
        writer.close()
    print(f"wrote {args.out} frames={n} size={args.width}x{args.height} fps={args.fps}")


if __name__ == "__main__":
    main()
