#!/usr/bin/env python3
"""Offline: video → per-frame face boxes jsonl (+ optional 256 face ref)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lightning.preset_clip import default_halfbody_box, load_video_frames  # noqa: E402


def _detect_box_mediapipe(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    try:
        import mediapipe as mp
    except ImportError:
        return None
    h, w = rgb.shape[:2]
    det = mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.3
    )
    try:
        res = det.process(rgb)
    finally:
        det.close()
    if not res.detections:
        return None
    # pick highest score
    best = max(res.detections, key=lambda d: d.score[0] if d.score else 0.0)
    bb = best.location_data.relative_bounding_box
    x1 = int(bb.xmin * w)
    y1 = int(bb.ymin * h)
    x2 = int((bb.xmin + bb.width) * w)
    y2 = int((bb.ymin + bb.height) * h)
    # expand to square ~face_frac friendly
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    side = int(max(x2 - x1, y2 - y1) * 1.35)
    side = max(64, min(side, min(w, h)))
    x1 = int(max(0, cx - side / 2))
    y1 = int(max(0, cy - side / 2 - side * 0.05))
    x2 = min(w, x1 + side)
    y2 = min(h, y1 + side)
    x1 = max(0, x2 - side)
    y1 = max(0, y2 - side)
    return x1, y1, x2, y2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--face-ref", type=Path, default=None, help="write 256x256 face crop")
    ap.add_argument("--face-ref-size", type=int, default=256)
    ap.add_argument(
        "--fallback-heuristic",
        action="store_true",
        default=True,
        help="use half-body heuristic when detector misses (default on)",
    )
    args = ap.parse_args()

    frames, fps = load_video_frames(args.video)
    h, w = frames[0].shape[:2]
    last = default_halfbody_box(w, h)
    used_mp = 0
    used_fb = 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for i, frame in enumerate(frames):
            box = _detect_box_mediapipe(frame)
            if box is None:
                if args.fallback_heuristic:
                    box = last if i > 0 else default_halfbody_box(w, h)
                    used_fb += 1
                else:
                    raise RuntimeError(f"no face on frame {i}; install mediapipe or use fallback")
            else:
                used_mp += 1
            last = box
            rec = {
                "frame_idx": i,
                "x1": int(box[0]),
                "y1": int(box[1]),
                "x2": int(box[2]),
                "y2": int(box[3]),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if args.face_ref is not None:
        x1, y1, x2, y2 = last if used_mp == 0 else _first_detected_or(frames, last)
        # prefer first frame box from file
        first_line = args.out.read_text(encoding="utf-8").splitlines()[0]
        b0 = json.loads(first_line)
        x1, y1, x2, y2 = b0["x1"], b0["y1"], b0["x2"], b0["y2"]
        crop = Image.fromarray(frames[0]).crop((x1, y1, x2, y2))
        crop = crop.resize((args.face_ref_size, args.face_ref_size), Image.Resampling.LANCZOS)
        args.face_ref.parent.mkdir(parents=True, exist_ok=True)
        crop.save(args.face_ref, quality=95)
        print(f"wrote face_ref {args.face_ref}")

    print(
        f"wrote {args.out} frames={len(frames)} fps={fps:.2f} "
        f"mediapipe={used_mp} heuristic={used_fb} size={w}x{h}"
    )


def _first_detected_or(frames, last):
    return last


if __name__ == "__main__":
    main()
