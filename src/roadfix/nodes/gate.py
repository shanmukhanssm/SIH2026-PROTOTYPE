"""Confidence gate — deterministic publish/recheck/drop ruling per event.

Consumes the accumulated `verdicts` (reduced with operator.add across verify
passes) and recomputes `gate_decisions` WHOLESALE each pass (overwrite, never
accumulate — replays stay idempotent). Decisions key on `_latest_by_event`:
the highest-attempt verdict per event_id wins, so reducer accumulation across
passes can never double-publish or resurrect a dropped event.

Threshold rules (graph-design.md, attempt-aware; GATE_PUBLISH=0.7,
GATE_RECHECK=0.4) with the exact rule strings:

- attempt == 0 (first pass):
  - confirmed and confidence >= GATE_PUBLISH     -> publish / first_pass_publish
  - confirmed and GATE_RECHECK <= conf < PUBLISH -> recheck / first_pass_recheck
  - not confirmed and conf >= GATE_RECHECK       -> drop / first_pass_drop_unconfirmed
    (the inspector refused at a usable confidence — honesty over confidence)
  - confidence < GATE_RECHECK (confirmed or not) -> drop / first_pass_drop_low_conf
    (below the recheck band there is nothing worth another look, regardless of
    the confirmed flag)
- attempt >= 1 (final pass, bounded by MAX_VERIFY_ATTEMPTS):
  - confirmed                                    -> publish / final_pass_publish
    (includes recheck-band confidences: the extra look settles the event)
  - not confirmed                                -> drop / final_pass_drop

Consistency with route_after_gate (graph.py, READ-ONLY): every recheck
decision's verdict satisfies the router's re-Send condition (confirmed, in
the band, attempt+1 < MAX_VERIFY_ATTEMPTS) and, with MAX_VERIFY_ATTEMPTS == 2,
the router re-Sends exactly the recheck set — attempt >= 1 verdicts never
recheck because attempt+1 < 2 fails there, and the final-pass rule ends them
at publish/drop instead.
"""

from typing import Literal

from roadfix.config import GATE_PUBLISH, GATE_RECHECK
from roadfix.state import GateDecision, RoadfixState, Verdict

_Action = Literal["publish", "recheck", "drop"]


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


def _first_pass_rule(verdict: Verdict) -> tuple[_Action, str]:
    """(action, rule) for a first-look verdict (attempt == 0) — see module docstring."""
    if verdict.confirmed and verdict.confidence >= GATE_PUBLISH:
        return "publish", "first_pass_publish"
    if verdict.confirmed and GATE_RECHECK <= verdict.confidence < GATE_PUBLISH:
        return "recheck", "first_pass_recheck"
    if not verdict.confirmed and verdict.confidence >= GATE_RECHECK:
        return "drop", "first_pass_drop_unconfirmed"
    return "drop", "first_pass_drop_low_conf"


def _final_pass_rule(verdict: Verdict) -> tuple[_Action, str]:
    """(action, rule) for a final-pass verdict (attempt >= 1) — see module docstring."""
    if verdict.confirmed:
        return "publish", "final_pass_publish"
    return "drop", "final_pass_drop"


def gate(state: RoadfixState) -> dict[str, object]:
    """Publish/recheck/drop ruling per event from the latest verdicts; recomputed each pass."""
    latest = _latest_by_event(state.verdicts)
    decisions: list[GateDecision] = []
    # WHY sorted: deterministic, stable output order for a given verdict set —
    # race-invariant tests assert counts and content, never reducer arrival order.
    for event_id in sorted(latest):
        verdict = latest[event_id]
        if verdict.attempt == 0:
            action, rule = _first_pass_rule(verdict)
        else:
            action, rule = _final_pass_rule(verdict)
        decisions.append(GateDecision(event_id=event_id, action=action, rule=rule))
    return {"gate_decisions": decisions}
