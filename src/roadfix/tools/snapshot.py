"""save_event_snapshot tool — persist the evidence photo for one flagged event.

Contract: context/tool-registry.md `save_event_snapshot`. Writes the re-encoded
full frame plus the trigger-box crop (clamped to image bounds, minimum 1x1).
Unreadable frame -> ok=False; crop failure alone -> degraded, ok stays True.
"""

from __future__ import annotations

import logging
import math
import os

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_JPEG_QUALITY: int = 85


class SnapshotArgs(BaseModel):
    """Args for save_event_snapshot."""

    frame_path: str
    event_id: str
    out_dir: str  # data/runs/<run_id>/snapshots
    bbox: list[float] | None = None  # xyxy pixels


class SnapshotResult(BaseModel):
    """Paths written ("" = not persisted) plus ok."""

    full_path: str  # "" on failure
    crop_path: str  # "" when bbox None or crop fails
    ok: bool


def _clamp_bbox(
    bbox: list[float], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Clamp an xyxy box to image bounds (minimum 1x1); None when unusable."""
    if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
        logger.warning("[save_event_snapshot] unusable bbox %s — crop skipped", bbox)
        return None
    x0 = min(max(int(round(bbox[0])), 0), width - 1)
    y0 = min(max(int(round(bbox[1])), 0), height - 1)
    x1 = min(max(int(round(bbox[2])), x0 + 1), width)
    y1 = min(max(int(round(bbox[3])), y0 + 1), height)
    return x0, y0, x1, y1


def save_event_snapshot(args: SnapshotArgs) -> SnapshotResult:
    """SNAP — persist full-frame jpg (+ trigger-box crop) for one event. Never raises."""
    try:
        import cv2  # lazy heavy import (code-standards.md)

        image = cv2.imread(args.frame_path)
        if image is None:
            logger.warning(
                "[save_event_snapshot] unreadable frame %s (event %s)",
                args.frame_path,
                args.event_id,
            )
            return SnapshotResult(full_path="", crop_path="", ok=False)
        os.makedirs(args.out_dir, exist_ok=True)
        full_path = os.path.join(args.out_dir, f"{args.event_id}_full.jpg")
        params = [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY]
        if not cv2.imwrite(full_path, image, params):
            logger.warning("[save_event_snapshot] imwrite failed for %s", full_path)
            return SnapshotResult(full_path="", crop_path="", ok=False)
        crop_path = ""
        if args.bbox is not None:
            box = _clamp_bbox(args.bbox, image.shape[1], image.shape[0])
            if box is not None:
                x0, y0, x1, y1 = box
                candidate = os.path.join(args.out_dir, f"{args.event_id}_crop.jpg")
                if cv2.imwrite(candidate, image[y0:y1, x0:x1], params):
                    crop_path = candidate
                else:
                    logger.warning("[save_event_snapshot] crop imwrite failed for %s", candidate)
        return SnapshotResult(full_path=full_path, crop_path=crop_path, ok=True)
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning("[save_event_snapshot] failed for event %s: %s", args.event_id, exc)
        return SnapshotResult(full_path="", crop_path="", ok=False)
