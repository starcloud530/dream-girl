from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lightning.paths import default_models_root, repo_root

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


@dataclass
class QwenTTSConfig:
    model_id: str
    model_dir: Path | None
    device: str
    dtype: str
    attn_implementation: str
    speaker: str
    language: str
    instruct: str
    sample_rate: int
    non_streaming_mode: bool

    @classmethod
    def from_yaml(cls, path: str | Path) -> QwenTTSConfig:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)
        root = repo_root()
        models_root = Path(raw.get("models_root") or default_models_root())
        model_dir = raw.get("model_dir")
        resolved_dir = None
        if model_dir:
            p = Path(str(model_dir))
            if not p.is_absolute():
                p = (root / p).resolve()
            elif not p.exists():
                p = (models_root / p.name).resolve()
            resolved_dir = p if p.exists() else p

        return cls(
            model_id=str(raw.get("model_id", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")),
            model_dir=resolved_dir,
            device=str(raw.get("device", "cuda:0")),
            dtype=str(raw.get("dtype", "bfloat16")),
            attn_implementation=str(raw.get("attn_implementation", "flash_attention_2")),
            speaker=str(raw.get("speaker", "Serena")),
            language=str(raw.get("language", "Chinese")),
            instruct=str(raw.get("instruct", "温柔亲切，适合日常对话")),
            sample_rate=int(raw.get("sample_rate", 16000)),
            non_streaming_mode=bool(raw.get("non_streaming_mode", False)),
        )
