#!/usr/bin/env python3
"""测 RealESRGAN / UltraSharp 单帧放大时延（毫秒级？）。"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import torch


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--model",
        default="/root/autodl-fs/models/upscale_models/RealESRGAN_x4plus.pth",
    )
    p.add_argument("--size", type=int, default=256, help="input square size")
    p.add_argument("--out-size", type=int, default=512, help="target size (scale or resize)")
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--runs", type=int, default=30)
    args = p.parse_args()

    # Prefer spandrel (ComfyUI); fallback tip
    try:
        from spandrel import ModelLoader
    except ImportError as exc:
        raise SystemExit(
            "need spandrel: pip install spandrel  (or run inside ComfyUI env)"
        ) from exc

    device = torch.device("cuda")
    model = ModelLoader().load_from_file(args.model)
    model = model.eval().to(device)
    if hasattr(model, "half"):
        try:
            model = model.half()
            dtype = torch.float16
        except Exception:
            dtype = torch.float32
    else:
        dtype = torch.float32

    scale = int(getattr(model, "scale", 4) or 4)
    x = torch.rand(1, 3, args.size, args.size, device=device, dtype=dtype)

    def once() -> torch.Tensor:
        with torch.inference_mode():
            y = model(x)
            if y.shape[-1] != args.out_size:
                y = torch.nn.functional.interpolate(
                    y, size=(args.out_size, args.out_size), mode="bicubic", align_corners=False
                )
            torch.cuda.synchronize()
            return y

    for _ in range(args.warmup):
        once()

    times = []
    for _ in range(args.runs):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        once()
        times.append((time.perf_counter() - t0) * 1000)

    arr = np.asarray(times, dtype=np.float64)
    print(
        f"model={Path(args.model).name} in={args.size} out={args.out_size} "
        f"native_scale={scale} dtype={dtype}"
    )
    print(
        f"ms/frame: mean={arr.mean():.2f} p50={np.median(arr):.2f} "
        f"p95={np.percentile(arr,95):.2f} min={arr.min():.2f} max={arr.max():.2f}"
    )
    print(f"budget@20fps=50ms → {'OK' if np.median(arr) < 50 else 'TOO SLOW'}")


if __name__ == "__main__":
    main()
