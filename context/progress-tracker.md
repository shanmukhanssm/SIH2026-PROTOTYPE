# Progress Tracker

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update after every completed feature — same commit as the feature. Format per context-references/progress-tracker.md.

---

## Current Status

**Phase:** Phase 0 — Skeleton
**Last completed:** 01 Project Scaffold
**Next:** 02 Stub Graph End-to-End

---

## Progress

### Phase 0 — Skeleton

- [x] 01 Project Scaffold
- [ ] 02 Stub Graph End-to-End

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
