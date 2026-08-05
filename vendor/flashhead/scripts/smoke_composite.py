#!/usr/bin/env python3
"""CPU smoke: load preset + boxes, paste dummy face, write one jpeg."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from lightning.composite import paste_face_onto_body  # noqa: E402
from lightning.preset_clip import PresetClipPlayer  # noqa: E402


def main() -> None:
    video = _ROOT / "assets/preset_clips/xiaoya_idle_512x768.mp4"
    boxes = _ROOT / "assets/preset_clips/xiaoya_idle_512x768.boxes.jsonl"
    face_ref = _ROOT / "assets/preset_clips/xiaoya_face_ref_256.jpg"
    out = _ROOT / "results/v2/smoke_composite.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)

    player = PresetClipPlayer(video, boxes, canvas_size=(512, 768))
    bodies, bxs = player.next_n(1)
    face = np.asarray(Image.open(face_ref).convert("RGB"), dtype=np.uint8)
    comp = paste_face_onto_body(bodies[0], face, bxs[0], feather_px=14)
    assert comp.shape == (768, 512, 3), comp.shape
    Image.fromarray(comp).save(out, quality=92)
    print(f"ok wrote {out} shape={comp.shape} box={bxs[0]}")


if __name__ == "__main__":
    main()
