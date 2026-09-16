"""Layer-1 tool evals for yolo_street / yolo_pothole (eval-plan.md): stub determinism,
shared-filter behavior (class filter + conf threshold), degraded-eye contracts — never
real inference."""

import sys
from pathlib import Path

from roadfix.tools.detectors import DetectArgs, yolo_pothole, yolo_street

_STREET_CLASSES = {"car", "bus", "truck", "motorcycle", "bicycle", "person"}
_NON_STREET_CLASSES = {"cat", "potted_plant"}
_POTHOLE_CLASSES = {"pothole", "crack"}

# --- yolo_street ---------------------------------------------------------------


def test_street_stub_same_filename_is_deterministic(stub_env, make_image) -> None:
    """Stub emits identical detections across calls; the hash input is the FILE name."""
    path = make_image("frame_00042.jpg")
    first = yolo_street(DetectArgs(frame_path=path))
    second = yolo_street(DetectArgs(frame_path=path))

    assert first.model_loaded is True
    assert first.error is None
    assert first.detections, "stub street eye must emit a candidate mix"
    assert all(d.frame_id == "frame_00042.jpg" for d in first.detections)  # basename join
    assert first.detections == second.detections
    # Same name in another dir -> same boxes (stub never touches the filesystem).
    twin = str(Path(path).parent / "twin" / Path(path).name)
    assert yolo_street(DetectArgs(frame_path=twin)).detections == first.detections


def test_street_stub_class_filter(stub_env, make_image) -> None:
    """Non-street candidates never survive; every detection has source='street'."""
    path = make_image("frame_00007.jpg")
    result = yolo_street(DetectArgs(frame_path=path))
    dets = result.detections

    assert result.model_loaded is True
    assert dets, "stub mix must be non-empty for the filter to be exercised"
    assert all(d.source == "street" for d in dets)
    names = {d.class_name for d in dets}
    assert names & _NON_STREET_CLASSES == set()  # filter applied AFTER (stub) predict
    assert names <= _STREET_CLASSES


def test_street_stub_conf_threshold(stub_env, make_image) -> None:
    """conf=0.9 keeps fewer-or-equal boxes than conf=0.1; kept confs clear the bar."""
    path = make_image("frame_00011.jpg")
    low = yolo_street(DetectArgs(frame_path=path, conf=0.1))
    high = yolo_street(DetectArgs(frame_path=path, conf=0.9))

    assert low.model_loaded and high.model_loaded
    assert low.detections, "conf=0.1 must keep the stub mix non-vacuous"
    assert len(high.detections) <= len(low.detections)
    assert high.detections, "stub confs straddle 0.9 — high tier must be non-empty"
    assert all(d.conf >= 0.9 for d in high.detections)


# --- degraded eyes ---------------------------------------------------------------


def test_street_degraded_without_ultralytics(stub_env, make_image, monkeypatch) -> None:
    """Real mode + ultralytics missing -> model_loaded=False, empty, zero raises."""
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)  # leave stub mode
    monkeypatch.setitem(sys.modules, "ultralytics", None)  # lazy import must fail
    path = make_image("frame_00003.jpg")  # degraded path never reads it

    result = yolo_street(DetectArgs(frame_path=path))  # must not raise

    assert result.model_loaded is False
    assert result.detections == []
    assert result.error


def test_pothole_degraded_without_weights(stub_env, make_image, monkeypatch) -> None:
    """Real mode + POTHOLE_MODEL_PATH unset -> eye disabled, empty, zero raises."""
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)
    monkeypatch.delenv("POTHOLE_MODEL_PATH", raising=False)

    result = yolo_pothole(DetectArgs(frame_path=make_image("frame_00005.jpg")))  # no raise

    assert result.model_loaded is False
    assert result.detections == []
    assert result.error


# --- yolo_pothole ----------------------------------------------------------------


def test_pothole_stub_deterministic(stub_env, make_image) -> None:
    """Stub pothole eye emits deterministic pothole/crack boxes, source='pothole'."""
    path = make_image("frame_00013.jpg")
    first = yolo_pothole(DetectArgs(frame_path=path))
    second = yolo_pothole(DetectArgs(frame_path=path))

    assert first.model_loaded is True
    assert first.error is None
    assert first.detections, "stub pothole eye must emit boxes"
    assert all(d.source == "pothole" for d in first.detections)
    assert {d.class_name for d in first.detections} <= _POTHOLE_CLASSES  # kept verbatim
    assert first.detections == second.detections
    twin = str(Path(path).parent / "twin" / Path(path).name)
    assert yolo_pothole(DetectArgs(frame_path=twin)).detections == first.detections
