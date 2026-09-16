"""Stub e2e — START→END flow, published lands in final state, state-diff per writer audit."""

from collections import Counter
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from roadfix.config import GATE_PUBLISH, RECURSION_LIMIT
from roadfix.graph import build_graph

# Writer audit from graph-design.md — each node writes EXACTLY its own keys.
WRITER_AUDIT: dict[str, set[str]] = {
    "ingest": {"frames"},
    "detect": {"detections"},
    "track_flag": {"events", "track_summary"},
    "verify_event": {"verdicts"},
    "gate": {"gate_decisions"},
    "publish": {"published", "dropped_count", "map_path", "report_path", "status"},
}


@pytest.fixture()
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated data dir per test — reads happen at call time, so setenv is enough."""
    monkeypatch.setenv("ROADFIX_DATA_DIR", str(tmp_path))
    return tmp_path


def _field(value: object, name: str) -> object:
    """Read a field from a state value that may round-trip as model instance or dict."""
    if isinstance(value, BaseModel):
        return getattr(value, name)
    assert isinstance(value, dict), f"unexpected state value type: {type(value)!r}"
    return value[name]


def _run_stub(run_id: str) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
    """Invoke the checkpointered stub graph; return (final state, per-node writes)."""
    with SqliteSaver.from_conn_string(str(run_id / "checkpoints.sqlite")) as saver:
        graph = build_graph(saver)
        config = {
            "configurable": {"thread_id": "video:stubrun1"},
            "recursion_limit": RECURSION_LIMIT,
        }
        initial: dict[str, object] = {
            "video_path": "stub.mp4",
            "gps_track_path": None,
            "run_id": "stubrun1",
        }
        writes: list[tuple[str, dict[str, object]]] = []
        for chunk in graph.stream(initial, config, stream_mode="updates"):
            writes.extend(
                (node, update) for node, update in chunk.items() if not node.startswith("__")
            )
        return dict(graph.get_state(config).values), writes


@pytest.mark.e2e
def test_stub_run_end_to_end(run_dir: Path) -> None:
    state, writes = _run_stub(run_dir)

    # Node execution counts: five nodes once, verify once per event (Send fan-out).
    counts = Counter(node for node, _ in writes)
    assert counts["ingest"] == 1
    assert counts["detect"] == 1
    assert counts["track_flag"] == 1
    assert counts["gate"] == 1
    assert counts["publish"] == 1

    # State-diff: every node wrote exactly its audited keys — no more, no fewer.
    for node, update in writes:
        assert set(update) == WRITER_AUDIT[node], f"{node} wrote {sorted(update)}"

    # WATCH: 6 frames on disk, monotonic timestamps.
    frames = state["frames"]
    assert isinstance(frames, list) and len(frames) == 6
    times = [float(_field(f, "t_seconds")) for f in frames]
    assert times == sorted(times)
    for frame in frames:
        assert Path(str(_field(frame, "path"))).is_file()

    # SPOT: deterministic stub composition — 6 cars + 3 persons + 1 bus + 2 potholes.
    detections = state["detections"]
    assert isinstance(detections, list) and len(detections) == 12
    composition = Counter(
        (str(_field(d, "source")), str(_field(d, "class_name"))) for d in detections
    )
    assert composition == Counter(
        {
            ("street", "car"): 6,
            ("street", "person"): 3,
            ("street", "bus"): 1,
            ("pothole", "pothole"): 2,
        }
    )

    # TRACK & FLAG: two events, both snapshots on disk.
    events = state["events"]
    assert isinstance(events, list) and len(events) == 2
    kinds = {_field(e, "kind") for e in events}
    assert kinds == {"POTHOLE", "TRAFFIC_JAM"}
    for event in events:
        assert Path(str(_field(event, "snapshot_path"))).is_file()

    # DOUBLE-CHECK: one verdict per event (race invariant — count, never order),
    # each carried by a single-item worker write through the operator.add reducer.
    verdicts = state["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == len(events) == 2
    assert all(len(update["verdicts"]) == 1 for node, update in writes if node == "verify_event")

    # Gate honesty: every decision publishes a confirmed, above-threshold verdict.
    decisions = state["gate_decisions"]
    assert isinstance(decisions, list) and len(decisions) == 2
    assert all(_field(d, "action") == "publish" for d in decisions)
    assert all(bool(_field(v, "confirmed")) for v in verdicts)
    assert all(float(_field(v, "confidence")) >= GATE_PUBLISH for v in verdicts)

    # SHOW: published pairs land in final state; nothing dropped; run done.
    published = state["published"]
    assert isinstance(published, list) and len(published) == 2
    assert state["dropped_count"] == 0
    assert state["status"] == "done"
    published_ids = {str(_field(_field(p, "event"), "event_id")) for p in published}
    verdict_ids = {str(_field(v, "event_id")) for v in verdicts}
    assert published_ids == verdict_ids
    assert state["map_path"] == str(run_dir / "runs" / "stubrun1" / "map.html")
    assert state["report_path"] == str(run_dir / "runs" / "stubrun1" / "report.md")

    # Checkpointing wired: the thread finished cleanly and the saver persisted it.
    assert (run_dir / "checkpoints.sqlite").is_file()
