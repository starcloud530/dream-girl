"""Avatar image I/O: top-square crop / letterbox in → model size; upscale out."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageFilter

if TYPE_CHECKING:
    import numpy as np


def crop_top_square(
    src: Image.Image | Path | str,
    *,
    out_size: int | None = None,
) -> Image.Image:
    """从 2:3 立绘顶部精确裁 1:1（无留白、无模糊），供前端叠层无缝对齐。"""
    img = src if isinstance(src, Image.Image) else Image.open(src).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = 0
    crop = img.crop((left, top, left + side, top + side))
    if out_size and out_size > 0 and crop.size != (out_size, out_size):
        crop = crop.resize((out_size, out_size), Image.Resampling.LANCZOS)
    return crop


def resize_exact(
    src: Image.Image | Path | str,
    size: tuple[int, int],
) -> Image.Image:
    """强制缩放到目标分辨率（无 letterbox / 无模糊边）。"""
    img = src if isinstance(src, Image.Image) else Image.open(src).convert("RGB")
    tw, th = int(size[0]), int(size[1])
    if img.size == (tw, th):
        return img
    return img.resize((tw, th), Image.Resampling.LANCZOS)


def make_upper_body_square(
    src: Image.Image | Path | str,
    *,
    out_size: int = 1024,
    blur_radius: float = 36.0,
) -> Image.Image:
    """上半身优先的 1:1 构图：人物居中，背景用模糊填充（仅离线素材用，叠层勿用）。"""
    img = src if isinstance(src, Image.Image) else Image.open(src).convert("RGB")
    w, h = img.size

    # 取顶部方形区域（上半身），竖图时 side=宽
    side = min(w, h)
    if h >= w:
        # 略微上移重心：从顶部取 square
        top = 0
        left = (w - side) // 2
    else:
        left = (w - side) // 2
        top = max(0, (h - side) // 5)  # 略偏上
    subject = img.crop((left, top, left + side, top + side))

    # 背景：整图拉满正方形再强模糊
    bg = img.resize((out_size, out_size), Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # 主体等比放入（留一点边距，更像「正中上半身」）
    margin = int(out_size * 0.04)
    fit = out_size - 2 * margin
    sub = subject.resize((fit, fit), Image.Resampling.LANCZOS)
    canvas = bg.copy()
    canvas.paste(sub, (margin, margin))
    return canvas


def letterbox_to_size(
    src: Image.Image | Path | str,
    size: tuple[int, int],
    *,
    blur_fill: bool = True,
    blur_radius: float = 28.0,
    fill_color: tuple[int, int, int] = (12, 12, 18),
) -> Image.Image:
    """等比缩小后居中贴到目标分辨率，空白用模糊背景或纯色填充（不裁切人物）。"""
    img = src if isinstance(src, Image.Image) else Image.open(src).convert("RGB")
    tw, th = int(size[0]), int(size[1])
    if tw <= 0 or th <= 0:
        raise ValueError(f"invalid size {size}")

    w, h = img.size
    scale = min(tw / w, th / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)

    if blur_fill:
        canvas = img.resize((tw, th), Image.Resampling.LANCZOS)
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    else:
        canvas = Image.new("RGB", (tw, th), fill_color)

    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def upscale_frame(
    frame: "np.ndarray | Image.Image",
    *,
    out_size: int | None = None,
    scale: float = 1.0,
) -> Image.Image:
    """推理帧等比放大到展示分辨率。"""
    if isinstance(frame, Image.Image):
        img = frame
    else:
        img = Image.fromarray(frame)
    if out_size and out_size > 0:
        if img.size == (out_size, out_size):
            return img
        return img.resize((out_size, out_size), Image.Resampling.LANCZOS)
    if scale and abs(scale - 1.0) > 1e-6:
        w, h = img.size
        return img.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.Resampling.LANCZOS,
        )
    return img
