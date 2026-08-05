#!/usr/bin/env python3
"""Qwen3-TTS 服务启动器（与 run_gateway.py 同模式）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

from serve.tts_server import main

if __name__ == "__main__":
    main()
