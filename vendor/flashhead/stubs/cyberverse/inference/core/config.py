"""Stub of CyberVerse ``inference.core.config``.

FlashHead's ``flash_head.inference`` imports ``load_config`` at module import time.
Our lightning wrapper always calls ``configure_infer_params`` / ``configure_runtime_options``
directly, so YAML loading is unused — this stub only satisfies the import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_config(config_path: str | Path) -> dict[str, Any]:
    raise RuntimeError(
        "CyberVerse YAML config loading is disabled in lightning-FlashHead. "
        f"Pass infer_params via configure_infer_params() instead (requested: {config_path})."
    )
