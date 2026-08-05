from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from lightning.config import AppConfig

logger = logging.getLogger(__name__)


def _ensure_flash_head_on_path(models_dir: Path) -> None:
    resolved = str(models_dir.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def sage_available() -> bool:
    try:
        from sageattn3 import sageattn3_blackwell  # noqa: F401

        return True
    except Exception:
        pass
    try:
        from sageattention import sageattn  # noqa: F401

        return True
    except Exception:
        return False


def sage_backend() -> str:
    try:
        from sageattn3 import sageattn3_blackwell  # noqa: F401

        return "sageattn3"
    except Exception:
        pass
    try:
        from sageattention import sageattn  # noqa: F401

        return "sageattention2"
    except Exception:
        return "none"


@dataclass
class ChunkMetrics:
    chunk_index: int
    num_frames: int
    height: int
    width: int
    fps: int
    elapsed_s: float

    @property
    def rtp(self) -> float:
        if self.num_frames <= 0 or self.fps <= 0:
            return float("inf")
        return self.elapsed_s / (self.num_frames / float(self.fps))

    @property
    def eff_fps(self) -> float:
        if self.elapsed_s <= 0:
            return 0.0
        return self.num_frames / self.elapsed_s


class FlashHeadRuntime:
    """Thin wrapper around CyberVerse flash_head inference (single-GPU streaming)."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.pipeline = None
        self.infer_params: dict = {}
        self._lock = threading.Lock()
        self.audio_deque: deque | None = None
        self._pending_audio = np.array([], dtype=np.float32)
        self._slice_len_samples = 0
        self._chunk_counter = 0
        self._fn_get_base_data = None
        self._fn_get_audio_embedding = None
        self._fn_run_pipeline = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        _ensure_flash_head_on_path(self.cfg.cyberverse_models_dir)
        from flash_head.inference import (
            configure_infer_params,
            configure_runtime_options,
            get_audio_embedding,
            get_base_data,
            get_infer_params,
            get_pipeline,
            run_pipeline,
        )

        configure_runtime_options(
            {
                "compile_model": self.cfg.runtime.compile_model,
                "compile_vae": self.cfg.runtime.compile_vae,
            }
        )
        configure_infer_params(self.cfg.infer_params.as_dict())

        ckpt = self.cfg.runtime.checkpoint_dir
        wav2vec = self.cfg.runtime.wav2vec_dir
        if not ckpt.exists():
            raise FileNotFoundError(f"checkpoint_dir missing: {ckpt}")
        if not wav2vec.exists():
            raise FileNotFoundError(f"wav2vec_dir missing: {wav2vec}")

        logger.info(
            "Loading FlashHead model_type=%s compile=%s/%s sage=%s ckpt=%s",
            self.cfg.runtime.model_type,
            self.cfg.runtime.compile_model,
            self.cfg.runtime.compile_vae,
            sage_available(),
            ckpt,
        )
        self.pipeline = get_pipeline(
            world_size=self.cfg.runtime.world_size,
            ckpt_dir=str(ckpt),
            model_type=self.cfg.runtime.model_type,
            wav2vec_dir=str(wav2vec),
        )
        self.infer_params = get_infer_params()
        self._fn_get_base_data = get_base_data
        self._fn_get_audio_embedding = get_audio_embedding
        self._fn_run_pipeline = run_pipeline
        self._init_audio_deque()
        self._ready = True

    def set_avatar(self, image_path: str | Path, *, use_face_crop: bool | None = None) -> None:
        if not self._ready:
            raise RuntimeError("runtime not loaded")
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(path)
        crop = self.cfg.runtime.use_face_crop if use_face_crop is None else use_face_crop

        feed_path = path
        h = int(self.cfg.infer_params.height)
        w = int(self.cfg.infer_params.width)
        cache_dir = Path("/tmp/flashhead_avatar")
        if self.cfg.runtime.letterbox_input and not crop:
            from lightning.image_io import letterbox_to_size

            boxed = letterbox_to_size(path, (w, h), blur_fill=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            feed_path = cache_dir / f"letterbox_{w}x{h}.jpg"
            boxed.save(feed_path, quality=95)
            logger.info("letterbox avatar %s -> %s (%dx%d)", path, feed_path, w, h)
        elif not crop:
            # 顶裁 1:1 已对齐立绘时：只做精确缩放，避免模糊边框
            from lightning.image_io import resize_exact

            exact = resize_exact(path, (w, h))
            cache_dir.mkdir(parents=True, exist_ok=True)
            feed_path = cache_dir / f"exact_{w}x{h}.jpg"
            exact.save(feed_path, quality=95)
            logger.info("exact-resize avatar %s -> %s (%dx%d)", path, feed_path, w, h)

        with self._lock:
            self._fn_get_base_data(
                self.pipeline,
                str(feed_path),
                base_seed=self.cfg.runtime.seed,
                use_face_crop=crop,
            )
            self._reset_audio_state()

    def _init_audio_deque(self) -> None:
        sr = int(self.infer_params["sample_rate"])
        duration = int(self.infer_params["cached_audio_duration"])
        self.audio_deque = deque(maxlen=sr * duration)
        self.audio_deque.extend(np.zeros(sr * duration, dtype=np.float64))
        frame_num = int(self.infer_params.get("frame_num", 33))
        motion_frames_num = int(self.infer_params.get("motion_frames_num", 5))
        tgt_fps = int(self.infer_params.get("tgt_fps", 20))
        net_frames = frame_num - motion_frames_num
        self._slice_len_samples = net_frames * sr // tgt_fps
        self._pending_audio = np.array([], dtype=np.float32)

    def _reset_audio_state(self) -> None:
        self._init_audio_deque()
        self._chunk_counter = 0

    def reset(self) -> None:
        with self._lock:
            if self.pipeline is not None and getattr(self.pipeline, "ref_img_latent", None) is not None:
                self.pipeline.latent_motion_frames = (
                    self.pipeline.ref_img_latent[:, :1].clone()
                )
            self._reset_audio_state()

    def warmup(self) -> ChunkMetrics | None:
        if not self._ready:
            raise RuntimeError("runtime not loaded")
        sr = int(self.infer_params["sample_rate"])
        silent = np.zeros(self._slice_len_samples, dtype=np.float32)
        frames_list = []
        metrics = None
        for frames, m, _pcm in self.push_audio(silent, sample_rate=sr, is_final=True):
            frames_list.append(frames)
            metrics = m
        return metrics

    def push_audio(
        self,
        pcm_f32: np.ndarray,
        *,
        sample_rate: int = 16000,
        is_final: bool = False,
    ) -> Iterator[tuple[np.ndarray, ChunkMetrics, bytes]]:
        """Yield (frames NHWC uint8, metrics, pcm_s16le) per slice — 音画同流绑定。"""
        with self._lock:
            yield from self._generate_locked(pcm_f32, sample_rate=sample_rate, is_final=is_final)

    def push_pcm_s16le(
        self,
        data: bytes,
        *,
        sample_rate: int = 16000,
        is_final: bool = False,
    ) -> Iterator[tuple[np.ndarray, ChunkMetrics, bytes]]:
        if data:
            audio = (np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0).copy()
        else:
            audio = np.array([], dtype=np.float32)
        yield from self.push_audio(audio, sample_rate=sample_rate, is_final=is_final)

    def _generate_locked(
        self,
        audio_np: np.ndarray,
        *,
        sample_rate: int,
        is_final: bool,
    ) -> Iterator[tuple[np.ndarray, ChunkMetrics, bytes]]:
        import torch

        tgt_sr = int(self.infer_params["sample_rate"])
        if audio_np.size and sample_rate != tgt_sr:
            n_dst = max(int(round(audio_np.shape[0] * tgt_sr / sample_rate)), 1)
            t_src = np.arange(audio_np.shape[0], dtype=np.float64) / float(sample_rate)
            t_end = (audio_np.shape[0] - 1) / float(sample_rate) if audio_np.shape[0] > 1 else 0.0
            t_dst = np.linspace(0.0, t_end, n_dst, dtype=np.float64)
            audio_np = np.interp(t_dst, t_src, audio_np.astype(np.float64)).astype(np.float32)

        if audio_np.size:
            self._pending_audio = (
                audio_np
                if self._pending_audio.size == 0
                else np.concatenate([self._pending_audio, audio_np])
            )

        ip = self.infer_params
        audio_end_idx = ip["cached_audio_duration"] * ip["tgt_fps"]
        audio_start_idx = audio_end_idx - ip["frame_num"]
        to_generate: list[np.ndarray] = []
        while int(self._pending_audio.shape[0]) >= self._slice_len_samples:
            one = self._pending_audio[: self._slice_len_samples]
            self._pending_audio = self._pending_audio[self._slice_len_samples :]
            to_generate.append(one)
        if is_final and int(self._pending_audio.shape[0]) > 0:
            tail = self._pending_audio
            self._pending_audio = np.array([], dtype=np.float32)
            pad = self._slice_len_samples - int(tail.shape[0])
            if pad > 0:
                tail = np.concatenate([tail, np.zeros(pad, dtype=np.float32)])
            to_generate.append(tail)

        for idx, consume in enumerate(to_generate):
            assert self.audio_deque is not None
            self.audio_deque.extend(consume)
            audio_array = np.array(self.audio_deque, dtype=np.float64)
            t0 = time.perf_counter()
            audio_embedding = self._fn_get_audio_embedding(
                self.pipeline, audio_array, audio_start_idx, audio_end_idx
            )
            video = self._fn_run_pipeline(self.pipeline, audio_embedding)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - t0
            if video is None:
                continue
            motion_frames = int(ip.get("motion_frames_num", 5))
            video = video[motion_frames:]
            frames = np.clip(video.cpu().numpy(), 0, 255).astype(np.uint8)
            self._chunk_counter += 1
            m = ChunkMetrics(
                chunk_index=self._chunk_counter,
                num_frames=int(frames.shape[0]),
                height=int(frames.shape[1]),
                width=int(frames.shape[2]),
                fps=int(ip["tgt_fps"]),
                elapsed_s=float(elapsed),
            )
            # 与本段动画绑定的 PCM（s16le），供 AutoDL 音画同流
            pcm_s16 = (
                np.clip(consume, -1.0, 1.0) * 32767.0
            ).astype(np.int16).tobytes()
            logger.info(
                "FlashHead chunk=%d frames=%d %dx%d fps=%d elapsed=%.3fs rtp=%.3f pcm=%d",
                m.chunk_index,
                m.num_frames,
                m.width,
                m.height,
                m.fps,
                m.elapsed_s,
                m.rtp,
                len(pcm_s16),
            )
            yield frames, m, pcm_s16

    def max_vram_gb(self) -> float:
        try:
            import torch

            if not torch.cuda.is_available():
                return 0.0
            return float(torch.cuda.max_memory_allocated() / (1024**3))
        except Exception:
            return 0.0
