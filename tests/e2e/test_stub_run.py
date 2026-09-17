"""Stub-mode e2e — real nodes end-to-end, planted detector boxes for determinism.

Phase 2 version: ingest/detect/track_flag/verify_event/gate are REAL nodes calling
the real Phase 1 tools (frame extraction from a real cv2-written clip, ByteTrack,
the three rules, real snapshot files, vlm stub verdicts). Only the detector EYES are
planted (monkeypatched at the detect node's import binding) so the fired events are
deterministic; everything downstream of detections is the real implementation.

Planted scenario (per graph-design.md rules):
- 13 distinct cars, same box every frame -> 13 stable track IDs -> TRAFFIC_JAM fires
  once (13 > JAM_MIN_VEHICLES=12 inside one 30 s window; clip span 1.6 s << 30 s).
- One pothole box on the first two consecutive frames (IoU 1.0) -> POTHOLE fires once,
  anchored at the first frame (>= POTHOLE_MIN_CONSECUTIVE=2).
- No persons -> KIDS_CROSSING silent.

Stub verdicts (ROADFIX_STUB_MODELS=1) confirm both events above GATE_PUBLISH, so the
gate publishes both — mirroring the Phase 0 shape with real nodes in the loop.
"""

from collections import Counter
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from roadfix.config import GATE_PUBLISH, RECURSION_LIMIT
from roadfix.graph import build_graph
from roadfix.state import Detection
from roadfix.tools.detectors import DetectArgs, DetectResult

# Writer audit from graph-design.md — each node writes EXACTLY its own keys.
WRITER_AUDIT: dict[str, set[str]] = {
    "ingest": {"frames"},
    "detect": {"detections"},
    "track_flag": {"events", "track_summary"},
    "verify_event": {"verdicts"},
    "gate": {"gate_decisions"},
    "publish": {"published", "dropped_count", "map_path", "report_path", "status"},
}

N_CARS = 13  # > JAM_MIN_VEHICLES (12) — one qualifying jam window


def _field(value: object, name: str) -> object:
    """Read a field from a state value that may round-trip as model instance or dict."""
    if isinstance(value, BaseModel):
        return getattr(value, name)
    assert isinstance(value, dict), f"unexpected state value type: {type(value)!r}"
    return value[name]


def _car_boxes(frame_id: str) -> list[Detection]:
    """13 fixed, distinct car boxes — same position every frame so ByteTrack IDs them
    immediately on the first tracker frame (supervision 0.30.3 semantics)."""
    return [
        Detection(
            frame_id=frame_id,
            source="street",
            class_name="car",
            conf=0.9,
            bbox=[float(i * 24), 20.0, float(i * 24 + 20), 60.0],
        )
        for i in range(N_CARS)
    ]


def _fake_street(args: DetectArgs) -> DetectResult:
    """Planted street eye: 13 cars on every frame (frame_id from the file basename)."""
    frame_id = Path(args.frame_path).name
    return DetectResult(detections=_car_boxes(frame_id), model_loaded=True, error=None)


def _fake_pothole(args: DetectArgs) -> DetectResult:
    """Planted pothole eye: one box on the first two frames only (consecutive chain)."""
    frame_id = Path(args.frame_path).name
    boxes: list[Detection] = []
    if frame_id in {"f00000.jpg", "f00001.jpg"}:
        boxes.append(
            Detection(
                frame_id=frame_id,
                source="pothole",
                class_name="pothole",
                conf=0.8,
                bbox=[100.0, 150.0, 140.0, 190.0],
            )
        )
    return DetectResult(detections=boxes, model_loaded=True, error=None)


@pytest.fixture()
def run_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_clip
) -> tuple[Path, str]:
    """Isolated data dir + real tiny clip + stub verdicts + planted detector eyes."""
    monkeypatch.setenv("ROADFIX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROADFIX_STUB_MODELS", "1")
    # 2 s @ 25 fps = 50 source frames -> 5 extracted at 2.5 fps (stride 10).
    clip = make_clip(name="planted.mp4", seconds=2.0, fps=25, size=(320, 240))
    monkeypatch.setattr("roadfix.nodes.detect.yolo_street", _fake_street)
    monkeypatch.setattr("roadfix.nodes.detect.yolo_pothole", _fake_pothole)
    return tmp_path, clip


def _run_graph(tmp_path: Path, video_path: str, run_id: str) -> tuple[
    dict[str, object], list[tuple[str, dict[str, object]]]
]:
    """Invoke the checkpointered graph; return (final state, per-node writes)."""
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        graph = build_graph(saver)
        config = {
            "configurable": {"thread_id": f"video:{run_id}"},
            "recursion_limit": RECURSION_LIMIT,
        }
        initial: dict[str, object] = {
            "video_path": video_path,
            "gps_track_path": None,
            "run_id": run_id,
        }
        writes: list[tuple[str, dict[str, object]]] = []
        for chunk in graph.stream(initial, config, stream_mode="updates"):
            writes.extend(
                (node, update) for node, update in chunk.items() if not node.startswith("__")
            )
        return dict(graph.get_state(config).values), writes


@pytest.mark.e2e
def test_stub_run_end_to_end(run_env: tuple[Path, str]) -> None:
    tmp_path, clip = run_env
    state, writes = _run_graph(tmp_path, clip, "stubrun1")

    # Node execution counts: pipeline nodes once, verify once per event (Send fan-out).
    counts = Counter(node for node, _ in writes)
    assert counts["ingest"] == 1
    assert counts["detect"] == 1
    assert counts["track_flag"] == 1
    assert counts["verify_event"] == 2
    assert counts["gate"] == 1
    assert counts["publish"] == 1

    # State-diff: every node wrote exactly its audited keys — no more, no fewer.
    for node, update in writes:
        assert set(update) == WRITER_AUDIT[node], f"{node} wrote {sorted(update)}"

    # WATCH: real extraction — 5 frames on disk, monotonic timestamps, basename joins.
    frames = state["frames"]
    assert isinstance(frames, list) and len(frames) == 5
    times = [float(_field(f, "t_seconds")) for f in frames]
    assert times == sorted(times) == pytest.approx([0.0, 0.4, 0.8, 1.2, 1.6])
    frame_ids = {str(_field(f, "frame_id")) for f in frames}
    assert frame_ids == {f"f{i:05d}.jpg" for i in range(5)}
    for frame in frames:
        assert Path(str(_field(frame, "path"))).is_file()

    # SPOT: 13 cars x 5 frames + 2 pothole boxes, planted verbatim by the fake eyes.
    detections = state["detections"]
    assert isinstance(detections, list) and len(detections) == N_CARS * 5 + 2
    composition = Counter(
        (str(_field(d, "source")), str(_field(d, "class_name"))) for d in detections
    )
    assert composition == Counter({("street", "car"): N_CARS * 5, ("pothole", "pothole"): 2})

    # TRACK & FLAG: jam + pothole fire exactly once each; snapshots really on disk.
    events = state["events"]
    assert isinstance(events, list) and len(events) == 2
    by_kind = {str(_field(e, "kind")): e for e in events}
    assert set(by_kind) == {"POTHOLE", "TRAFFIC_JAM"}

    pothole = by_kind["POTHOLE"]
    assert str(_field(pothole, "frame_id")) == "f00000.jpg"  # anchored at streak start
    assert _field(pothole, "evidence") == {"consecutive_count": 2}
    assert Path(str(_field(pothole, "snapshot_path"))).is_file()

    jam = by_kind["TRAFFIC_JAM"]
    assert str(_field(jam, "frame_id")) == "f00004.jpg"  # last frame in the 30 s window
    assert _field(jam, "evidence") == {"unique_vehicles": 13, "window_s": 30.0}
    assert Path(str(_field(jam, "snapshot_path"))).is_file()

    summary = state["track_summary"]
    assert isinstance(summary, dict)
    assert summary["unique_vehicles"] == N_CARS
    assert summary["per_class"] == {"car": N_CARS * 5}

    # DOUBLE-CHECK: one stub verdict per event, each a single-item worker write
    # through the operator.add reducer (race invariant — counts, never order).
    verdicts = state["verdicts"]
    assert isinstance(verdicts, list) and len(verdicts) == 2
    assert all(len(update["verdicts"]) == 1 for node, update in writes if node == "verify_event")
    v_by_kind = {str(_field(v, "kind")): v for v in verdicts}
    assert bool(_field(v_by_kind["POTHOLE"], "confirmed"))
    assert float(_field(v_by_kind["POTHOLE"], "confidence")) == pytest.approx(0.85)
    assert float(_field(v_by_kind["TRAFFIC_JAM"], "confidence")) == pytest.approx(0.8)

    # Gate honesty: every decision publishes a confirmed, above-threshold verdict.
    decisions = state["gate_decisions"]
    assert isinstance(decisions, list) and len(decisions) == 2
    assert all(_field(d, "action") == "publish" for d in decisions)
    assert all(str(_field(d, "rule")) == "first_pass_publish" for d in decisions)
    assert all(bool(_field(v, "confirmed")) for v in verdicts)
    assert all(float(_field(v, "confidence")) >= GATE_PUBLISH for v in verdicts)

    # SHOW: published pairs land in final state; nothing dropped; run done.
    published = state["published"]
    assert isinstance(published, list) and len(published) == 2
    assert state["dropped_count"] == 0
    assert state["status"] == "done"
    published_ids = {str(_field(_field(p, "event"), "event_id")) for p in published}
    assert published_ids == {str(_field(e, "event_id")) for e in events}

    # Checkpointing wired: the thread finished cleanly and the saver persisted it.
    assert (tmp_path / "checkpoints.sqlite").is_file()


@pytest.mark.e2e
def test_unreadable_video_degrades_to_honest_empty_run(run_env: tuple[Path, str]) -> None:
    """Missing video -> zero-frame run flows START->END without events, status done."""
    tmp_path, _clip = run_env
    state, writes = _run_graph(tmp_path, str(tmp_path / "nope.mp4"), "emptyrun1")

    counts = Counter(node for node, _ in writes)
    assert counts["ingest"] == 1
    assert counts["detect"] == 1
    assert counts["track_flag"] == 1
    assert counts["verify_event"] == 0  # no events -> publish directly, no fan-out
    assert counts["gate"] == 0
    assert counts["publish"] == 1

    for node, update in writes:
        assert set(update) == WRITER_AUDIT[node], f"{node} wrote {sorted(update)}"

    assert state["frames"] == []
    assert state["detections"] == []
    assert state["events"] == []
    assert state["verdicts"] == []
    # WHY .get: langgraph materializes reducer-channel defaults (verdicts) in
    # get_state, but an overwrite channel gate never wrote stays ABSENT from
    # checkpoint values — absence here IS the evidence the gate never ran.
    assert state.get("gate_decisions", []) == []
    assert state["published"] == []
    assert state["dropped_count"] == 0
    assert state["status"] == "done"
