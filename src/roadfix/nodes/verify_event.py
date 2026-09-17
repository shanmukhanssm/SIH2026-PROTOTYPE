"""DOUBLE-CHECK worker — one vlm_inspect verdict per Send, mapped onto the state Verdict.

Langgraph hands Send payloads to workers as RAW DICTS (input_schema does not
coerce at runtime), so the worker validates its own scoped payload at the
boundary (`VerifyPayload.model_validate`) and maps the tool's InspectResult
onto the state-level Verdict 1:1 — confirmed/severity/confidence/reason/
verifier pass through untouched, event_id/kind/attempt come from the payload.
The return contract is exactly `{"verdicts": [verdict]}` — a single-item list
the `operator.add` reducer merges; rebuilding the full verdict list here would
be the classic vanished/duplicated-result bug.

Never raises past the worker: the whole body is wrapped once. Any unexpected
failure becomes an honest error verdict (confirmed=False, confidence=0.0,
verifier="error:worker_exception") that the gate drops. vlm_inspect itself
already never raises — this is belt-and-braces at the worker boundary against
payload/validation surprises.
"""

import logging

from roadfix.state import Verdict, VerifyPayload
from roadfix.tools.vlm import InspectArgs, vlm_inspect

logger = logging.getLogger(__name__)

_MAX_REASON_CHARS: int = 200  # error-verdict causes stay short and greppable


def _error_verdict(payload: object, exc: Exception) -> Verdict:
    """Honest drop verdict for an unexpected worker failure.

    Identity fields are best-effort: taken from the payload when its shape
    allows (validated model or raw dict), else empty defaults — an error
    verdict must still exist so the reducer write lands and the gate can drop
    the event instead of the whole run failing.
    """
    event_id = ""
    kind = "UNKNOWN"
    attempt = 0
    if isinstance(payload, VerifyPayload):
        event_id = payload.event.event_id
        kind = payload.event.kind
        attempt = payload.attempt
    elif isinstance(payload, dict):
        raw_event: object = payload.get("event")
        if isinstance(raw_event, dict):
            event_id = str(raw_event.get("event_id", ""))
            kind = str(raw_event.get("kind", "UNKNOWN"))
        raw_attempt: object = payload.get("attempt")
        # WHY the bool guard: bool is an int subclass and Verdict.attempt is int
        # (pydantic v2 rejects bools) — a bool here would raise inside the
        # error path and defeat the never-raises contract.
        if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool):
            attempt = raw_attempt
    return Verdict(
        event_id=event_id,
        kind=kind,
        attempt=attempt,
        confirmed=False,
        severity="low",
        confidence=0.0,
        reason=f"{type(exc).__name__}: {exc}"[:_MAX_REASON_CHARS],
        verifier="error:worker_exception",
    )


def verify_event(payload: VerifyPayload) -> dict[str, object]:
    """DOUBLE-CHECK worker — one VLM call for one event. Verdict appended via reducer."""
    try:
        # WHY boundary validation: langgraph passes Send payloads as raw dicts even
        # though graph.py wires this worker as a RunnableLambda with
        # input_schema=VerifyPayload — validate the scoped payload here itself.
        validated = VerifyPayload.model_validate(payload)
        result = vlm_inspect(InspectArgs(event=validated.event, attempt=validated.attempt))
        verdict = Verdict(
            event_id=validated.event.event_id,
            kind=validated.event.kind,
            attempt=validated.attempt,
            confirmed=result.confirmed,
            severity=result.severity,
            confidence=result.confidence,
            reason=result.reason,
            verifier=result.verifier,
        )
    except Exception as exc:  # boundary — never propagate into the reducer
        logger.warning("[verify_event] worker exception: %s: %s", type(exc).__name__, exc)
        verdict = _error_verdict(payload, exc)
    # WHY: single-item list — the operator.add reducer merges it; a rebuilt full
    # list is a bug.
    return {"verdicts": [verdict]}
