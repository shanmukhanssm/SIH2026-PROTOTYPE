# Architecture

> Generated from Intake for SIH2026-PROTOTYPE. Stack decisions from `adr-001-architecture.md`.

---

## Stack

| Layer | Tool | Purpose |
| --- | --- | --- |
| Graph framework | LangGraph (Python) ≥ 0.4 | State graph, Send fan-out, checkpointing |
| Language | Python 3.12, full typing | Throughout |
| Street detector | ultralytics YOLO (yolov8n, COCO) | Cars, buses, bikes, people boxes per frame |
| Pothole detector | ultralytics YOLO + user-supplied RDD2022 weights | Pothole/crack boxes per frame |
| Tracker | supervision `ByteTrack` | Frame-to-frame vehicle/pedestrian IDs |
| Vision-language inspector | Qwen2.5-VL via `openai` client (OpenAI-compatible base_url) | Per-event verification, strict JSON |
| Imaging | opencv-python-headless | Frame extraction, snapshot crops |
| Checkpointer | langgraph-checkpoint-sqlite (`SqliteSaver`) | Run persistence, crash resume |
| Event store | SQLite via stdlib `sqlite3` | System of record for published events |
| Map | folium (+ HeatMap plugin) | Per-run heatmap HTML |
| Dashboard | Streamlit | Read-only map + feed + report viewer |
| Schema validation | Pydantic v2 | State models, tool args, inspector form |
| Testing | pytest | Unit, node, e2e (stub-model mode) |
| Lint / types | ruff + mypy (strict) | CI-quality gate |

---

## Folder Structure

```
/
├── AGENTS.md                    → Operating manual (workflow kernel, project-agnostic)
├── context/                     → The ten project truth files + adr-001
├── skills/                      → Skill library (copied from AI-SYSTEMS; read-only in projects)
├── context-references/          → Format examples for the ten context files
├── src/roadfix/
│   ├── __init__.py
│   ├── config.py                → Constants: fps target, gate thresholds, recursion limit, paths
│   ├── state.py                 → RoadfixState + all Pydantic models + reducers
│   ├── graph.py                 → Main graph assembly (the ONLY file that wires the root graph)
│   ├── nodes/
│   │   ├── ingest.py            → WATCH: video → frames + GPS sync
│   │   ├── detect.py            → SPOT: both YOLO eyes per frame
│   │   ├── track_flag.py        → TRACK & FLAG: tracker + 3 rules → events
│   │   ├── verify_event.py      → DOUBLE-CHECK worker (Send target)
│   │   ├── gate.py              → confidence gate + recheck router
│   │   └── publish.py           → SHOW: store upserts, map, report
│   ├── tools/
│   │   ├── frames.py            → extract_frames + gps_sync
│   │   ├── detectors.py         → yolo_street, yolo_pothole (stub-mode aware)
│   │   ├── tracker.py           → bytetrack wrapper
│   │   ├── snapshot.py          → save_event_snapshot (full frame + crop)
│   │   ├── vlm.py               → vlm_inspect (OpenAI-compatible, strict JSON)
│   │   ├── store.py             → event_store upsert/query (SQLite)
│   │   ├── map_builder.py       → folium heatmap html
│   │   └── report.py            → markdown report builder
│   └── prompts/
│       └── inspector.py         → INSPECTOR_V1 prompt text (version-pinned)
├── ui/
│   └── dashboard.py             → Streamlit app (reads events.db + run dirs)
├── scripts/
│   └── run_pipeline.py          → CLI: one video → full run
├── data/
│   ├── inbox/                   → operator drops videos + GPS CSVs here
│   └── runs/                    → per-run outputs (frames, snapshots, map, report, checkpoints)
├── tests/
│   ├── tools/                   → unit tests per tool (models mocked/stubbed)
│   ├── nodes/                   → node state-in/state-out tests incl. failure paths
│   └── e2e/                     → stub-mode end-to-end runs
├── evals/
│   ├── datasets/                → golden inputs (jsonl)
│   ├── graders/                 → structure, gate-behavior, (later) precision/recall
│   └── run_evals.py             → eval entrypoint per layer
├── langgraph.json               → Studio config: graph → roadfix.graph:graph
├── pyproject.toml
├── .env.example                 → every required var, no values
└── .gitignore                   → includes .env, data/runs/, *.sqlite, frames
```

---

## System Boundaries

| Folder | Owns | Never contains |
| --- | --- | --- |
| `src/roadfix/nodes/` | Node functions: read state, call tools, return state updates | Graph wiring, other nodes' logic |
| `src/roadfix/tools/` | Tool implementations + arg schemas | Node logic, prompt text, gate thresholds |
| `src/roadfix/prompts/` | Prompt text constants, versioned | Code logic |
| `src/roadfix/graph.py` | Root graph assembly only | Business logic inside nodes |
| `src/roadfix/state.py` | State models + reducers | Tool or node behavior |
| `ui/dashboard.py` | Read-only rendering of store + run dirs | Agent logic, run triggering |
| `scripts/run_pipeline.py` | CLI concerns: parse args, mint thread_id, invoke | Agent logic |
| `tests/`, `evals/` | Verification | Anything imported by `src/` at runtime |
| `context/` | Truth documents | Executable code |

---

## Data Flow

```
CLI --video clip.mp4 [--gps route.csv]
        ↓
run_pipeline.py — mint run_id + thread_id, build initial state
        ↓
graph.ainvoke(state, config={thread_id, recursion_limit})
        ↓
ingest ──→ detect ──→ track_flag
(frames+gps) (detections)  (events)
                        ↓
             route_to_verify (conditional edge)
             one Send per event ──→ verify_event workers (parallel, same super-step)
                        ↓            each returns {"verdicts": [v]}  (operator.add)
             static edge verify_event → gate
                        ↓
             gate ── recheck Sends (attempt < MAX) ──→ verify_event again
                  └─ no rechecks left → publish
                        ↓
             publish: events.db upserts + map.html + report.md
        ↓
CLI prints run_meta; dashboard reads store + run dir
```

Frames and snapshots live on disk (`data/runs/<run_id>/`); state holds paths and metadata only.

---

## External Services

| Service | Used by | Failure policy |
| --- | --- | --- |
| OpenAI-compatible VLM endpoint (`VLM_BASE_URL`) | verify_event | 1 retry with backoff; second failure → verdict `{confirmed: False, confidence: 0.0, verifier_error}` → gate drops; run never crashes |
| ultralytics model download (first street-model use) | detect | Pre-download note in README; offline + no cached weights → street detector returns [] and logs (degraded run) |
| RDD2022 weights file (`POTHOLE_MODEL_PATH`) | detect | Missing file → pothole eye disabled, logged; street eye still runs |
| GPS CSV | ingest | Missing/malformed → frames carry `lat/lon=None`; map clusters at city fallback with a warning banner |

Tracing: none in v1 (LangSmith deliberately out of scope for the prototype; prints/logging only).

---

## Persistence

- Checkpointer: `SqliteSaver` at `data/runs/checkpoints.sqlite`
- One thread per run: `thread_id = f"video:{run_id}"` minted in `run_pipeline.py`
- Event store: `data/runs/events.db` — upsert by `event_id` (idempotent across resumes/replays)
- Run artifacts: `data/runs/<run_id>/{frames/, snapshots/, map.html, report.md}`
- All three are gitignored; the store is rebuilt by re-running clips if lost

---

## Environment Variables

| Variable | Used in | Notes |
| --- | --- | --- |
| `VLM_BASE_URL` | tools/vlm.py | OpenAI-compatible endpoint (vLLM/ollama/OpenRouter/DashScope-compatible) |
| `VLM_API_KEY` | tools/vlm.py | Endpoint key; `ollama` accepts any placeholder |
| `VLM_MODEL` | tools/vlm.py | Default `qwen2.5-vl-7b-instruct` |
| `POTHOLE_MODEL_PATH` | tools/detectors.py | Path to RDD2022-trained .pt; absent → pothole eye disabled |
| `YOLO_STREET_WEIGHTS` | tools/detectors.py | Default `yolov8n.pt` (auto-download) |
| `ROADFIX_DATA_DIR` | config.py | Override `data/` (tests) |
| `ROADFIX_STUB_MODELS` | tools/detectors.py, tools/vlm.py | `1` → deterministic stub detections/verdicts (tests, no-GPU demos) |

Push credentials (`GITHUB_USERNAME`, `GITHUB_TOKEN`) are pipeline-level, used by the AGENTS.md git protocol only — never referenced by application code.

---

## Invariants

Rules the implementation must never violate:

- Only `graph.py` wires the root graph. Node names match the topology table in graph-design.md exactly.
- Node functions never import from `ui/` or `scripts/`. The dashboard never triggers runs.
- All model/config literals (fps, thresholds, model names) live in `config.py`, sourced from the registries — never inline in nodes.
- Nodes never call ultralytics/openai/cv2 directly — all external work goes through registered tools.
- State updates are returned as partial dicts merged by reducers — no node ever mutates the state object.
- Frames, snapshots, and other bulk data never enter graph state — paths and metadata only.
- The verify→gate cycle is bounded: `attempt` field, capped by `MAX_VERIFY_ATTEMPTS` in config.
- Every tool failure returns a structured result or raises `ToolError` caught at the node — a run degrades, never dies.
- Published events are upserted by `event_id` — replay/resume never duplicates rows.
- `recursion_limit` is set in `config.py` and referenced everywhere — never hardcoded at invoke sites.
- Prompt text lives only in `src/roadfix/prompts/`, version-pinned to prompt-registry.md.
