from __future__ import annotations

import argparse
import statistics
import time
from datetime import datetime
from pathlib import Path

import librosa
import numpy as np
import yaml

from lightning.config import load_config
from lightning.runtime import FlashHeadRuntime, sage_available, sage_backend


def _load_audio(path: Path, sr: int) -> np.ndarray:
    audio, _ = librosa.load(str(path), sr=sr, mono=True)
    return audio.astype(np.float32)


def run_bench(config_path: Path) -> Path:
    cfg = load_config(config_path)
    cfg.benchmark.output_dir.mkdir(parents=True, exist_ok=True)

    preset = None
    if cfg.composite.enabled and cfg.composite.preset_video and cfg.composite.boxes_jsonl:
        from lightning.composite import composite_chunk
        from lightning.preset_clip import PresetClipPlayer

        preset = PresetClipPlayer(
            Path(cfg.composite.preset_video),
            Path(cfg.composite.boxes_jsonl),
            canvas_size=(cfg.composite.canvas_width, cfg.composite.canvas_height),
        )

    rt = FlashHeadRuntime(cfg)
    rt.load()
    rt.set_avatar(cfg.sample_image)

    audio = _load_audio(cfg.sample_audio, cfg.infer_params.sample_rate)
    audio_dur = float(audio.shape[0] / cfg.infer_params.sample_rate)

    for i in range(cfg.benchmark.warmup_runs):
        print(f"[warmup {i+1}/{cfg.benchmark.warmup_runs}]")
        rt.reset()
        if preset is not None:
            preset.reset()
        list(rt.push_audio(audio, sample_rate=cfg.infer_params.sample_rate, is_final=True))

    run_rows = []
    for i in range(cfg.benchmark.runs):
        rt.reset()
        if preset is not None:
            preset.reset()
        if hasattr(__import__("torch").cuda, "reset_peak_memory_stats"):
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        total_frames = 0
        chunk_rtps = []
        composite_s = 0.0
        for frames, m, _pcm in rt.push_audio(
            audio, sample_rate=cfg.infer_params.sample_rate, is_final=True
        ):
            total_frames += m.num_frames
            chunk_rtps.append(m.rtp)
            if preset is not None:
                tc0 = time.perf_counter()
                face = np.asarray(frames, dtype=np.uint8)
                bodies, boxes = preset.next_n(int(face.shape[0]))
                _ = composite_chunk(
                    face, bodies, boxes, feather_px=int(cfg.composite.feather_px)
                )
                composite_s += time.perf_counter() - tc0
        wall = time.perf_counter() - t0
        rtp = wall / (total_frames / cfg.infer_params.tgt_fps) if total_frames else float("inf")
        row = {
            "run": i + 1,
            "wall_s": round(wall, 4),
            "frames": total_frames,
            "rtp": round(rtp, 4),
            "eff_fps": round(total_frames / wall, 3) if wall > 0 else 0.0,
            "chunk_rtp_median": round(statistics.median(chunk_rtps), 4) if chunk_rtps else None,
            "max_vram_gb": round(rt.max_vram_gb(), 3),
            "composite_s": round(composite_s, 4) if preset is not None else None,
        }
        print(f"[run {i+1}] {row}")
        run_rows.append(row)

    rtps = [r["rtp"] for r in run_rows]
    result = {
        "tier": cfg.tier,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model_type": cfg.runtime.model_type,
        "height": cfg.infer_params.height,
        "width": cfg.infer_params.width,
        "tgt_fps": cfg.infer_params.tgt_fps,
        "compile_model": cfg.runtime.compile_model,
        "compile_vae": cfg.runtime.compile_vae,
        "sage_available": sage_available(),
        "sage_backend": sage_backend(),
        "sample_image": str(cfg.sample_image),
        "sample_audio": str(cfg.sample_audio),
        "audio_duration_s": round(audio_dur, 3),
        "composite_enabled": bool(cfg.composite.enabled),
        "canvas_width": cfg.composite.canvas_width if cfg.composite.enabled else None,
        "canvas_height": cfg.composite.canvas_height if cfg.composite.enabled else None,
        "runs": run_rows,
        "median_rtp": round(statistics.median(rtps), 4) if rtps else None,
        "median_eff_fps": round(statistics.median([r["eff_fps"] for r in run_rows]), 3),
        "pass_rtp_lt_1": bool(rtps) and statistics.median(rtps) < 1.0,
    }
    out = cfg.benchmark.output_dir / f"{cfg.tier}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    out.write_text(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"wrote {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="FlashHead RTP benchmark")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "configs" / "t_v2_face256.yaml",
    )
    args = parser.parse_args()
    run_bench(args.config)


if __name__ == "__main__":
    main()
