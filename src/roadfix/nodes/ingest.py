"""WATCH — chop the run video into snapshot jpgs at FRAMES_PER_SECOND, then sync GPS.

Consumes tools `extract_frames` + `gps_sync` (tool-registry.md); writes ONLY the
`frames` key (writer audit, graph-design.md). Unreadable video / zero frames ->
`frames=[]` — the run continues and ends `done` with an honest empty report.
Corrupt/missing GPS CSV is NOT an error: gps_sync degrades with a reason and
lat/lon stay None. Never raises past the node.
"""

import logging

from roadfix.config import frames_per_second, runs_dir
from roadfix.state import RoadfixState
from roadfix.tools.frames import ExtractFramesArgs, GpsSyncArgs, extract_frames, gps_sync

logger = logging.getLogger(__name__)


def ingest(state: RoadfixState) -> dict[str, object]:
    """WATCH — extract frames + sync GPS by nearest timestamp. Returns state UPDATE only."""
    try:
        result = extract_frames(
            ExtractFramesArgs(
                video_path=state.video_path,
                out_dir=str(runs_dir() / state.run_id / "frames"),
                fps=frames_per_second(),
            )
        )
        if not result.ok:
            logger.warning("[ingest] extract_frames failed: %s", result.error)
            return {"frames": []}
        # WHY the ignore: this repo runs mypy WITHOUT the pydantic plugin, so any
        # Field()-defaulted arg is synthesized as required — but tolerance_s really
        # defaults to 1.5 per the registry, and the tool default stays the single source.
        synced = gps_sync(
            GpsSyncArgs(  # type: ignore[call-arg]
                frames=result.frames, gps_csv_path=state.gps_track_path
            )
        )
        if synced.reason_unmatched is not None:
            # "no csv provided" is the EXPECTED no-GPS path (many runs have no track) —
            # info, not warning; corrupt/missing CSV variants are real anomalies.
            if synced.reason_unmatched == "no csv provided":
                logger.info("[ingest] gps_sync: %s", synced.reason_unmatched)
            else:
                logger.warning("[ingest] gps_sync: %s", synced.reason_unmatched)
        return {"frames": synced.frames}
    except Exception as exc:  # boundary — degrade to a zero-frame run, never propagate
        logger.warning("[ingest] %s: %s", type(exc).__name__, exc)
        return {"frames": []}
