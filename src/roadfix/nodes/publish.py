"""SHOW (stub) — pairs events with verdicts into published results;
store/map/report tools land in Phase 1.
"""

from roadfix.config import runs_dir
from roadfix.nodes.gate import _latest_by_event
from roadfix.state import PublishedEvent, RoadfixState


def publish(state: RoadfixState) -> dict[str, object]:
    """Pair events with their latest verdicts and set the run's artifact paths."""
    latest = _latest_by_event(state.verdicts)
    published = [
        PublishedEvent(event=event, verdict=latest[event.event_id])
        for event in state.events
        if event.event_id in latest
    ]
    # WHY: stub pairing keys on verdict presence; Phase 1 filters via gate_decisions.
    # Fake paths only — Phase 0 writes no files.
    run_dir = runs_dir() / state.run_id
    return {
        "published": published,
        "dropped_count": len(state.events) - len(published),
        "map_path": str(run_dir / "map.html"),
        "report_path": str(run_dir / "report.md"),
        "status": "done",
    }
