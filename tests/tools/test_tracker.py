"""Layer-1 tests for bytetrack_tracks (tool-registry.md) — synthetic sequences, no video files.

supervision 0.30.3 semantics, verified against the installed source
(supervision/tracker/byte_tracker/core.py + single_object_track.py) and empirically:
- update_with_detections returns ONLY matched boxes — unmatched input rows are dropped
  (never returned with -1); output preserves fed order and boxes verbatim.
- Objects present in tracker frame 1 get an external ID immediately; objects first seen
  at tracker frame >= 2 need 2 consecutive appearances before an ID
  (minimum_consecutive_frames=1 activation quirk in STrack.activate/update).
Hence in the eval sequence: car A (frames 1-3) keeps one stable ID; car B (first seen
frame 2) is unmatched on frame 2 and gets a DIFFERENT ID on frame 3; person C seen only
on frame 3 stays unmatched (contract: unmatched → track_id None). A variant sequence
with person C present on frames 2-3 shows C tracked — the eval-plan "person tracked"
intent (a 1-frame-only object cannot be IDed by this library version).
"""

import pytest

from roadfix.state import Detection
from roadfix.tools.tracker import TrackArgs, bytetrack_tracks

CAR_A = ([10.0, 100.0, 60.0, 150.0], [40.0, 100.0, 90.0, 150.0], [70.0, 100.0, 120.0, 150.0])
CAR_B = [200.0, 100.0, 250.0, 150.0]
PERSON_C = [110.0, 180.0, 130.0, 220.0]


def _det(frame_id: str, class_name: str, bbox: list[float], conf: float = 0.9) -> Detection:
    return Detection(
        frame_id=frame_id, source="street", class_name=class_name, conf=conf, bbox=bbox
    )


def _sequence() -> list[Detection]:
    """3 frames @2.5fps: A slides right (f1-f3); B appears f2; C appears f3. Mixed order."""
    return [
        _det("f00000", "car", list(CAR_A[0])),
        _det("f00001", "car", CAR_B),
        _det("f00001", "car", list(CAR_A[1])),
        _det("f00002", "car", CAR_B),
        _det("f00002", "person", PERSON_C),
        _det("f00002", "car", list(CAR_A[2])),
    ]


def _ids(detections: list[Detection]) -> dict[tuple[str, tuple[float, ...]], int | None]:
    return {(d.frame_id, tuple(d.bbox)): d.track_id for d in detections}


def test_car_a_keeps_one_stable_track_id() -> None:
    result = bytetrack_tracks(TrackArgs(detections=_sequence()))
    assert result.tracked_ok is True
    assert result.error is None
    ids = _ids(result.detections)
    a_ids = {
        ids[("f00000", tuple(CAR_A[0]))],
        ids[("f00001", tuple(CAR_A[1]))],
        ids[("f00002", tuple(CAR_A[2]))],
    }
    assert all(i is not None for i in a_ids)
    assert len(a_ids) == 1  # same ID on every frame


def test_car_b_gets_id_different_from_car_a() -> None:
    result = bytetrack_tracks(TrackArgs(detections=_sequence()))
    ids = _ids(result.detections)
    # B is first seen on frame 2 — this library gives it an ID only on its 2nd
    # consecutive appearance; frame-2 B comes back unmatched (contract: None).
    assert ids[("f00001", tuple(CAR_B))] is None
    b_id = ids[("f00002", tuple(CAR_B))]
    a_id = ids[("f00000", tuple(CAR_A[0]))]
    assert b_id is not None
    assert a_id is not None
    assert b_id != a_id


def test_person_c_one_frame_unmatched_but_tracked_when_seen_two_frames() -> None:
    # Main sequence: C appears only on frame 3 → never confirmed → None (contract).
    ids = _ids(bytetrack_tracks(TrackArgs(detections=_sequence())).detections)
    assert ids[("f00002", tuple(PERSON_C))] is None

    # Variant (eval-plan "person tracked"): C present frames 2-3 → IDed on frame 3.
    variant = [
        _det("f00000", "car", list(CAR_A[0])),
        _det("f00001", "car", list(CAR_A[1])),
        _det("f00001", "person", PERSON_C),
        _det("f00002", "car", list(CAR_A[2])),
        _det("f00002", "person", PERSON_C),
    ]
    vres = bytetrack_tracks(TrackArgs(detections=variant))
    assert vres.tracked_ok is True
    vids = _ids(vres.detections)
    assert vids[("f00001", tuple(PERSON_C))] is None
    person_id = vids[("f00002", tuple(PERSON_C))]
    assert person_id is not None
    car_ids = {vids[("f00000", tuple(CAR_A[0]))], vids[("f00002", tuple(CAR_A[2]))]}
    assert person_id not in car_ids  # person never shares a vehicle's ID


def test_tracker_failure_returns_untracked_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import supervision as sv

    def _boom(self: sv.ByteTrack, detections: sv.Detections) -> sv.Detections:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sv.ByteTrack, "update_with_detections", _boom)
    seq = _sequence()
    result = bytetrack_tracks(TrackArgs(detections=seq))
    assert result.tracked_ok is False
    assert result.error is not None and "RuntimeError" in result.error
    assert len(result.detections) == len(seq)
    assert all(d.track_id is None for d in result.detections)
    assert [tuple(d.bbox) for d in result.detections] == [tuple(d.bbox) for d in seq]


def test_inputs_not_mutated_outputs_are_copies_in_input_order() -> None:
    seq = _sequence()
    result = bytetrack_tracks(TrackArgs(detections=seq))
    assert all(d.track_id is None for d in seq)  # inputs untouched
    assert all(a is not b for a, b in zip(seq, result.detections, strict=True))  # fresh objects
    assert [(d.frame_id, tuple(d.bbox)) for d in result.detections] == [
        (d.frame_id, tuple(d.bbox)) for d in seq
    ]  # input order preserved, same boxes


def test_empty_input_is_ok_and_empty() -> None:
    result = bytetrack_tracks(TrackArgs(detections=[]))
    assert result.tracked_ok is True
    assert result.error is None
    assert result.detections == []
