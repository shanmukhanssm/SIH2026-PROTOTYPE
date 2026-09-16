# Tool Registry

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update in the same commit as any tool change. Format per context-references/tool-registry.md.

**How to use this file:** before building any tool, check it exists here. After adding or changing any tool, update this file first, then the code. A tool that is not in this registry does not exist.

---

## Registry Overview

| Tool | File | Consumed by | Side effects | Eval status |
| --- | --- | --- | --- | --- |
| `extract_frames` | `src/roadfix/tools/frames.py` | ingest | Writes frame jpgs to run dir | PASS (v1, incl. stub) |
| `gps_sync` | `src/roadfix/tools/frames.py` | ingest | — | PASS (v1) |
| `yolo_street` | `src/roadfix/tools/detectors.py` | detect | First use downloads COCO weights | PASS (v1, stub + real) |
| `yolo_pothole` | `src/roadfix/tools/detectors.py` | detect | — | PASS (v1, stub; real needs weights) |
| `bytetrack_tracks` | `src/roadfix/tools/tracker.py` | track_flag | — | PASS (v1) |
| `save_event_snapshot` | `src/roadfix/tools/snapshot.py` | track_flag | Writes full-frame jpg (+ crop) per event | PASS (v1) |
| `vlm_inspect` | `src/roadfix/tools/vlm.py` | verify_event | External API call | PASS (v1, stub; real needs endpoint) |
| `store_upsert_events` | `src/roadfix/tools/store.py` | publish | Upserts rows in events.db | PASS (v1) |
| `store_query_events` | `src/roadfix/tools/store.py` | ui/dashboard.py | — | PASS (v1) |
| `build_map` | `src/roadfix/tools/map_builder.py` | publish | Writes map.html | PASS (v1) |
| `build_report` | `src/roadfix/tools/report.py` | publish | Writes report.md | PASS (v1) |

This list is closed. No other tool may be called from any node. Adding a tool: register here → update graph-design.md node spec → build → eval → set status.

---

## `extract_frames`

**Purpose:** chop one video into snapshot jpgs at a fixed fps so two neighbors are ~90% identical but every moment is covered.

```python
from pydantic import BaseModel, Field

class ExtractFramesArgs(BaseModel):
    video_path: str = Field(..., description="Path to dashcam video")
    out_dir: str                    # data/runs/<run_id>/frames
    fps: float = Field(2.5, gt=0, le=5)

class ExtractedFrames(BaseModel):
    frames: list[FrameRef]          # frame_id, path, t_seconds (lat/lon unset)
    duration_s: float
    ok: bool
    error: str | None = None        # None on success

# Signature
def extract_frames(args: ExtractFramesArgs) -> ExtractedFrames: ...
```

| Property | Value |
| --- | --- |
| Timeout | None (local file), bounded by video length |
| Retry | No retry — a corrupt video stays corrupt |
| Error behavior | Missing/unreadable video or codec failure → `ok=False, frames=[]` — never raises |
| Frame naming | `f{index:05d}.jpg`, index = frame ordinal at chosen fps |

**Consumers:** `ingest` node.
**Eval:** 6-fixture set (short mpv-generated clips: 1s, 2s, 5s, 10s, corrupt file, missing file) — gate = all 6 handled per contract, zero raises. Current: PASS.

---

## `gps_sync`

**Purpose:** attach lat/lon to each frame by nearest timestamp from the bus GPS log (CSV `t_seconds,lat,lon`).

```python
class GpsSyncArgs(BaseModel):
    frames: list[FrameRef]
    gps_csv_path: str | None = None
    tolerance_s: float = Field(1.5, gt=0)

class GpsSyncResult(BaseModel):
    frames: list[FrameRef]          # same list, lat/lon filled where matched
    matched: int
    reason_unmatched: str | None    # "no csv provided" / "csv malformed" / "no fix within tolerance"

# Signature
def gps_sync(args: GpsSyncArgs) -> GpsSyncResult: ...
```

| Property | Value |
| --- | --- |
| Error behavior | Missing CSV → all unmatched, `reason_unmatched="no csv provided"`; malformed rows skipped; never raises |
| Matching | Nearest-neighbor on `t_seconds`; beyond tolerance → left `None` (honest gaps) |

**Consumers:** `ingest` node.
**Eval:** unit — 5 cases (no csv, clean csv, malformed csv, gap beyond tolerance, exact match); gate 5/5. Current: PASS.

---

## `yolo_street`

**Purpose:** Eye #1 — pre-trained COCO YOLO over one frame; boxes cars, buses, trucks, bikes, people. Downloaded, never trained.

```python
class DetectArgs(BaseModel):
    frame_path: str
    conf: float = Field(0.35, ge=0.0, le=1.0)

class DetectResult(BaseModel):
    detections: list[Detection]     # source="street", classes from STREET_CLASSES
    model_loaded: bool
    error: str | None = None

# Signature
def yolo_street(args: DetectArgs) -> DetectResult: ...
```

| Property | Value |
| --- | --- |
| Classes (COCO ids) | car 2, motorcycle 3, bus 5, truck 7, bicycle 1, person 0 |
| Timeout | 30 s per frame (CPU headroom) |
| Error behavior | Weights missing/offline-first-run → `model_loaded=False, detections=[]` (street eye degraded, logged); inference failure same. Never raises |
| Stub mode | `ROADFIX_STUB_MODELS=1` → deterministic boxes from frame filename hash (tests, no-GPU demos) |

**Consumers:** `detect` node (every frame).
**Eval:** stub-mode unit tests assert class filter + conf threshold + degraded-mode contract; real-model check is an operator run (documented in evals/run_evals.py `--layer tools --real-models`). Current: PASS (stub).

---

## `yolo_pothole`

**Purpose:** Eye #2 — RDD2022-trained YOLO over one frame; boxes potholes and cracks. The street eye cannot see these; that is why there are two eyes.

```python
# Same args/result shapes as yolo_street; source="pothole"
# Signature
def yolo_pothole(args: DetectArgs) -> DetectResult: ...
```

| Property | Value |
| --- | --- |
| Weights | `POTHOLE_MODEL_PATH` env (user-supplied RDD2022-trained .pt) |
| Classes | whatever the weights define (pothole/crack/damaged-road family) — kept verbatim |
| Timeout | 30 s per frame |
| Error behavior | Weights not configured/missing → `model_loaded=False, detections=[]` — pothole eye disabled, street eye unaffected. Never raises |
| Stub mode | Same as street — deterministic pothole boxes on stub frames |

**Consumers:** `detect` node (every frame).
**Eval:** stub-mode unit tests as street; real weights check documented operator run. Current: PASS (stub).

---

## `bytetrack_tracks`

**Purpose:** stick an ID number on every vehicle/person and follow them frame to frame, so the same car is counted once, never twice.

```python
class TrackArgs(BaseModel):
    detections: list[Detection]     # street-source only, ordered by frame t_seconds

class TrackResult(BaseModel):
    detections: list[Detection]     # same boxes with track_id filled (unmatched → None)
    tracked_ok: bool
    error: str | None = None

# Signature
def bytetrack_tracks(args: TrackArgs) -> TrackResult: ...
```

| Property | Value |
| --- | --- |
| Library | supervision `ByteTrack`, per-frame `update_with_detections` |
| Error behavior | Tracker failure → `tracked_ok=False`, detections returned untracked (ID-dependent rules degrade); never raises |
| Scope | Street boxes only — potholes are static, matched by IoU rule instead |

**Consumers:** `track_flag` node.
**Eval:** unit — synthetic 3-frame sequence (one car crossing, one appearing later) → stable IDs, new ID for later car; failure-path returns untracked. Gate 4/4. Current: PASS.

---

## `save_event_snapshot`

**Purpose:** persist the evidence photo for one event — full annotated-frame jpg plus the crop of the triggering box.

```python
class SnapshotArgs(BaseModel):
    frame_path: str
    event_id: str
    out_dir: str                    # data/runs/<run_id>/snapshots
    bbox: list[float] | None = None

class SnapshotResult(BaseModel):
    full_path: str                  # "" on failure
    crop_path: str                  # "" when bbox None or crop fails
    ok: bool

# Signature
def save_event_snapshot(args: SnapshotArgs) -> SnapshotResult: ...
```

| Property | Value |
| --- | --- |
| Side effect | Writes 1–2 jpgs per event |
| Error behavior | Unreadable frame → `ok=False`, empty paths; event still emitted upstream with empty path. Never raises |

**Consumers:** `track_flag` node (one call per flagged event).
**Eval:** unit — 3 cases (with bbox, without bbox, unreadable frame); gate 3/3. Current: PASS.

---

## `vlm_inspect`

**Purpose:** the DOUBLE-CHECK inspector — one VLM call per event that "reads" the snapshot photo and fills the fixed form in strict JSON.

```python
class InspectArgs(BaseModel):
    event: Event                    # snapshot_path, kind, evidence included
    attempt: int

class InspectResult(BaseModel):
    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    verifier: str                   # model name or "stub" or "error:<code>"

# Signature
def vlm_inspect(args: InspectArgs) -> InspectResult: ...
```

| Property | Value |
| --- | --- |
| Endpoint | OpenAI-compatible: `VLM_BASE_URL` + `VLM_API_KEY` + `VLM_MODEL` |
| Image transport | base64 data URL in `image_url` content part |
| Timeout | 10 s |
| Retry | 1 retry on transient/timeout; 1 corrective re-ask on invalid JSON (field-specific feedback), cap 2 total attempts |
| Error behavior | All retries exhausted → `confirmed=False, confidence=0.0, verifier="error:<code>"` — gate drops. Never raises |
| Structured output | JSON object mode + Pydantic validation; schema per prompt-registry.md inspector v1 |
| Stub mode | Deterministic verdict from event kind + evidence (tests, offline demos) |

**Consumers:** `verify_event` worker (one call per Send).
**Eval:** stub unit tests (valid event → form fields present; malformed-JSON endpoint fixture → corrective path → error verdict). Real endpoint check documented operator run. Current: PASS (stub).

---

## `store_upsert_events`

**Purpose:** the system of record — upsert accepted events (with verdicts) into `data/runs/events.db`. Upsert by `event_id` makes replay/resume idempotent.

```python
class UpsertArgs(BaseModel):
    events: list[PublishedEvent]
    db_path: str

class UpsertResult(BaseModel):
    upserted: int
    ok: bool
    error: str | None = None

# Signature
def store_upsert_events(args: UpsertArgs) -> UpsertResult: ...
```

| Property | Value |
| --- | --- |
| Schema | `events(event_id PK, kind, severity, confirmed, confidence, t_seconds, lat, lon, snapshot_path, run_id, reason, published_at)` |
| Side effect | INSERT OR REPLACE per row |
| Error behavior | DB failure → `ok=False` + log; publish sets `status="failed"` honestly. Never raises |

**Consumers:** `publish` node; `store_query_events` (read side) consumed by `ui/dashboard.py`.
**Eval:** unit — 4 cases (insert, replace-same-id, query-filter-by-run, db-unwritable); gate 4/4. Current: PASS.

---

## `build_map`

**Purpose:** render the run's heatmap — streets with more events glow more — plus per-event markers with photo popups, as one HTML file.

```python
class MapArgs(BaseModel):
    events: list[PublishedEvent]
    out_path: str                   # data/runs/<run_id>/map.html
    fallback_center: tuple[float, float] = (17.3850, 78.4867)  # when no GPS

class MapResult(BaseModel):
    path: str
    event_count: int
    used_fallback_center: bool
    ok: bool

# Signature
def build_map(args: MapArgs) -> MapResult: ...
```

| Property | Value |
| --- | --- |
| Library | folium + HeatMap plugin |
| Error behavior | Failure → `ok=False` (report still written; publish continues). Never raises |
| No-GPS runs | HeatMap skipped, fallback center marker + warning recorded in report |

**Consumers:** `publish` node. **Eval:** unit — 3 cases (with GPS, without GPS, empty events); gate 3/3. Current: PASS.

---

## `build_report`

**Purpose:** auto-generate the run's markdown report — event table, severity counts, run meta, honest-caveats section. Video in, sorted road-work list out.

```python
class ReportArgs(BaseModel):
    run_id: str
    published: list[PublishedEvent]
    dropped_count: int
    track_summary: dict
    out_path: str                   # data/runs/<run_id>/report.md

class ReportResult(BaseModel):
    path: str
    ok: bool
    error: str | None = None

# Signature
def build_report(args: ReportArgs) -> ReportResult: ...
```

| Property | Value |
| --- | --- |
| Sections | Summary counts · event table (kind, severity, confidence, time, GPS, photo link) · dropped count · caveats |
| Error behavior | Failure → `ok=False` (map/store unaffected). Never raises |

**Consumers:** `publish` node. **Eval:** unit — 3 cases (full, empty, severity ordering); gate 3/3. Current: PASS.

---

## Rules

- This registry is the closed tool list. Nodes never call external systems except through these tools.
- Signature changes require: update this file → update graph-design.md node spec → change code → re-run tool eval → update status. In the same commit.
- Every tool: Pydantic args, explicit timeout (where applicable), explicit error behavior — never raise past the tool boundary.
- Eval status values: `UNTESTED` → `PASS (vN)` / `FAIL`. A tool with `UNTESTED` may not be consumed by a phase-2 gate run.
- Stub modes exist so tests and no-GPU demos run without weights or endpoints; stub/real behavior splits are tool-internal, never node-visible.
