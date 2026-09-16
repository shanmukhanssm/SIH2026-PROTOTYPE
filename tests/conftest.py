"""Shared pytest fixtures for RoadFix tool tests.

External services and models are stubbed via `ROADFIX_STUB_MODELS=1`
(library-docs.md pytest section); clip/image fixtures write real tiny files
via cv2 so extraction tests run against genuine video bytes.
"""

from collections.abc import Callable

import pytest


@pytest.fixture()
def stub_env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Deterministic stub detectors/verdicts + isolated data dir."""
    monkeypatch.setenv("ROADFIX_STUB_MODELS", "1")
    monkeypatch.setenv("ROADFIX_DATA_DIR", str(tmp_path))


@pytest.fixture()
def make_clip(tmp_path) -> Callable[..., str]:
    """Write a real tiny mp4 (mp4v codec) and return its path."""

    def _make(
        name: str = "clip.mp4",
        seconds: float = 2.0,
        fps: int = 25,
        size: tuple[int, int] = (320, 240),
    ) -> str:
        import cv2
        import numpy as np

        path = str(tmp_path / name)
        w, h = size
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        assert writer.isOpened(), "VideoWriter failed to open (codec/ffmpeg missing?)"
        n_frames = max(1, int(round(seconds * fps)))
        for i in range(n_frames):
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            frame[:, :, i % 3] = 200  # varying content so frames are not identical
            writer.write(frame)
        writer.release()
        return path

    return _make


@pytest.fixture()
def make_image(tmp_path) -> Callable[..., str]:
    """Write a real tiny jpg and return its path."""

    def _make(name: str = "frame.jpg", size: tuple[int, int] = (320, 240)) -> str:
        import cv2
        import numpy as np

        path = str(tmp_path / name)
        w, h = size
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[..., 0] = 128
        assert cv2.imwrite(path, img), "cv2.imwrite returned False"
        return path

    return _make
