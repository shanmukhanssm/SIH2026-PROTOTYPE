"""DOUBLE-CHECK worker (stub) — one deterministic confirmed verdict per Send;
real VLM inspect lands in Phase 1.
"""

from roadfix.state import Verdict, VerifyPayload


def verify_event(payload: VerifyPayload) -> dict[str, object]:
    """One deterministic confirmed verdict per Send, appended via the verdicts reducer."""
    # WHY boundary validation: langgraph 1.2 hands Send payloads to workers as raw
    # dicts (input_schema does not coerce) — validate the scoped payload here itself.
    payload = VerifyPayload.model_validate(payload)
    verdict = Verdict(
        event_id=payload.event.event_id,
        kind=payload.event.kind,
        attempt=payload.attempt,
        confirmed=True,
        severity="medium",
        # WHY: 0.87 >= GATE_PUBLISH (0.7) on purpose — stub verdicts publish end-to-end
        # so Phase 0 exercises verify -> confirmed verdict; gate -> publish.
        confidence=0.87,
        reason="stub verdict — VLM verification lands in Phase 1",
        verifier="stub",
    )
    # WHY: single-item list — the operator.add reducer merges it; a rebuilt full list is a bug.
    return {"verdicts": [verdict]}
