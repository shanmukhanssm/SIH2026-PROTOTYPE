"""Layer-1 tool evals for extract_frames + gps_sync (eval-plan.md, tool-registry.md).

extract_frames: 6 fixtures (1s/2s/5s/10s real mp4s via make_clip, corrupt file,
missing file). gps_sync: 5 registry cases + missing-file + copy-semantics.
"""

import os
from pathlib import Path

import pytest

from roadfix.state import FrameRef
from roadfix.tools.frames import ExtractFramesArgs, GpsSyncArgs, extract_frames, gps_sync

# --- extract_frames -----------------------------------------------------------


@pytest.mark.parametrize(("seconds", "expected"), [(1.0, 3), (2.0, 5), (5.0, 13), (10.0, 25)])
def test_extract_frames_stride_counts(make_clip, tmp_path, seconds, expected) -> None:
    """N-second clip at fps=2.5 yields round(N*2.5) frames +/- 1, monotonic, on disk."""
    out_dir = str(tmp_path / "frames")
    res = extract_frames(ExtractFramesArgs(video_path=make_clip(seconds=seconds), out_dir=out_dir))

    assert res.ok, res.error
    assert res.error is None
    assert abs(len(res.frames) - expected) <= 1  # round(N * 2.5) +/- 1
    ts = [f.t_seconds for f in res.frames]
    assert all(b > a for a, b in zip(ts, ts[1:], strict=False))  # strictly increasing
    assert all(os.path.isfile(f.path) for f in res.frames)
    assert all(f.frame_id == f"f{i:05d}.jpg" for i, f in enumerate(res.frames))
    assert all(f.frame_id == os.path.basename(f.path) for f in res.frames)
    assert all(f.lat is None and f.lon is None for f in res.frames)  # lat/lon unset
    assert res.duration_s == pytest.approx(seconds, abs=0.2)


def test_extract_frames_corrupt_file(tmp_path) -> None:
    """Corrupt bytes in a .mp4 path -> ok=False, frames=[], zero raises."""
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"not a video")
    res = extract_frames(
        ExtractFramesArgs(video_path=str(bad), out_dir=str(tmp_path / "frames"))
    )

    assert res.ok is False
    assert res.frames == []
    assert res.duration_s == 0.0
    assert res.error


def test_extract_frames_missing_file(tmp_path) -> None:
    """Missing video path -> ok=False, frames=[], zero raises."""
    res = extract_frames(
        ExtractFramesArgs(
            video_path=str(tmp_path / "missing.mp4"), out_dir=str(tmp_path / "frames")
        )
    )

    assert res.ok is False
    assert res.frames == []
    assert res.duration_s == 0.0
    assert res.error


# --- gps_sync -----------------------------------------------------------------


def _mk_frames(*t_seconds: float) -> list[FrameRef]:
    return [
        FrameRef(frame_id=f"f{i:05d}.jpg", path=f"/data/f{i:05d}.jpg", t_seconds=t)
        for i, t in enumerate(t_seconds)
    ]


def _write_gps_csv(path: Path, rows: list[tuple[float, float, float]]) -> str:
    lines = ["t_seconds,lat,lon", *(f"{t},{lat},{lon}" for t, lat, lon in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_gps_sync_no_csv() -> None:
    """gps_csv_path=None -> all lat/lon None, matched=0, honest reason."""
    frames = _mk_frames(0.0, 0.4)
    res = gps_sync(GpsSyncArgs(frames=frames))

    assert res.matched == 0
    assert res.reason_unmatched == "no csv provided"
    assert len(res.frames) == 2
    assert all(f.lat is None and f.lon is None for f in res.frames)


def test_gps_sync_clean_csv(tmp_path) -> None:
    """Clean csv -> nearest-timestamp values within tolerance, reason None."""
    csv_path = _write_gps_csv(
        tmp_path / "gps.csv",
        [(0.0, 17.1, 78.1), (0.4, 17.2, 78.2), (0.8, 17.3, 78.3)],
    )
    res = gps_sync(GpsSyncArgs(frames=_mk_frames(0.0, 0.7), gps_csv_path=csv_path))

    assert res.matched == 2
    assert res.reason_unmatched is None
    assert res.frames[0].lat == pytest.approx(17.1)
    assert res.frames[0].lon == pytest.approx(78.1)
    assert res.frames[1].lat == pytest.approx(17.3)  # nearest to 0.7 is row 0.8
    assert res.frames[1].lon == pytest.approx(78.3)


def test_gps_sync_malformed_rows_skipped(tmp_path) -> None:
    """Garbage rows mixed with good rows -> good rows still match, garbage skipped."""
    bad = tmp_path / "gps_mixed.csv"
    bad.write_text(
        "t_seconds,lat,lon\n"  # header (skipped)
        "garbage,not_a_float\n"  # wrong arity -> skipped
        "abc,1.0,2.0\n"  # non-float t -> skipped
        "0.0,17.1,78.1\n"
        "0.4,17.2,78.2\n"
        "x,y,z\n"  # non-float -> skipped
        "0.8\n",  # too few fields -> skipped
        encoding="utf-8",
    )
    res = gps_sync(GpsSyncArgs(frames=_mk_frames(0.4), gps_csv_path=str(bad)))

    assert res.matched == 1
    assert res.frames[0].lat == pytest.approx(17.2)
    assert res.frames[0].lon == pytest.approx(78.2)
    assert res.reason_unmatched is None


def test_gps_sync_gap_beyond_tolerance(tmp_path) -> None:
    """Frame far from any gps point stays None; mixed case -> 'no fix within tolerance'."""
    csv_path = _write_gps_csv(tmp_path / "gps.csv", [(0.0, 17.1, 78.1), (10.0, 17.9, 78.9)])
    res = gps_sync(GpsSyncArgs(frames=_mk_frames(0.0, 10.0, 20.0), gps_csv_path=csv_path))

    assert res.matched == 2
    assert res.frames[0].lat == pytest.approx(17.1)
    assert res.frames[1].lon == pytest.approx(78.9)
    assert res.frames[2].lat is None and res.frames[2].lon is None
    assert res.reason_unmatched == "no fix within tolerance"


def test_gps_sync_exact_match(tmp_path) -> None:
    """Exact timestamp match returns that row's lat/lon."""
    csv_path = _write_gps_csv(tmp_path / "gps.csv", [(0.0, 17.0, 78.0), (2.0, 17.5, 78.5)])
    res = gps_sync(GpsSyncArgs(frames=_mk_frames(2.0), gps_csv_path=csv_path))

    assert res.matched == 1
    assert res.frames[0].lat == pytest.approx(17.5)
    assert res.frames[0].lon == pytest.approx(78.5)
    assert res.frames[0].frame_id == "f00000.jpg"  # copy keeps identity fields


def test_gps_sync_missing_csv_file_is_malformed(tmp_path) -> None:
    """Path given but file absent -> 'csv malformed', never raises."""
    res = gps_sync(
        GpsSyncArgs(frames=_mk_frames(0.0), gps_csv_path=str(tmp_path / "absent.csv"))
    )

    assert res.matched == 0
    assert res.reason_unmatched == "csv malformed"
    assert all(f.lat is None and f.lon is None for f in res.frames)


def test_gps_sync_unparseable_csv_is_malformed(tmp_path) -> None:
    """CSV exists but zero rows parse -> 'csv malformed'."""
    bad = tmp_path / "gps_bad.csv"
    bad.write_text("t_seconds,lat,lon\nx,y,z\n1,2\n", encoding="utf-8")
    res = gps_sync(GpsSyncArgs(frames=_mk_frames(0.0), gps_csv_path=str(bad)))

    assert res.matched == 0
    assert res.reason_unmatched == "csv malformed"


def test_gps_sync_does_not_mutate_input(tmp_path) -> None:
    """Copy semantics: input FrameRef objects untouched, results are fresh objects."""
    csv_path = _write_gps_csv(tmp_path / "gps.csv", [(0.0, 17.1, 78.1)])
    frames = _mk_frames(0.0, 99.0)  # 99.0 is beyond tolerance -> unmatched
    res = gps_sync(GpsSyncArgs(frames=frames, gps_csv_path=csv_path))

    assert res.matched == 1
    assert frames[0].lat is None and frames[0].lon is None
    assert frames[1].lat is None and frames[1].lon is None
    assert res.frames is not frames
    assert res.frames[0] is not frames[0]
    assert res.frames[1] is not frames[1]
    assert res.frames[0].lat == pytest.approx(17.1)
