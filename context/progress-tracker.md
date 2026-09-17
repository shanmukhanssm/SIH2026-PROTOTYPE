# Progress Tracker

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update after every completed feature — same commit as the feature. Format per context-references/progress-tracker.md.

---

## Current Status

**Phase:** Phase 2 — Nodes (complete; built in one parallel session, independent review passed)
**Last completed:** 09–11 (all three Phase 2 features) — real ingest/detect/track_flag/verify_event/gate
**Next:** 12 Real wiring + CLI runner — Phase 3 (gate: Layer 2 node evals green, see Notes)

---

## Progress

### Phase 0 — Skeleton

- [x] 01 Project Scaffold
- [x] 02 Stub Graph End-to-End

### Phase 1 — Tools

- [x] 03 extract_frames + gps_sync
- [x] 04 yolo_street + yolo_pothole
- [x] 05 bytetrack_tracks
- [x] 06 vlm_inspect
- [x] 07 store_upsert_events + store_query_events
- [x] 08 save_event_snapshot + build_map + build_report

### Phase 2 — Nodes

- [x] 09 ingest + detect nodes (real)
- [x] 10 track_flag node (real)
- [x] 11 verify worker + gate (real)

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

### Phase 2 — Nodes (features 09–11) — built in one parallel session, independent review passed

**Gate result — Layer 2 node evals: GREEN.** Full suite: 118 passed (53 Phase-1 tool evals + 57 node tests + 3 config + 2 e2e), `ruff check` clean, `mypy --strict` clean (22 source files). All five real nodes land: ingest (extract_frames → gps_sync, honest zero-frame path), detect (both eyes per frame, per-eye degradation composes), track_flag (ByteTrack + three PURE-FUNCTION rules + real snapshot files), verify_event (Send worker over vlm_inspect, raw-dict boundary validation), gate (attempt-aware thresholds, wholesale recompute, `_latest_by_event` preserved). The e2e was rewritten for real nodes: a planted-detector run (13 cars + 2-frame pothole → TRAFFIC_JAM + POTHOLE fire once, stub verdicts publish, writer audit asserted per node write) plus an honest empty-run path (missing video → zero everything → `status="done"`).

**Decisions & caveats to carry into Phase 3:**
- **Rule semantics pinned by tests (they ARE the contract):** pothole IoU ≥ 0.4 INCLUSIVE over ≥ 2 consecutive timeline frames, anchored at streak start, same-physical-pothole dedupe (only flagged chains' boxes pooled — deliberate asymmetry, WHY-commented); kids = tracked person, bbox CENTER-y in [0.55, 1.0] band (inclusive), dwell > 2.0 strict, median height ratio < 0.25 strict, one event per track; jam = unique vehicles > 12 strict per closed 30 s window, deduped to non-overlapping windows, anchor = last frame in window.
- **Review fix (MAJOR):** jam candidate window starts are ALL distinct appearance times, not each track's first — the first-appearance scan provably missed qualifying windows (multi-burst occlusion chains); regression test `test_jam_rule_qualifying_window_far_from_first_appearances` pins it. Boundary tests added: IoU == 0.4 chains; smallness ratio == 0.25 silent.
- Gate rule strings: `first_pass_publish` / `first_pass_recheck` / `first_pass_drop_unconfirmed` / `first_pass_drop_low_conf` / `final_pass_publish` / `final_pass_drop`; gate recheck set ⇔ `route_after_gate` re-Send set under MAX_VERIFY_ATTEMPTS=2 (verified bidirectionally). Flagged for a future library commit: if MAX_VERIFY_ATTEMPTS is ever raised above 2, `route_after_gate` (attempt+1 < MAX) becomes more eager than the gate's final-pass rule.
- langgraph 1.2 state observation: channels never written by any node behave asymmetrically in `get_state().values` — reducer channels (verdicts) materialize their default, overwrite channels (gate_decisions) stay ABSENT until first write; the empty-run e2e asserts absence as evidence the gate never ran.
- `ingest` keeps one `# type: ignore[call-arg]` on `GpsSyncArgs` (repo runs mypy without the pydantic plugin, which synthesizes Field-defaulted args as required); enabling the pydantic mypy plugin repo-wide would remove it — deferred to a library commit.
- publish node intentionally still stub (build-plan: real store/map/report wiring is feature 12, Phase 3); `_latest_by_event` import contract for publish.py preserved byte-identical.
- Build executed via three parallel subagents (09 ∥ 10 ∥ 11) + main-agent integration (e2e rewrite, gate runs) + an independent review subagent (verdict REQUEST_CHANGES → one MAJOR fixed, two MINORs fixed, one documented); the track_flag subagent hit a context deadline after writing code but before verification — main agent completed its verify loop (defaultdict KeyError fix + one test-side anchor assertion corrected against the pinned docstring interpretation).

### Merge: Phase 1 (local) × Phase 0 (origin/main) — DONE

- **Merge resolution:** `state.py` → Phase 0's verbatim (identical contract shape; only comments differed). `pyproject.toml` → Phase 0's verbatim (versioned dependency floors + third-party mypy overrides; re-verified my full tree passes its ruff/mypy/pytest config). `config.py` → unified superset: Phase 0's threshold constants, call-time path helpers (`data_dir`/`runs_dir`/`checkpoint_db`/`events_db`) and import-time env names (`STUB_MODELS`, `VLM_*`, `POTHOLE_MODEL_PATH`, `YOLO_STREET_WEIGHTS`) now derived FROM Phase 1's call-time readers (single read-logic per env var — import-time snapshots don't see post-import env changes; readers stay canonical for tools/tests). `progress-tracker.md` → this combined record.
- **frame_id convention note:** Phase 0 stub `ingest` emits `f{index:05d}` (no extension); Phase 1 `extract_frames` emits basename `f{index:05d}.jpg`. Both self-consistent; consumers join via `basename(path)`. No test couples the two (e2e asserts no frame_id format).
- **Merge gate:** ruff + `mypy --strict` + full pytest re-run on the combined tree — see Phase 1 gate line below for counts; e2e stub run re-verified green post-merge.

### Phase 1 — Tools (features 03–08) — built in one parallel session

**Gate result — Layer 1 tool evals: GREEN.** Full suite: 53 passed (tests/tools/*, real tiny mp4/jpg fixtures), `ruff check` clean, `mypy --strict` clean (14 source files). Beyond the unit gate, a cross-tool integration smoke chained extract_frames → gps_sync → detectors (REAL mode: yolov8n.pt auto-downloaded, real inference; pothole eye degraded honestly without weights) → bytetrack_tracks → save_event_snapshot → store upsert/query → build_map → build_report — all seams verified (frame_id joins, event_id→run_id derivation, PublishedEvent round-trip). Ultralytics installed post-hoc (torch CPU wheels; CUDA wheels exceed sandbox disk) — the detector module stays importable and strict-clean without it (lazy `importlib` + Protocols).

**Per-tool eval counts (registry cases implemented in pytest):** frames 6/6 fixtures + 8 gps cases (registry asked 5; extra: missing-file, zero-parse, copy-semantics) · detectors 6/6 · tracker 6 (registry asked 4; extra: mutation-guard + person-variant) · vlm 9 (registry asked 4; extra: json-mode auto-fallback/off, rate-limit recovery, no-endpoint, unreadable-snapshot) · store 8 (insert/roundtrip, replace-same-id, filter matrix, db-unwritable, replay idempotency, NULL round-trip) · snapshot 4 + map/report 6. Note: eval-plan.md says "10/10 across the four store/map/report/snapshot tools" but tool-registry lists 4+3+3+3 = 13 cases; the registry (13) was implemented as the more specific document.

**Decisions & caveats to carry into Phase 2:**
- `frame_id` convention: basename incl. extension (`f00000.jpg`) = file naming rule in tool-registry; state.py comments updated to match (graph-design.md's `f{index:05d}` comment was ambiguous). Joins between FrameRef/Detection/Event are simple equality.
- supervision 0.30.3 ByteTrack: unmatched rows are DROPPED from `update_with_detections` output (not returned as None); wrapper maps back via order-preserving bbox match. Objects first seen at frame ≥ 2 need 2 consecutive appearances for an ID. `sv.ByteTrack` deprecated (removal v0.31) — registry pins it; upgrade path flagged for a library commit.
- `store_query_events` v1 signature was absent from tool-registry.md — full signature + `StoredEvent` read model added to the registry in this commit. `run_id` is derived from `event_id` (prefix before first `-{kind}-`); `PublishedEvent` intentionally stays without a run_id field.
- Inspector prompt v1 build note added to prompt-registry.md (static text + appended kind/evidence data block).
- VLM stub severities: POTHOLE→medium/0.85, KIDS_CROSSING→high/0.9, TRAFFIC_JAM→low/0.8 — chosen so gate-honesty golden cases (G1–G3) will publish under stub mode; revisit if gate thresholds change.
- Foundation files (pyproject.toml, config.py, state.py, tests/conftest.py) were created contract-conformantly by this session because Phase 1 imports them and Phase 0 was in flight elsewhere; reconciled with agent-0's Phase 0 output at merge (see Merge block above) — deviations kept: `dict` → `dict[str, object]` (mypy strict), env-derived config as call-time reader functions (monkeypatch-friendly), no nodes/graph/langgraph.json (Phase 0 scope untouched).

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
