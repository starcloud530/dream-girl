from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cyber_gf_root() -> Path:
    env = os.environ.get("CYBER_GF_ROOT") or os.environ.get("CYBER_GF_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # repo: .../SoulX-FlashHead-1_3B/lightning-FlashHead → 赛博女友
    return repo_root().parents[2]


def default_models_root() -> Path:
    env = os.environ.get("MODELS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    autodl = Path("/root/autodl-fs/models/flashhead")
    if autodl.parent.exists():
        return autodl
    return repo_root() / "models"


def cyberverse_models_dir() -> Path:
    """Directory that contains the `flash_head` Python package."""
    env = os.environ.get("CYBERVERSE_MODELS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    vendor = repo_root() / "vendor" / "flash_head_models"
    if (vendor / "flash_head").exists():
        return vendor
    upstream = (
        repo_root().parents[0] / "github" / "CyberVerse" / "models"
    )
    if (upstream / "flash_head").exists():
        return upstream
    return vendor


def checkpoint_dir(models_root: Path | None = None) -> Path:
    root = models_root or default_models_root()
    return root / "SoulX-FlashHead-1_3B"


def wav2vec_dir(models_root: Path | None = None) -> Path:
    root = models_root or default_models_root()
    return root / "wav2vec2-base-960h"


def default_sample_image() -> Path:
    env = os.environ.get("SAMPLE_IMAGE")
    if env:
        return Path(env).expanduser().resolve()
    return cyber_gf_root() / "demo" / "assets" / "character" / "xiaoya-v1.jpg"


def default_sample_audio() -> Path:
    env = os.environ.get("SAMPLE_AUDIO")
    if env:
        return Path(env).expanduser().resolve()
    capture = cyber_gf_root() / "demo" / "tmp" / "frame_capture"
    preferred = capture / "tone_20260727_142329.wav"
    if preferred.exists():
        return preferred
    for path in sorted(capture.glob("tone_*.wav")):
        return path
    for path in sorted(capture.glob("*.wav")):
        return path
    return preferred
