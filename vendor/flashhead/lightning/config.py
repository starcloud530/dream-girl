from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lightning.paths import (
    checkpoint_dir,
    cyberverse_models_dir,
    default_models_root,
    default_sample_audio,
    default_sample_image,
    repo_root,
    wav2vec_dir,
)

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return os.environ.get(key, match.group(0))

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


@dataclass
class RuntimeConfig:
    model_type: str = "pro"
    checkpoint_dir: Path = field(default_factory=checkpoint_dir)
    wav2vec_dir: Path = field(default_factory=wav2vec_dir)
    seed: int = 9999
    use_face_crop: bool = False
    compile_model: bool = True
    compile_vae: bool = True
    world_size: int = 1
    # 输入：等比缩小 + 背景填充到模型 HxW（避免 centercrop 裁脸）
    letterbox_input: bool = True
    # 输出：推理帧再等比放大到展示边长（1:1）
    display_size: int = 928


@dataclass
class InferParams:
    frame_num: int = 33
    motion_frames_latent_num: int = 2
    tgt_fps: int = 20
    sample_rate: int = 16000
    sample_shift: float = 5.0
    color_correction_strength: float = 1.0
    cached_audio_duration: int = 8
    num_heads: int = 12
    height: int = 464
    width: int = 464

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_num": self.frame_num,
            "motion_frames_latent_num": self.motion_frames_latent_num,
            "tgt_fps": self.tgt_fps,
            "sample_rate": self.sample_rate,
            "sample_shift": self.sample_shift,
            "color_correction_strength": self.color_correction_strength,
            "cached_audio_duration": self.cached_audio_duration,
            "num_heads": self.num_heads,
            "height": self.height,
            "width": self.width,
        }


@dataclass
class BenchmarkConfig:
    warmup_runs: int = 1
    runs: int = 3
    output_dir: Path = field(default_factory=lambda: repo_root() / "results" / "v1")


@dataclass
class UpscaleConfig:
    """推理后 GPU 放大（RealESRGAN 等）；infer HxW → display_size。"""

    enabled: bool = False
    model: Path | None = None


@dataclass
class CompositeConfig:
    """V2: paste FlashHead face onto preset body video / 静态立绘。"""

    enabled: bool = False
    # preset：循环底片；paste_back：静态 2:3 立绘 + 固定 face box
    mode: str = "preset"
    preset_video: Path | None = None
    boxes_jsonl: Path | None = None
    face_ref: Path | None = None
    body_image: Path | None = None
    # 相对 body_image 原图像素的 xyxy；加载时缩到 canvas
    face_box: tuple[int, int, int, int] | None = None
    # 多底片：manifest.json（优先）或单片 fallback
    preset_manifest: Path | None = None
    random_clip: bool = True
    canvas_width: int = 512
    canvas_height: int = 768
    feather_px: int = 14


@dataclass
class AppConfig:
    tier: str
    config_path: Path
    models_root: Path
    cyberverse_models_dir: Path
    sample_image: Path
    sample_audio: Path
    runtime: RuntimeConfig
    infer_params: InferParams
    benchmark: BenchmarkConfig
    composite: CompositeConfig = field(default_factory=CompositeConfig)
    upscale: UpscaleConfig = field(default_factory=UpscaleConfig)


def _resolve_repo_path(value: str | Path | None, *, root: Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    p = Path(str(value))
    if not p.is_absolute():
        p = (root / p).resolve()
    return p


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)
    root = repo_root()

    paths = raw.get("paths") or {}
    rt = raw.get("runtime") or {}
    ip = raw.get("infer_params") or {}
    bm = raw.get("benchmark") or {}
    comp = raw.get("composite") or {}
    up = raw.get("upscale") or {}

    models_root = Path(paths.get("models_root") or default_models_root())
    cv_models = Path(paths.get("cyberverse_models_dir") or cyberverse_models_dir())

    runtime = RuntimeConfig(
        model_type=str(rt.get("model_type", "pro")),
        checkpoint_dir=Path(rt.get("checkpoint_dir") or checkpoint_dir(models_root)),
        wav2vec_dir=Path(rt.get("wav2vec_dir") or wav2vec_dir(models_root)),
        seed=int(rt.get("seed", 9999)),
        use_face_crop=bool(rt.get("use_face_crop", False)),
        compile_model=bool(rt.get("compile_model", True)),
        compile_vae=bool(rt.get("compile_vae", True)),
        world_size=int(rt.get("world_size", 1)),
        letterbox_input=bool(rt.get("letterbox_input", True)),
        display_size=int(rt.get("display_size", 928)),
    )
    infer = InferParams(
        frame_num=int(ip.get("frame_num", 33)),
        motion_frames_latent_num=int(ip.get("motion_frames_latent_num", 2)),
        tgt_fps=int(ip.get("tgt_fps", 20)),
        sample_rate=int(ip.get("sample_rate", 16000)),
        sample_shift=float(ip.get("sample_shift", 5)),
        color_correction_strength=float(ip.get("color_correction_strength", 1.0)),
        cached_audio_duration=int(ip.get("cached_audio_duration", 8)),
        num_heads=int(ip.get("num_heads", 12)),
        height=int(ip.get("height", 464)),
        width=int(ip.get("width", 464)),
    )
    out_dir = Path(bm.get("output_dir") or (repo_root() / "results" / "v1"))
    if not out_dir.is_absolute():
        out_dir = repo_root() / out_dir

    sample_image = _resolve_repo_path(paths.get("sample_image"), root=root) or Path(
        default_sample_image()
    )
    face_ref = _resolve_repo_path(comp.get("face_ref"), root=root)
    if face_ref and face_ref.exists():
        sample_image = face_ref

    face_box = None
    raw_box = comp.get("face_box")
    if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
        face_box = tuple(int(v) for v in raw_box)

    composite = CompositeConfig(
        enabled=bool(comp.get("enabled", False)),
        mode=str(comp.get("mode") or "preset"),
        preset_video=_resolve_repo_path(comp.get("preset_video"), root=root),
        boxes_jsonl=_resolve_repo_path(comp.get("boxes_jsonl"), root=root),
        face_ref=face_ref,
        body_image=_resolve_repo_path(comp.get("body_image"), root=root),
        face_box=face_box,
        preset_manifest=_resolve_repo_path(comp.get("preset_manifest"), root=root),
        random_clip=bool(comp.get("random_clip", True)),
        canvas_width=int(comp.get("canvas_width", 512)),
        canvas_height=int(comp.get("canvas_height", 768)),
        feather_px=int(comp.get("feather_px", 14)),
    )
    upscale_model = up.get("model")
    upscale = UpscaleConfig(
        enabled=bool(up.get("enabled", False)),
        model=Path(str(upscale_model)) if upscale_model else None,
    )

    return AppConfig(
        tier=str(raw.get("tier") or config_path.stem),
        config_path=config_path,
        models_root=models_root,
        cyberverse_models_dir=cv_models,
        sample_image=sample_image,
        sample_audio=Path(paths.get("sample_audio") or default_sample_audio()),
        runtime=runtime,
        infer_params=infer,
        benchmark=BenchmarkConfig(
            warmup_runs=int(bm.get("warmup_runs", 1)),
            runs=int(bm.get("runs", 3)),
            output_dir=out_dir,
        ),
        composite=composite,
        upscale=upscale,
    )
