"""Layer-2 node tests for detect (state-in -> state-update-out, per code-standards.md).

Stub mode (ROADFIX_STUB_MODELS=1) is deterministic per frame FILENAME (worklog 2-b),
so the composition is asserted exactly. Degradation uses sys.modules["ultralytics"]=None
-> importlib ImportError path -> street eye degrades; pothole eye degrades via unset
POTHOLE_MODEL_PATH. No langgraph — the node function is called directly.
"""

import logging
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from roadfix.config import FRAMES_PER_SECOND, POTHOLE_CONF, STREET_CONF
from roadfix.nodes.detect import detect
from roadfix.state import Detection, FrameRef, RoadfixState
from roadfix.tools.detectors import DetectArgs, yolo_pothole, yolo_street

_STREET_CLASSES = {"car", "person", "bus", "bicycle", "motorcycle", "truck"}


def _state_with_frames(paths: list[str]) -> tuple[RoadfixState, list[FrameRef]]:
    """Real jpgs from make_image -> FrameRef list (frame_id = basename, the join key)."""
    frames = [
        FrameRef(frame_id=Path(p).name, path=p, t_seconds=i / FRAMES_PER_SECOND)
        for i, p in enumerate(paths)
    ]
    state = RoadfixState(video_path="unused.mp4", run_id="detecttest", frames=frames)
    return state, frames


def test_detect_stub_happy_path(stub_env: None, make_image: Callable[..., str]) -> None:
    """Stub frames -> non-empty detections, both eyes present, joins + conf floors hold."""
    paths = [make_image(name="f00000.jpg"), make_image(name="f00001.jpg")]
    state, frames = _state_with_frames(paths)
    result = detect(state)

    detections = result["detections"]
    assert isinstance(detections, list) and detections
    sources = {d.source for d in detections}
    assert sources == {"street", "pothole"}  # both eyes ran and contributed
    frame_ids = {f.frame_id for f in frames}
    for d in detections:
        assert d.source in {"street", "pothole"}
        assert d.frame_id in frame_ids  # Detection.frame_id == FrameRef.frame_id join
        if d.source == "street":
            assert d.conf >= STREET_CONF  # conf floor honored through the node
            assert d.class_name in _STREET_CLASSES  # stub-planted "cat" filtered out
        else:
            assert d.conf >= POTHOLE_CONF
            assert d.class_name in {"pothole", "crack"}


def test_detect_empty_frames(stub_env: None) -> None:
    """Empty frames input -> detections=[] immediately."""
    state = RoadfixState(video_path="unused.mp4", run_id="detectempty")
    result = detect(state)

    assert result == {"detections": []}


def test_detect_degraded_eyes_no_raise(
    monkeypatch: pytest.MonkeyPatch,
    make_image: Callable[..., str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stub OFF + ultralytics poisoned + no pothole weights -> both eyes empty, no raise."""
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)
    monkeypatch.delenv("POTHOLE_MODEL_PATH", raising=False)
    monkeypatch.setitem(sys.modules, "ultralytics", None)  # importlib loader -> ImportError
    state, _frames = _state_with_frames([make_image(name="f00000.jpg")])
    with caplog.at_level(logging.WARNING, logger="roadfix.nodes.detect"):
        result = detect(state)  # must not raise

    assert set(result) == {"detections"}
    assert result["detections"] == []
    assert "[detect]" in caplog.text  # degraded eyes logged, run continues


def test_detect_state_out_shape_and_verbatim(
    stub_env: None, make_image: Callable[..., str]
) -> None:
    """Returns exactly {"detections": ...}; content equals the union of both eyes verbatim."""
    paths = [make_image(name="f00000.jpg"), make_image(name="f00001.jpg")]
    state, frames = _state_with_frames(paths)
    result = detect(state)

    assert set(result) == {"detections"}
    detections = result["detections"]
    assert isinstance(detections, list)
    expected: list[Detection] = []
    for frame in frames:
        expected.extend(yolo_street(DetectArgs(frame_path=frame.path, conf=STREET_CONF)).detections)
        expected.extend(
            yolo_pothole(DetectArgs(frame_path=frame.path, conf=POTHOLE_CONF)).detections
        )
    # Node = pure composition of the two eyes, in frame order, nothing rewritten.
    assert [d.model_dump() for d in detections] == [d.model_dump() for d in expected]


def test_detect_does_not_mutate_state(stub_env: None, make_image: Callable[..., str]) -> None:
    """Input state untouched — detections key absent initially and still absent after the call."""
    state, _frames = _state_with_frames([make_image(name="f00000.jpg")])
    before = state.model_dump()
    result = detect(state)

    assert state.model_dump() == before
    assert state.detections == []
    detections = result["detections"]
    assert isinstance(detections, list) and detections  # output went through the return
