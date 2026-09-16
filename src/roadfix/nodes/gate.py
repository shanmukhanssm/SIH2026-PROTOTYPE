"""Confidence gate (stub) — publish decision per event from the latest verdict;
threshold logic lands in Phase 2.
"""

from roadfix.state import GateDecision, RoadfixState, Verdict


def _latest_by_event(verdicts: list[Verdict]) -> dict[str, Verdict]:
    """Keep the highest-attempt verdict per event_id (duplicate-verdict safety)."""
    latest: dict[str, Verdict] = {}
    for verdict in verdicts:
        current = latest.get(verdict.event_id)
        # WHY: highest attempt wins; >= keeps the later reducer write on ties, so
        # verdicts accumulated across passes can never double-publish an event.
        if current is None or verdict.attempt >= current.attempt:
            latest[verdict.event_id] = verdict
    return latest


def gate(state: RoadfixState) -> dict[str, object]:
    """Publish decision per event from the latest verdict; recomputed each pass."""
    latest = _latest_by_event(state.verdicts)
    return {
        "gate_decisions": [
            GateDecision(event_id=event_id, action="publish", rule="stub_publish")
            for event_id in latest
        ]
    }
