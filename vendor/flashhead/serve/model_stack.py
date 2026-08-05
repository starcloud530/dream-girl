"""FlashHead 模型栈：权重加载 + PCM 推理（Engine / 单体 Gateway 共用）。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class ModelStack:
    def __init__(self) -> None:
        self.cfg = None
        self.runtime = None
        self.preset = None
        self.preset_pool = None
        self.composite_enabled: bool = False
        self.paste_back_enabled: bool = False
        self.feather_px: int = 14
        self.random_clip: bool = True
        self.upscaler = None
        self._infer_lock = threading.Lock()
        self._paste_body: np.ndarray | None = None  # HxWx3 uint8 canvas
        self._paste_box: tuple[int, int, int, int] | None = None

    @property
    def ready(self) -> bool:
        return self.runtime is not None and bool(getattr(self.runtime, "ready", False))

    def load(self, config_path: Path, avatar_image: Path | None = None) -> None:
        from lightning.config import load_config
        from lightning.preset_pool import PresetClipPool
        from lightning.runtime import FlashHeadRuntime

        self.cfg = load_config(config_path)
        self.runtime = FlashHeadRuntime(self.cfg)
        self.runtime.load()
        image = avatar_image or self.cfg.sample_image
        self.runtime.set_avatar(image)

        comp = self.cfg.composite
        self.composite_enabled = bool(comp.enabled)
        self.paste_back_enabled = False
        self.feather_px = int(comp.feather_px)
        self.random_clip = bool(comp.random_clip)
        self.preset = None
        self.preset_pool = None
        self._paste_body = None
        self._paste_box = None
        if self.composite_enabled and str(comp.mode) == "paste_back":
            self._load_paste_back(comp)
        elif self.composite_enabled:
            canvas = (int(comp.canvas_width), int(comp.canvas_height))
            if comp.preset_manifest and Path(comp.preset_manifest).exists():
                self.preset_pool = PresetClipPool.from_manifest(
                    Path(comp.preset_manifest), canvas_size=canvas
                )
            elif comp.preset_video and comp.boxes_jsonl:
                if not Path(comp.preset_video).exists():
                    raise RuntimeError(f"preset video missing: {comp.preset_video}")
                if not Path(comp.boxes_jsonl).exists():
                    raise RuntimeError(f"boxes jsonl missing: {comp.boxes_jsonl}")
                self.preset_pool = PresetClipPool.from_single(
                    Path(comp.preset_video),
                    Path(comp.boxes_jsonl),
                    canvas_size=canvas,
                )
            else:
                raise RuntimeError(
                    "composite.enabled needs mode=paste_back or preset_manifest/preset_video"
                )
            self.preset = self.preset_pool.active
            logger.info(
                "V2 composite ON canvas=%sx%s feather=%s clips=%s active=%s",
                comp.canvas_width,
                comp.canvas_height,
                self.feather_px,
                len(self.preset_pool.specs),
                self.preset_pool.active_id,
            )

        up = self.cfg.upscale
        self.upscaler = None
        if up.enabled and up.model:
            try:
                from lightning.upscale import GpuUpscaler

                self.upscaler = GpuUpscaler(up.model)
                dummy = np.zeros(
                    (1, self.cfg.infer_params.height, self.cfg.infer_params.width, 3),
                    dtype=np.uint8,
                )
                _ = self.upscaler.upscale_frames(
                    dummy, int(self.cfg.runtime.display_size)
                )
            except Exception as exc:
                logger.warning("GPU upscale disabled, fallback PIL: %s", exc)
                self.upscaler = None

        logger.info(
            "ModelStack ready avatar=%s composite=%s paste_back=%s infer=%sx%s display=%s upscale=%s",
            image,
            self.composite_enabled,
            self.paste_back_enabled,
            self.cfg.infer_params.width,
            self.cfg.infer_params.height,
            self.cfg.runtime.display_size,
            getattr(self.upscaler, "model_name", None) if self.upscaler else False,
        )

    def _load_paste_back(self, comp) -> None:
        """静态 2:3 立绘 + 固定 box：脸推理后贴回。"""
        body_path = comp.body_image
        if body_path is None or not Path(body_path).exists():
            raise RuntimeError(f"paste_back needs body_image: {body_path}")
        if comp.face_box is None:
            raise RuntimeError("paste_back needs face_box [x1,y1,x2,y2]")
        cw, ch = int(comp.canvas_width), int(comp.canvas_height)
        img = Image.open(body_path).convert("RGB")
        ow, oh = img.size
        body = np.asarray(
            img.resize((cw, ch), Image.Resampling.LANCZOS), dtype=np.uint8
        )
        x1, y1, x2, y2 = [int(v) for v in comp.face_box]
        sx, sy = cw / float(ow), ch / float(oh)
        box = (
            int(round(x1 * sx)),
            int(round(y1 * sy)),
            int(round(x2 * sx)),
            int(round(y2 * sy)),
        )
        self._paste_body = body
        self._paste_box = box
        self.paste_back_enabled = True
        logger.info(
            "paste_back ON body=%s native=%sx%s canvas=%sx%s box=%s feather=%s",
            body_path,
            ow,
            oh,
            cw,
            ch,
            box,
            self.feather_px,
        )

    def _paste_back_face_chunk(self, face_frames: np.ndarray) -> np.ndarray:
        from lightning.composite import paste_face_onto_body

        assert self._paste_body is not None and self._paste_box is not None
        body = self._paste_body
        box = self._paste_box
        out = []
        for i in range(int(face_frames.shape[0])):
            out.append(
                paste_face_onto_body(
                    body, face_frames[i], box, feather_px=int(self.feather_px)
                )
            )
        return np.stack(out, axis=0)

    def warmup(self, runs: int = 2) -> None:
        """消化 torch.compile + SageAttn3 首启；避免用户首轮卡 30s+。"""
        if not self.ready or self.runtime is None or self.cfg is None:
            return
        import time

        sr = int(self.cfg.infer_params.sample_rate)
        # 一片净帧 ≈ (frame_num - motion) / fps 秒
        slice_s = 1.4
        n = max(int(sr * slice_s), sr // 2)
        silent = (np.zeros(n, dtype=np.int16)).tobytes()
        for i in range(max(int(runs), 1)):
            self.reset_session()
            t0 = time.perf_counter()
            items = self.infer_pcm_encoded(silent, sample_rate=sr, end=True)
            dt = time.perf_counter() - t0
            rtp = items[0].meta.get("rtp") if items else None
            mp4_b = items[0].meta.get("mp4_bytes") if items else None
            logger.info(
                "warmup %d/%d wall=%.3fs chunks=%s first_rtp=%s mp4=%s",
                i + 1,
                runs,
                dt,
                len(items),
                f"{float(rtp):.3f}" if rtp is not None else "n/a",
                mp4_b,
            )
        self.reset_session()

    def pick_preset_for_session(self) -> None:
        if self.preset_pool is None:
            return
        if self.random_clip and len(self.preset_pool.specs) > 1:
            self.preset = self.preset_pool.pick_random()
        else:
            self.preset_pool.reset_active()
            self.preset = self.preset_pool.active

    def reset_session(self) -> None:
        assert self.runtime is not None
        self.runtime.reset()
        self.pick_preset_for_session()

    def _maybe_upscale_frames(self, frames: np.ndarray) -> np.ndarray:
        display = 0
        if self.cfg is not None:
            display = int(getattr(self.cfg.runtime, "display_size", 0) or 0)
        if display <= 0 or int(frames.shape[2]) == display:
            return frames
        if self.upscaler is not None:
            try:
                return self.upscaler.upscale_frames(frames, display)
            except Exception as exc:
                logger.warning("GPU upscale failed, fallback bilinear: %s", exc)
        # 双线性：比 Lanczos/ESRGAN 快一个数量级，质量略软
        try:
            import cv2

            out = [
                cv2.resize(
                    frames[i],
                    (display, display),
                    interpolation=cv2.INTER_LINEAR,
                )
                for i in range(frames.shape[0])
            ]
            return np.stack(out, axis=0)
        except Exception:
            from PIL import Image

            out = []
            for i in range(frames.shape[0]):
                img = Image.fromarray(frames[i]).resize(
                    (display, display), Image.Resampling.BILINEAR
                )
                out.append(np.asarray(img, dtype=np.uint8))
            return np.stack(out, axis=0)

    def _composite_face_chunk(self, face_frames: np.ndarray) -> np.ndarray:
        from lightning.composite import composite_chunk

        preset = self.preset
        if preset is None and self.preset_pool is not None:
            preset = self.preset_pool.active
            self.preset = preset
        assert preset is not None
        n = int(face_frames.shape[0])
        bodies, boxes = preset.next_n(n)
        return composite_chunk(
            face_frames, bodies, boxes, feather_px=int(self.feather_px)
        )

    def infer_pcm(
        self, pcm: bytes, *, sample_rate: int | None = None, end: bool = False
    ) -> list[tuple[np.ndarray, dict[str, Any], bytes]]:
        """返回 (frames_u8, meta, pcm_s16le) 列表；含 upscale/composite。"""
        assert self.runtime is not None
        assert self.cfg is not None
        sr = int(sample_rate or self.cfg.infer_params.sample_rate)
        out: list[tuple[np.ndarray, dict[str, Any], bytes]] = []
        with self._infer_lock:
            for frames, m, pcm_s16 in self.runtime.push_pcm_s16le(
                pcm, sample_rate=sr, is_final=end
            ):
                face_u8 = np.asarray(frames, dtype=np.uint8)
                if self.paste_back_enabled:
                    frames_u8 = self._paste_back_face_chunk(face_u8)
                elif self.composite_enabled and self.preset is not None:
                    frames_u8 = self._composite_face_chunk(face_u8)
                else:
                    frames_u8 = self._maybe_upscale_frames(face_u8)
                meta = {
                    "chunk_index": int(m.chunk_index),
                    "fps": int(m.fps),
                    "sample_rate": sr,
                    "elapsed_s": float(m.elapsed_s),
                    "rtp": float(m.rtp),
                    "n_frames": int(frames_u8.shape[0]),
                    "height": int(frames_u8.shape[1]),
                    "width": int(frames_u8.shape[2]),
                }
                out.append((frames_u8, meta, pcm_s16))
        return out

    def infer_pcm_encoded(
        self, pcm: bytes, *, sample_rate: int | None = None, end: bool = False
    ):
        """推理 + 双线性放大 + 片内编码 MP4/JPEG（供 Engine IPC）。"""
        from serve.av_encode import frames_pcm_to_mp4, preview_jpeg
        from serve.engine_codec import EncodedChunk

        raw = self.infer_pcm(pcm, sample_rate=sample_rate, end=end)
        out: list[EncodedChunk] = []
        for frames_u8, meta, pcm_s16 in raw:
            mp4 = frames_pcm_to_mp4(
                frames_u8,
                pcm_s16,
                fps=int(meta["fps"]),
                sample_rate=int(meta["sample_rate"]),
            )
            jpeg = preview_jpeg(frames_u8)
            meta = dict(meta)
            meta["mp4_bytes"] = len(mp4)
            meta["jpeg_bytes"] = len(jpeg)
            out.append(
                EncodedChunk(meta=meta, pcm=pcm_s16, mp4=mp4, jpeg=jpeg)
            )
        return out

    def health_dict(self) -> dict[str, Any]:
        ip = self.cfg.infer_params if self.cfg else None
        comp = self.cfg.composite if self.cfg else None
        return {
            "status": "ok" if self.ready else "starting",
            "ready": self.ready,
            "height": getattr(ip, "height", None),
            "width": getattr(ip, "width", None),
            "tgt_fps": getattr(ip, "tgt_fps", None),
            "sample_rate": getattr(ip, "sample_rate", None),
            "display_size": getattr(self.cfg.runtime, "display_size", None)
            if self.cfg
            else None,
            "upscale": bool(self.upscaler),
            "upscale_model": getattr(self.upscaler, "model_name", None)
            if self.upscaler
            else None,
            "composite": bool(self.composite_enabled),
            "paste_back": bool(self.paste_back_enabled),
            "canvas_width": getattr(comp, "canvas_width", None) if comp else None,
            "canvas_height": getattr(comp, "canvas_height", None) if comp else None,
            "aspect_ratio": "2:3"
            if self.composite_enabled or self.paste_back_enabled
            else "1:1",
            "layout": "backend_composite"
            if self.composite_enabled or self.paste_back_enabled
            else "frontend_stack",
            "tier": getattr(self.cfg, "tier", None) if self.cfg else None,
            "preset_clip_id": getattr(self.preset_pool, "active_id", None),
        }

    def preview_jpeg(self, frames: np.ndarray, quality: int = 70) -> bytes:
        import io

        buf = io.BytesIO()
        Image.fromarray(frames[-1]).save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
