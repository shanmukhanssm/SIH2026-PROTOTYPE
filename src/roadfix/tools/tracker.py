"""bytetrack_tracks tool — ByteTrack IDs for street detections across frames (registry contract)."""

import logging

from pydantic import BaseModel

from roadfix.state import Detection

logger = logging.getLogger(__name__)


class TrackArgs(BaseModel):
    """Street-source detections of one clip, ordered by frame t_seconds."""

    detections: list[Detection]


class TrackResult(BaseModel):
    """Same boxes with track_id filled (unmatched → None); tracked_ok=False on failure."""

    detections: list[Detection]
    tracked_ok: bool
    error: str | None = None


def bytetrack_tracks(args: TrackArgs) -> TrackResult:
    """TRACK — ID every vehicle/person frame to frame so the same car counts once. Never raises.

    Street boxes only — potholes are static, matched by IoU in the rule engine (node layer).
    """
    # New Detection objects — inputs are never mutated; unmatched stays None on failure.
    out_boxes: list[Detection] = [
        d.model_copy(update={"track_id": None}) for d in args.detections
    ]
    try:
        import numpy as np
        import supervision as sv  # lazy heavy import

        # One FRESH tracker per call; IDs are only meaningful when frames arrive in
        # t_seconds order (nodes feed one clip's frames per run, in sequence).
        tracker = sv.ByteTrack()
        class_ids = {
            name: i for i, name in enumerate(sorted({d.class_name for d in args.detections}))
        }

        # Group input indices by frame_id. Detection carries no t_seconds field, so the
        # registry's "order groups by min t_seconds" is realized as frame_id order —
        # ids are f{index:05d} ordinals at a fixed fps, sorting chronologically.
        groups: dict[str, list[int]] = {}
        for i, d in enumerate(args.detections):
            groups.setdefault(d.frame_id, []).append(i)

        for frame_id in sorted(groups):
            idxs = groups[frame_id]
            if not idxs:  # empty frame group — keep the tracker timeline aligned
                tracker.update_with_detections(sv.Detections.empty())
                continue
            dets = [args.detections[i] for i in idxs]
            tracked = tracker.update_with_detections(
                sv.Detections(
                    xyxy=np.array([d.bbox for d in dets], dtype=np.float32),
                    confidence=np.array([d.conf for d in dets], dtype=np.float32),
                    class_id=np.array([class_ids[d.class_name] for d in dets], dtype=int),
                )
            )
            # supervision 0.30.3 returns only matched rows (unmatched dropped), in fed
            # order with boxes verbatim — two-pointer maps rows back to source Detections.
            if len(tracked) == 0 or tracked.tracker_id is None:
                continue
            j = 0
            for row in range(len(tracked)):
                while j < len(dets) and not np.allclose(dets[j].bbox, tracked.xyxy[row]):
                    j += 1
                if j == len(dets):
                    break
                out_boxes[idxs[j]].track_id = int(tracked.tracker_id[row])
                j += 1
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning("[bytetrack_tracks] tracker failed, returning untracked: %s", exc)
        return TrackResult(
            detections=out_boxes, tracked_ok=False, error=f"{type(exc).__name__}: {exc}"
        )
    return TrackResult(detections=out_boxes, tracked_ok=True, error=None)
