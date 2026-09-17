"""Layer-2 tests for the track_flag node — eval-plan.md rules dataset (6 cases) + extras.

Synthetic Detection sequences are pure data; real tiny jpgs (conftest make_image, known
320x240 size so band/smallness thresholds are computable) are written ONLY where the node
needs them: the KIDS rule reads real frame heights and save_event_snapshot persists the
anchor frame. Threshold numbers (0.4 IoU, 2 consecutive, 2.0 s dwell, 0.25 smallness,
12 vehicles, 30.0 s window) are pinned literally — these tests ARE the contract
(graph-design.md track_flag spec + config.py); a config change must break them loudly.

Boundary note (TRAFFIC_JAM): the task brief's case list said "13 vehicles -> silent",
which contradicts the rule it states in the same breath — unique count > JAM_MIN_VEHICLES
(12, strict >), per config.py and graph-design.md. Strict > 12 makes 13 the smallest
FIRING count, so the boundary is pinned here as 12 -> silent, 13 -> fires; the
discrepancy is reported to the orchestrator.
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

import pytest

from roadfix.nodes.track_flag import (
    kids_crossing_rule,
    pothole_rule,
    track_flag,
    traffic_jam_rule,
)
from roadfix.state import Detection, Event, FrameRef, RoadfixState

RUN_ID = "run-p2b"
STEP = 0.4  # 2.5 fps frame spacing
# Small person fully inside the road band on a 320x240 frame:
# center_y 175 -> 175/240 = 0.729 in [0.55, 1.0]; height 50 -> 50/240 = 0.208 < 0.25.
PERSON_BOX = [100.0, 150.0, 130.0, 200.0]
TIMES_3S = [0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8]  # 3 s span at 2.5 fps


# --- builders ---------------------------------------------------------------


def _d(
    frame_id: str,
    class_name: str,
    bbox: list[float],
    *,
    source: Literal["street", "pothole"] = "street",
    conf: float = 0.9,
    track_id: int | None = None,
) -> Detection:
    return Detection(
        frame_id=frame_id,
        source=source,
        class_name=class_name,
        conf=conf,
        bbox=bbox,
        track_id=track_id,
    )


def _pothole(frame_id: str, bbox: list[float]) -> Detection:
    return _d(frame_id, "pothole", bbox, source="pothole", conf=0.8)


def _person(frame_id: str, bbox: list[float], track_id: int) -> Detection:
    return _d(frame_id, "person", bbox, track_id=track_id)


def _frames(
    tmp_path: Path,
    times: list[float],
    *,
    real: bool,
    make_image: Callable[..., str] | None = None,
    size: tuple[int, int] = (320, 240),
) -> list[FrameRef]:
    """FrameRefs at f{i:05d}/{t_seconds}; real=True writes jpgs via make_image."""
    frames: list[FrameRef] = []
    for i, t in enumerate(times):
        frame_id = f"f{i:05d}"
        if real:
            assert make_image is not None, "real=True requires the make_image fixture"
            path = make_image(name=f"{frame_id}.jpg", size=size)
        else:
            path = str(tmp_path / f"{frame_id}.jpg")  # intentionally never written
        frames.append(FrameRef(frame_id=frame_id, path=path, t_seconds=t))
    return frames


def _state(
    frames: list[FrameRef],
    detections: list[Detection],
    run_id: str = RUN_ID,
) -> RoadfixState:
    return RoadfixState(
        video_path="inbox/clip.mp4", run_id=run_id, frames=frames, detections=detections
    )


def _events(out: dict[str, object]) -> list[Event]:
    events = out["events"]
    assert isinstance(events, list)
    assert all(isinstance(e, Event) for e in events)
    return [e for e in events if isinstance(e, Event)]


def _summary(out: dict[str, object]) -> dict[str, object]:
    summary = out["track_summary"]
    assert isinstance(summary, dict)
    return summary


def _vehicle_grid(count: int, frame_id: str, x_offset: float = 0.0) -> list[Detection]:
    """`count` distinct non-overlapping vehicle boxes on one frame (5-column grid)."""
    return [
        _d(
            frame_id,
            "car",
            [x_offset + 10.0 + (i % 5) * 120.0, 10.0 + (i // 5) * 60.0,
             x_offset + 70.0 + (i % 5) * 120.0, 50.0 + (i // 5) * 60.0],
        )
        for i in range(count)
    ]


def _vehicles(frame_id: str, track_ids: Iterable[int]) -> list[Detection]:
    """Pre-tracked vehicle detections for pure jam-rule tests (box layout irrelevant)."""
    return [_d(frame_id, "car", [10.0, 10.0, 50.0, 50.0], track_id=t) for t in track_ids]


def _kids_inputs() -> tuple[dict[str, float], dict[str, int]]:
    """frame_times/frame_heights for 8 frames spanning 2.8 s at 240 px height."""
    frame_ids = [f"f{i:05d}" for i in range(8)]
    times = {fid: t for fid, t in zip(frame_ids, TIMES_3S, strict=True)}
    heights = {fid: 240 for fid in frame_ids}
    return times, heights


# --- eval-plan case 1: 2-consecutive pothole fires once, anchored at FIRST frame ---


def test_two_consecutive_pothole_fires_once_anchored_at_first_frame(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=True, make_image=make_image)
    box = [100.0, 100.0, 140.0, 140.0]
    out = track_flag(_state(frames, [_pothole("f00000", box), _pothole("f00001", box)]))
    events = _events(out)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "POTHOLE"
    assert ev.frame_id == "f00000"
    assert ev.t_seconds == 0.0
    assert ev.bbox == box  # streak FIRST box
    assert ev.track_id is None
    assert ev.evidence == {"consecutive_count": 2}
    assert ev.event_id == f"{RUN_ID}-POTHOLE-f00000-1"
    assert ev.snapshot_path != ""  # real anchor frame -> snapshot persisted
    assert _summary(out) == {"unique_vehicles": 0, "per_class": {}}


# --- eval-plan case 2: 1-frame pothole is silent ---


def test_single_frame_pothole_is_silent(stub_env: None, tmp_path: Path) -> None:
    frames = _frames(tmp_path, [0.0], real=False)
    out = track_flag(_state(frames, [_pothole("f00000", [100.0, 100.0, 140.0, 140.0])]))
    assert _events(out) == []
    assert _summary(out) == {"unique_vehicles": 0, "per_class": {}}


# --- eval-plan case 3: gap-IoU pothole fires once (dedupe) ---


def test_gap_iou_pothole_deduped_to_single_event(stub_env: None, tmp_path: Path) -> None:
    frames = _frames(tmp_path, [0.0, 0.4, 0.8, 1.2, 1.6], real=False)
    first = [100.0, 100.0, 140.0, 140.0]
    reappearing = [110.0, 100.0, 150.0, 140.0]  # IoU 0.6 with `first` — same physical pothole
    dets = [
        _pothole("f00000", first),
        _pothole("f00001", first),
        # f00002: the gap — no pothole detection, the streak must break here
        _pothole("f00003", reappearing),
        _pothole("f00004", reappearing),
    ]
    out = track_flag(_state(frames, dets))
    events = _events(out)
    assert len(events) == 1
    assert events[0].frame_id == "f00000"
    assert events[0].evidence == {"consecutive_count": 2}


# --- eval-plan case 4: 3 s small tracked person in the road band fires once ---


def test_small_person_in_road_band_for_3s_fires_once(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, TIMES_3S, real=True, make_image=make_image)
    dets = [_d(f.frame_id, "person", list(PERSON_BOX)) for f in frames]
    out = track_flag(_state(frames, dets))
    events = _events(out)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "KIDS_CROSSING"
    assert ev.frame_id == "f00000"
    assert ev.t_seconds == 0.0
    # ByteTrack confirms the person immediately: present from tracker frame 1 onwards
    # (worklog 2-c: objects present in tracker frame 1 get an external ID at once).
    assert ev.track_id is not None
    assert ev.bbox == PERSON_BOX  # constant box -> median box == the box
    assert ev.evidence == {"dwell_s": 2.8, "looks_small": True}
    assert ev.evidence["dwell_s"] > 2.0
    assert ev.snapshot_path != ""
    assert _summary(out) == {"unique_vehicles": 0, "per_class": {"person": 8}}


# --- eval-plan case 5: 1 s person is silent ---


def test_one_second_person_is_silent(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, 0.4, 0.8, 1.2], real=True, make_image=make_image)
    dets = [_d(f.frame_id, "person", list(PERSON_BOX)) for f in frames]
    out = track_flag(_state(frames, dets))
    assert _events(out) == []  # tracked and small, but dwell 1.2 s is not > 2.0 s
    assert _summary(out) == {"unique_vehicles": 0, "per_class": {"person": 4}}


# --- eval-plan case 6: 15 unique vehicles in a 30 s window fires once ---


def test_fifteen_unique_vehicles_in_30s_window_fires_once(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=True, make_image=make_image)
    dets = _vehicle_grid(15, "f00000") + _vehicle_grid(15, "f00001")
    out = track_flag(_state(frames, dets))
    events = _events(out)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "TRAFFIC_JAM"
    assert ev.frame_id == "f00001"  # anchor = last frame inside the window [0, 30]
    assert ev.t_seconds == STEP
    assert ev.bbox is None
    assert ev.track_id is None
    assert ev.evidence == {"unique_vehicles": 15, "window_s": 30.0}
    assert ev.snapshot_path != ""
    assert _summary(out)["unique_vehicles"] == 15


# --- jam boundary of the strict > rule ---


def test_twelve_vehicles_is_silent_at_strict_boundary(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=True, make_image=make_image)
    dets = _vehicle_grid(12, "f00000") + _vehicle_grid(12, "f00001")
    out = track_flag(_state(frames, dets))
    assert _events(out) == []  # 12 is not > 12
    assert _summary(out)["unique_vehicles"] == 12


def test_thirteen_vehicles_fire_above_strict_boundary(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=True, make_image=make_image)
    dets = _vehicle_grid(13, "f00000") + _vehicle_grid(13, "f00001")
    out = track_flag(_state(frames, dets))
    events = _events(out)
    assert len(events) == 1  # 13 > 12: smallest firing count under strict >
    assert events[0].evidence == {"unique_vehicles": 13, "window_s": 30.0}


# --- extras: degradation, dedupe, format, empties, purity ---


def test_tracker_failure_disables_id_rules_but_pothole_still_fires(
    stub_env: None,
    tmp_path: Path,
    make_image: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import supervision as sv

    def _boom(self: sv.ByteTrack, detections: sv.Detections) -> sv.Detections:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sv.ByteTrack, "update_with_detections", _boom)
    frames = _frames(tmp_path, [0.0, 0.4, 0.8, 1.2], real=True, make_image=make_image)
    dets = (
        [_d(f.frame_id, "person", list(PERSON_BOX)) for f in frames]  # would qualify kids
        + _vehicle_grid(13, "f00000")  # would qualify jam
        + _vehicle_grid(13, "f00001")
        + [_pothole("f00000", [100.0, 100.0, 140.0, 140.0])]
        + [_pothole("f00001", [100.0, 100.0, 140.0, 140.0])]
    )
    out = track_flag(_state(frames, dets))
    events = _events(out)
    assert len(events) == 1
    assert events[0].kind == "POTHOLE"  # the ID-independent rule still runs
    assert events[0].snapshot_path != ""
    summary = _summary(out)
    assert summary["unique_vehicles"] == 0  # no IDs without the tracker
    assert summary["per_class"] == {"car": 26, "person": 4}  # box counts stay honest


def test_snapshot_failure_event_still_emitted_with_empty_path(
    stub_env: None, tmp_path: Path
) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=False)  # paths point at nonexistent files
    box = [100.0, 100.0, 140.0, 140.0]
    out = track_flag(_state(frames, [_pothole("f00000", box), _pothole("f00001", box)]))
    events = _events(out)
    assert len(events) == 1  # rule fired and the event is emitted regardless
    assert events[0].snapshot_path == ""
    assert events[0].evidence == {"consecutive_count": 2}


def test_two_non_overlapping_jam_windows_yield_two_events(
    stub_env: None, tmp_path: Path, make_image: Callable[..., str]
) -> None:
    frames = _frames(tmp_path, [0.0, 25.0, 31.0, 56.0], real=True, make_image=make_image)
    early = _vehicle_grid(13, "f00000") + _vehicle_grid(13, "f00001")  # IDs from tracker frame 1
    # Different boxes (x offset) so the tracker spawns NEW tracks at t=31/56 instead of
    # re-matching the still-buffered early ones; 2-frame confirmation IDs them at t=56.
    late = _vehicle_grid(13, "f00002", x_offset=400.0) + _vehicle_grid(
        13, "f00003", x_offset=400.0
    )
    out = track_flag(_state(frames, early + late))
    events = [e for e in _events(out) if e.kind == "TRAFFIC_JAM"]
    assert len(events) == 2
    first, second = events
    assert (first.frame_id, first.t_seconds) == ("f00001", 25.0)  # window [0, 30]
    assert first.evidence == {"unique_vehicles": 13, "window_s": 30.0}
    assert (second.frame_id, second.t_seconds) == ("f00003", 56.0)  # window [56, 86]
    assert second.evidence == {"unique_vehicles": 13, "window_s": 30.0}
    assert first.event_id.endswith("-1") and second.event_id.endswith("-2")
    assert first.snapshot_path != "" and second.snapshot_path != ""
    assert _summary(out)["unique_vehicles"] == 26


def test_event_id_round_trip_parse_with_hyphenated_run_id(
    stub_env: None, tmp_path: Path
) -> None:
    run_id = "2026-09-17-shift1"
    frames = _frames(tmp_path, [0.0, STEP], real=False)
    box = [100.0, 100.0, 140.0, 140.0]
    out = track_flag(
        _state(frames, [_pothole("f00000", box), _pothole("f00001", box)], run_id=run_id)
    )
    ev = _events(out)[0]
    marker = f"-{ev.kind}-"
    parsed_run = ev.event_id.split(marker, 1)[0]  # store.py _derive_run_id semantics
    assert parsed_run == run_id
    tail = ev.event_id[len(run_id) + len(marker):]
    frame_id, ordinal = tail.rsplit("-", 1)
    assert (frame_id, ordinal) == ("f00000", "1")
    assert ev.event_id == f"{run_id}-{ev.kind}-{frame_id}-{ordinal}"


def test_empty_detections_yield_empty_everything(stub_env: None, tmp_path: Path) -> None:
    frames = _frames(tmp_path, [0.0, STEP], real=False)
    out = track_flag(_state(frames, []))
    assert out == {"events": [], "track_summary": {"unique_vehicles": 0, "per_class": {}}}


def test_empty_frames_short_circuit_even_with_detections(stub_env: None) -> None:
    out = track_flag(_state([], [_pothole("f00000", [0.0, 0.0, 10.0, 10.0])]))
    assert out == {"events": [], "track_summary": {"unique_vehicles": 0, "per_class": {}}}


def test_input_state_is_not_mutated(stub_env: None, tmp_path: Path) -> None:
    frames = _frames(tmp_path, [0.0], real=False)
    dets = [
        _d("f00000", "car", [10.0, 10.0, 70.0, 50.0]),
        _pothole("f00000", [100.0, 100.0, 140.0, 140.0]),
    ]
    state = _state(frames, dets)
    snapshot = state.model_copy(deep=True)
    track_flag(state)
    assert state == snapshot  # every field, nested models included, untouched
    assert all(d.track_id is None for d in state.detections)
    assert state.events == [] and state.track_summary == {}


# --- pure rule functions, unit-tested directly (no state, no files, no cv2) ---


def test_pothole_rule_direct_parallel_chains() -> None:
    box_a = (10.0, 10.0, 50.0, 50.0)
    box_b = (200.0, 200.0, 240.0, 240.0)
    dets = [
        _d("f00000", "pothole", list(box_a), source="pothole"),
        _d("f00000", "pothole", list(box_b), source="pothole"),
        _d("f00001", "pothole", list(box_a), source="pothole"),
        _d("f00001", "pothole", list(box_b), source="pothole"),
    ]
    cands = pothole_rule(
        dets, ["f00000", "f00001"], iou_threshold=0.4, min_consecutive=2
    )
    assert [(c.first_frame_id, c.bbox, c.consecutive_count) for c in cands] == [
        ("f00000", box_a, 2),
        ("f00000", box_b, 2),
    ]


def test_pothole_rule_iou_below_threshold_breaks_chain() -> None:
    x = [0.0, 0.0, 100.0, 100.0]
    y = [50.0, 0.0, 150.0, 100.0]  # IoU(x, y) = 5000/15000 = 1/3 < 0.4
    dets = [
        _d("f00000", "pothole", x, source="pothole"),
        _d("f00001", "pothole", y, source="pothole"),
    ]
    assert pothole_rule(
        dets, ["f00000", "f00001"], iou_threshold=0.4, min_consecutive=2
    ) == []  # two separate 1-frame sightings -> no streak


def test_pothole_rule_iou_above_threshold_chains_to_first_box() -> None:
    x = [0.0, 0.0, 100.0, 100.0]
    z = [30.0, 0.0, 130.0, 100.0]  # IoU(x, z) = 7000/13000 ~ 0.538 >= 0.4
    dets = [
        _d("f00000", "pothole", x, source="pothole"),
        _d("f00001", "pothole", z, source="pothole"),
    ]
    cands = pothole_rule(
        dets, ["f00000", "f00001"], iou_threshold=0.4, min_consecutive=2
    )
    assert len(cands) == 1
    assert cands[0].first_frame_id == "f00000"
    assert cands[0].bbox == (0.0, 0.0, 100.0, 100.0)  # streak FIRST box
    assert cands[0].consecutive_count == 2


def test_pothole_rule_iou_exactly_threshold_chains() -> None:
    # IoU(x, y) = 2000/5000 = 0.4 EXACTLY — the contract's >= is inclusive, so the
    # chain forms (a flip to strict > must fail this test loudly).
    x = [0.0, 0.0, 100.0, 50.0]  # area 5000
    y = [0.0, 30.0, 100.0, 50.0]  # area 2000; inter 2000; union 5000
    dets = [
        _d("f00000", "pothole", x, source="pothole"),
        _d("f00001", "pothole", y, source="pothole"),
    ]
    cands = pothole_rule(
        dets, ["f00000", "f00001"], iou_threshold=0.4, min_consecutive=2
    )
    assert len(cands) == 1
    assert cands[0].consecutive_count == 2


def test_pothole_rule_gap_then_reappearing_overlap_deduped() -> None:
    first = [100.0, 100.0, 140.0, 140.0]
    reappearing = [110.0, 100.0, 150.0, 140.0]
    dets = [
        _d("f00000", "pothole", first, source="pothole"),
        _d("f00001", "pothole", first, source="pothole"),
        _d("f00003", "pothole", reappearing, source="pothole"),
        _d("f00004", "pothole", reappearing, source="pothole"),
    ]
    cands = pothole_rule(
        dets,
        ["f00000", "f00001", "f00002", "f00003", "f00004"],
        iou_threshold=0.4,
        min_consecutive=2,
    )
    assert len(cands) == 1  # the re-appearing streak overlaps the flagged one -> suppressed
    assert cands[0].first_frame_id == "f00000"


def test_kids_rule_band_edge_is_inclusive() -> None:
    times, heights = _kids_inputs()
    box = [100.0, 107.0, 130.0, 157.0]  # center_y 132 -> 132/240 == 0.55 exactly; height 50
    dets = [_person(fid, box, track_id=7) for fid in times]
    cands = kids_crossing_rule(
        list(dets), times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert len(cands) == 1
    assert cands[0].track_id == 7
    assert cands[0].first_frame_id == "f00000"
    assert cands[0].dwell_s == 2.8


def test_kids_rule_above_band_is_silent() -> None:
    times, heights = _kids_inputs()
    box = [100.0, 106.0, 130.0, 156.0]  # center_y 131 -> 131/240 = 0.5458 < 0.55: out of band
    dets = [_person(fid, box, track_id=7) for fid in times]
    cands = kids_crossing_rule(
        list(dets), times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert cands == []


def test_kids_rule_tall_person_is_not_small() -> None:
    times, heights = _kids_inputs()
    box = [100.0, 130.0, 130.0, 191.0]  # in band (center 0.669) but height 61 -> 0.254 >= 0.25
    dets = [_person(fid, box, track_id=7) for fid in times]
    cands = kids_crossing_rule(
        list(dets), times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert cands == []


def test_kids_rule_dwell_exactly_threshold_is_silent() -> None:
    frame_ids = ["f00000", "f00001", "f00002"]
    times = {"f00000": 0.0, "f00001": 1.0, "f00002": 2.0}
    heights = {fid: 240 for fid in frame_ids}
    dets = [_person(fid, list(PERSON_BOX), track_id=7) for fid in frame_ids]
    cands = kids_crossing_rule(
        dets, times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert cands == []  # dwell 2.0 is NOT > 2.0 (strict)


def test_kids_rule_smallness_exactly_threshold_is_silent() -> None:
    # Height ratio EXACTLY 0.25 (60/240): strict < means NOT small -> silent even with
    # dwell and band satisfied (a flip to <= must fail this test loudly).
    frame_ids = ["f00000", "f00001", "f00002"]
    times = {"f00000": 0.0, "f00001": 1.0, "f00002": 2.4}
    heights = {fid: 240 for fid in frame_ids}
    box = [100.0, 150.0, 130.0, 210.0]  # height 60 -> 0.25; center_y 180 -> 0.75 in band
    dets = [_person(fid, list(box), track_id=7) for fid in frame_ids]
    cands = kids_crossing_rule(
        dets, times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert cands == []


def test_kids_rule_median_box_and_one_event_per_track() -> None:
    frame_ids = ["f00000", "f00001", "f00002"]
    times = {"f00000": 0.0, "f00001": 1.0, "f00002": 2.4}
    heights = {fid: 240 for fid in frame_ids}
    moving = [[100.0, 150.0, 130.0, 200.0], [110.0, 150.0, 140.0, 200.0],
              [120.0, 150.0, 150.0, 200.0]]
    dets = [_person(fid, box, track_id=7) for fid, box in zip(frame_ids, moving, strict=True)]
    dets += [_person(fid, [300.0, 150.0, 330.0, 200.0], track_id=9) for fid in frame_ids]
    cands = kids_crossing_rule(
        dets, times, heights, road_band=(0.55, 1.0), dwell_s=2.0, small_fraction=0.25
    )
    assert [c.track_id for c in cands] == [7, 9]  # one event per track_id, sorted
    assert cands[0].bbox == (110.0, 150.0, 140.0, 200.0)  # element-wise median box
    assert cands[1].bbox == (300.0, 150.0, 330.0, 200.0)
    assert cands[0].dwell_s == 2.4 and cands[1].dwell_s == 2.4


def test_jam_rule_overlapping_window_is_suppressed() -> None:
    times = {"f00000": 0.0, "f00001": 25.0, "f00002": 29.0}
    dets = (
        _vehicles("f00000", range(1, 14))
        + _vehicles("f00001", range(1, 14))
        + _vehicles("f00002", range(14, 27))
    )
    cands = traffic_jam_rule(dets, times, window_s=30.0, min_vehicles=12)
    assert len(cands) == 1  # greedy: the 29.0 start lies inside window 1 -> suppressed
    assert cands[0].window_start_s == 0.0
    assert cands[0].anchor_frame_id == "f00002"  # last timeline frame inside [0, 30]
    assert cands[0].unique_vehicles == 26


def test_jam_rule_qualifying_window_far_from_first_appearances() -> None:
    """Regression (review MAJOR-1): a qualifying window whose start coincides with NO
    track's first appearance must still fire — candidate starts are ALL appearance
    times, not just each track's first (the old first-appearance scan returned [])."""
    times = {"f00000": 60.0, "f00001": 95.0, "f00002": 128.0, "f00003": 129.0, "f00004": 130.0}
    dets = (
        [_vehicles("f00000", [1]), _vehicles("f00002", [1])]  # A: 60, 128
        + [_vehicles("f00001", [2]), _vehicles("f00003", [2])]  # B: 95, 129
        + [_vehicles("f00004", [3])]  # C: 130
    )
    flat = [d for group in dets for d in group]
    cands = traffic_jam_rule(flat, times, window_s=30.0, min_vehicles=2)
    # Window [128, 158] sees A(128), B(129), C(130) -> 3 > 2; no earlier window
    # ([60,90] -> 1, [95,125] -> 1) qualifies, and 128/129/130 are nobody's FIRST
    # appearance except C's — only the all-times scan finds this window.
    assert len(cands) == 1
    assert cands[0].window_start_s == 128.0
    assert cands[0].anchor_frame_id == "f00004"
    assert cands[0].unique_vehicles == 3


def test_jam_rule_non_overlapping_windows_fire_separately() -> None:
    times = {"f00000": 0.0, "f00001": 40.0}
    dets = (
        _vehicles("f00000", range(1, 14))
        + _vehicles("f00001", range(1, 14))
        + _vehicles("f00001", range(14, 27))
    )
    cands = traffic_jam_rule(dets, times, window_s=30.0, min_vehicles=12)
    assert [(c.window_start_s, c.anchor_frame_id, c.unique_vehicles) for c in cands] == [
        (0.0, "f00000", 13),
        (40.0, "f00001", 26),
    ]


def test_jam_rule_ignores_persons() -> None:
    times = {"f00000": 0.0}
    dets = _vehicles("f00000", range(1, 13)) + [
        _person("f00000", [200.0, 150.0, 230.0, 200.0], track_id=t) for t in range(100, 105)
    ]
    cands = traffic_jam_rule(dets, times, window_s=30.0, min_vehicles=12)
    assert cands == []  # 12 vehicles is not > 12; persons never count toward jams
