"""Random pool of preset body clips for V2 composite."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from lightning.preset_clip import PresetClipPlayer

logger = logging.getLogger(__name__)


@dataclass
class PresetClipSpec:
    clip_id: str
    video: Path
    boxes: Path


class PresetClipPool:
    """Load multiple preset clips; pick one at random per session."""

    def __init__(
        self,
        specs: list[PresetClipSpec],
        *,
        canvas_size: tuple[int, int],
        preload: bool = True,
    ) -> None:
        if not specs:
            raise ValueError("empty preset clip specs")
        self.specs = list(specs)
        self.canvas_size = canvas_size
        self._players: dict[str, PresetClipPlayer] = {}
        if preload:
            for s in self.specs:
                self._players[s.clip_id] = PresetClipPlayer(
                    s.video, s.boxes, canvas_size=canvas_size
                )
        self.active: PresetClipPlayer | None = None
        self.active_id: str | None = None
        # default pick first so gateway can run before first session
        self.pick_random()

    @classmethod
    def from_manifest(
        cls,
        manifest_path: Path,
        *,
        canvas_size: tuple[int, int],
        root: Path | None = None,
    ) -> "PresetClipPool":
        root = root or manifest_path.parent
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        specs: list[PresetClipSpec] = []
        for item in data.get("clips") or []:
            video = Path(item["video"])
            boxes = Path(item["boxes"])
            if not video.is_absolute():
                video = (root / video).resolve()
            if not boxes.is_absolute():
                boxes = (root / boxes).resolve()
            if not video.exists() or not boxes.exists():
                logger.warning("skip missing clip %s video=%s boxes=%s", item.get("id"), video, boxes)
                continue
            specs.append(
                PresetClipSpec(
                    clip_id=str(item.get("id") or video.stem),
                    video=video,
                    boxes=boxes,
                )
            )
        if not specs:
            raise RuntimeError(f"no valid clips in manifest {manifest_path}")
        return cls(specs, canvas_size=canvas_size)

    @classmethod
    def from_single(
        cls,
        video: Path,
        boxes: Path,
        *,
        canvas_size: tuple[int, int],
        clip_id: str = "default",
    ) -> "PresetClipPool":
        return cls(
            [PresetClipSpec(clip_id=clip_id, video=video, boxes=boxes)],
            canvas_size=canvas_size,
        )

    def pick_random(self) -> PresetClipPlayer:
        spec = random.choice(self.specs)
        player = self._players.get(spec.clip_id)
        if player is None:
            player = PresetClipPlayer(spec.video, spec.boxes, canvas_size=self.canvas_size)
            self._players[spec.clip_id] = player
        player.reset()
        self.active = player
        self.active_id = spec.clip_id
        logger.info("preset clip picked id=%s video=%s", spec.clip_id, spec.video.name)
        return player

    def reset_active(self) -> None:
        if self.active is not None:
            self.active.reset()
