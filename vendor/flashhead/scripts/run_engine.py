#!/usr/bin/env python3
"""常驻 FlashHead Engine 启动器（加载模型，监听 :6009）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def run(argv: list[str] | None = None) -> None:
    from serve.engine import main

    main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    run()
