"""Feature 11 eval — real verify_event worker + gate node (Layer 2 verify -> gate cycle).

Gate cases build Verdict objects directly — the gate is pure over RoadfixState.
Worker cases run in stub mode (ROADFIX_STUB_MODELS=1 — deterministic verdicts,
no endpoint touched, snapshot_path "" reads no files) or against a faked
endpoint installed by monkeypatching the `roadfix.tools.vlm._get_client` seam
(_FakeClient pattern copied from tests/tools/test_vlm.py). Consistency with
route_after_gate (roadfix.graph, read-only) is asserted in both directions:
every recheck decision must be re-Sent with attempt+1, and a state with no
recheck decisions must route to publish.
"""

from collections.abc import Callable
from functools import reduce
from operator import add
from types import SimpleNamespace
from typing import Any, Literal

import httpx
import openai
import pytest

from roadfix.graph import route_after_gate
from roadfix.nodes.gate import _latest_by_event, gate
from roadfix.nodes.verify_event import verify_event
from roadfix.state import Event, GateDecision, RoadfixState, Verdict, VerifyPayload

Kind = Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]

# --- builders -------------------------------------------------------------------


def _event(event_id: str, kind: Kind = "POTHOLE", snapshot_path: str = "") -> Event:
    return Event(
        event_id=event_id,
        kind=kind,
        frame_id="f00003",
        t_seconds=1.2,
        snapshot_path=snapshot_path,  # gate tests never read files; stub worker reads none
        evidence={"consecutive_count": 2},
    )


def _verdict(
    event_id: str,
    *,
    attempt: int = 0,
    confirmed: bool = True,
    confidence: float = 0.9,
    severity: Literal["low", "medium", "high"] = "medium",
    verifier: str = "stub",
) -> Verdict:
    return Verdict(
        event_id=event_id,
        kind="POTHOLE",
        attempt=attempt,
        confirmed=confirmed,
        severity=severity,
        confidence=confidence,
        reason="test verdict",
        verifier=verifier,
    )


def _state(
    verdicts: list[Verdict] | None = None,
    events: list[Event] | None = None,
) -> RoadfixState:
    return RoadfixState(
        video_path="stub.mp4",
        run_id="run1",
        verdicts=verdicts or [],
        events=events or [],
    )


def _decide(state: RoadfixState) -> list[GateDecision]:
    """gate() narrowed to its decisions list."""
    decisions = gate(state)["gate_decisions"]
    assert isinstance(decisions, list)
    return [item for item in decisions if isinstance(item, GateDecision)]


def _by_id(decisions: list[GateDecision]) -> dict[str, GateDecision]:
    return {decision.event_id: decision for decision in decisions}


# --- gate: eval-plan layer-2 case 1 — publish-first-pass --------------------------


def test_publish_first_pass() -> None:
    state = _state(verdicts=[_verdict("run1-POTHOLE-f00003-1", confidence=0.85)])
    decisions = _by_id(_decide(state))
    assert decisions["run1-POTHOLE-f00003-1"].action == "publish"
    assert decisions["run1-POTHOLE-f00003-1"].rule == "first_pass_publish"


# --- gate: case 2 — recheck-then-publish (full verify -> gate -> re-Send cycle) ----


def test_recheck_then_publish_full_cycle() -> None:
    event = _event("run1-POTHOLE-f00003-1")
    first = _verdict(event.event_id, attempt=0, confirmed=True, confidence=0.5)
    state0 = _state(verdicts=[first], events=[event])

    decisions0 = _by_id(_decide(state0))
    assert decisions0[event.event_id].action == "recheck"
    assert decisions0[event.event_id].rule == "first_pass_recheck"

    # route_after_gate must re-Send exactly this event with an attempt+1 payload
    sends = route_after_gate(state0)
    assert isinstance(sends, list) and len(sends) == 1
    assert sends[0].node == "verify_event"
    assert sends[0].arg["event"]["event_id"] == event.event_id
    assert sends[0].arg["attempt"] == 1  # verdict.attempt + 1

    # re-gate on the accumulated verdicts (operator.add): the attempt-1 look clears it
    state1 = _state(
        verdicts=[
            first,
            _verdict(event.event_id, attempt=1, confirmed=True, confidence=0.75),
        ],
        events=[event],
    )
    decisions1 = _by_id(_decide(state1))
    assert len(decisions1) == 1
    assert decisions1[event.event_id].action == "publish"
    assert decisions1[event.event_id].rule == "final_pass_publish"

    assert route_after_gate(state1) == "publish"  # cycle terminates: nothing rechecks


# --- gate: case 3 — recheck-then-drop ------------------------------------------------


def test_recheck_then_drop() -> None:
    event = _event("run1-POTHOLE-f00003-1")
    state0 = _state(
        verdicts=[_verdict(event.event_id, attempt=0, confirmed=True, confidence=0.5)],
        events=[event],
    )
    assert _by_id(_decide(state0))[event.event_id].action == "recheck"

    state1 = _state(
        verdicts=[
            _verdict(event.event_id, attempt=0, confirmed=True, confidence=0.5),
            _verdict(event.event_id, attempt=1, confirmed=False, confidence=0.6),
        ],
        events=[event],
    )
    decisions = _by_id(_decide(state1))
    assert decisions[event.event_id].action == "drop"
    assert decisions[event.event_id].rule == "final_pass_drop"
    assert route_after_gate(state1) == "publish"  # unconfirmed verdicts never recheck


# --- gate: case 4 — drop-first-pass (low confidence + unconfirmed) -------------------


def test_drop_first_pass_low_conf_and_unconfirmed() -> None:
    verdicts = [
        _verdict("run1-POTHOLE-f00003-1", confirmed=True, confidence=0.3),
        _verdict("run1-KIDS_CROSSING-f00005-1", confirmed=False, confidence=0.9),
    ]
    events = [
        _event("run1-POTHOLE-f00003-1"),
        _event("run1-KIDS_CROSSING-f00005-1", kind="KIDS_CROSSING"),
    ]
    decisions = _by_id(_decide(_state(verdicts=verdicts, events=events)))
    low = decisions["run1-POTHOLE-f00003-1"]
    dishonest = decisions["run1-KIDS_CROSSING-f00005-1"]
    assert low.action == "drop" and low.rule == "first_pass_drop_low_conf"
    assert dishonest.action == "drop" and dishonest.rule == "first_pass_drop_unconfirmed"
    assert route_after_gate(_state(verdicts=verdicts, events=events)) == "publish"


# --- gate: case 5 — verifier-error verdicts drop, never publish ----------------------


def test_verifier_error_never_publishes() -> None:
    verdict = _verdict(
        "run1-POTHOLE-f00003-1",
        confirmed=False,
        confidence=0.0,
        verifier="error:timeout",
    )
    decisions = _by_id(_decide(_state(verdicts=[verdict])))
    decision = decisions[verdict.event_id]
    assert decision.action == "drop"
    assert decision.action != "publish"
    assert decision.rule == "first_pass_drop_low_conf"


# --- gate: case 6 — unconfirmed high confidence drops (honesty over confidence) ------


def test_unconfirmed_high_confidence_drops() -> None:
    verdict = _verdict("run1-POTHOLE-f00003-1", confirmed=False, confidence=0.95)
    decisions = _by_id(_decide(_state(verdicts=[verdict])))
    assert decisions[verdict.event_id].action == "drop"
    assert decisions[verdict.event_id].rule == "first_pass_drop_unconfirmed"


# --- gate: case 7 — first-pass boundary matrix ----------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected_action", "expected_rule"),
    [
        (0.7, "publish", "first_pass_publish"),
        (0.4, "recheck", "first_pass_recheck"),
        (0.399, "drop", "first_pass_drop_low_conf"),
    ],
)
def test_first_pass_boundary_matrix(
    confidence: float, expected_action: str, expected_rule: str
) -> None:
    verdict = _verdict("run1-POTHOLE-f00003-1", confirmed=True, confidence=confidence)
    decisions = _by_id(_decide(_state(verdicts=[verdict])))
    assert decisions[verdict.event_id].action == expected_action
    assert decisions[verdict.event_id].rule == expected_rule


# --- gate: case 8 — attempt-1 recheck-band verdict publishes (final-pass rule) --------


def test_final_pass_recheck_band_publishes() -> None:
    verdict = _verdict("run1-POTHOLE-f00003-1", attempt=1, confirmed=True, confidence=0.5)
    decisions = _by_id(_decide(_state(verdicts=[verdict])))
    assert decisions[verdict.event_id].action == "publish"
    assert decisions[verdict.event_id].rule == "final_pass_publish"
    # consistency: the router must NOT re-Send an attempt-1 verdict (attempt+1 < 2 fails)
    state = _state(verdicts=[verdict], events=[_event(verdict.event_id)])
    assert route_after_gate(state) == "publish"


# --- gate: case 9 — duplicate verdicts: highest attempt wins, one decision -------------


def test_duplicate_verdicts_latest_attempt_wins() -> None:
    event_id = "run1-POTHOLE-f00003-1"
    attempt0 = _verdict(event_id, attempt=0, confirmed=True, confidence=0.5)  # recheck if won
    attempt1 = _verdict(event_id, attempt=1, confirmed=False, confidence=0.6)  # final drop

    for order in ([attempt0, attempt1], [attempt1, attempt0]):
        decisions = _by_id(_decide(_state(verdicts=order)))
        assert len(decisions) == 1  # _latest_by_event keeps attempt 1 only
        assert decisions[event_id].action == "drop"
        assert decisions[event_id].rule == "final_pass_drop"

    latest = _latest_by_event([attempt0, attempt1])
    assert list(latest) == [event_id]
    assert latest[event_id].attempt == 1


# --- gate: case 10 — decisions recomputed wholesale, never accumulated ----------------


def test_gate_decisions_recomputed_not_accumulated() -> None:
    keep = _verdict("run1-POTHOLE-f00003-1", confidence=0.85)
    gone = _verdict("run1-TRAFFIC_JAM-f00007-1", confirmed=False, confidence=0.2)

    assert len(_decide(_state(verdicts=[keep, gone]))) == 2

    # verdicts shrank between passes (e.g. a replay) — decisions must match the
    # latest verdict set exactly: no accumulation across gate invocations.
    shrunk = _state(verdicts=[keep])
    again = _decide(shrunk)
    assert len(again) == 1
    assert again[0].event_id == keep.event_id
    assert _decide(shrunk) == again  # pure function: identical inputs, identical output


# --- gate: case 11 — route_after_gate consistency with recheck decisions ---------------


def test_route_after_gate_matches_recheck_decisions() -> None:
    recheck_id = "run1-POTHOLE-f00003-1"
    publish_id = "run1-KIDS_CROSSING-f00005-1"
    drop_id = "run1-TRAFFIC_JAM-f00007-1"
    verdicts = [
        _verdict(recheck_id, attempt=0, confirmed=True, confidence=0.5),
        _verdict(publish_id, attempt=0, confirmed=True, confidence=0.85),
        _verdict(drop_id, attempt=0, confirmed=False, confidence=0.95),
    ]
    events = [
        _event(recheck_id),
        _event(publish_id, kind="KIDS_CROSSING"),
        _event(drop_id, kind="TRAFFIC_JAM"),
    ]
    state = _state(verdicts=verdicts, events=events)

    decisions = _by_id(_decide(state))
    rechecks = {eid for eid, decision in decisions.items() if decision.action == "recheck"}
    assert rechecks == {recheck_id}

    sends = route_after_gate(state)
    assert isinstance(sends, list) and len(sends) == 1  # exactly the recheck decision
    assert sends[0].node == "verify_event"
    assert sends[0].arg["event"]["event_id"] == recheck_id
    assert sends[0].arg["attempt"] == 1  # recheck verdict attempt (0) + 1


# --- gate: case 12 — race invariant: N single-item writes -> N decisions ---------------


def test_race_invariant_reducer_merge_counts() -> None:
    ids = [f"run1-POTHOLE-{i:05d}-1" for i in range(4)]
    writes: list[dict[str, list[Verdict]]] = [
        {"verdicts": [_verdict(event_id, confidence=0.85)]} for event_id in ids
    ]

    def merge(order: list[int]) -> list[Verdict]:
        return reduce(add, [writes[i]["verdicts"] for i in order], [])

    # the same writes merged in different reducer arrival orders
    for merged in (merge([0, 1, 2, 3]), merge([3, 2, 1, 0]), merge([2, 0, 3, 1])):
        decisions = _decide(_state(verdicts=merged))
        assert len(decisions) == len(ids)  # N decisions for N unique events
        assert {decision.event_id for decision in decisions} == set(ids)
        assert all(decision.action == "publish" for decision in decisions)
        # stable output order: sorted by event_id regardless of reducer arrival order
        assert [decision.event_id for decision in decisions] == sorted(ids)


# --- worker: endpoint fake (monkeypatches roadfix.tools.vlm._get_client) ---------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records create() kwargs; replays a script of contents / exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._script = list(script)

    def create(self, **kwargs: Any) -> Any:  # fake boundary, no strict typing needed
        self.calls.append(kwargs)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://vlm.local/v1"))


@pytest.fixture()
def worker_vlm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-endpoint mode with fake credentials; stub mode off (mirrors test_vlm.vlm_env)."""
    monkeypatch.setenv("VLM_BASE_URL", "http://vlm.local/v1")
    monkeypatch.setenv("VLM_API_KEY", "test-key")
    monkeypatch.setenv("VLM_JSON_MODE", "auto")
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)


# --- worker: case 13 — stub mode deterministic verdict per kind ------------------------


def test_worker_stub_verdict_matches_table(stub_env: None) -> None:
    table: list[tuple[Kind, bool, Literal["low", "medium", "high"], float]] = [
        ("POTHOLE", True, "medium", 0.85),
        ("KIDS_CROSSING", True, "high", 0.9),
        ("TRAFFIC_JAM", True, "low", 0.8),
    ]
    for kind, confirmed, severity, confidence in table:
        event = _event(f"run1-{kind}-f00003-1", kind=kind)  # snapshot_path "" — no file reads
        for attempt in (0, 1):
            result = verify_event(VerifyPayload(event=event, attempt=attempt))
            verdicts = result["verdicts"]
            assert isinstance(verdicts, list) and len(verdicts) == 1
            verdict = verdicts[0]
            assert isinstance(verdict, Verdict)
            assert verdict.event_id == event.event_id
            assert verdict.kind == kind
            assert verdict.attempt == attempt  # attempt passthrough
            assert verdict.confirmed is confirmed
            assert verdict.severity == severity
            assert verdict.confidence == confidence
            assert verdict.verifier == "stub"


# --- worker: case 14 — endpoint timeout maps to an honest error verdict ----------------


def test_worker_maps_endpoint_timeout_to_error_verdict(
    worker_vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    completions = _FakeCompletions([_timeout(), _timeout()])
    monkeypatch.setattr("roadfix.tools.vlm._get_client", lambda: _FakeClient(completions))
    event = _event("run1-POTHOLE-f00003-1", snapshot_path=make_image("snap-worker.jpg"))

    result = verify_event(VerifyPayload(event=event, attempt=1))

    verdicts = result["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == 1
    verdict = verdicts[0]
    assert isinstance(verdict, Verdict)
    assert verdict.verifier.startswith("error:")
    assert verdict.verifier == "error:timeout"
    assert verdict.confirmed is False  # worker maps the failure honestly
    assert verdict.confidence == 0.0
    assert verdict.event_id == event.event_id
    assert verdict.attempt == 1
    assert len(completions.calls) == 2  # initial call + 1 transient retry inside the tool


# --- worker: case 15 — raw-dict Send payload accepted (model_validate path) -------------


def test_worker_accepts_raw_dict_payload(stub_env: None) -> None:
    payload = VerifyPayload(event=_event("run1-POTHOLE-f00003-1"), attempt=0)
    via_model = verify_event(payload)
    via_dict = verify_event(payload.model_dump())  # what langgraph actually hands the worker
    assert via_model == via_dict
    verdicts = via_dict["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == 1
    verdict = verdicts[0]
    assert isinstance(verdict, Verdict)
    assert verdict.attempt == 0
    assert verdict.verifier == "stub"


# --- worker: case 16 — returns ONLY the verdicts key ------------------------------------


def test_worker_returns_only_verdicts_key(stub_env: None) -> None:
    result = verify_event(VerifyPayload(event=_event("run1-POTHOLE-f00003-1"), attempt=0))
    assert set(result) == {"verdicts"}
    verdicts = result["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == 1
    assert isinstance(verdicts[0], Verdict)


# --- worker: belt-and-braces — never raises on any payload shape -------------------------


def test_worker_never_raises_on_garbage_payload() -> None:
    """Any payload surprise becomes a single honest error verdict, never a raise."""
    # dict payload whose event fails Event validation — identity is best-effort extracted
    partial = verify_event({"event": {"event_id": "run1-POTHOLE-f00003-1"}, "attempt": 0})
    verdicts = partial["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == 1
    verdict = verdicts[0]
    assert isinstance(verdict, Verdict)
    assert verdict.verifier == "error:worker_exception"
    assert verdict.confirmed is False
    assert verdict.confidence == 0.0
    assert verdict.event_id == "run1-POTHOLE-f00003-1"
    assert verdict.kind == "UNKNOWN"
    assert verdict.attempt == 0

    for garbage in ("not-a-payload", 42):  # type: ignore[arg-type]
        result = verify_event(garbage)  # type: ignore[arg-type]
        assert set(result) == {"verdicts"}
        fallback = result["verdicts"]
        assert isinstance(fallback, list) and len(fallback) == 1
        assert isinstance(fallback[0], Verdict)
        assert fallback[0].verifier == "error:worker_exception"
        assert fallback[0].event_id == ""
