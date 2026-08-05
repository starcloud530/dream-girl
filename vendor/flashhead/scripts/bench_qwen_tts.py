#!/usr/bin/env python3
"""Qwen3-TTS 本地 bench：TTFA / RTF。"""

from __future__ import annotations

import argparse
import statistics
import time
from datetime import datetime
from pathlib import Path

from lightning.qwen_tts import Qwen3TTSRuntime
from lightning.qwen_tts_config import QwenTTSConfig


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-c", "--config", type=Path, default=Path("configs/qwen_tts.yaml"))
    p.add_argument("-t", "--text", default="你好呀，我是小雅，今天想和你聊聊天。")
    p.add_argument("-n", "--runs", type=int, default=3)
    args = p.parse_args()

    cfg = QwenTTSConfig.from_yaml(args.config)
    rt = Qwen3TTSRuntime(cfg)
    rt.load()

    rows = []
    for i in range(args.runs):
        t0 = time.perf_counter()
        pcm, meta = rt.synthesize_pcm(args.text)
        wall_ms = int((time.perf_counter() - t0) * 1000)
        rows.append({**meta, "wall_ms": wall_ms, "pcm_bytes": len(pcm)})
        print(
            f"run {i+1}/{args.runs} audio_ms={meta['audio_ms']} "
            f"elapsed_ms={meta['elapsed_ms']} rtf={meta['rtf']:.3f} wall_ms={wall_ms}"
        )

    med_rtf = statistics.median(r["rtf"] for r in rows)
    med_elapsed = statistics.median(r["elapsed_ms"] for r in rows)
    print(f"\nmedian rtf={med_rtf:.3f} median_elapsed_ms={med_elapsed}")

    out = Path("results/v1") / f"qwen_tts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"text: {args.text!r}\nruns: {args.runs}\nmedian_rtf: {med_rtf:.4f}\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
