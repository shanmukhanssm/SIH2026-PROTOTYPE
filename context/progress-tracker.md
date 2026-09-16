# Progress Tracker

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update after every completed feature — same commit as the feature. Format per context-references/progress-tracker.md.

---

## Current Status

**Phase:** Phase 0 — Skeleton (complete)
**Last completed:** 02 Stub Graph End-to-End
**Next:** 03 extract_frames + gps_sync (Phase 1 — Tools)

---

## Progress

### Phase 0 — Skeleton

- [x] 01 Project Scaffold
- [x] 02 Stub Graph End-to-End

### Phase 1 — Tools

- [ ] 03 extract_frames + gps_sync
- [ ] 04 yolo_street + yolo_pothole
- [ ] 05 bytetrack_tracks
- [ ] 06 vlm_inspect
- [ ] 07 store_upsert_events + store_query_events
- [ ] 08 save_event_snapshot + build_map + build_report

### Phase 2 — Nodes

- [ ] 09 ingest + detect nodes (real)
- [ ] 10 track_flag node (real)
- [ ] 11 verify worker + gate (real)

### Phase 3 — Main Graph

- [ ] 12 Real wiring + CLI runner
- [ ] 13 Resume integrity
- [ ] 14 Streamlit dashboard

### Phase 4 — Evals

- [ ] 15 Eval runner + golden datasets

---

## Decisions Made During Intake

- **Language:** Python 3.12 (user environment; LangGraph-first ecosystem).
- **Verdict:** workflow, not agent — fixed 5-stage pipeline with Send fan-out only for verification (ADR-001). Agency L0–L1.
- **VLM access:** OpenAI-compatible endpoint (`VLM_BASE_URL`/`VLM_API_KEY`/`VLM_MODEL`, default qwen2.5-vl-7b-instruct) — user choice; works with vLLM/ollama/OpenRouter/DashScope-compatible.
- **Checkpointer:** SqliteSaver (user choice) — single-run-at-a-time acceptable for prototype; Postgres deferred.
- **Input data:** user adds real bus videos to `data/inbox/`; no synthetic video generator shipped. Tests use tiny cv2-written fixture clips instead.
- **Prototype flag:** HARDEN stage skipped — `prototype: no harden stage` (user decision, AGENTS.md §4). Eval scaffold (Phase 4) still built.
- **Git:** local only for now; user will create the GitHub repo and push when ready.
- **Report:** markdown auto-report per run (user choice); CSV/PDF exports out of scope.
- **Stub-model mode:** `ROADFIX_STUB_MODELS=1` — deterministic detectors/verdicts so tests and no-GPU demos run without weights or endpoints. Real-model runs are documented operator procedures.
- **HITL:** zero interrupts in v1 — deterministic confidence gate is cheap and reversible (ADR-001).
- **Package name:** `roadfix` under `src/roadfix/`; repo SIH2026-PROTOTYPE carries the full AI-SYSTEMS workflow assets (AGENTS.md, skills/, context-references/) so the project is self-contained.

---

## Notes

*(append one block per completed feature, newest first)*

### 02 Stub Graph End-to-End — DONE

- **Gate (feature 02 / Phase 0):** `ruff check .` 0 errors · `ruff format --check src tests` clean · `mypy --strict` clean (13 source files) · `pytest` 4/4 — incl. `tests/e2e/test_stub_run.py`: START→END on stub input, 6 frames on disk, 12 deterministic detections, 2 events (POTHOLE + TRAFFIC_JAM), 2 verdicts via Send fan-out, publish decisions, published=2/dropped=0, status=done, checkpoint sqlite persisted. State-diff asserted per writer audit (each node writes exactly its keys); race invariant asserted as counts, never order.
- **Builder wiring verified structurally:** static edges `START→ingest→detect→track_flag`, `verify_event→gate` (fan-in), `publish→END`; routers `route_to_verify`/`route_after_gate` own track_flag/gate exclusively — no static+conditional double-fire.
- **Files:** src/roadfix/state.py (8 models verbatim from graph-design.md), nodes/ingest.py + detect.py + track_flag.py (stubs), nodes/verify_event.py + gate.py + publish.py (stubs), graph.py (build_graph + routers + module-level `graph = build_graph()` for langgraph.json), tests/e2e/test_stub_run.py.
- **Framework-behavior conflicts observed on langgraph 1.2.11 / mypy 2.3.1 (per AGENTS.md §21 — observed behavior wins, skills/library-docs to be updated in a library commit):**
  1. Send payloads reach workers as RAW DICTS — neither the worker's `VerifyPayload` annotation nor `add_node(..., input_schema=VerifyPayload)` coerces at runtime. Worker-side `VerifyPayload.model_validate(payload)` at the boundary is REQUIRED (library-docs/code-standards templates implied coercion).
  2. `add_node` typing (StateNode protocol) rejects bare callables whose first param is not named `state` — every `(payload: VerifyPayload)`-style signature fails mypy --strict regardless of body or positional-only markers. Fix: register the worker as `RunnableLambda(verify_event)` at the wiring site (langchain-core is already a langgraph dependency); worker signature stays template-exact.
  3. `StateGraph` inference inside a function body mis-solves the Input/Output TypeVar defaults — pin with `g: StateGraph[RoadfixState] = StateGraph(RoadfixState)`.
  4. `graph.get_graph()` drawing omits Send/static edges to late nodes — inspect `graph.builder.edges`/`branches` for wiring truth.
- **Decisions:**
  - `state.py` adaptations for mypy --strict: `evidence`/`track_summary` typed `dict[str, object]` (bare `dict` violates disallow_any_generics; contract shape otherwise verbatim).
  - `graph.py` exposes module-level `graph = build_graph()` (checkpointer=None default) so langgraph.json's `roadfix.graph:graph` resolves with zero import side effects; run_pipeline/e2e build their own SqliteSaver-backed graph. Checkpointer type pinned `BaseCheckpointSaver[str]` (SqliteSaver's version type).
  - Stub detect emits exactly 12 deterministic boxes (6 car + 3 person + 1 bus + 2 pothole over 6 frames) — matches build-plan "~10 boxes"; stub track_flag emits POTHOLE + TRAFFIC_JAM (mirrors golden case G1 shape).
  - Publish stub writes no files (fake map/report paths) — honest Phase 0; real store/map/report tools land in Phase 1.
- **Skills used:** langgraph-builder (primary; templates + state-and-reducers + checkpointing references), ponytail (governing). Build executed via two parallel subagents (state+3 nodes ∥ 3 nodes+graph) + main-agent integration (e2e test, gate fixes, this record).
- **Result: `pytest` — 4 passed; Phase 0 gate green.**

### 01 Project Scaffold — DONE

- **Gate (feature 01):** `ruff check .` 0 errors · `mypy --strict` clean (5 source files) · `pytest` 3/3 green (config contract tests). All re-verified independently after the build.
- **Files:** pyproject.toml (closed dependency list, ruff/mypy-strict/pytest config, hatchling), src/roadfix/config.py (all code-standards constants + env-derived settings + call-time path helpers), .env.example (all 7 vars, no values), .gitignore, langgraph.json (→ roadfix.graph:graph, module lands with feature 02), folder tree per architecture.md, tests/test_config.py.
- **Decisions:**
  - mypy overrides use comma-separated module list (mypy 2.3.1 rejects pipe patterns) for ultralytics/cv2/supervision/folium/streamlit.
  - `ROADFIX_DATA_DIR` honored by call-time functions (`data_dir()`/`runs_dir()`/`checkpoint_db()`/`events_db()`), not import-time constants — tests monkeypatch env after import.
  - Heavy deps (ultralytics/torch, supervision, opencv, openai, folium, streamlit) declared in pyproject but not installed until Phase 1 needs them — Phase 0 code imports only langgraph/pydantic/stdlib (lazy-import rule keeps the stack out).
  - `data/inbox/*` re-added to .gitignore (operator videos must never be committed); `!data/inbox/.gitkeep` keeps the dir tracked.
- **Skills used:** planning-and-task-breakdown (governing), ponytail (governing), per AGENTS.md §3 pipeline. Build executed via one scaffold subagent + independent main-agent verification.
- **Versions observed:** Python 3.12.14 · langgraph 1.2.11 · langgraph-checkpoint-sqlite 3.1.1 · pydantic 2.13.5 · pytest 9.1.1 · ruff 0.16.8 · mypy 2.3.1
