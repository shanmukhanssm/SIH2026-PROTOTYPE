"""TRACK & FLAG (stub) — two deterministic events + summary; tracker and rules land in Phase 2."""

import logging

from roadfix.state import Event, RoadfixState

logger = logging.getLogger(__name__)


def track_flag(state: RoadfixState) -> dict[str, object]:
    """TRACK & FLAG (stub) — two deterministic events + summary. Returns state UPDATE only."""
    if not state.frames:
        # Honest empty run — mirrors the ingest zero-frames failure path in graph-design.md.
        return {"events": [], "track_summary": {"unique_vehicles": 0, "per_class": {}}}

    first_frame = state.frames[0]
    # Full stub run anchors the jam at frame 3; shorter runs reuse the last frame.
    jam_frame = state.frames[3] if len(state.frames) > 3 else state.frames[-1]
    events: list[Event] = [
        Event(
            event_id=f"{state.run_id}-POTHOLE-{first_frame.frame_id}-1",
            kind="POTHOLE",
            frame_id=first_frame.frame_id,
            t_seconds=first_frame.t_seconds,
            lat=first_frame.lat,
            lon=first_frame.lon,
            snapshot_path=first_frame.path,  # stub ingest already wrote this jpg to disk
            bbox=[300.0, 500.0, 420.0, 560.0],
            evidence={"consecutive_count": 2},
        ),
        Event(
            event_id=f"{state.run_id}-TRAFFIC_JAM-{jam_frame.frame_id}-1",
            kind="TRAFFIC_JAM",
            frame_id=jam_frame.frame_id,
            t_seconds=jam_frame.t_seconds,
            lat=jam_frame.lat,
            lon=jam_frame.lon,
            snapshot_path=jam_frame.path,
            evidence={"unique_vehicles": 13},
        ),
    ]
    track_summary: dict[str, object] = {
        "unique_vehicles": 13,
        "per_class": {"car": 6, "bus": 1, "person": 3},
    }
    logger.info("[track_flag] stub: emitted %d events", len(events))
    return {"events": events, "track_summary": track_summary}
