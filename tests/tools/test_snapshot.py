"""Contract tests for save_event_snapshot (tool-registry.md: 3 cases + clamp)."""

import os

from roadfix.tools.snapshot import SnapshotArgs, save_event_snapshot


def test_with_bbox_writes_full_and_crop(make_image, tmp_path) -> None:  # type: ignore[no-untyped-def]
    frame = make_image()  # 320x240 (w x h)
    out_dir = str(tmp_path / "snapshots")
    result = save_event_snapshot(
        SnapshotArgs(
            frame_path=frame,
            event_id="e1",
            out_dir=out_dir,
            bbox=[10.0, 20.0, 110.0, 120.0],
        )
    )
    assert result.ok is True
    assert result.full_path.endswith("e1_full.jpg")
    assert result.crop_path.endswith("e1_crop.jpg")
    assert os.path.exists(result.full_path)
    assert os.path.exists(result.crop_path)

    import cv2  # lazy — mirror the tool

    full = cv2.imread(result.full_path)
    crop = cv2.imread(result.crop_path)
    assert full is not None and crop is not None
    assert crop.shape[0] <= full.shape[0]
    assert crop.shape[1] <= full.shape[1]


def test_without_bbox_skips_crop(make_image, tmp_path) -> None:  # type: ignore[no-untyped-def]
    frame = make_image()
    result = save_event_snapshot(
        SnapshotArgs(frame_path=frame, event_id="e2", out_dir=str(tmp_path / "snapshots"))
    )
    assert result.ok is True
    assert result.crop_path == ""
    assert os.path.exists(result.full_path)


def test_unreadable_frame_returns_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = str(tmp_path / "missing.jpg")
    result = save_event_snapshot(
        SnapshotArgs(frame_path=missing, event_id="e3", out_dir=str(tmp_path / "snapshots"))
    )
    assert result.ok is False
    assert result.full_path == ""
    assert result.crop_path == ""


def test_bbox_partially_outside_is_clamped(make_image, tmp_path) -> None:  # type: ignore[no-untyped-def]
    frame = make_image()  # 320x240 (w x h)
    result = save_event_snapshot(
        SnapshotArgs(
            frame_path=frame,
            event_id="e4",
            out_dir=str(tmp_path / "snapshots"),
            bbox=[-50.0, -50.0, 400.0, 300.0],
        )
    )
    assert result.ok is True
    assert result.crop_path.endswith("e4_crop.jpg")

    import cv2

    full = cv2.imread(result.full_path)
    crop = cv2.imread(result.crop_path)
    assert full is not None and crop is not None
    assert crop.shape == full.shape  # clamped to image bounds, never outside
