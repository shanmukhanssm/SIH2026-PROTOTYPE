# Build Plan

> Generated from Intake for SIH2026-PROTOTYPE under the `planning-and-task-breakdown` skill. Format per context-references/build-plan.md.

---

## Core Principle

Design top-down, build bottom-up. The full graph exists as stubs before any real logic — the shape is proven by a local stub run on day one, then real tools, then real node logic, then real wiring, each layer gated by tests and evals. Every feature is complete, tested, recorded, and committed before the next begins. Skills: `planning-and-task-breakdown` governs how this file's features are sliced and ordered; `ponytail` governs all code written for every feature below.

---

## Phase 0 — Skeleton

### 01 Project Scaffold
**Logic:**
- pyproject.toml with the closed dependency list; ruff + mypy strict configured
- Folder tree exactly per architecture.md; empty `__init__.py` files
- config.py with all constants; .env.example with every variable, no values
- langgraph.json pointing at `roadfix.graph:graph` (stub graph initially)
- .gitignore: `.env`, `data/runs/`, `*.sqlite`, `__pycache__`
**Acceptance criteria:** `ruff check`, `mypy --strict`, `pytest` (empty suite) all green.
**Files:** pyproject.toml, .env.example, .gitignore, langgraph.json, src/roadfix/config.py, package inits.
**Estimated scope:** Small.

### 02 Stub Graph End-to-End
**Logic:**
- state.py: full RoadfixState with reducers exactly per graph-design.md
- All 6 nodes as stubs returning hardcoded realistic values matching schemas (ingest → 6 fake frames on disk; detect → ~10 boxes; track_flag → 2 events; verify → confirmed verdict; gate → publish; publish → fake paths)
- graph.py wired per topology (router + Send + static fan-in); SqliteSaver checkpointer
- Stub e2e test: stub input flows START→END, published lands in final state
**Verification:** `pytest tests/e2e/test_stub_run.py` green; state-diff assertions pass.
**Gate:** stub e2e green.
**Files:** src/roadfix/state.py, src/roadfix/nodes/* (stubs), src/roadfix/graph.py, tests/e2e/test_stub_run.py.
**Estimated scope:** Medium.

---

## Phase 1 — Tools

### 03 extract_frames + gps_sync
**Logic:** implement per tool-registry.md contract — cv2 stride extraction, FrameRef list, GPS nearest-timestamp sync with tolerance; 6-clip + 5-GPS-case unit tests incl. corrupt/missing video and malformed CSV.
**Gate:** tool eval layer 1 (frames) — 6/6 + 5/5; registry status PASS.
**Files:** src/roadfix/tools/frames.py, tests/tools/test_frames.py.
**Estimated scope:** Medium.

### 04 yolo_street + yolo_pothole
**Logic:** implement per registry — ultralytics predict, class filter, conf threshold, per-eye degradation (`model_loaded=False` → empty), stub mode via `ROADFIX_STUB_MODELS`; unit tests in stub mode for both eyes' contracts.
**Gate:** tool eval layer 1 (detectors) 6/6 stub; registry status PASS (stub; real-model operator run documented).
**Files:** src/roadfix/tools/detectors.py, tests/tools/test_detectors.py.
**Estimated scope:** Medium.

### 05 bytetrack_tracks
**Logic:** implement per registry — supervision ByteTrack wrapper over street detections in t_seconds order; failure path returns untracked; 4-case unit tests on synthetic sequences.
**Gate:** tool eval layer 1 (tracker) 4/4; registry status PASS.
**Files:** src/roadfix/tools/tracker.py, tests/tools/test_tracker.py.
**Estimated scope:** Small.

### 06 vlm_inspect
**Logic:** implement per registry — OpenAI-compatible client, base64 image, JSON-object mode with config-gated fallback, Pydantic validation + 1 corrective retry, transient retry, error verdict, stub mode; 4-case unit tests with mocked endpoint.
**Gate:** tool eval layer 1 (vlm) 4/4; registry status PASS (stub; real endpoint operator run documented).
**Files:** src/roadfix/tools/vlm.py, src/roadfix/prompts/inspector.py, tests/tools/test_vlm.py.
**Estimated scope:** Medium.

### 07 store_upsert_events + store_query_events
**Logic:** implement per registry — sqlite3 schema, INSERT OR REPLACE idempotency, query filters; 4-case unit tests incl. db-unwritable.
**Gate:** tool eval layer 1 (store) 4/4; registry status PASS.
**Files:** src/roadfix/tools/store.py, tests/tools/test_store.py.
**Estimated scope:** Small.

### 08 save_event_snapshot + build_map + build_report
**Logic:** implement per registry — snapshot full+crop with failure path; folium heatmap + markers + fallback center; markdown report with honest counts and caveats; 9-case unit tests across the three.
**Gate:** tool eval layer 1 (snapshot/map/report) 9/9; registry status PASS.
**Files:** src/roadfix/tools/snapshot.py, src/roadfix/tools/map_builder.py, src/roadfix/tools/report.py, tests/tools/test_snapshot.py, tests/tools/test_outputs.py.
**Estimated scope:** Medium.

---

## Phase 2 — Nodes

### 09 ingest + detect nodes (real)
**Logic:**
- ingest: extract_frames → gps_sync → `frames` overwrite; empty-video degraded path
- detect: loop frames × both eyes, merge `detections`; per-eye degradation composes
- Replace stubs; node tests state-in/state-out incl. failure paths
**Tests:** layer 1 tools already green; node tests 4/4.
**Gate:** node evals (ingest, detect) green.
**Files:** src/roadfix/nodes/ingest.py, src/roadfix/nodes/detect.py, tests/nodes/test_ingest.py, tests/nodes/test_detect.py.
**Estimated scope:** Small.

### 10 track_flag node (real)
**Logic:**
- bytetrack over street boxes; IoU-based pothole persistence matching; the three rules exactly per graph-design.md thresholds; snapshot per event; track_summary
- Rule engine as pure functions; empty/degraded inputs handled
**Tests:** layer 2 rule dataset — 6/6 (fires/silent/dedupe cases).
**Gate:** layer 2 (rules) green.
**Files:** src/roadfix/nodes/track_flag.py, tests/nodes/test_track_flag.py.
**Estimated scope:** Medium.

### 11 verify worker + gate (real)
**Logic:**
- verify_event: real Send worker over vlm_inspect; VerifyPayload; single-verdict return
- gate: threshold logic per graph-design.md (attempt-aware), route_after_gate recheck Sends, bounded cycle
- State-diff + race tests: N Sends → N verdicts; gate writes only gate_decisions; cycle terminates
**Tests:** layer 2 gate dataset — 6/6; race invariant asserted.
**Gate:** layer 2 (verify+gate) green.
**Files:** src/roadfix/nodes/verify_event.py, src/roadfix/nodes/gate.py, tests/nodes/test_verify_gate.py.
**Estimated scope:** Medium.

---

## Phase 3 — Main Graph

### 12 Real wiring + CLI runner
**Logic:**
- All nodes real; graph.py final per library-docs.md assembly rules; SqliteSaver
- scripts/run_pipeline.py: args, run_id/thread_id mint, invoke, run_meta print, --resume flag, single-run lockfile
**Tests:** e2e golden cases G1–G3 (structure validity, store/report consistency, gate honesty).
**Gate:** layer 3 non-resume graders green.
**Files:** src/roadfix/graph.py, scripts/run_pipeline.py, tests/e2e/test_golden.py.
**Estimated scope:** Medium.

### 13 Resume integrity
**Logic:**
- Kill-and-resume harness: start G1, abort after detect checkpoint, re-invoke same thread_id
- Assert completed nodes not re-run (side-effect counter), published set equals uninterrupted run
**Gate:** layer 3 resume-integrity grader green.
**Files:** tests/e2e/test_resume.py.
**Estimated scope:** Small.

### 14 Streamlit dashboard
**Logic:**
- ui/dashboard.py: run selector, map embed, event feed with photo thumbnails, report viewer, severity filter
- Read-only: store_query_events + run-dir files; never imports nodes/graph
**Tests:** import-level smoke test + store-query integration (dashboard logic kept thin).
**Gate:** manual demo check recorded in progress-tracker; smoke test green.
**Files:** ui/dashboard.py, tests/e2e/test_dashboard_smoke.py.
**Estimated scope:** Medium.

---

## Phase 4 — Evals (scaffold; prototype: no harden stage)

### 15 Eval runner + golden datasets
**Logic:**
- evals/run_evals.py with `--layer tools|nodes|graph` flags; datasets from eval-plan.md seeded as jsonl
- Real-model precision/recall procedure documented (operator labeling 20–30 clips — user work, out of session scope)
**Gate:** `python -m evals.run_evals --layer graph` green on stub golden cases.
**Files:** evals/run_evals.py, evals/datasets/*.jsonl, evals/graders/*.py.

---

## Feature Count

| Phase | Features |
| --- | --- |
| Phase 0 — Skeleton | 2 |
| Phase 1 — Tools | 6 |
| Phase 2 — Nodes | 3 |
| Phase 3 — Main Graph | 3 |
| Phase 4 — Evals | 1 |
| **Total** | **15** |

---

## Standing Rules for This File

- Written and every future extension follows the `planning-and-task-breakdown` skill.
- Every feature lists exactly one Gate. No gate, no next feature.
- Feature order is the ladder: no skipping phases, no reordering without a recorded decision.
