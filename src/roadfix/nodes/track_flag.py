"""TRACK & FLAG — ByteTrack IDs over street boxes + three deterministic rules -> events.

Contract: context/graph-design.md `track_flag` node spec (the three rules and their
exact thresholds) + context/tool-registry.md (`bytetrack_tracks`, `save_event_snapshot`).
Design mandate (build-plan feature 10): the rule engine is a set of PURE module-level
functions over plain data (Detection lists, frame dims/times, thresholds) — no state,
no tool calls, no logging inside the rules. The node orchestrates:
partition -> tracker -> pothole rule -> kids rule -> jam rule -> snapshots -> events.

Interpretations pinned here (expanded in each rule's docstring):
- KIDS_ROAD_BAND membership uses the bbox CENTER-y (not the bottom edge): center_y /
  frame_height inside the band, both ends inclusive.
- "Consecutive" for potholes means consecutive frames of the extracted timeline
  (state.frames ordered by t_seconds); one frame without a matching box breaks the streak.
- TRAFFIC_JAM windows are closed intervals [start, start + JAM_WINDOW_S], deduped
  greedily to non-overlapping windows; the anchor is the last timeline frame whose
  t_seconds lies inside the window span.
"""

import logging
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Literal

from roadfix.config import (
    JAM_MIN_VEHICLES,
    JAM_WINDOW_S,
    KIDS_DWELL_S,
    KIDS_ROAD_BAND,
    KIDS_SMALL_FRACTION,
    POTHOLE_IOU_MATCH,
    POTHOLE_MIN_CONSECUTIVE,
    runs_dir,
)
from roadfix.state import Detection, Event, FrameRef, RoadfixState
from roadfix.tools.snapshot import SnapshotArgs, save_event_snapshot
from roadfix.tools.tracker import TrackArgs, bytetrack_tracks

logger = logging.getLogger(__name__)

type EventKind = Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]

# graph-design.md TRAFFIC_JAM rule: vehicles only — a person is never a jam contributor.
VEHICLE_CLASSES: frozenset[str] = frozenset(
    {"car", "bus", "truck", "motorcycle", "bicycle"}
)


# --- Rule-engine result types (plain data — the node turns these into Events) ---


@dataclass(frozen=True)
class PotholeCandidate:
    """One qualifying pothole streak — anchored at its first frame, box = first box."""

    first_frame_id: str
    bbox: tuple[float, float, float, float]
    consecutive_count: int


@dataclass(frozen=True)
class KidsCandidate:
    """One qualifying tracked person — anchored at its first in-band frame."""

    track_id: int
    first_frame_id: str
    bbox: tuple[float, float, float, float]  # element-wise median box over band members
    dwell_s: float


@dataclass(frozen=True)
class JamCandidate:
    """One qualifying jam window — anchored at the last timeline frame in the window."""

    anchor_frame_id: str
    window_start_s: float
    unique_vehicles: int


@dataclass
class _PotholeChain:
    """Mutable chain of one physical pothole across consecutive timeline frames."""

    frame_ids: list[str]
    boxes: list[tuple[float, float, float, float]]
    last_box: tuple[float, float, float, float]
    last_frame_idx: int
    status: str = "open"  # open -> flagged (event) | suppressed (duplicate)


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    """xyxy intersection-over-union; 0.0 for malformed or zero-area boxes."""
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _xyxy(d: Detection) -> tuple[float, float, float, float]:
    """First four bbox coords as a typed tuple (malformed shorter boxes guarded upstream)."""
    return (float(d.bbox[0]), float(d.bbox[1]), float(d.bbox[2]), float(d.bbox[3]))


# --- Rule 1: POTHOLE (static objects — no tracker, IoU chain over the frame timeline) ---


def pothole_rule(
    detections: Sequence[Detection],
    ordered_frame_ids: Sequence[str],
    *,
    iou_threshold: float,
    min_consecutive: int,
) -> list[PotholeCandidate]:
    """POTHOLE rule — pure. Same box (IoU >= iou_threshold) on >= min_consecutive consecutive
    timeline frames -> one candidate anchored at the FIRST frame of the streak, bbox = first
    box, consecutive_count = full streak length.

    Semantics:
    - Only pothole-source detections participate; detections on frames missing from the
      timeline are ignored (consecutiveness is undefined without a timeline position).
    - Chains advance only through CONSECUTIVE timeline frames: a chain may extend from the
      previous frame only; any frame without a matching box breaks the streak.
    - Multiple parallel chains are supported: each detection matches the alive chain with
      the highest IoU to the chain's last box (first chain wins ties); unmatched detections
      spawn new chains. A chain takes at most one box per frame.
    - Dedupe (eval-plan "gap-IoU pothole: fires once, deduped"): chains are flagged in
      discovery order; a later qualifying streak any of whose boxes overlaps (IoU >=
      iou_threshold) ANY box of an already-flagged pothole is the same physical pothole and
      is suppressed, never emitted.
    """
    dets_by_frame: dict[str, list[Detection]] = {}
    for d in detections:
        if d.source == "pothole" and len(d.bbox) == 4:
            dets_by_frame.setdefault(d.frame_id, []).append(d)

    chains: list[_PotholeChain] = []
    # WHY only FLAGGED chains' boxes enter this pool (not suppressed ones): the eval-plan
    # dedupe case is "a re-appearing box overlapping an already-flagged pothole". A
    # suppressed chain's own evolution is intentionally NOT pooled — a suppressed streak
    # is by definition a duplicate of a flagged pothole, so its boxes track the same
    # physical object the flagged pool already covers; pooling them too would compound
    # suppression across nearby-but-distinct potholes. Asymmetry is deliberate.
    flagged_boxes: list[tuple[float, float, float, float]] = []
    flagged_chains: list[_PotholeChain] = []

    for idx, frame_id in enumerate(ordered_frame_ids):
        for d in dets_by_frame.get(frame_id, []):
            box = _xyxy(d)
            best: _PotholeChain | None = None
            best_iou = -1.0
            for chain in chains:
                # Alive means "extended on the previous timeline frame" — this also makes
                # a chain extended earlier in THIS frame ineligible (one box per frame).
                if chain.last_frame_idx != idx - 1:
                    continue
                overlap = _iou(chain.last_box, box)
                if overlap >= iou_threshold and (best is None or overlap > best_iou):
                    best, best_iou = chain, overlap
            if best is not None:
                chain = best
                chain.frame_ids.append(frame_id)
                chain.boxes.append(box)
                chain.last_box = box
                chain.last_frame_idx = idx
            else:
                chain = _PotholeChain(
                    frame_ids=[frame_id], boxes=[box], last_box=box, last_frame_idx=idx
                )
                chains.append(chain)
            if chain.status == "open" and len(chain.boxes) >= min_consecutive:
                if any(
                    _iou(mine, seen) >= iou_threshold
                    for mine in chain.boxes
                    for seen in flagged_boxes
                ):
                    chain.status = "suppressed"
                else:
                    chain.status = "flagged"
                    flagged_chains.append(chain)
                    flagged_boxes.extend(chain.boxes)
            elif chain.status == "flagged":
                flagged_boxes.append(box)  # flagged streak grew — keep the dedupe pool current

    return [
        PotholeCandidate(
            first_frame_id=c.frame_ids[0],
            bbox=c.boxes[0],
            consecutive_count=len(c.boxes),
        )
        for c in flagged_chains
    ]


# --- Rule 2: KIDS_CROSSING (tracked persons, road band, dwell + smallness) ---


def kids_crossing_rule(
    detections: Sequence[Detection],
    frame_times: Mapping[str, float],
    frame_heights: Mapping[str, int],
    *,
    road_band: tuple[float, float],
    dwell_s: float,
    small_fraction: float,
) -> list[KidsCandidate]:
    """KIDS_CROSSING rule — pure. Tracked persons (street source, track_id not None) whose
    bbox CENTER-y falls inside the road band dwell in the band longer than dwell_s (strict >)
    while being small (median bbox height < small_fraction of frame height) -> ONE candidate
    per track_id, anchored at the first in-band frame.

    Interpretations (pinned):
    - Band membership uses the bbox CENTER-y (not the bottom edge): center_y / frame_height
      within road_band, both ends inclusive. The center is robust against box jitter at the
      head/feet edges of small bodies.
    - Members of a track are only detections whose frame has a known t_seconds AND a known
      pixel height (frames the dim-lookup helper could not read are skipped — honest
      degradation); dwell is measured across in-band members only (time spent ON the road).
    - Smallness: median of per-member (bbox_height / frame_height) < small_fraction (strict).
      For one clip's constant-height frames this equals median(height) < fraction * height.
    - bbox = element-wise median box over the track's in-band members (the "median box").
    """
    members_by_track: defaultdict[int, list[tuple[float, str, int, Detection]]] = defaultdict(list)
    for d in detections:
        if d.source != "street" or d.class_name != "person" or d.track_id is None:
            continue
        if len(d.bbox) != 4:
            continue
        t = frame_times.get(d.frame_id)
        height = frame_heights.get(d.frame_id)
        if t is None or height is None or height <= 0:
            continue
        center_y = (d.bbox[1] + d.bbox[3]) / 2.0
        ratio = center_y / float(height)
        if not (road_band[0] <= ratio <= road_band[1]):
            continue
        members_by_track[d.track_id].append((t, d.frame_id, height, d))

    candidates: list[KidsCandidate] = []
    for track_id in sorted(members_by_track):
        members = sorted(members_by_track[track_id], key=lambda m: (m[0], m[1]))
        first_t, first_frame_id, _, _ = members[0]
        last_t = members[-1][0]
        dwell = last_t - first_t
        height_ratios = [(m[3].bbox[3] - m[3].bbox[1]) / float(m[2]) for m in members]
        if dwell > dwell_s and median(height_ratios) < small_fraction:
            med_box = (
                median([m[3].bbox[0] for m in members]),
                median([m[3].bbox[1] for m in members]),
                median([m[3].bbox[2] for m in members]),
                median([m[3].bbox[3] for m in members]),
            )
            candidates.append(
                KidsCandidate(
                    track_id=track_id,
                    first_frame_id=first_frame_id,
                    bbox=med_box,
                    dwell_s=dwell,
                )
            )
    return candidates


# --- Rule 3: TRAFFIC_JAM (unique tracked vehicles per sliding window) ---


def traffic_jam_rule(
    detections: Sequence[Detection],
    frame_times: Mapping[str, float],
    *,
    window_s: float,
    min_vehicles: int,
) -> list[JamCandidate]:
    """TRAFFIC_JAM rule — pure. Unique tracked vehicle track_ids (VEHICLE_CLASSES, street
    source, track_id not None) within any closed window [start, start + window_s] strictly
    greater than min_vehicles -> one candidate per window, deduped to NON-OVERLAPPING
    windows.

    Semantics:
    - Candidate starts are ALL distinct appearance times across tracked vehicles. WHY not
      just first appearances: a track may reappear (occlusion chains) far from its first
      sighting, so a qualifying window can exist whose start coincides with NO track's
      first appearance — scanning every appearance time is strictly faithful to the
      "within ANY window" contract (checked exhaustively against brute force in review).
    - Greedy dedupe: scan starts ascending; the first qualifying window fires, then only
      starts STRICTLY AFTER its end are considered ("continue scanning after its end").
    - Anchor = the last timeline frame (greatest t_seconds, frame_id tie-break) whose
      t_seconds lies within the window span.
    """
    appearances: dict[int, set[float]] = {}
    for d in detections:
        if d.source != "street" or d.class_name not in VEHICLE_CLASSES or d.track_id is None:
            continue
        t = frame_times.get(d.frame_id)
        if t is None:
            continue
        appearances.setdefault(d.track_id, set()).add(t)

    frames_by_time = sorted(frame_times.items(), key=lambda kv: (kv[1], kv[0]))
    candidates: list[JamCandidate] = []
    last_end: float | None = None
    starts = sorted({t for times in appearances.values() for t in times})
    for start in starts:
        if last_end is not None and start <= last_end:
            continue
        end = start + window_s
        unique = sum(
            1 for times in appearances.values() if any(start <= t <= end for t in times)
        )
        if unique <= min_vehicles:
            continue
        in_window = [(t, fid) for fid, t in frames_by_time if start <= t <= end]
        anchor = max(in_window)[1]
        candidates.append(
            JamCandidate(
                anchor_frame_id=anchor, window_start_s=start, unique_vehicles=unique
            )
        )
        last_end = end
    return candidates


# --- Node orchestration (tools, snapshots, events — everything with side effects) ---


def _frame_heights(frames: Sequence[FrameRef], needed: set[str]) -> dict[str, int]:
    """Real pixel height per needed frame via cv2 (lazy import, imread on the frame path).

    Frames that are missing from `frames` or unreadable on disk are omitted — the kids
    rule then skips their detections (honest degradation, no invented dimensions).
    """
    if not needed:
        return {}
    import cv2  # lazy heavy import (code-standards.md)

    by_id = {f.frame_id: f for f in frames}
    heights: dict[str, int] = {}
    for frame_id in sorted(needed):
        ref = by_id.get(frame_id)
        if ref is None:
            continue
        image = cv2.imread(ref.path)
        if image is None:
            logger.warning("[track_flag] unreadable frame %s — height unknown", ref.path)
            continue
        heights[frame_id] = int(image.shape[0])
    return heights


def _flag_event(
    run_id: str,
    kind: EventKind,
    ordinal: int,
    anchor: FrameRef,
    bbox: list[float] | None,
    track_id: int | None,
    evidence: dict[str, object],
) -> Event:
    """Snapshot the anchor frame and build the Event (snapshot failure -> empty path)."""
    event_id = f"{run_id}-{kind}-{anchor.frame_id}-{ordinal}"
    snap = save_event_snapshot(
        SnapshotArgs(
            frame_path=anchor.path,
            event_id=event_id,
            out_dir=str(runs_dir() / run_id / "snapshots"),
            bbox=bbox,
        )
    )
    snapshot_path = snap.full_path if snap.ok else ""
    if not snap.ok:
        logger.warning(
            "[track_flag] snapshot failed for %s — event emitted without photo", event_id
        )
    return Event(
        event_id=event_id,
        kind=kind,
        frame_id=anchor.frame_id,
        t_seconds=anchor.t_seconds,
        lat=anchor.lat,
        lon=anchor.lon,
        snapshot_path=snapshot_path,
        bbox=bbox,
        track_id=track_id,
        evidence=evidence,
    )


def track_flag(state: RoadfixState) -> dict[str, object]:
    """TRACK & FLAG — IDs + the three deterministic rules. Returns state UPDATE only
    ({"events", "track_summary"} — writer audit per graph-design.md). Never raises.
    """
    empty_summary: dict[str, object] = {"unique_vehicles": 0, "per_class": {}}
    events: list[Event] = []
    track_summary: dict[str, object] = empty_summary
    try:
        if not state.frames:
            # No timeline — nothing can be anchored (honest empty run, per contract).
            return {"events": [], "track_summary": empty_summary}

        frame_map = {f.frame_id: f for f in state.frames}
        time_map = {f.frame_id: f.t_seconds for f in state.frames}
        ordered_ids = [
            f.frame_id
            for f in sorted(state.frames, key=lambda fr: (fr.t_seconds, fr.frame_id))
        ]

        street = [d for d in state.detections if d.source == "street"]
        # Registry contract: tracker input ordered by frame t_seconds (unknown frames last,
        # frame_id tie-break — ids are f{index:05d} ordinals, so still chronological).
        street.sort(key=lambda d: (time_map.get(d.frame_id, math.inf), d.frame_id))
        pothole_dets = [d for d in state.detections if d.source == "pothole"]

        tracked: list[Detection] = street
        id_rules_on = False
        if street:
            result = bytetrack_tracks(TrackArgs(detections=street))
            tracked = result.detections
            id_rules_on = result.tracked_ok
            if not result.tracked_ok:
                logger.warning(
                    "[track_flag] tracker failed (%s) — jam/kids rules off, boxes untracked",
                    result.error,
                )

        counters: dict[EventKind, int] = {
            "POTHOLE": 0,
            "KIDS_CROSSING": 0,
            "TRAFFIC_JAM": 0,
        }

        # POTHOLE — static objects, IoU chain, no tracker; runs even when tracking degrades.
        for p_cand in pothole_rule(
            pothole_dets,
            ordered_ids,
            iou_threshold=POTHOLE_IOU_MATCH,
            min_consecutive=POTHOLE_MIN_CONSECUTIVE,
        ):
            counters["POTHOLE"] += 1
            events.append(
                _flag_event(
                    state.run_id,
                    "POTHOLE",
                    counters["POTHOLE"],
                    frame_map[p_cand.first_frame_id],
                    list(p_cand.bbox),
                    None,
                    {"consecutive_count": p_cand.consecutive_count},
                )
            )

        if id_rules_on:
            person_frames = {
                d.frame_id
                for d in tracked
                if d.class_name == "person" and d.track_id is not None
            }
            heights = _frame_heights(state.frames, person_frames)
            for k_cand in kids_crossing_rule(
                tracked,
                time_map,
                heights,
                road_band=KIDS_ROAD_BAND,
                dwell_s=KIDS_DWELL_S,
                small_fraction=KIDS_SMALL_FRACTION,
            ):
                counters["KIDS_CROSSING"] += 1
                events.append(
                    _flag_event(
                        state.run_id,
                        "KIDS_CROSSING",
                        counters["KIDS_CROSSING"],
                        frame_map[k_cand.first_frame_id],
                        list(k_cand.bbox),
                        k_cand.track_id,
                        {"dwell_s": round(k_cand.dwell_s, 2), "looks_small": True},
                    )
                )
            for j_cand in traffic_jam_rule(
                tracked,
                time_map,
                window_s=JAM_WINDOW_S,
                min_vehicles=JAM_MIN_VEHICLES,
            ):
                counters["TRAFFIC_JAM"] += 1
                events.append(
                    _flag_event(
                        state.run_id,
                        "TRAFFIC_JAM",
                        counters["TRAFFIC_JAM"],
                        frame_map[j_cand.anchor_frame_id],
                        None,
                        None,
                        {"unique_vehicles": j_cand.unique_vehicles,
                         "window_s": JAM_WINDOW_S},
                    )
                )

        vehicle_ids = {
            d.track_id
            for d in tracked
            if d.class_name in VEHICLE_CLASSES and d.track_id is not None
        }
        per_class: dict[str, object] = {
            name: n for name, n in sorted(Counter(d.class_name for d in tracked).items())
        }
        track_summary = {"unique_vehicles": len(vehicle_ids), "per_class": per_class}
        logger.info(
            "[track_flag] %d events (%d pothole, %d kids, %d jam); unique vehicles %d",
            len(events),
            counters["POTHOLE"],
            counters["KIDS_CROSSING"],
            counters["TRAFFIC_JAM"],
            len(vehicle_ids),
        )
    except Exception as exc:  # boundary — best effort, never propagate
        logger.warning(
            "[track_flag] node degraded to best effort: %s: %s", type(exc).__name__, exc
        )
    return {"events": events, "track_summary": track_summary}
