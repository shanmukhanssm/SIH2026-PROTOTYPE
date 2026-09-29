"""Frame tools — extract_frames (video -> snapshot jpgs) and gps_sync (attach GPS by time).

Contract: context/tool-registry.md `extract_frames` / `gps_sync`. Pydantic in/out,
`ok`/`error` over exceptions, heavy imports lazy, never raises past the boundary.
"""

import csv
import logging
import os

from pydantic import BaseModel, Field

from roadfix.state import FrameRef

logger = logging.getLogger(__name__)

type GpsPoint = tuple[float, float, float]  # (t_seconds, lat, lon)


# --- extract_frames -----------------------------------------------------------


class ExtractFramesArgs(BaseModel):
    video_path: str = Field(..., description="Path to dashcam video")
    out_dir: str  # data/runs/<run_id>/frames
    fps: float = Field(2.5, gt=0, le=30)


class ExtractedFrames(BaseModel):
    frames: list[FrameRef]  # frame_id, path, t_seconds (lat/lon unset)
    duration_s: float
    ok: bool
    error: str | None = None  # None on success


def extract_frames(args: ExtractFramesArgs) -> ExtractedFrames:
    """WATCH — chop video into snapshot jpgs at fps. Never raises."""
    try:
        import cv2  # lazy heavy import

        cap = cv2.VideoCapture(args.video_path)
        try:
            if not cap.isOpened():
                raise ValueError(f"cannot open video: {args.video_path}")
            src_fps = cap.get(cv2.CAP_PROP_FPS)
            if src_fps <= 0:
                raise ValueError(f"invalid source fps {src_fps}: {args.video_path}")
            grab_every = max(1, round(src_fps / args.fps))
            os.makedirs(args.out_dir, exist_ok=True)
            frames: list[FrameRef] = []
            src_idx = 0
            while True:
                ok_read, image = cap.read()
                if not ok_read or image is None:
                    break
                if src_idx % grab_every == 0:
                    ordinal = len(frames)
                    path = os.path.join(args.out_dir, f"f{ordinal:05d}.jpg")
                    # imwrite returns False silently on bad paths — failed write is an
                    # error path (ok=False, frames=[]), matching the other failure modes
                    if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                        raise RuntimeError(f"imwrite failed: {path}")
                    frames.append(
                        FrameRef(
                            frame_id=f"f{ordinal:05d}.jpg",
                            path=path,
                            t_seconds=src_idx / src_fps,
                        )
                    )
                src_idx += 1
        finally:
            cap.release()
        # duration from observed frame count — consistent with t_seconds = src_idx / src_fps
        duration_s = src_idx / src_fps
        logger.info(
            "[extract_frames] wrote %d frames from %s (src_fps=%.2f, stride=%d)",
            len(frames),
            args.video_path,
            src_fps,
            grab_every,
        )
        return ExtractedFrames(frames=frames, duration_s=duration_s, ok=True)
    except Exception as exc:  # boundary — translate, never propagate
        logger.error("[extract_frames] %s: %s", type(exc).__name__, exc)
        return ExtractedFrames(
            frames=[], duration_s=0.0, ok=False, error=f"{type(exc).__name__}: {exc}"
        )


# --- gps_sync -----------------------------------------------------------------


class GpsSyncArgs(BaseModel):
    frames: list[FrameRef]
    gps_csv_path: str | None = None
    tolerance_s: float = Field(1.5, gt=0)


class GpsSyncResult(BaseModel):
    frames: list[FrameRef]  # same list, lat/lon filled where matched
    matched: int
    reason_unmatched: str | None = None
    # "no csv provided" / "csv malformed" / "no fix within tolerance"


def _copy_all(frames: list[FrameRef]) -> list[FrameRef]:
    """Fresh FrameRef copies — the input list is never mutated."""
    return [f.model_copy() for f in frames]


def _read_gps_csv(path: str) -> list[GpsPoint]:
    """Parse `t_seconds,lat,lon` rows; header and rows failing float() are skipped."""
    points: list[GpsPoint] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) != 3:
                continue
            try:
                points.append((float(row[0]), float(row[1]), float(row[2])))
            except ValueError:
                continue
    return points


def _nearest(points: list[GpsPoint], t: float) -> GpsPoint:
    """Nearest gps point by |t_seconds - t|. ponytail: O(n) scan; bisect if logs grow."""
    return min(points, key=lambda p: abs(p[0] - t))


def gps_sync(args: GpsSyncArgs) -> GpsSyncResult:
    """Attach lat/lon to each frame by nearest GPS-log timestamp within tolerance. Never raises."""
    try:
        if args.gps_csv_path is None:
            logger.info("[gps_sync] no csv provided; %d frames unmatched", len(args.frames))
            return GpsSyncResult(
                frames=_copy_all(args.frames), matched=0, reason_unmatched="no csv provided"
            )
        points = _read_gps_csv(args.gps_csv_path)
        if not points:
            # missing file or zero parseable rows are both "csv malformed" (registry contract)
            logger.error("[gps_sync] csv malformed: %s", args.gps_csv_path)
            return GpsSyncResult(
                frames=_copy_all(args.frames), matched=0, reason_unmatched="csv malformed"
            )
        out: list[FrameRef] = []
        matched = 0
        for frame in args.frames:
            t_gps, lat, lon = _nearest(points, frame.t_seconds)
            if abs(t_gps - frame.t_seconds) <= args.tolerance_s:
                out.append(frame.model_copy(update={"lat": lat, "lon": lon}))
                matched += 1
            else:
                out.append(frame.model_copy())  # honest gap: lat/lon stay None
        reason: str | None = None if matched == len(args.frames) else "no fix within tolerance"
        logger.info(
            "[gps_sync] matched %d/%d frames from %s", matched, len(args.frames), args.gps_csv_path
        )
        return GpsSyncResult(frames=out, matched=matched, reason_unmatched=reason)
    except Exception as exc:  # boundary — translate, never propagate
        logger.error("[gps_sync] %s: %s", type(exc).__name__, exc)
        return GpsSyncResult(
            frames=_copy_all(args.frames), matched=0, reason_unmatched="csv malformed"
        )
