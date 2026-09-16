# Progress Tracker

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update after every completed feature — same commit as the feature. Format per context-references/progress-tracker.md.

---

## Current Status

**Phase:** Phase 1 — Tools
**Last completed:** 03–08 (all six Phase 1 features)
**Next:** 09 ingest + detect nodes (real) — Phase 2 (gate: Layer 1 tool evals green, see Notes)

---

## Progress

### Phase 0 — Skeleton

- [ ] 01 Project Scaffold
- [ ] 02 Stub Graph End-to-End

### Phase 1 — Tools

- [x] 03 extract_frames + gps_sync
- [x] 04 yolo_street + yolo_pothole
- [x] 05 bytetrack_tracks
- [x] 06 vlm_inspect
- [x] 07 store_upsert_events + store_query_events
- [x] 08 save_event_snapshot + build_map + build_report

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

### Phase 1 — Tools (features 03–08) — built in one parallel session

**Gate result — Layer 1 tool evals: GREEN.** Full suite: 53 passed (tests/tools/*, real tiny mp4/jpg fixtures), `ruff check` clean, `mypy --strict` clean (14 source files). Beyond the unit gate, a cross-tool integration smoke chained extract_frames → gps_sync → detectors (REAL mode: yolov8n.pt auto-downloaded, real inference; pothole eye degraded honestly without weights) → bytetrack_tracks → save_event_snapshot → store upsert/query → build_map → build_report — all seams verified (frame_id joins, event_id→run_id derivation, PublishedEvent round-trip). Ultralytics installed post-hoc (torch CPU wheels; CUDA wheels exceed sandbox disk) — the detector module stays importable and strict-clean without it (lazy `importlib` + Protocols).

**Per-tool eval counts (registry cases implemented in pytest):** frames 6/6 fixtures + 8 gps cases (registry asked 5; extra: missing-file, zero-parse, copy-semantics) · detectors 6/6 · tracker 6 (registry asked 4; extra: mutation-guard + person-variant) · vlm 9 (registry asked 4; extra: json-mode auto-fallback/off, rate-limit recovery, no-endpoint, unreadable-snapshot) · store 8 (insert/roundtrip, replace-same-id, filter matrix, db-unwritable, replay idempotency, NULL round-trip) · snapshot 4 + map/report 6. Note: eval-plan.md says "10/10 across the four store/map/report/snapshot tools" but tool-registry lists 4+3+3+3 = 13 cases; the registry (13) was implemented as the more specific document.

**Decisions & caveats to carry into Phase 2:**
- `frame_id` convention: basename incl. extension (`f00000.jpg`) = file naming rule in tool-registry; state.py comments updated to match (graph-design.md's `f{index:05d}` comment was ambiguous). Joins between FrameRef/Detection/Event are simple equality.
- supervision 0.30.3 ByteTrack: unmatched rows are DROPPED from `update_with_detections` output (not returned as None); wrapper maps back via order-preserving bbox match. Objects first seen at frame ≥ 2 need 2 consecutive appearances for an ID. `sv.ByteTrack` deprecated (removal v0.31) — registry pins it; upgrade path flagged for a library commit.
- `store_query_events` v1 signature was absent from tool-registry.md — full signature + `StoredEvent` read model added to the registry in this commit. `run_id` is derived from `event_id` (prefix before first `-{kind}-`); `PublishedEvent` intentionally stays without a run_id field.
- Inspector prompt v1 build note added to prompt-registry.md (static text + appended kind/evidence data block).
- VLM stub severities: POTHOLE→medium/0.85, KIDS_CROSSING→high/0.9, TRAFFIC_JAM→low/0.8 — chosen so gate-honesty golden cases (G1–G3) will publish under stub mode; revisit if gate thresholds change.
- Foundation files (pyproject.toml, config.py, state.py, tests/conftest.py) were created contract-conformantly by this session because Phase 1 imports them and Phase 0 was in flight elsewhere; reconcile with agent-0's Phase 0 output on merge — deviations are limited to: `dict` → `dict[str, object]` (mypy strict), env-derived config as call-time reader functions (monkeypatch-friendly), no nodes/graph/langgraph.json (Phase 0 scope untouched).
