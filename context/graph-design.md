# Graph Design

> Generated from Intake for SIH2026-PROTOTYPE. This file is the CONTRACT: written before any real code, changed only when the topology legitimately changes (and then tool-registry/prompt-registry are updated in the same commit).

---

## Topology

```
                 START
                   │
                   ▼
               ┌────────┐
               │ ingest │  WATCH — video → snapshots (2–3 fps) + GPS sync
               └───┬────┘
                   ▼
               ┌────────┐
               │ detect │  SPOT — YOLO street eye + YOLO pothole eye per frame
               └───┬────┘
                   ▼
              ┌────────────┐
              │ track_flag │  TRACK & FLAG — ByteTrack IDs + 3 if-else rules → events
              └─────┬──────┘
                    │
        ◆ route_to_verify (conditional edge) ◆
        one Send per event (N known only at runtime)
        (events empty → publish directly)
                    │
                    ▼
             ┌──────────────┐
             │ verify_event │  DOUBLE-CHECK worker — Qwen2.5-VL fills the fixed form
             └──────┬───────┘  (parallel workers, one per Send; verdicts reduced with add)
                    │
                    ▼  (static edge — fan-in ordering guarantee)
               ┌────────┐   recheck Send (attempt < MAX_VERIFY_ATTEMPTS)
               │  gate  │──────────────────────────────────────────→ verify_event
               │        │   very sure → publish · half sure → one more look
               └───┬────┘   not sure → drop
                   ▼  (no rechecks pending)
               ┌─────────┐
               │ publish │  SHOW — events.db upserts, map.html, report.md
               └────┬────┘
                    ▼
                   END (run_meta in state)
```

---

## Topology Table

| Node | Type | Model | Tools | Consumes from state | Writes to state |
| --- | --- | --- | --- | --- | --- |
| `ingest` | node | — | extract_frames, gps_sync | `video_path`, `gps_track_path`, `run_id` | `frames` |
| `detect` | node | yolov8n + RDD2022 YOLO | yolo_street, yolo_pothole | `frames` | `detections` |
| `track_flag` | node | — | bytetrack_tracks, save_event_snapshot | `frames`, `detections` | `events`, `track_summary` |
| `verify_event` | Send worker | Qwen2.5-VL (prompt-registry: inspector) | vlm_inspect | Send payload `{event, attempt}` | `verdicts` (reduced) |
| `gate` | node | — | — | `verdicts` | `gate_decisions` |
| `publish` | node | — | store_upsert_events, build_map, build_report | `frames`, `events`, `verdicts`, `gate_decisions` | `published`, `dropped_count`, `map_path`, `report_path`, `status` |

| Conditional edge | From | Condition | To |
| --- | --- | --- | --- |
| `route_to_verify` | `track_flag` | `events` non-empty | `[Send("verify_event", {event, attempt=0}) for each event]` |
| `route_to_verify` | `track_flag` | `events` empty | `publish` |
| `route_after_gate` | `gate` | any event pending recheck (attempt+1 < MAX_VERIFY_ATTEMPTS) | `[Send("verify_event", {event, attempt+1}) ...]` |
| `route_after_gate` | `gate` | no rechecks pending | `publish` |

Static edges: `START → ingest → detect → track_flag`; `verify_event → gate`; `publish → END`.

---

## State Schema

```python
from typing import Annotated, Literal
from operator import add
from pydantic import BaseModel, Field

class FrameRef(BaseModel):
    frame_id: str                 # f{index:05d}
    path: str                     # absolute path to jpg on disk
    t_seconds: float
    lat: float | None = None
    lon: float | None = None

class Detection(BaseModel):
    frame_id: str
    source: Literal["street", "pothole"]
    class_name: str               # car, bus, truck, motorcycle, bicycle, person, pothole, crack...
    conf: float = Field(ge=0.0, le=1.0)
    bbox: list[float]             # xyxy pixels
    track_id: int | None = None   # filled by tracker for street classes

class Event(BaseModel):
    event_id: str                 # {run_id}-{kind}-{frame_id}-{n}
    kind: Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]
    frame_id: str                 # anchor frame
    t_seconds: float
    lat: float | None = None
    lon: float | None = None
    snapshot_path: str            # full-frame jpg; crop path derived
    bbox: list[float] | None = None
    track_id: int | None = None
    evidence: dict                # rule-specific: consecutive_count / dwell_s+looks_small / unique_vehicles

class Verdict(BaseModel):
    event_id: str
    kind: str
    attempt: int                  # 0 = first look, 1 = recheck — bounds the verify/gate cycle
    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    verifier: str                 # "qwen2.5-vl-..." or "stub"

class GateDecision(BaseModel):
    event_id: str
    action: Literal["publish", "recheck", "drop"]
    rule: str                     # threshold rule that fired

class PublishedEvent(BaseModel):
    event: Event
    verdict: Verdict

class RoadfixState(BaseModel):
    # inputs
    video_path: str
    gps_track_path: str | None = None
    run_id: str
    # WATCH
    frames: list[FrameRef] = []                                       # single writer: ingest — overwrite
    # SPOT
    detections: list[Detection] = []                                  # single writer: detect — overwrite
    # TRACK & FLAG
    events: list[Event] = []                                          # single writer: track_flag — overwrite
    track_summary: dict = {}                                          # single writer: track_flag — overwrite
    # DOUBLE-CHECK (fan-out)
    verdicts: Annotated[list[Verdict], add] = []                      # REDUCER REQUIRED — parallel workers, same super-step
    gate_decisions: list[GateDecision] = []                           # single writer: gate — overwrite (recomputed per pass)
    # SHOW
    published: list[PublishedEvent] = []                              # single writer: publish — overwrite
    dropped_count: int = 0
    map_path: str | None = None
    report_path: str | None = None
    status: Literal["running", "done", "failed"] = "running"

class VerifyPayload(BaseModel):
    """Worker-scoped Send payload — workers never see the full graph state."""
    event: Event
    attempt: int
```

### Reducer Rules

- **Appends** (`verdicts`): `operator.add` — parallel verify workers write in the SAME super-step; without the reducer the default overwrite silently drops all but the last verdict (the classic vanished-result bug).
- **Overwrites** (`frames`, `detections`, `events`, `gate_decisions`, `published`, `dropped_count`, `map_path`, `report_path`, `track_summary`): last-writer-wins — each is owned by exactly one node (writer audit below).
- **Writer audit:** every key has exactly one writer node, except `verdicts` (many workers → reducer). `gate_decisions` is recomputed wholesale each gate pass — overwrite, never accumulate, so replays stay idempotent.
- If a new field needs a reducer not listed here, add the reducer AND this schema section in the same commit.

---

## Node Specs

### `ingest`
- **Responsibility:** extract snapshots from `video_path` at `FRAMES_PER_SECOND` (2–3), sync GPS from `gps_track_path` by nearest timestamp, write jpgs under `data/runs/<run_id>/frames/`.
- **Input:** `video_path`, `gps_track_path`, `run_id`. **Output:** `frames` (overwrite).
- **Failure:** unreadable video / zero frames extracted → `frames=[]`, `status` stays `running`; `route_to_verify`-style emptiness at track_flag plus publish handles empty runs; a zero-frame run ends `done` with an honest empty report. Corrupt GPS CSV → logged, `lat/lon=None` on all frames.
- **Never raises past the node.**

### `detect`
- **Responsibility:** run both YOLO eyes on every frame; merge boxes into `detections` with `source` marking which eye saw it.
- **Input:** `frames`. **Output:** `detections` (overwrite).
- **Details:** street eye = COCO classes {car, bus, truck, motorcycle, bicycle, person} conf ≥ `STREET_CONF`; pothole eye = user weights classes {pothole, crack...} conf ≥ `POTHOLE_CONF`. Either eye may be absent (weights missing) — the other still runs; both absent → `detections=[]`.
- **Failure:** model load failure → empty contributions from that eye + log; never raises.

### `track_flag`
- **Responsibility:** run ByteTrack over street detections frame-to-frame (IDs so the same vehicle counts once); apply the three rules; snapshot every flagged event; emit `events`.
- **Rules (deterministic, thresholds in config.py):**
  - `POTHOLE`: the same pothole box (IoU ≥ `POTHOLE_IOU_MATCH` between consecutive frames) appears in ≥ `POTHOLE_MIN_CONSECUTIVE` (2) consecutive snapshots → event anchored at first frame.
  - `KIDS_CROSSING`: a tracked `person` on the road region (lower `KIDS_ROAD_BAND` fraction of frame) with dwell > `KIDS_DWELL_S` (2 s) and median bbox height < `KIDS_SMALL_FRACTION` of frame height → event.
  - `TRAFFIC_JAM`: unique tracked vehicles within any `JAM_WINDOW_S` (30 s) sliding window > `JAM_MIN_VEHICLES` → one event per qualifying window (deduped to non-overlapping windows).
- **Input:** `frames`, `detections`. **Output:** `events` (overwrite), `track_summary` (overwrite: unique vehicle count, per-class counts).
- **Failure:** tracker error → fall back to untracked detections (rules that need IDs degrade: jam rule off, kids rule off); snapshot save failure → event still emitted with `snapshot_path=""` and logged. Never raises.

### `verify_event` (Send worker)
- **Responsibility:** verify ONE event: read its snapshot, ask the VLM to fill the fixed form, validate strict JSON.
- **Input:** Send payload `{event, attempt}` (worker-scoped — never the graph state). **Output:** `{"verdicts": [verdict]}` — one verdict, appended via reducer.
- **Structured output:** `InspectorVerdict {confirmed, severity, confidence, reason}` — validated Pydantic; malformed JSON → 1 corrective retry inside the tool → verdict `confirmed=False, confidence=0.0, reason="invalid_json"` if still invalid.
- **Failure:** endpoint unreachable/timeout (10 s) → 1 retry → verdict `{confirmed: False, confidence: 0.0, verifier="error:<code>"}` → gate drops it. **Unverified never publishes — honest degradation.**

### `gate`
- **Responsibility:** deterministic precision knob over accumulated `verdicts`. Very sure → publish; half sure → one more look; not sure → drop.
- **Rules:** `attempt == 0`: `confidence ≥ GATE_PUBLISH` (0.7) and confirmed → publish · `GATE_RECHECK` (0.4) ≤ confidence < GATE_PUBLISH → recheck (Send attempt+1) · below, or not confirmed → drop. `attempt ≥ 1` (final pass): confirmed → publish · else drop.
- **Input:** `verdicts`. **Output:** `gate_decisions` (overwrite — recomputed each pass).
- **Routing:** `route_after_gate` re-Sends only events whose LATEST verdict needs a recheck; when none pend, routes to `publish`. Cycle bounded by `MAX_VERIFY_ATTEMPTS = 2` — no unbounded loop.
- **Duplicate-verdict safety:** gate keys decisions by `event_id` keeping the highest-attempt verdict; reducer accumulation across passes therefore cannot double-publish.

### `publish`
- **Responsibility:** SHOW — upsert accepted events into `events.db`, build the Folium heatmap (`map.html`), write the markdown report (`report.md`), set run meta.
- **Input:** `events`, `verdicts`, `gate_decisions`, `frames`, `run_id`. **Output:** `published`, `dropped_count`, `map_path`, `report_path`, `status="done"`.
- **Idempotency:** store upsert by `event_id` — replay/resume re-runs write the same rows.
- **Failure:** store failure → logged, `status="failed"`, publish still returns honest counts; report/map failure degrades independently (each wrapped). Never raises.

---

## HITL Contract

| Aspect | Value |
| --- | --- |
| Interrupt count | 0 — N/A (prototype; deterministic gate is cheap and reversible) |

---

## Run Limits

| Limit | Value | Defined in |
| --- | --- | --- |
| `recursion_limit` | 50 | config.py |
| `FRAMES_PER_SECOND` | 2.5 | config.py |
| `MAX_VERIFY_ATTEMPTS` | 2 | config.py |
| `GATE_PUBLISH` / `GATE_RECHECK` | 0.7 / 0.4 | config.py |
| `POTHOLE_MIN_CONSECUTIVE` | 2 | config.py |
| `KIDS_DWELL_S` | 2.0 | config.py |
| `JAM_WINDOW_S` / `JAM_MIN_VEHICLES` | 30 / 12 | config.py |
| VLM timeout | 10 s, 1 retry | tools/vlm.py |
