"""Load preset body video + per-frame face boxes for V2 composite."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Box:
    frame_idx: int
    x1: int
    y1: int
    x2: int
    y2: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)


def load_boxes_jsonl(path: Path) -> list[Box]:
    boxes: list[Box] = []
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"bad jsonl {path}:{line_no}: {exc}") from exc
        boxes.append(
            Box(
                frame_idx=int(obj.get("frame_idx", len(boxes))),
                x1=int(obj["x1"]),
                y1=int(obj["y1"]),
                x2=int(obj["x2"]),
                y2=int(obj["y2"]),
            )
        )
    if not boxes:
        raise ValueError(f"empty boxes jsonl: {path}")
    boxes.sort(key=lambda b: b.frame_idx)
    return boxes


def load_video_frames(path: Path) -> tuple[list[np.ndarray], float]:
    """Return RGB uint8 frames and fps."""
    import imageio.v2 as imageio

    reader = imageio.get_reader(str(path))
    meta = reader.get_meta_data()
    fps = float(meta.get("fps") or 20.0)
    frames: list[np.ndarray] = []
    try:
        for frame in reader:
            arr = np.asarray(frame)
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr], axis=-1)
            if arr.shape[-1] == 4:
                arr = arr[..., :3]
            frames.append(arr.astype(np.uint8))
    finally:
        reader.close()
    if not frames:
        raise ValueError(f"no frames in {path}")
    return frames, fps


class PresetClipPlayer:
    """Cycle through preset body frames + boxes in lockstep with face stream."""

    def __init__(
        self,
        video_path: Path,
        boxes_path: Path,
        *,
        canvas_size: tuple[int, int] | None = None,
    ) -> None:
        self.video_path = Path(video_path)
        self.boxes_path = Path(boxes_path)
        self.frames, self.fps = load_video_frames(self.video_path)
        self.boxes = load_boxes_jsonl(self.boxes_path)
        if len(self.boxes) < len(self.frames):
            # pad last box
            last = self.boxes[-1]
            for i in range(len(self.boxes), len(self.frames)):
                self.boxes.append(
                    Box(frame_idx=i, x1=last.x1, y1=last.y1, x2=last.x2, y2=last.y2)
                )
        elif len(self.boxes) > len(self.frames):
            self.boxes = self.boxes[: len(self.frames)]

        if canvas_size is not None:
            tw, th = int(canvas_size[0]), int(canvas_size[1])
            oh, ow = int(self.frames[0].shape[0]), int(self.frames[0].shape[1])
            if (ow, oh) != (tw, th):
                sx, sy = tw / float(ow), th / float(oh)
                self.boxes = [
                    Box(
                        frame_idx=b.frame_idx,
                        x1=int(round(b.x1 * sx)),
                        y1=int(round(b.y1 * sy)),
                        x2=int(round(b.x2 * sx)),
                        y2=int(round(b.y2 * sy)),
                    )
                    for b in self.boxes
                ]
                self.frames = [_resize_frame(f, tw, th) for f in self.frames]

        self._cursor = 0
        logger.info(
            "preset clip loaded video=%s frames=%s fps=%.2f boxes=%s size=%sx%s",
            self.video_path.name,
            len(self.frames),
            self.fps,
            len(self.boxes),
            self.frames[0].shape[1],
            self.frames[0].shape[0],
        )

    @property
    def n_frames(self) -> int:
        return len(self.frames)

    def reset(self) -> None:
        self._cursor = 0

    def next_n(self, n: int) -> tuple[list[np.ndarray], list[tuple[int, int, int, int]]]:
        bodies: list[np.ndarray] = []
        boxes: list[tuple[int, int, int, int]] = []
        for _ in range(n):
            i = self._cursor % self.n_frames
            bodies.append(self.frames[i])
            boxes.append(self.boxes[i].as_tuple())
            self._cursor += 1
        return bodies, boxes


def _resize_frame(frame: np.ndarray, tw: int, th: int) -> np.ndarray:
    from PIL import Image

    h, w = frame.shape[:2]
    if w == tw and h == th:
        return frame
    img = Image.fromarray(frame).resize((tw, th), Image.Resampling.LANCZOS)
    return np.asarray(img, dtype=np.uint8)


def default_halfbody_box(
    width: int, height: int, *, face_frac: float = 1.0 / 3.0
) -> tuple[int, int, int, int]:
    """Heuristic centered face box for 2:3 half-body (no detector)."""
    side = int(round(height * face_frac))
    side = max(64, min(side, min(width, height)))
    cx = width // 2
    # face center slightly above geometric center
    cy = int(height * 0.32)
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - side // 2)
    x2 = min(width, x1 + side)
    y2 = min(height, y1 + side)
    # snap if clipped
    x1 = max(0, x2 - side)
    y1 = max(0, y2 - side)
    return x1, y1, x2, y2
