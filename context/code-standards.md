# Code Standards

> Generated from Intake for SIH2026-PROTOTYPE. Implementation rules for every session. Format per context-references/code-standards.md. NOTE: all code below is written under the `ponytail` skill — this file shows the standard, the skill governs the practice.

---

## Engineering Mindset

- Think before implementing — verify against graph-design.md before writing a line.
- Scope is sacred: only the current feature, nothing "while we're here".
- Every feature testable immediately after implementation, before the next begins.
- Clean over clever: a junior developer must be able to follow the graph by reading graph.py and the node files top to bottom.
- Failures are expected and handled — a run degrades, never dies. Unverified events drop; missing weights disable one eye; missing GPS blanks coordinates. Zero crash paths.

---

## Python

- Python 3.12. Full type hints — every function signature complete; `mypy --strict` clean.
- Never `Any` — use `object` and narrow, or proper generics.
- Pydantic v2 models for: state, tool args/results, inspector verdict. Never raw dicts where a model applies.
- Sync tools and nodes throughout (CPU-bound cv2/ultralytics work; LangGraph handles sync nodes natively). No async theater.
- `const`-style module constants in `config.py` — UPPER_SNAKE, typed.
- No default mutable args; no star-imports; ruff clean.
- Heavy imports (ultralytics, cv2, supervision, openai, folium, streamlit) are lazy — imported inside the tool function that needs them, so the package imports and tests run without the full stack.

---

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Nodes | lower_snake verb-or-role | `ingest`, `detect`, `track_flag`, `verify_event`, `gate`, `publish` |
| Tools | lower_snake verb | `extract_frames`, `yolo_street`, `vlm_inspect`, `store_upsert_events` |
| State models | PascalCase + role suffix | `RoadfixState`, `VerifyPayload`, `PublishedEvent` |
| Files | lower_snake, one node/tool per file | `ingest.py`, `frames.py` |
| Tests | mirror the source tree | `tests/tools/test_frames.py` |
| Constants | UPPER_SNAKE in config.py | `GATE_PUBLISH`, `MAX_VERIFY_ATTEMPTS`, `FRAMES_PER_SECOND` |

---

## Node Template

Every node follows this exact shape:

```python
# src/roadfix/nodes/detect.py
from roadfix.config import STREET_CONF
from roadfix.state import Detection, RoadfixState
from roadfix.tools.detectors import DetectArgs, yolo_pothole, yolo_street

def detect(state: RoadfixState) -> dict:
    """SPOT — run both YOLO eyes over every frame. Returns state UPDATE only."""
    detections: list[Detection] = []
    for frame in state.frames:
        street = yolo_street(DetectArgs(frame_path=frame.path, conf=STREET_CONF))
        pothole = yolo_pothole(DetectArgs(frame_path=frame.path, conf=POTHOLE_CONF))
        detections.extend(street.detections)
        detections.extend(pothole.detections)
    return {"detections": detections}
```

Rules: typed state in, partial-dict state out. No mutation of `state`. No model/prompt literals. Failure handling lives inside tools; nodes compose results. A node that needs per-item error isolation loops over items and accumulates.

## Worker Template (Send target)

```python
# src/roadfix/nodes/verify_event.py
from roadfix.state import VerifyPayload
from roadfix.tools.vlm import InspectArgs, vlm_inspect

def verify_event(payload: VerifyPayload) -> dict:
    """DOUBLE-CHECK worker — one VLM call for one event. Verdict appended via reducer."""
    result = vlm_inspect(InspectArgs(event=payload.event, attempt=payload.attempt))
    return {"verdicts": [result.to_verdict(payload.event.event_id, payload.attempt)]}
```

Rules: the worker parameter is the Send payload (worker-scoped), NOT the graph state. Returns a single-item list — the `operator.add` reducer on `verdicts` merges it.

---

## Tool Template

Every tool follows this exact shape:

```python
# src/roadfix/tools/frames.py
from roadfix.state import FrameRef
from pydantic import BaseModel, Field

class ExtractFramesArgs(BaseModel):
    video_path: str
    out_dir: str
    fps: float = Field(2.5, gt=0, le=5)

class ExtractedFrames(BaseModel):
    frames: list[FrameRef]
    duration_s: float
    ok: bool
    error: str | None = None

def extract_frames(args: ExtractFramesArgs) -> ExtractedFrames:
    """WATCH — chop video into snapshot jpgs at fps. Never raises."""
    try:
        import cv2  # lazy heavy import
        ...
    except Exception as exc:  # boundary — translate, never propagate
        return ExtractedFrames(frames=[], duration_s=0.0, ok=False, error=f"{type(exc).__name__}: {exc}")
```

Rules: Pydantic args in, Pydantic result out. `ok` + `error` fields over exceptions — tools never raise past the boundary. Empty/missing inputs are valid degraded results, clearly marked. Heavy imports lazy. Logging via `logging` module, one line per significant event, prefix `[tool-or-node-name]`. Never `print` in src/ (CLI scripts may print).

---

## State Update Rules

- Nodes return partial dicts of ONLY the keys they own.
- `verdicts` appends go through `Annotated[list[Verdict], operator.add]` — a worker returning a full rebuilt list is a bug.
- Overwrite fields are owned by exactly one node each (see graph-design.md topology table).
- Workers receive `VerifyPayload`, never the graph state.
- Bulk data (frames, snapshots) lives on disk; state holds paths. Frames are never base64'd into state or checkpoints.

---

## Error Handling

- Tools return `ok=False` + `error` string on failure — they never raise past the boundary.
- Nodes let tool results carry failure state; node-level try/except only where composition itself can fail (e.g. rule engine), and it translates to empty/degraded state, never a raise.
- Logging: `logging`, one line per significant event, prefix `[node-or-tool-name]`. Never `print`.
- User-facing surfaces (CLI, dashboard) never see raw tracebacks — mapped to `{status, error}` summaries.
- No empty `except:` blocks — ever. Catch `Exception`, log, translate.

---

## Config Constants

Single source in `config.py`; import everywhere; never redeclare:

```python
FRAMES_PER_SECOND: float = 2.5
STREET_CONF: float = 0.35
POTHOLE_CONF: float = 0.30
POTHOLE_IOU_MATCH: float = 0.4
POTHOLE_MIN_CONSECUTIVE: int = 2
KIDS_DWELL_S: float = 2.0
KIDS_SMALL_FRACTION: float = 0.25
KIDS_ROAD_BAND: tuple[float, float] = (0.55, 1.0)
JAM_WINDOW_S: float = 30.0
JAM_MIN_VEHICLES: int = 12
GATE_PUBLISH: float = 0.7
GATE_RECHECK: float = 0.4
MAX_VERIFY_ATTEMPTS: int = 2
VLM_TIMEOUT_S: float = 10.0
RECURSION_LIMIT: int = 50
DEFAULT_MAP_CENTER: tuple[float, float] = (17.3850, 78.4867)  # Hyderabad
```

---

## Dependencies (closed list)

`langgraph`, `langgraph-checkpoint-sqlite`, `pydantic`, `ultralytics`, `supervision`, `opencv-python-headless`, `numpy`, `openai`, `folium`, `streamlit`, `pytest`, `ruff`, `mypy`.

Nothing else without updating this list and `pyproject.toml` in the same commit.

---

## Comments and Docstrings

- One-line docstring per node/tool stating responsibility — matches its registry entry.
- Comments only for WHY (a non-obvious decision), never WHAT.
- No TODO comments in pushed code — TODOs become build-plan features or are dropped.

---

## Testing Standards

- pytest; external services and models stubbed via `ROADFIX_STUB_MODELS=1` and endpoint fixtures — no real weights or endpoints in unit/CI runs.
- Every tool: contract tests per its registry error behavior (success, degraded, failure).
- Every node: state-in → state-update-out tests, including the failure path.
- E2E: golden cases G1–G3 from eval-plan.md, marked `@pytest.mark.e2e`.
- Coverage target: every node and tool has at least its happy path + one failure path tested. No percentage chasing.
