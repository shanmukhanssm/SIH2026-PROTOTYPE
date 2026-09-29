# SIH2026-PROTOTYPE (RoadFix) — Complete Project Guide

**Project**: RoadFix — Smart India Hackathon 2026 (problem SIH26124)
**Type**: LangGraph workflow (NOT a free-form agent, per ADR-001)
**Stack**: Python 3.12 · LangGraph ≥ 0.4 · ultralytics YOLOv8 · supervision ByteTrack · OpenAI-compatible VLM (Qwen2.5-VL) · SQLite · Folium
**Repo**: https://github.com/shanmukhanssm/SIH2026-PROTOTYPE
**Guide generated**: 2026-09-29

---

## 1. TL;DR — the 30-second pitch

A **public bus dashcam video** goes in. A **sorted, verified road-work list** comes out.

The pipeline: chop video into frames → run two pre-trained YOLO "eyes" (COCO street + RDD2022 pothole) → ByteTrack-tracks vehicles → fire three deterministic rules (`POTHOLE`, `KIDS_CROSSING`, `TRAFFIC_JAM`) → fan each flagged event out to a Qwen2.5-VL inspector that fills a strict-JSON verdict form → apply a deterministic confidence gate (publish / recheck-once / drop) → upsert accepted events into SQLite + render a Folium heatmap + a markdown report.

Every node is **resilient**: unreadable video, missing GPS, missing YOLO weights, missing VLM endpoint, rate-limited API — all produce honest empty/error verdicts, never exceptions. The run is checkpointed via `SqliteSaver` so a crash resumes from the last super-step.

---

## 2. Project Identity

| Field | Value |
|---|---|
| **Name** | RoadFix (repo: SIH2026-PROTOTYPE) |
| **Built for** | Smart India Hackathon 2026, problem code SIH26124 |
| **Domain** | Road infrastructure / civic tech |
| **Pattern** | Single-graph LangGraph workflow (ADR-001: "workflow, not agent") |
| **Agency dial** | L0-L1 (no tool-calling loops; deterministic routing only) |
| **Phases done** | 0 (skeleton), 1 (tools), 2 (real nodes + 118 tests passing) |
| **Phases pending** | 3 (real wiring + CLI + Streamlit dashboard + resume integrity), 4 (eval runner + golden datasets) |
| **Source size** | 22 .py files, 2,432 LOC in `src/roadfix/` |
| **Docs size** | 11 .md files, 1,890 LOC in `context/` |
| **Tests** | 13 files, 113 static `def test_*`, ~118 at runtime (parametrized) |
| **Skills** | 12 AI-systems skill packages in `skills/` (DESIGN → PLAN → BUILD → HARDEN → OPERATE lifecycle) |

---

## 3. The Flow — 6-node state graph

### 3.1 ASCII topology

```
START
  │
  ▼
ingest ────────────► detect ───────────► track_flag
                                              │
                            ◆ route_to_verify ◆  (conditional edge)
                            one Send per event OR "publish" if events == []
                                              │
                                              ▼
                                    verify_event   (Send worker, parallel)
                                              │
                                              │ (static fan-in edge)
                                              ▼
                                            gate
                                              │
                            ◆ route_after_gate ◆  (conditional edge)
                            re-Send recheck-band events (attempt+1 < MAX) OR "publish"
                                              │
                                              ▼
                                           publish ───► END
```

### 3.2 Node-by-node breakdown

| Node | Type | Models / External | Tools consumed | Reads from state | Writes to state |
|---|---|---|---|---|---|
| **ingest** | WATCH node | — | `extract_frames`, `gps_sync` | `video_path`, `gps_track_path`, `run_id` | `frames` (overwrite) |
| **detect** | SPOT node | yolov8n.pt (COCO) + RDD2022 YOLO `.pt` | `yolo_street`, `yolo_pothole` | `frames` | `detections` (overwrite) |
| **track_flag** | TRACK & FLAG node | ByteTrack (supervision) | `bytetrack_tracks`, `save_event_snapshot` | `frames`, `detections` | `events`, `track_summary` (overwrite) |
| **verify_event** | **Send worker** (fan-out) | Qwen2.5-VL (OpenAI-compatible) | `vlm_inspect` | Send payload `{event, attempt}` | `verdicts` (**reducer: `operator.add`**) |
| **gate** | GATE node | — | — | `verdicts` | `gate_decisions` (overwrite — recomputed wholesale each pass) |
| **publish** | SHOW node | — | `store_upsert_events`, `build_map`, `build_report` | `events`, `verdicts`, `gate_decisions`, `frames`, `run_id` | `published`, `dropped_count`, `map_path`, `report_path`, `status` |

### 3.3 The three pure rules in `track_flag`

1. **POTHOLE** — static objects, no tracker. Same box (IoU ≥ `POTHOLE_IOU_MATCH=0.4`, inclusive) on ≥ `POTHOLE_MIN_CONSECUTIVE=2` consecutive timeline frames → fires once per physical pothole (deduped).
2. **KIDS_CROSSING** — uses tracker IDs. A `person` (street eye class) with `track_id`, dwell > `KIDS_DWELL_S=2.0s` (strict), small (`median bbox-height / frame-height < KIDS_SMALL_FRACTION=0.25`, strict), bbox center y inside `KIDS_ROAD_BAND=(0.55, 1.0)` (both ends inclusive) → fires.
3. **TRAFFIC_JAM** — uses tracker IDs. Sliding window `JAM_WINDOW_S=30.0s`, unique tracked vehicles strictly > `JAM_MIN_VEHICLES=12` in any window → fires.

### 3.4 The deterministic gate (`gate.py`)

Two-pass rule engine over the verdicts:

- **First pass**: for each event's latest verdict:
  - `confirmed=True & confidence ≥ GATE_PUBLISH=0.7` → `first_pass_publish`
  - `confirmed=True & GATE_RECHECK=0.4 ≤ conf < GATE_PUBLISH` → `first_pass_recheck` (re-Send with `attempt+1`)
  - `confirmed=True & conf < GATE_RECHECK` → `drop_low_conf`
  - `confirmed=False` → `drop_unconfirmed`
- **Final pass**: events still in the recheck band after `MAX_VERIFY_ATTEMPTS=2` exhausted:
  - `confirmed=True & conf ≥ GATE_PUBLISH` → `final_pass_publish`
  - else → `final_pass_drop`

`route_after_gate` re-Sends only `first_pass_recheck` events whose `attempt+1 < MAX_VERIFY_ATTEMPTS`; everything else routes to `publish`. The cycle is bounded by `MAX_VERIFY_ATTEMPTS=2` plus the `RECURSION_LIMIT=50` backstop applied by the CLI.

### 3.5 Why a Send fan-out + reducer pattern

The `verify_event` worker runs **one VLM call per event**, which is the slowest, most expensive step. LangGraph's `Send` API lets all events be inspected in parallel — every worker writes its verdict to the same `verdicts` channel, which has a custom reducer `Annotated[list[Verdict], operator.add]` so the verdicts accumulate (rather than overwrite). The static edge `verify_event → gate` is the fan-in guarantee: `gate` is scheduled only AFTER all parallel workers finish.

---

## 4. State Schema — `RoadfixState` (`state.py`)

Eight Pydantic v2 models:

| Model | Purpose |
|---|---|
| `FrameRef` | One extracted frame: `frame_id` (basename like `f00015.jpg`), `path`, `t_seconds`, optional `lat`/`lon` |
| `Detection` | One YOLO box: `frame_id`, `source` (`"street"` \| `"pothole"`), `class_name`, `conf`, `bbox=[x1,y1,x2,y2]` |
| `Event` | One flagged road issue: `event_id`, `kind` (`POTHOLE` \| `KIDS_CROSSING` \| `TRAFFIC_JAM`), `frame_id`, `t_seconds`, optional `lat`/`lon`, `snapshot_path`, `bbox`, `track_id`, `evidence` dict |
| `Verdict` | One inspector decision: `event_id`, `kind`, `attempt` (0=first look, 1=recheck), `confirmed`, `severity`, `confidence` [0.0-1.0], `reason`, `verifier` (model name or `"stub"` or `"error:<code>"`) |
| `GateDecision` | One gate outcome: `event_id`, `action` (`publish` \| `recheck` \| `drop`), `rule` (one of 6 rule strings), `attempt`, `confidence_at_decision` |
| `PublishedEvent` | One accepted event bound for the SQLite store: `event_id`, `kind`, `t_seconds`, `lat`, `lon`, `snapshot_path`, `severity`, `confidence`, `verifier`, `reason` |
| `RoadfixState` | The graph state — see below |
| `VerifyPayload` | The Send payload: `{event: Event, attempt: int}` |

### `RoadfixState` fields

| Field | Type | Reducer | Writer | Default |
|---|---|---|---|---|
| `video_path` | `str` | overwrite | caller (initial state) | (required) |
| `gps_track_path` | `str \| None` | overwrite | caller | `None` |
| `run_id` | `str` | overwrite | caller | (required) |
| `frames` | `list[FrameRef]` | overwrite | `ingest` | `[]` |
| `detections` | `list[Detection]` | overwrite | `detect` | `[]` |
| `events` | `list[Event]` | overwrite | `track_flag` | `[]` |
| `track_summary` | `dict` | overwrite | `track_flag` | `{}` |
| **`verdicts`** | `list[Verdict]` | **`operator.add`** | `verify_event` (Send workers) | `[]` |
| `gate_decisions` | `list[GateDecision]` | overwrite | `gate` | `[]` |
| `published` | `list[PublishedEvent]` | overwrite | `publish` | `[]` |
| `dropped_count` | `int` | overwrite | `publish` | `0` |
| `map_path` | `str \| None` | overwrite | `publish` | `None` |
| `report_path` | `str \| None` | overwrite | `publish` | `None` |
| `status` | `Literal["running","done","failed"]` | overwrite | `publish` | `"running"` |

The only non-trivial reducer is `verdicts` (operator.add) — every other field is overwrite, owned by exactly one writer node (the "writer audit" rule from `graph-design.md`).

---

## 5. File-by-File Description

### 5.1 Root config & metadata

| File | Purpose |
|---|---|
| `pyproject.toml` | PEP 621 project metadata + closed dep list (`langgraph`, `langgraph-checkpoint-sqlite`, `ultralytics`, `supervision`, `opencv-python-headless`, `openai`, `pydantic`, `folium`, `streamlit`) + `ruff`/`mypy --strict`/`pytest` config. Python ≥ 3.12. |
| `langgraph.json` | LangGraph CLI manifest: declares the graph at `roadfix.graph:graph` so `langgraph dev` finds it. |
| `.env.example` | Template env file with all `VLM_*`, `POTHOLE_MODEL_PATH`, `YOLO_STREET_WEIGHTS`, `ROADFIX_DATA_DIR`, `ROADFIX_STUB_MODELS`, `FRAMES_PER_SECOND`, `POTHOLE_CONF`, `VLM_TIMEOUT_S`, `VLM_JSON_MODE`. Copy to `.env` and fill in. |
| `AGENTS.md` | 412-LOC AI-systems workflow kernel: 12 skill packages (DESIGN→PLAN→BUILD→HARDEN→OPERATE lifecycle), governing skills (`planning-and-task-breakdown`, `ponytail`), context file canonical names, format spec compliance. |
| `FORMAT_SPEC.md` | 90-LOC compliance spec for skill files: frontmatter schema, ≤400 LOC per SKILL.md, references/ one hop deep, trigger phrases + sibling exclusions. |
| `run_video.py` | **Operational CLI runner** (193 LOC). Wraps `build_graph` + `SqliteSaver` + a final-state pretty-printer. Supports 4 modes: stub / real-detect / full real with xkiro / full real with custom VLM. **NOTE**: `scripts/run_pipeline.py` is referenced throughout `context/*.md` as the future CLI but is NOT YET BUILT (Phase 3 feature 12). Use `run_video.py` for now. |
| `.gitignore` | Standard Python ignores: `__pycache__`, `.venv`, `data/runs/`, `.env`, `*.pt` weights, etc. |

### 5.2 Source code — `src/roadfix/` (22 .py files, 2,432 LOC)

#### `__init__.py` (4 LOC)
Package marker + docstring ("workflow, not agent per ADR-001").

#### `config.py` (145 LOC)
**Single source of truth** for:
- **19 constants** (detection/rule thresholds, model policy, run limits)
- **11 call-time reader functions** (env-overridable — see §7)
- **6 import-time snapshots** (derived from readers; backward-compat for tests)
- **4 path helpers** (`data_dir`, `runs_dir`, `checkpoint_db`, `events_db`)

#### `state.py` (109 LOC)
All 8 Pydantic v2 state models (see §4). The `verdicts` field has a custom reducer annotation `Annotated[list[Verdict], operator.add]` — the only non-overwrite field.

#### `graph.py` (92 LOC)
The ONLY root-graph wiring file. Exposes:
- `route_to_verify(state) -> list[Send] | str` — conditional edge after `track_flag`
- `route_after_gate(state) -> list[Send] | str` — conditional edge after `gate`
- `build_graph(checkpointer=None) -> StateGraph` — assembles `START → ingest → detect → track_flag → route_to_verify → verify_event → gate → route_after_gate → publish → END`
- Module-level `graph = build_graph()` — for `langgraph.json` discovery

#### `nodes/` (6 node files, 822 LOC total)

| File | LOC | Node | Key abstractions |
|---|---:|---|---|
| `nodes/ingest.py` | 50 | WATCH | `ingest(state) -> dict[str, object]` — calls `extract_frames` + `gps_sync`; degrades to `frames=[]` on failure |
| `nodes/detect.py` | 43 | SPOT | `detect(state)` — runs both YOLO eyes per frame; per-eye degradation composes; uses `pothole_conf()` (env-overridable) |
| `nodes/track_flag.py` | 528 | TRACK & FLAG | Three pure functions: `pothole_rule`, `kids_crossing_rule`, `traffic_jam_rule`; plus ByteTrack + `save_event_snapshot`; `track_flag(state)`. Dataclasses: `PotholeCandidate`, `KidsCandidate`, `JamCandidate`, `_PotholeChain`. `VEHICLE_CLASSES` frozenset = `{car, bus, truck, motorcycle}`. |
| `nodes/verify_event.py` | 90 | DOUBLE-CHECK (Send worker) | `verify_event(payload: VerifyPayload)` — calls `vlm_inspect`; boundary translates any unexpected exception to an `error:worker_exception` verdict |
| `nodes/gate.py` | 84 | GATE | Two-pass rule engine: `_latest_by_event`, `_first_pass_rule`, `_final_pass_rule`, `gate(state)`. 6 rule strings. |
| `nodes/publish.py` | 27 | SHOW (Phase 0 stub) | `publish(state)` — sets `status="done"`, returns fake `map_path`/`report_path` (real wiring to `store_upsert_events`/`build_map`/`build_report` is Phase 3 feature 12) |

#### `prompts/` (2 files, 53 LOC)

| File | LOC | Purpose |
|---|---:|---|
| `prompts/__init__.py` | 2 | docstring |
| `prompts/inspector.py` | 51 | Version-pinned inspector prompt constants: `INSPECTOR_V1` (the v1 system prompt, includes the strict-JSON verdict schema), `CORRECTION_TEMPLATE` (re-ask on invalid JSON), `_VERDICT_JSON_LINE` (the JSON example, braces doubled for `str.format`) |

#### `tools/` (8 tool files, 1,318 LOC total)

| File | LOC | Tool(s) | Key abstractions |
|---|---:|---|---|
| `tools/frames.py` | 163 | `extract_frames`, `gps_sync` | `ExtractFramesArgs` (with `fps=Field(2.5, gt=0, le=30)`), `extract_frames`; `GpsSyncArgs`, `gps_sync`; helpers `_read_gps_csv`, `_nearest`, `_copy_all` |
| `tools/detectors.py` | 243 | `yolo_street`, `yolo_pothole` | `STREET_CLASS_IDS={0,1,2,3,5,7}` (COCO person/bicycle/car/motorcycle/bus/truck); `DetectArgs`/`DetectResult`; 4 Protocol types for mypy --strict without requiring ultralytics at import time; `_load_model` (cached `_CACHE` dict); `_pothole_weights` (returns `None` if `POTHOLE_MODEL_PATH` unset → pothole eye disabled); stub mode (`_STUB_*` constants, `_StubRow/_StubBox/_StubResults`, `_stub_results` derives boxes from sha256 of frame FILENAME); `_collect` (post-predict pipeline shared by real+stub); `_detect`, `yolo_street`, `yolo_pothole` |
| `tools/tracker.py` | 83 | `bytetrack_tracks` | `TrackArgs`/`TrackResult`/`bytetrack_tracks`; two-pointer bbox match back to source `Detection`s (supervision 0.30.3 drops unmatched rows silently) |
| `tools/snapshot.py` | 84 | `save_event_snapshot` | `_JPEG_QUALITY=85`; `SnapshotArgs`/`SnapshotResult`; `_clamp_bbox` (so crops don't go off-frame); writes both `_full.jpg` and `_crop.jpg` when bbox provided |
| `tools/vlm.py` | 368 | `vlm_inspect` | `_MAX_MODEL_ATTEMPTS=2`; `InspectArgs`/`InspectResult`/`InspectorVerdict`; `_STUB_VERDICTS` (per-kind table for stub mode); `_EndpointError`; helpers: `_error_result`, `_evidence_text`, `_snapshot_data_url` (base64 data URL), `_get_client` (test seam — fresh OpenAI client per call), `_user_message`, `_raw_call`, `_transient_code` (maps `openai.APITimeoutError/APIConnectionError/RateLimitError/InternalServerError` to greppable codes), `_chat_once` (json-mode auto-fallback on `BadRequestError` + 1 transient retry), `_validate_reply`, `_stub_verdict`, `_inspect_via_endpoint`, `vlm_inspect` |
| `tools/store.py` | 192 | `store_upsert_events`, `store_query_events` | SQLite system of record. `_CREATE_TABLE`/`_INSERT_SQL`/`_SELECT_SQL`; `UpsertArgs`/`UpsertResult`/`StoreQueryArgs`/`StoredEvent`/`QueryResult`; `_derive_run_id(event_id, kind)` (prefix before first `-{kind}-`); `store_query_events` opens read-only via `?mode=ro` URI |
| `tools/map_builder.py` | 129 | `build_map` | `MapArgs`/`MapResult`; `_popup_html`; `_rel_snapshot_events` (resolves relative snapshot paths for photo popups); `_render_map` (pure assembly — Folium HeatMap + markers); `build_map` |
| `tools/report.py` | 125 | `build_report` | `_SEVERITY_RANK` (high>medium>low ordering); `ReportArgs`/`ReportResult`; `_summary_lines`, `_event_table`, `_caveat_lines`, `_render_markdown`, `build_report` |

### 5.3 Context docs — `context/` (11 .md files, 1,890 LOC)

| File | LOC | Purpose |
|---|---:|---|
| `context/project-overview.md` | 109 | One-page product spec: About / Problem / Triggers / One Perfect Run / Inputs & Outputs / Scope: In-Out / HITL (none) / Success Criteria / Target User |
| `context/architecture.md` | 180 | Stack (12 layers), folder tree, system boundaries, data-flow ASCII diagram, external services, persistence (3 stores: checkpoint SQLite + events SQLite + run artifacts), env vars, 10 invariants |
| `context/graph-design.md` | 224 | **THE CONTRACT** — topology ASCII, Topology Table (6 nodes), Conditional Edge Table, full Pydantic State Schema verbatim, Reducer Rules + writer audit, 6 Node Specs, HITL contract, Run Limits |
| `context/build-plan.md` | 170 | 15-feature ladder across 5 phases: Phase 0 (01-02: scaffold + stub), Phase 1 (03-08: tools), Phase 2 (09-11: nodes), Phase 3 (12-14: wiring + resume + dashboard), Phase 4 (15: eval runner), Feature Count, Standing Rules |
| `context/code-standards.md` | 182 | Engineering mindset, Python 3.12 rules, Naming table, Node/Worker/Tool Templates, State Update Rules, Error Handling, Config Constants verbatim, closed dep list, Testing Standards |
| `context/library-docs.md` | 238 | Per-library usage patterns: LangGraph (assembly/Send/checkpointer/test invariants), ultralytics (lazy import, COCO class filter), supervision ByteTrack (deprecated since 0.28), OpenAI-compatible VLM (image + response_format:json_object), opencv (VideoCapture/VideoWriter/imwrite), folium (HeatMap + Marker + photo popup), Streamlit, pytest |
| `context/tool-registry.md` | 379 | **LIVING FILE** — closed list of 10 tools with full Pydantic signatures + property tables (timeout/retry/error/stub) + Rules |
| `context/prompt-registry.md` | 73 | **LIVING FILE** — model policy (inspector/street/pothole) + version-pinned prompts; `inspector` v1 entry (verbatim prompt + correction message); version-bump policy |
| `context/eval-plan.md` | 132 | Layered eval philosophy: Layer 1 (tools: 6+5+6+4+4+10), Layer 2 (rules 6 + verify→gate 6), Layer 3 (G1-G3 golden + 4 graders), Real-model evals, Gate mapping, Regression/Flakiness rules |
| `context/progress-tracker.md` | 130 | **LIVING FILE** — current status (Phase 2 done, 118 tests passing), Phase checklists (01-11 [x], 12-15 [ ]), 10 intake decisions, Phase 2 detail (langgraph 1.2 behavior notes), Phase 1 detail (supervision 0.30.3 quirks), Merge block, Phase 0 detail |
| `context/adr-001-architecture.md` | 54 | ADR — "workflow, not agent": CONTEXT, DECISION (pattern stack, single graph, agency dial per edge, framework, granularity, deployment), ALTERNATIVES CONSIDERED, CONSEQUENCES (+/−, Budgets table per 10s clip), EVIDENCE (pending) |

**`context-references/`** (10 .md files + README) is a **read-only mirror** of `context/` for format reference only — `context/` is the authoritative source of project truth.

### 5.4 Tests — `tests/` (13 files, 113 static `def test_*`, ~118 at runtime)

| File | Layer | # tests | What it covers |
|---|---|---:|---|
| `tests/conftest.py` | (shared fixtures) | 0 | `stub_env` (monkeypatches env), `make_clip` (real tiny mp4 via cv2 VideoWriter), `make_image` (real tiny jpg via cv2 imwrite) |
| `tests/test_config.py` | unit | 3 | Constants match code-standards; `data_dir()` default+override; `runs_dir/checkpoint_db/events_db` paths |
| `tests/e2e/test_stub_run.py` | **e2e** (`@pytest.mark.e2e`) | 2 | Planted 13 cars + 2-frame pothole → TRAFFIC_JAM + POTHOLE fire once; stub verdicts publish; **WRITER_AUDIT dict asserted per node write**; `len(verdicts)==len(events)` race invariant; honest empty-run path (missing video → zero everything → `status="done"`; reducer-channel `verdicts` materializes default but `gate_decisions` stays ABSENT) |
| `tests/nodes/test_ingest.py` | unit (node) | 6 | Happy path; missing video → `[]`; corrupt video → `[]`; GPS sync clean CSV; malformed CSV → frames extracted, lat/lon None; state not mutated |
| `tests/nodes/test_detect.py` | unit (node) | 5 | Stub happy path (both eyes, "cat" filtered); empty frames → `[]`; degraded eyes (`sys.modules["ultralytics"]=None` + no `POTHOLE_MODEL_PATH`) → `[]` no raise; verbatim composition; state not mutated |
| `tests/nodes/test_track_flag.py` | unit (node + pure rules) | 30 | **6 eval cases** + 9 boundary + 15 pure-rule tests. Pins IoU≥0.4 inclusive, dwell>2.0 strict, smallness<0.25 strict, jam>12 strict; review MAJOR-1 regression `test_jam_rule_qualifying_window_far_from_first_appearances` |
| `tests/nodes/test_verify_gate.py` | unit (gate + worker) | 17 | 12 gate cases (publish/recheck-then-publish/recheck-then-drop/drop-first-pass/verifier-error/unconfirmed-high/boundary-matrix parametrized 3×/final-pass-recheck-band/duplicate-verdicts/recomputed-not-accumulated/route-after-gate-consistency/race-invariant-reducer-merge); 5 worker cases (stub verdict per kind/timeout→`error:timeout`/raw-dict payload/only-`verdicts`-key/never-raises-on-garbage). Uses `_FakeClient`/`_FakeCompletions` pattern + `route_after_gate` bidirectional consistency |
| `tests/tools/test_frames.py` | unit (tool) | 11 | `extract_frames` 4 parametrized stride counts + corrupt + missing; `gps_sync` 8 cases (no csv/clean/malformed/gap/exact/missing file/unparseable/non-mutation) |
| `tests/tools/test_detectors.py` | unit (tool) | 6 | Street stub deterministic by filename; class filter; conf threshold; degraded eyes (street no ultralytics, pothole no weights); pothole stub deterministic |
| `tests/tools/test_tracker.py` | unit (tool) | 6 | Car A stable ID; Car B different ID; Person C one-frame unmatched but tracked when seen 2 frames; tracker failure → untracked no raise; inputs not mutated; empty input |
| `tests/tools/test_snapshot.py` | unit (tool) | 4 | With bbox writes full+crop; without bbox skips crop; unreadable frame → `ok=False`; bbox partially outside clamped (crop==full shape) |
| `tests/tools/test_vlm.py` | unit (tool) | 9 | Valid JSON returns scripted verdict; malformed → corrective re-ask → `error:invalid_json` (cap 2); timeout → `error:timeout`; rate-limit recovers; stub mode per kind (no network/file); json-mode auto fallback on `BadRequestError`; json-mode off never sends; no-endpoint → `error:no_endpoint`; unreadable snapshot → `error:snapshot_unreadable` |
| `tests/tools/test_store.py` | unit (tool) | 8 | Insert roundtrip; replace same id; query filters (run_id/kind/combined/limit, `ORDER BY t_seconds ASC`); db unwritable; replay idempotent; NULL gps roundtrip; empty events creates db; corrupt db → `ok=False` |
| `tests/tools/test_outputs.py` | unit (tool) | 6 | Map with GPS (HeatMap+2 markers); map without GPS (fallback+1 marker); map empty; report full (counts by kind+severity, dropped, vehicles, per-class, gps, photo link, verifier, severity-ordered 3 rows); report empty; report severity ordering (high→high→medium→low, confidence DESC) |

### 5.5 Skills — `skills/` (12 skill packages)

The `skills/` directory is the project's AI-systems workflow kernel. Each skill is a `SKILL.md` (≤400 LOC) + `references/` folder (one hop deep). Loaded in DESIGN → PLAN → BUILD → HARDEN → OPERATE lifecycle order.

| Skill | Stage | 1-line purpose |
|---|---|---|
| `planning-and-task-breakdown` | **GOVERNING** (PLAN) | Decomposes work into small, verifiable tasks with acceptance criteria; loaded BEFORE writing/extending `build-plan.md` |
| `ponytail` | **GOVERNING** (ALL) | Forces the laziest solution that works: YAGNI, stdlib first, minimal code (lite/full/ultra ladder); applied to every line of code |
| `agent-architecture-advisor` | DESIGN | Decides WHAT agent system to build before any code: whether to build, agentic pattern, single vs multi, topology, framework, cost/latency/reliability budget — produces an ADR |
| `langgraph-builder` | BUILD | Writes production-correct single-graph LangGraph code: StateGraph, typed state+reducers, nodes, static+conditional edges, Send fan-out/fan-in, Command routing, checkpointers, streaming |
| `agent-tool-designer` | BUILD | Designs tool interfaces: signatures, four-part descriptions, parameter schemas, error contracts, idempotency keys, system prompts, structured-output schemas |
| `multi-agent-builder` | BUILD | Implements supervisor/handoff/hierarchical/network/event-driven topologies, supervisor prompt + done-tracker, A2A/MCP interop, failure detection |
| `hitl-builder` | BUILD | Adds human approval/edit/review via `interrupt()` + `Command(resume=...)`, payload spec, async queues, audit trails, 15-point review |
| `agent-memory-builder` | BUILD | Designs agent memory: taxonomy, Store namespaces, extraction prompts, write/consolidate/forget policies, retrieval, privacy + tenant isolation, benchmark suite |
| `agent-reliability-hardener` | HARDEN | Hardens a working agent for production failure: failure taxonomy, idempotency keys, retries w/ jittered backoff, fallback ladders, circuit breakers, durable execution, SLOs, CI fault-injection |
| `agent-guardrails-builder` | HARDEN | Adds the safety/security layer: threat model, prompt-injection defense, tool security, output moderation, PII, rate limiting, audit logging, red-team cadence |
| `agent-eval-builder` | HARDEN | Builds the eval system: layered taxonomy, golden dataset curation, trajectory checks, calibrated LLM-as-judge, CI gates with power math, red-team suites |
| `agent-debugger` | OPERATE | Wires observability + responds to incidents: 6-span instrumentation, structured logs, 4 core metrics, 10-minute triage, symptom router, 16 incident runbooks, latency audit |

### 5.6 Empty placeholder directories

| Directory | Purpose (planned, not yet implemented) |
|---|---|
| `data/inbox/` | Drop dashcam clips here for ingestion |
| `data/runs/` | Per-run output artifacts (frames, snapshots, map.html, report.md, checkpoints.sqlite, events.db) |
| `evals/datasets/` | Phase 4: golden datasets for the eval runner |
| `evals/graders/` | Phase 4: LLM-as-judge graders for the eval runner |
| `scripts/` | Phase 3 feature 12: `run_pipeline.py` (the future operational CLI; for now use `run_video.py` at repo root) |
| `ui/` | Phase 3 feature 14: Streamlit dashboard for browsing published events |

---

## 6. Environment Variables

| Env var | Default | What it controls | Read by |
|---|---|---|---|
| `VLM_BASE_URL` | `None` | OpenAI-compatible endpoint URL (vLLM/ollama/OpenRouter/DashScope/xkiro/etc.); unset + stub off → `error:no_endpoint` | `config.vlm_base_url()` → `tools/vlm.py::_get_client` |
| `VLM_API_KEY` | `None` | Endpoint API key; ollama accepts any placeholder; unset → `error:no_endpoint` | `config.vlm_api_key()` → `tools/vlm.py::_get_client` |
| `VLM_MODEL` | `qwen2.5-vl-7b-instruct` (via `DEFAULT_VLM_MODEL`) | VLM model id; returned as `verifier` on success | `config.vlm_model()` → `tools/vlm.py::_inspect_via_endpoint` |
| `VLM_JSON_MODE` | `auto` | `auto` = send `response_format={"type":"json_object"}` then auto-fallback on `BadRequestError`; `off` = never send | `config.vlm_json_mode()` → `tools/vlm.py::_inspect_via_endpoint` |
| `VLM_TIMEOUT_S` | `30.0` | Per-request HTTP timeout (s); generous for free-tier gateways like xkiro (3-8s/req + retries + JSON-mode fallback) | `config.vlm_timeout_s()` → `tools/vlm.py::_get_client` |
| `POTHOLE_MODEL_PATH` | `None` | Path to RDD2022-trained `.pt` file; unset/missing → pothole eye **DISABLED** (street eye still runs) | `config.pothole_model_path()` → `tools/detectors.py::_pothole_weights` |
| `YOLO_STREET_WEIGHTS` | `yolov8n.pt` (via `DEFAULT_STREET_WEIGHTS`) | Street-eye weights filename (auto-downloaded by ultralytics on first use, ~6 MB) | `config.street_weights()` → `tools/detectors.py::_load_model` |
| `ROADFIX_DATA_DIR` | `data` (relative) | Root data directory; override for tests/isolation | `config.data_dir()` → all path helpers |
| `ROADFIX_STUB_MODELS` | `""` (off) | `1` → deterministic stub detections (sha256 of frame FILENAME) + stub verdicts (per-kind table); no GPU/network | `config.stub_models_enabled()` → `tools/detectors.py::_detect`, `tools/vlm.py::vlm_inspect` |
| `FRAMES_PER_SECOND` | `2.5` | Frame-sampling rate (fps); bump to 5 for urban traffic / 10-15 for highway speeds | `config.frames_per_second()` → `nodes/ingest.py::ingest` |
| `POTHOLE_CONF` | `0.30` | Pothole-eye YOLO confidence threshold; lower to 0.20-0.25 to catch weaker detections (VLM filters false positives downstream) | `config.pothole_conf()` → `nodes/detect.py::detect` |

---

## 7. Config Constants (`config.py`)

| Name | Value | What it controls |
|---|---|---|
| `FRAMES_PER_SECOND` | `2.5` | Default frame-sampling rate (fps) |
| `STREET_CONF` | `0.35` | Street-eye YOLO confidence threshold |
| `POTHOLE_CONF` | `0.30` | Pothole-eye YOLO confidence threshold |
| `POTHOLE_IOU_MATCH` | `0.4` | IoU threshold for pothole consecutive-frame chain (inclusive `>=`) |
| `POTHOLE_MIN_CONSECUTIVE` | `2` | Minimum consecutive timeline frames to fire POTHOLE |
| `KIDS_DWELL_S` | `2.0` | Minimum dwell time (s) in road band (strict `>`) |
| `KIDS_SMALL_FRACTION` | `0.25` | Max median bbox-height/frame-height ratio (strict `<`) |
| `KIDS_ROAD_BAND` | `(0.55, 1.0)` | Road band as fraction of frame height (bbox CENTER-y, both ends inclusive) |
| `JAM_WINDOW_S` | `30.0` | Sliding window length (s) for TRAFFIC_JAM |
| `JAM_MIN_VEHICLES` | `12` | Min unique tracked vehicles in window (strict `>`) |
| `GATE_PUBLISH` | `0.7` | First-pass confidence threshold for publish (confirmed & `>=`) |
| `GATE_RECHECK` | `0.4` | Recheck band lower bound (confirmed & `GATE_RECHECK ≤ conf < GATE_PUBLISH`) |
| `MAX_VERIFY_ATTEMPTS` | `2` | Cap on verify attempts per event (attempt+1 < MAX bounds `route_after_gate`) |
| `VLM_TIMEOUT_S` | `30.0` | Default VLM per-request HTTP timeout (s) |
| `RECURSION_LIMIT` | `50` | LangGraph `recursion_limit` backstop applied per-invoke by CLI |
| `DEFAULT_MAP_CENTER` | `(17.3850, 78.4867)` | Folium fallback center (Hyderabad) when no GPS |
| `DEFAULT_VLM_MODEL` | `"qwen2.5-vl-7b-instruct"` | VLM model id when `VLM_MODEL` env unset |
| `DEFAULT_STREET_WEIGHTS` | `"yolov8n.pt"` | Street-eye weights filename |
| `VLM_TEMPERATURE` | `0.0` | VLM sampling temperature (deterministic form-filling) |
| `VLM_MAX_TOKENS` | `200` | VLM max output tokens per call |

---

## 8. CLI Args — `run_video.py`

| Arg name | Type | Default | Description |
|---|---|---|---|
| `--video` | str (path) | (required) | Path to a dashcam `.mp4` file |
| `--run-id` | str | `"video-demo"` | Run identifier (used in `thread_id="video:{run_id}"`, run dir, `event_id` prefix) |
| `--data-dir` | str (path) | `None` → tempdir if env unset | Override `ROADFIX_DATA_DIR` |
| `--recursion-limit` | int | `None` → `RECURSION_LIMIT` (50) | Override LangGraph recursion limit |
| `--stub` | flag (mutex mode) | False | Stub BOTH detectors AND VLM (no GPU/network); sets `ROADFIX_STUB_MODELS=1` |
| `--real-detect` | flag (mutex mode) | False | Real YOLO + real VLM (needs ultralytics + VLM_* env); gate drops all events with `error:no_endpoint` if VLM unset — expected |
| `--vlm-base-url` | str | env `VLM_BASE_URL` | OpenAI-compatible VLM endpoint URL |
| `--vlm-api-key` | str | env `VLM_API_KEY` | VLM API key |
| `--vlm-model` | str | env `VLM_MODEL` or `qwen2.5-vl-7b-instruct` | VLM model id |
| `--vlm-timeout` | float | env `VLM_TIMEOUT_S` or `30.0` | Per-request timeout (s) |
| `--vlm-json-mode` | `auto`\|`off` | env `VLM_JSON_MODE` or `auto` | Send `response_format:json_object` (auto = with fallback on 400; off = never) |
| `--pothole-weights` | str (path) | env `POTHOLE_MODEL_PATH` | Path to a pothole YOLO `.pt`. **REQUIRED for pothole detection** — without it the pothole eye is disabled. Recommended: `keremberke/yolov8n-pothole-segmentation/best.pt` (single class `pothole`, 6.8 MB) from `https://huggingface.co/keremberke/yolov8n-pothole-segmentation/resolve/main/best.pt` |
| `--fps` | float | env `FRAMES_PER_SECOND` or `2.5` | Frame-sampling rate; bump to 5 urban / 10-15 highway |
| `--pothole-conf` | float | env `POTHOLE_CONF` or `0.30` | Pothole YOLO confidence threshold; lower to 0.20-0.25 to catch weaker detections |

---

## 9. Setup Commands — Windows PowerShell

```powershell
# 1. Clone the repo
cd C:\Users\<you>\Desktop
git clone https://github.com/shanmukhanssm/SIH2026-PROTOTYPE.git
cd SIH2026-PROTOTYPE

# 2. Create + activate a venv (Python 3.12 required; 3.11 works)
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
.\.venv\Scripts\Activate.ps1

# 3. Upgrade pip + install the project in editable mode WITHOUT pulling torch/CUDA
#    (this is the disk-constrained install path; for the full install see §9.1)
python -m pip install --upgrade pip
pip install -e . --no-deps

# 4. Install only the stub-mode deps + dev tooling
pip install langgraph langgraph-checkpoint-sqlite pydantic opencv-python-headless `
    numpy supervision openai httpx folium pytest ruff mypy
pip install "langgraph-cli[inmem]"   # enables `langgraph dev` Studio UI

# 5. Copy the env template and fill in only what you need
Copy-Item .env.example .env
# Edit .env:
#   POTHOLE_MODEL_PATH=C:\path\to\pothole_yolov8.pt
#   VLM_BASE_URL=https://api.xkiro.com/v1
#   VLM_API_KEY=sk-xt-...
#   VLM_MODEL=qwen/qwen3-vl-plus:free
#   VLM_TIMEOUT_S=30
#   FRAMES_PER_SECOND=5  (or 15 for highway speeds)

# 6. Download the recommended pothole weights (one-time, 6.8 MB)
Invoke-WebRequest `
    -Uri "https://huggingface.co/keremberke/yolov8n-pothole-segmentation/resolve/main/best.pt" `
    -OutFile "pothole_yolov8.pt"

# 7. Verify install: tests green
python -m pytest tests/ -x --tb=short
# Expected: "118 passed, 1 warning in 2.x s"

# 8. (Optional) Install ultralytics for real YOLO inference (~2 GB, pulls PyTorch)
pip install ultralytics

# 9. (Optional) Smoke-test the LangGraph dev server
langgraph dev --no-browser --port 8765
# Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8765
```

### 9.1 Full install alternative (needs ~4 GB free; pulls torch + CUDA + ultralytics + streamlit)

```powershell
# Skip step 3-4 above; do this instead:
pip install -e ".[dev]"
```

### 9.2 Linux / macOS setup (for reference)

```bash
git clone https://github.com/shanmukhanssm/SIH2026-PROTOTYPE.git
cd SIH2026-PROTOTYPE
python3.12 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e . --no-deps
pip install langgraph langgraph-checkpoint-sqlite pydantic opencv-python-headless \
    numpy supervision openai httpx folium pytest ruff mypy
pip install "langgraph-cli[inmem]"
cp .env.example .env
curl -L -o pothole_yolov8.pt \
    https://huggingface.co/keremberke/yolov8n-pothole-segmentation/resolve/main/best.pt
pytest tests/ -x --tb=short
pip install ultralytics
```

---

## 10. Run Commands — 4 modes

> All examples assume venv activated, repo at `C:\Users\<you>\Desktop\SIH2026-PROTOTYPE`, clip at `C:\path\to\clip.mp4`.

### 10.1 Mode 1 — Stub (no GPU / no network — fastest, deterministic)

```powershell
python run_video.py --video C:\path\to\clip.mp4 --stub
```

Use this for: smoke testing the install, demonstrating the graph topology, verifying state propagation.

**Expected**: `status=done`, `detections>0` (deterministic stub boxes from filename hash), `events=0` (the planted scenario lives only in `tests/e2e/test_stub_run.py`).

### 10.2 Mode 2 — Real YOLO detection only (no VLM endpoint)

```powershell
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --fps 5 `
    --pothole-conf 0.25
```

Use this for: verifying YOLO actually detects cars/trucks/people/potholes in your video.

**Expected**: `status=done`, `detections>0` with `composition` showing real class counts (e.g. `{('street','car'): 180, ('pothole','pothole'): 5}`), `events` may fire (POTHOLE rule needs `POTHOLE_MIN_CONSECUTIVE=2` overlapping boxes), `verdicts=0` (VLM unset), `published=0` (gate drops everything with `error:no_endpoint`).

### 10.3 Mode 3 — Full real with xkiro VLM (free Qwen vision model)

```powershell
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --fps 5 `
    --pothole-conf 0.25 `
    --vlm-base-url https://api.xkiro.com/v1 `
    --vlm-api-key "sk-xt-...your-xkiro-key..." `
    --vlm-model "qwen/qwen3-vl-plus:free" `
    --vlm-timeout 60 `
    --vlm-json-mode auto `
    --run-id xkiro-demo
```

Use this for: production-style run with real detections AND real VLM verification.

**Important**: Do NOT use `qwen/qwen3.7-max:free` — it's a **text-only LLM, not a vision model**. The xkiro gateway returns HTTP 200 with a refusal message ("I cannot process images") instead of a 400, so the roadfix parser would silently misclassify refusals as verdicts. Use `qwen/qwen3-vl-plus:free` (vision-capable, free, accepts JSON mode).

**Expected on a real dashcam clip with potholes**:
```
status           : done
frames           : 150              ← 30s × 5 fps
detections       : 600
  composition    : {('street','car'): 540, ('pothole','pothole'): 12, ...}
events           : 3                ← POTHOLE rule fired
  by kind        : {'POTHOLE': 3}
verdicts         : 3                ← xkiro VLM verdicts
  by verifier   : {'qwen/qwen3-vl-plus:free': 3}
gate_decisions   : 3
  actions       : {'publish': 2, 'drop': 1}
published         : 2
dropped_count    : 1
```

### 10.4 Mode 4 — Full real with custom VLM (any OpenAI-compatible endpoint)

```powershell
# Example: local ollama running qwen2.5-vl-7b-instruct
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --vlm-base-url http://localhost:11434/v1 `
    --vlm-api-key ollama `
    --vlm-model qwen2.5-vl-7b-instruct `
    --vlm-timeout 60 `
    --vlm-json-mode auto `
    --fps 2.5

# Example: OpenRouter (paid Qwen2.5-VL)
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --vlm-base-url https://openrouter.ai/api/v1 `
    --vlm-api-key "sk-or-...your-openrouter-key..." `
    --vlm-model "qwen/qwen2.5-vl-7b-instruct" `
    --vlm-timeout 30 `
    --vlm-json-mode auto `
    --fps 2.5 `
    --run-id openrouter-qwen

# Example: DashScope (Aliyun)
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --vlm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 `
    --vlm-api-key "sk-..." `
    --vlm-model "qwen-vl-max" `
    --fps 2.5

# Example: self-hosted vLLM
python run_video.py `
    --video C:\path\to\clip.mp4 `
    --pothole-weights C:\path\to\SIH2026-PROTOTYPE\pothole_yolov8.pt `
    --vlm-base-url http://vllm-host:8000/v1 `
    --vlm-api-key "token-..." `
    --vlm-model qwen2.5-vl-7b-instruct `
    --fps 2.5
```

All four honor `response_format={"type":"json_object"}` per `library-docs.md` (the `--vlm-json-mode auto` default auto-falls-back on `BadRequestError`).

---

## 11. Test Commands

```powershell
# Full suite (118 tests passing in ~2.5s)
python -m pytest tests/ -x --tb=short

# Just the unit tests (skip the slow e2e)
python -m pytest tests/ --tb=short -m "not e2e"

# Just the e2e stub run
python -m pytest tests/e2e/test_stub_run.py -v

# Specific node tests
python -m pytest tests/nodes/test_track_flag.py -v

# With coverage
pip install pytest-cov
python -m pytest tests/ --cov=src/roadfix --cov-report=term-missing

# Lint + types
ruff check .
mypy --strict src
```

---

## 12. Output Artifacts

All output lands under `data/runs/<run-id>/` (or your `--data-dir` override):

```
data/runs/
├── checkpoints.sqlite          # LangGraph state checkpoint DB
├── events.db                   # Published events store (Phase 3 wiring)
└── <run-id>/
    ├── frames/
    │   ├── f00000.jpg          # extracted frames at FRAMES_PER_SECOND
    │   ├── f00001.jpg
    │   └── ...
    ├── snapshots/
    │   ├── <event_id>_full.jpg # full-frame snapshot at the event anchor frame
    │   ├── <event_id>_crop.jpg # cropped to the event bbox (when bbox provided)
    │   └── ...
    ├── map.html                # Folium heatmap + markers + photo popups (Phase 3)
    └── report.md               # markdown summary report (Phase 3)
```

**Note**: As of Phase 2 completion, `publish` returns **fake paths** for `map_path` and `report_path` — the real wiring to `build_map` / `build_report` / `store_upsert_events` is Phase 3 feature 12. The graph completes with `status=done`; just don't expect a real `map.html` file on disk yet.

---

## 13. Architecture Invariants (per `context/architecture.md`)

1. **Workflow, not agent** (ADR-001) — no tool-calling loops, deterministic routing only.
2. **Single graph** — one `StateGraph`, six nodes, two conditional edges, one Send fan-out + reducer.
3. **Writer audit** — every state field has exactly one writer; `verdicts` is the only non-overwrite field (reducer `operator.add`).
4. **Never raises past node boundary** — every node and tool catches all exceptions and returns a degraded result (`[]`, `ok=False`, `error:<code>` verdict).
5. **Closed tool registry** — exactly 10 tools (frames×2, detectors×2, tracker, snapshot, vlm, store×2, map_builder, report); no inline tool calls.
6. **Version-pinned prompts** — `inspector` v1 lives in `prompts/inspector.py`; any change bumps the version.
7. **Config single source of truth** — all constants in `config.py`; no redeclared thresholds elsewhere.
8. **Heavy deps lazy-imported** — `ultralytics`, `openai`, `folium` imported inside tool functions, not at module top-level; tests run without them installed.
9. **SqliteSaver checkpointing** — every super-step is checkpointed; crash-resume works.
10. **Ponytail principle** — laziest solution that works (YAGNI, stdlib first, minimal code per the governing `ponytail` skill).

---

## 14. Caveats, Drifts, and Unimplemented Phases

### 14.1 Known documentation/code drifts

| What | Doc says | Code says | Note |
|---|---|---|---|
| `VLM_TIMEOUT_S` | `code-standards.md` line 153: `10.0` | `config.py:27`: `30.0` | Phase 1 explicitly bumped to 30s for free-tier gateways. **Doc is stale**. |
| `ExtractFramesArgs.fps` validator | `code-standards.md` line 94: `Field(2.5, gt=0, le=5)` | `tools/frames.py:26`: `Field(2.5, gt=0, le=30)` | Phase 2.x hotfix raised cap to 30 to allow 15 fps for highway speeds. **Doc is stale**. |
| `scripts/run_pipeline.py` | Referenced throughout `context/*.md` as the CLI runner | `scripts/` contains only `.gitkeep` | **Phase 3 feature 12 NOT YET BUILT**. Use `run_video.py` at repo root instead. |
| `langgraph` version | `pyproject.toml`: `langgraph>=0.4` | installed `1.2.12` | `library-docs.md` and `progress-tracker.md` reference langgraph 1.2.x observed behavior (Send payloads reach workers as raw dicts, `add_node` typing rejects bare callables whose param isn't named `state`). |

### 14.2 Phase 3+ unimplemented features

Per `context/progress-tracker.md`:

| Feature # | Description | Status |
|---|---|---|
| 12 | Wire `publish` to real `store_upsert_events` + `build_map` + `build_report`; write `scripts/run_pipeline.py` | ⬜ not started |
| 13 | Resume integrity — verify a crashed run resumes from the last super-step and produces identical output | ⬜ not started |
| 14 | Streamlit dashboard at `ui/dashboard.py` for browsing published events | ⬜ not started |
| 15 | Eval runner `evals/run_evals.py` + golden datasets in `evals/datasets/` + LLM-as-judge graders in `evals/graders/` | ⬜ not started |

### 14.3 Operational caveats

- **xkiro Cloudflare bot-fight-mode** blocks `urllib`'s default User-Agent (HTTP 403 with `error code: 1010`). The `openai` Python SDK's httpx UA is whitelisted natively — so `vlm_inspect` works without any UA override. If you ever swap to `httpx`/`requests` directly, set `User-Agent: curl/8.0` or a Mozilla Chrome string.
- **Free-tier VLM latency** is 3-8s per request (occasionally up to 45s observed). With N events in the verify fan-out, expect N × 5s = N×5 seconds total. `VLM_TIMEOUT_S=60` is the right setting.
- **15 fps is 6× slower** than default 2.5 fps. YOLO inference on CPU is ~50-200ms/frame, so a 60s video = 900 frames × ~100ms = ~90s of YOLO inference alone. Dial back to 5 fps if too slow.
- **The street YOLO model has no "pothole" class** — it's COCO-trained (car/truck/person/bicycle/motorcycle/bus). Pothole detection requires a separate weights file via `POTHOLE_MODEL_PATH` env var (or `--pothole-weights` CLI arg). Without it, the pothole eye is **DISABLED** (logged, not raised).
- **supervision `ByteTrack()` deprecation** — `tools/tracker.py:41` calls `sv.ByteTrack()` which emits `FutureWarning: deprecated since v0.28.0, removed in v0.31.0`. Tracked in `progress-tracker.md` as a library-commit upgrade path. Not a blocker.

---

## 15. Quick Reference Cheatsheet

### One-liner to remember

> **`ingest → detect → track_flag → [Send: verify_event] → gate → publish`** with `verdicts` reduced via `operator.add` and the gate bounded by `MAX_VERIFY_ATTEMPTS=2`.

### Three knobs to fix "no potholes detected"

1. `--pothole-weights path/to/pothole.pt` (PRIMARY — without this the pothole eye is disabled)
2. `--fps 5` or `--fps 15` (SECONDARY — denser sampling helps the consecutive-frame rule)
3. `--pothole-conf 0.20` (TERTIARY — catch weaker detections; VLM filters false positives)

### Three knobs to fix "no events firing"

1. Verify `detections > 0` — YOLO is actually finding things
2. For POTHOLE: need `>= POTHOLE_MIN_CONSECUTIVE=2` overlapping boxes (IoU ≥ 0.4) on consecutive frames
3. For TRAFFIC_JAM: need `> JAM_MIN_VEHICLES=12` unique tracked vehicles in `JAM_WINDOW_S=30s`
4. For KIDS_CROSSING: need a `person` with stable track_id dwelling > 2s in the road band

### Three knobs to fix "events fire but published=0"

1. Check `verdicts` count == `events` count (race invariant — if not, the Send fan-out failed)
2. Check `verifiers` field — if it's `error:no_endpoint`, set `VLM_BASE_URL`/`VLM_API_KEY`/`VLM_MODEL`
3. If verifier is the model name but `published=0`, the VLM is rejecting events (low confidence) — check `gate_decisions` `actions` for the distribution (publish/recheck/drop)

### Where everything lives

| Want to find ... | Look in ... |
|---|---|
| The graph wiring | `src/roadfix/graph.py` |
| State schema | `src/roadfix/state.py` |
| All constants + env-var readers | `src/roadfix/config.py` |
| One node's logic | `src/roadfix/nodes/<node>.py` |
| One tool's implementation | `src/roadfix/tools/<tool>.py` |
| The inspector prompt | `src/roadfix/prompts/inspector.py` |
| The CLI runner | `run_video.py` (repo root) |
| LangGraph dev manifest | `langgraph.json` |
| Project deps | `pyproject.toml` |
| Project truth docs | `context/*.md` (11 files) |
| Phase status | `context/progress-tracker.md` |
| The contract (topology + state) | `context/graph-design.md` |
| Tests | `tests/{conftest.py, test_config.py, e2e/, nodes/, tools/}` |
| AI-systems skill library | `skills/<name>/SKILL.md` (12 packages) |

---

## 16. Conclusion

RoadFix is a **production-shaped LangGraph workflow** that takes a dashcam video in and produces a sorted, verified road-work list out — with two YOLO eyes, three deterministic rules, a Qwen2.5-VL inspector, a confidence gate, and SQLite-persisted outputs.

**Phases 0-2 are complete** (skeleton + tools + real nodes + 118 tests passing). **Phases 3-4 remain** (real publish wiring + Streamlit dashboard + resume integrity + eval runner). The code is `ruff` + `mypy --strict` clean, every node is resilient (never raises past the boundary), and the env-var-driven config means you can run it in three modes (stub / real-detect / full real) without code changes.

To run it for real: install Python 3.12 + the deps, download a pothole YOLO `.pt`, get a VLM endpoint (xkiro free tier works), set the env vars, and invoke `python run_video.py --video your_clip.mp4 ...`.

— end of guide —
