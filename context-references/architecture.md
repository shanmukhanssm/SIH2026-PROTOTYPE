# Architecture

> REFERENCE EXAMPLE — format for `context/architecture.md`. Content describes the example project (DeepResearch).

---

## Stack

| Layer | Tool | Purpose |
| --- | --- | --- |
| Graph framework | LangGraph (Python) ≥ 0.4 | State graph, subgraphs, interrupts, checkpointing |
| Language | Python 3.12, strict typing | Throughout |
| LLM — planning + synthesis | OpenAI gpt-4o | Plan, synthesize, critique nodes |
| LLM — extraction | OpenAI gpt-4o-mini | Note-taking node (high volume, cheap) |
| Web search | Tavily API | Search results for each sub-question |
| Page fetching | httpx + trafilatura | Full-page fetch + main-content extraction |
| Checkpointer | langgraph-checkpoint-sqlite | Run persistence, resume at the approval gate |
| Observability | LangSmith | Tracing every run, eval datasets and results |
| Schema validation | Pydantic v2 | State models, tool args, structured outputs |
| API | FastAPI | POST /research + resume endpoints |
| Testing | pytest + pytest-asyncio | Unit, subgraph, e2e tests |
| Lint / types | ruff + mypy (strict) | CI-quality gate |

---

## Folder Structure

```
/
├── AGENTS.md                    → Pipeline kernel (project-agnostic, not edited per project)
├── context/                     → The ten project truth files
├── skills/                      → User-provided skills (planning-and-task-breakdown, ponytail)
├── src/deep_research/
│   ├── __init__.py
│   ├── config.py                → Constants: depth→sub-question map, recursion limit, timeouts
│   ├── state.py                 → ResearchState, reducers, all Pydantic state models
│   ├── graph.py                 → Main graph assembly (the ONLY file that wires the root graph)
│   ├── nodes/
│   │   ├── plan.py              → plan node (decompose + emit interrupt payload)
│   │   ├── take_notes.py        → note extraction node (lives in research subgraph)
│   │   ├── synthesize.py        → briefing synthesis node
│   │   └── critique.py          → groundedness critique node
│   ├── subgraphs/
│   │   └── research.py          → research loop subgraph (search → fetch? → notes)
│   ├── tools/
│   │   ├── search.py            → tavily_search tool
│   │   ├── fetch.py             → fetch_page tool
│   │   └── notes.py             → save_note tool
│   ├── prompts/
│   │   ├── planner.py           → planner prompt text (v-pinned, referenced by prompt-registry)
│   │   ├── note_taker.py
│   │   ├── synthesizer.py
│   │   └── critique.py
│   └── api.py                   → FastAPI app: POST /research, POST /research/{thread_id}/resume
├── tests/
│   ├── tools/                   → unit tests per tool (external calls mocked)
│   ├── subgraphs/               → research subgraph isolated runs
│   └── e2e/                     → golden-case end-to-end runs
├── evals/
│   ├── datasets/                → golden inputs + expected properties (jsonl)
│   ├── graders/                 → groundedness grader, citation coverage, structure checks
│   └── run_evals.py             → eval entrypoint (per layer: tools / subgraphs / graph)
├── langgraph.json               → Studio config: graph → deep_research.graph:graph
├── pyproject.toml
├── .env.example                 → every required var, no values
└── .gitignore                   → includes .env, *.sqlite, __checkpoints__
```

---

## System Boundaries

| Folder | Owns | Never contains |
| --- | --- | --- |
| `src/deep_research/nodes/` | Node functions: read state, call model/tool, return state updates | Graph wiring, other nodes' logic |
| `src/deep_research/subgraphs/` | Internal wiring of one subgraph | Knowledge of the root graph |
| `src/deep_research/tools/` | Tool implementations + their arg schemas | Node logic, prompt text |
| `src/deep_research/prompts/` | Prompt text constants, versioned | Code logic |
| `src/deep_research/graph.py` | Root graph assembly only | Business logic inside nodes |
| `src/deep_research/api.py` | HTTP concerns: parse, invoke, resume, respond | Agent logic |
| `tests/`, `evals/` | Verification | Anything imported by `src/` at runtime |
| `context/` | Truth documents | Executable code |

---

## Data Flow

```
POST /research {question, depth}
        ↓
api.py — validate, create thread_id
        ↓
graph.astream(state, config={thread_id})
        ↓
plan ──→ INTERRUPT (approval gate) ──→ POST /research/{id}/resume {action: approve|edit|reject, sub_questions?}
        ↓                                     ↓
research subgraph loop                 state resumes from checkpoint
   per sub-question:                        ↓
   tavily_search → fetch_page?        research subgraph loop
   → take_notes                             ↓
        ↓                            synthesize → critique ──(unsupported claim, 1 retry)──→ research
all sub-questions done                     ↓ (pass)
        └──────────────────────→ briefing in final state ←────────┘
                                        ↓
api.py returns {briefing, run_meta}    full trace in LangSmith
```

---

## External Services

| Service | Used by | Failure policy |
| --- | --- | --- |
| OpenAI API | plan, take_notes, synthesize, critique | Retry once with backoff; on second failure node returns structured error state; run continues with degraded output, never crashes |
| Tavily API | tavily_search tool | Timeout 10s; empty results are valid output (logged), not an error |
| Target websites | fetch_page tool | Timeout 15s, max 1 redirect chain; 404/blocked → ToolError, treated as "skip this source" |
| LangSmith | all runs | Tracing failure must never fail a run — client configured non-blocking |

---

## Persistence

- Checkpointer: `SqliteSaver` at `./__checkpoints__/deep_research.sqlite`
- One thread per run: `thread_id = uuid4()` minted in `api.py`
- The approval interrupt persists mid-run: process may restart, resume continues from checkpoint
- Checkpoint DB is gitignored; no research data is treated as durable storage

---

## Environment Variables

| Variable | Used in | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | all LLM nodes | Required |
| `TAVILY_API_KEY` | tools/search.py | Required |
| `LANGSMITH_API_KEY` | tracing setup | Required |
| `LANGSMITH_PROJECT` | tracing setup | e.g. `deep-research` |
| `LANGSMITH_TRACING` | tracing setup | `"true"` in dev and prod |
| `DEEP_RESEARCH_DB_PATH` | config.py | Checkpointer path override (tests) |

GitHub push credentials (`GITHUB_USERNAME`, `GITHUB_TOKEN`) are pipeline-level, used by AGENTS.md git protocol only — never referenced by application code.

---

## Invariants

Rules the implementation must never violate:

- Only `graph.py` wires the root graph. Only `subgraphs/research.py` wires the research subgraph.
- Node functions never import from `api.py`. `api.py` never contains agent logic.
- All LLM calls read model + temperature from `prompt-registry.md` values via `config.py` — never inline literals in nodes.
- All tool calls go through the registered tools in `tools/` — nodes never call OpenAI/Tavily/httpx directly for tool purposes.
- State updates are returned as partial dicts and merged by reducers — no node ever mutates the state object.
- The approval interrupt is the only interrupt. Nothing else may call `interrupt()`.
- Every external call has a timeout. Every tool failure returns a structured error — never raises past the node.
- `recursion_limit` is set in `config.py` (default 50) and referenced everywhere — never hardcoded at invoke sites.
- Prompt text lives only in `src/deep_research/prompts/`, version-pinned to `prompt-registry.md`.
