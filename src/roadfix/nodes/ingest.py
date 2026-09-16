"""WATCH (stub) — writes 6 deterministic placeholder frames; real extraction lands in Phase 1."""

import logging

from roadfix.config import FRAMES_PER_SECOND, runs_dir
from roadfix.state import FrameRef, RoadfixState

logger = logging.getLogger(__name__)


def ingest(state: RoadfixState) -> dict[str, object]:
    """WATCH (stub) — write placeholder frames for the run. Returns state UPDATE only."""
    frame_dir = runs_dir() / state.run_id / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[FrameRef] = []
    for i in range(6):
        frame_id = f"f{i:05d}"
        path = frame_dir / f"{frame_id}.jpg"
        path.write_text("stub frame — real extraction lands in Phase 1")
        # Stub input has no GPS — lat/lon stay None.
        frames.append(FrameRef(frame_id=frame_id, path=str(path), t_seconds=i / FRAMES_PER_SECOND))
    logger.info("[ingest] stub: wrote %d placeholder frames", len(frames))
    return {"frames": frames}
