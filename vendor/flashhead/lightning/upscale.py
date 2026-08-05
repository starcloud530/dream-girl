"""GPU super-resolution for inference frames (RealESRGAN via spandrel)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from loguru import logger
from spandrel import ModelLoader


class GpuUpscaler:
    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str | None = None,
        batch_size: int = 8,
    ) -> None:
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"upscale model missing: {path}")
        self.model_path = path
        self.model_name = path.name
        self.batch_size = max(1, int(batch_size))
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self.model = ModelLoader().load_from_file(path).eval().to(self.device)
        self.native_scale = int(getattr(self.model, "scale", 4) or 4)
        if self.device.type == "cuda":
            self.model = self.model.half()
        logger.info(
            "GpuUpscaler ready model={} scale={}x device={} batch={}",
            self.model_name,
            self.native_scale,
            self.device,
            self.batch_size,
        )

    def upscale_frames(self, frames: np.ndarray, display_size: int) -> np.ndarray:
        """(N,H,W,3) uint8 → (N,display_size,display_size,3) uint8."""
        if frames.ndim != 4 or frames.shape[-1] != 3:
            raise ValueError(f"expected NHWC uint8, got {frames.shape}")
        n, h, w, _ = frames.shape
        if display_size <= 0 or (h == display_size and w == display_size):
            return frames

        out_chunks: list[np.ndarray] = []
        for start in range(0, n, self.batch_size):
            chunk = frames[start : start + self.batch_size]
            out_chunks.append(self._upscale_batch(chunk, display_size))
        return np.concatenate(out_chunks, axis=0)

    def _upscale_batch(self, batch: np.ndarray, display_size: int) -> np.ndarray:
        tensor = (
            torch.from_numpy(batch)
            .permute(0, 3, 1, 2)
            .contiguous()
            .to(device=self.device, dtype=torch.float32)
            .div_(255.0)
        )
        if self.device.type == "cuda":
            tensor = tensor.half()
        with torch.inference_mode():
            sr = self.model(tensor)
        if not isinstance(sr, torch.Tensor):
            sr = sr  # type: ignore[assignment]
        sr = sr.float().clamp_(0.0, 1.0)
        if sr.shape[-1] != display_size or sr.shape[-2] != display_size:
            sr = F.interpolate(
                sr,
                size=(display_size, display_size),
                mode="bilinear",
                align_corners=False,
            )
        return (
            sr.mul(255.0)
            .round()
            .to(torch.uint8)
            .permute(0, 2, 3, 1)
            .cpu()
            .numpy()
        )
