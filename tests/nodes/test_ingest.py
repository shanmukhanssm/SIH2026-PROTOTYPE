"""Layer-2 node tests for ingest (state-in -> state-update-out, per code-standards.md).

Real tools against a real tiny mp4 (make_clip): happy path, missing/corrupt video
degradation, GPS sync integration (clean + malformed CSV), non-mutation of the
input state. No langgraph — the node function is called directly.
"""

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from roadfix.nodes.ingest import ingest
from roadfix.state import RoadfixState


def test_ingest_happy_path(
    stub_env: None, make_clip: Callable[..., str], tmp_path: Path
) -> None:
    """Real clip -> non-empty frames, valid fields, files on disk, monotonic t_seconds."""
    video = make_clip(seconds=2.0, fps=25)
    state = RoadfixState(video_path=video, run_id="ingesthappy")
    result = ingest(state)

    assert set(result) == {"frames"}  # writer audit: ingest writes exactly one key
    frames = result["frames"]
    assert isinstance(frames, list) and frames
    expected_dir = tmp_path / "runs" / "ingesthappy" / "frames"  # ROADFIX_DATA_DIR = tmp_path
    ts: list[float] = []
    for i, f in enumerate(frames):
        assert f.frame_id == f"f{i:05d}.jpg"  # extract_frames naming = basename(path)
        assert f.frame_id == Path(f.path).name
        assert Path(f.path).is_file()
        assert Path(f.path).parent == expected_dir
        assert f.lat is None and f.lon is None  # no gps_track_path in this state
        ts.append(f.t_seconds)
    assert all(b > a for a, b in zip(ts, ts[1:], strict=False))  # strictly increasing


def test_ingest_missing_video(stub_env: None, tmp_path: Path) -> None:
    """Missing video path -> frames=[], no raise (zero-frame degraded run)."""
    state = RoadfixState(video_path=str(tmp_path / "nope.mp4"), run_id="ingestmissing")
    result = ingest(state)  # must not raise

    assert result == {"frames": []}


def test_ingest_corrupt_video(stub_env: None, tmp_path: Path) -> None:
    """Text bytes in a .mp4 path -> frames=[], no raise."""
    bad = tmp_path / "corrupt.mp4"
    bad.write_text("this is definitely not a video", encoding="utf-8")
    state = RoadfixState(video_path=str(bad), run_id="ingestcorrupt")
    result = ingest(state)  # must not raise

    assert result == {"frames": []}


def test_ingest_gps_sync_fills_coordinates(
    stub_env: None, make_clip: Callable[..., str], tmp_path: Path
) -> None:
    """Real clip + clean CSV covering frame timestamps -> lat/lon filled per nearest row."""
    video = make_clip(seconds=2.0, fps=25)
    # Multiples of 0.5 are binary-exact: nearest-row lat/lon equality is deterministic.
    rows = [
        (0.0, 17.0, 78.0),
        (0.4, 17.5, 78.5),
        (0.8, 18.0, 79.0),
        (1.2, 18.5, 79.5),
        (1.6, 19.0, 80.0),
    ]
    csv_path = tmp_path / "gps.csv"
    csv_path.write_text(
        "t_seconds,lat,lon\n" + "".join(f"{t},{lat},{lon}\n" for t, lat, lon in rows),
        encoding="utf-8",
    )
    state = RoadfixState(video_path=video, gps_track_path=str(csv_path), run_id="ingestgps")
    result = ingest(state)

    frames = result["frames"]
    assert isinstance(frames, list) and frames
    for f in frames:
        nearest = min(rows, key=lambda r: abs(r[0] - f.t_seconds))
        assert abs(nearest[0] - f.t_seconds) <= 1.5  # gps_sync default tolerance
        assert f.lat == nearest[1]
        assert f.lon == nearest[2]


def test_ingest_malformed_gps_csv_degrades(
    stub_env: None,
    make_clip: Callable[..., str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed CSV is NOT an error: frames still extracted, lat/lon stay None, reason logged."""
    video = make_clip(seconds=2.0, fps=25)
    csv_path = tmp_path / "gps.csv"
    csv_path.write_text("not,a,csv,row\nx,y,z\n1\n", encoding="utf-8")  # zero parseable rows
    state = RoadfixState(video_path=video, gps_track_path=str(csv_path), run_id="ingestbadgps")
    with caplog.at_level(logging.WARNING, logger="roadfix.nodes.ingest"):
        result = ingest(state)  # must not raise

    frames = result["frames"]
    assert isinstance(frames, list) and frames  # video still extracted fine
    assert all(f.lat is None and f.lon is None for f in frames)
    assert "[ingest]" in caplog.text and "csv malformed" in caplog.text


def test_ingest_does_not_mutate_state(stub_env: None, make_clip: Callable[..., str]) -> None:
    """Input state untouched — frames key absent initially and still absent after the call."""
    state = RoadfixState(video_path=make_clip(seconds=1.0, fps=25), run_id="ingestnomut")
    before = state.model_dump()
    result = ingest(state)

    assert state.model_dump() == before
    assert state.frames == []
    frames = result["frames"]
    assert isinstance(frames, list) and frames  # output went through the return, not mutation
