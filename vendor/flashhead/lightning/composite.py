"""Paste FlashHead face frames onto preset body frames with feathered mask."""

from __future__ import annotations

import numpy as np
from PIL import Image


def _feather_mask(h: int, w: int, feather: int) -> np.ndarray:
    """Float32 HxW alpha in [0,1], soft edges."""
    feather = max(0, int(feather))
    if feather <= 0 or h < 2 or w < 2:
        return np.ones((h, w), dtype=np.float32)
    yy = np.arange(h, dtype=np.float32)
    xx = np.arange(w, dtype=np.float32)
    dist_y = np.minimum(yy, (h - 1) - yy)[:, None]
    dist_x = np.minimum(xx, (w - 1) - xx)[None, :]
    dist = np.minimum(dist_y, dist_x)
    return np.clip(dist / float(feather), 0.0, 1.0).astype(np.float32)


def paste_face_onto_body(
    body: np.ndarray,
    face: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    feather_px: int = 12,
) -> np.ndarray:
    """Composite RGB uint8 face into body at box with edge feather.

    box: (x1, y1, x2, y2) in body pixel coords.
    """
    if body.ndim != 3 or body.shape[2] != 3:
        raise ValueError(f"bad body shape {body.shape}")
    if face.ndim != 3 or face.shape[2] != 3:
        raise ValueError(f"bad face shape {face.shape}")

    h, w = int(body.shape[0]), int(body.shape[1])
    x1, y1, x2, y2 = [int(v) for v in box]
    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(x1 + 1, min(x2, w))
    y2 = max(y1 + 1, min(y2, h))
    bw, bh = x2 - x1, y2 - y1

    face_r = np.asarray(
        Image.fromarray(face.astype(np.uint8)).resize((bw, bh), Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    out = body.astype(np.float32).copy()
    alpha = _feather_mask(bh, bw, feather_px)[..., None]
    roi = out[y1:y2, x1:x2]
    out[y1:y2, x1:x2] = roi * (1.0 - alpha) + face_r * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def composite_chunk(
    face_frames: np.ndarray,
    body_frames: list[np.ndarray],
    boxes: list[tuple[int, int, int, int]],
    *,
    feather_px: int = 12,
) -> np.ndarray:
    """face_frames: [N,Hf,Wf,3]; body/boxes length N. Returns [N,H,W,3]."""
    n = int(face_frames.shape[0])
    if len(body_frames) != n or len(boxes) != n:
        raise ValueError(
            f"length mismatch faces={n} bodies={len(body_frames)} boxes={len(boxes)}"
        )
    out = []
    for i in range(n):
        out.append(
            paste_face_onto_body(
                body_frames[i], face_frames[i], boxes[i], feather_px=feather_px
            )
        )
    return np.stack(out, axis=0)
