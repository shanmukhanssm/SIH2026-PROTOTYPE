# Tool Registry

> REFERENCE EXAMPLE — format for `context/tool-registry.md`. LIVING FILE: update in the same commit as any tool change. Format is authoritative; content is the example project.

**How to use this file:** before building any tool, check it exists here. After adding or changing any tool, update this file first, then the code. A tool that is not in this registry does not exist.

---

## Registry Overview

| Tool | File | Consumed by | Side effects | Eval status |
| --- | --- | --- | --- | --- |
| `tavily_search` | `src/deep_research/tools/search.py` | research subgraph (`search`) | External API call | PASS (v1) |
| `fetch_page` | `src/deep_research/tools/fetch.py` | research subgraph (`fetch`) | External HTTP fetch | PASS (v1) |
| `save_note` | `src/deep_research/tools/notes.py` | research subgraph (`take_notes`) | Appends to notes file (audit log) | PASS (v1) |

This list is closed. No other tool may be called from any node. Adding a tool: register here → update graph-design.md node spec → build → eval → set status.

---

## `tavily_search`

**Purpose:** web search for one sub-question. Returns a fixed-size result list with scores and URLs for downstream fetch/note decisions.

```python
from pydantic import BaseModel, Field

class SearchArgs(BaseModel):
    query: str = Field(..., min_length=3, description="Search query for one sub-question")
    max_results: int = Field(5, ge=1, le=10)

class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str
    score: float          # Tavily relevance 0.0–1.0

# Signature
def tavily_search(args: SearchArgs) -> list[SearchResult]: ...
```

| Property | Value |
| --- | --- |
| Timeout | 10 s |
| Retry | 1 retry, 500 ms backoff |
| Error behavior | Tavily down / timeout → raises `ToolError("search_unavailable")` — caught by node, sub-question proceeds with zero results |
| Empty results | Valid output — `[]` is success, logged at info level |
| Rate limit | Self-imposed 1 req/s between calls in the loop |

**Consumers:** `search` node (every loop iteration).
**Eval:** golden-query set (12 queries in `evals/datasets/tool_search.jsonl`); gate = ≥ 10/12 queries return ≥ 1 relevant result (relevance judged by URL/domain match on known-answer queries). Current: 12/12.

---

## `fetch_page`

**Purpose:** fetch one URL and extract main-page text for note extraction. Only called when `should_fetch` routes to it.

```python
class FetchArgs(BaseModel):
    url: str = Field(..., pattern=r"^https?://")
    max_chars: int = Field(8000, ge=1000, le=20000, description="Main-content cap")

class PageContent(BaseModel):
    url: str
    title: str
    text: str             # main content, truncated to max_chars
    fetched_ok: bool

# Signature
def fetch_page(args: FetchArgs) -> PageContent: ...
```

| Property | Value |
| --- | --- |
| Timeout | 15 s, max 3 redirects |
| Retry | No retry — a blocked page stays blocked |
| Error behavior | 404 / 429 / bot-blocked / non-HTML → returns `PageContent(fetched_ok=False, text="")` — never raises. Node treats it as "skip this source". |
| Extraction | `trafilatura.extract()` main content; nav/footer/boilerplate stripped |
| Size guard | text truncated to `max_chars` before returning |

**Consumers:** `fetch` node (max 2 fetches per sub-question, per config).
**Eval:** fixture set of 8 URLs (2 healthy, 2 404, 1 blocked, 1 non-HTML, 1 redirect, 1 oversized) — gate = all 8 handled per contract, zero raises. Current: PASS.

---

## `save_note`

**Purpose:** append one extracted note to the run's audit log file. The note ALSO flows through state — this tool exists so notes survive process restarts independent of the checkpoint and are reviewable outside the graph.

```python
class SaveNoteArgs(BaseModel):
    thread_id: str
    note: str             # rendered note line: [sub_question_id] content (source_url)

# Signature
def save_note(args: SaveNoteArgs) -> bool:   # True on success
```

| Property | Value |
| --- | --- |
| Side effect | Appends one line to `./__checkpoints__/notes_{thread_id}.log` |
| Timeout | 2 s |
| Retry | 1 retry |
| Error behavior | Disk failure → returns `False`, logs; the note still lives in graph state — audit loss is degraded, never fatal |

**Consumers:** `take_notes` node (one call per extracted note).
**Eval:** unit — 3 append cases incl. concurrent-append ordering; gate = all pass, file ends well-formed. Current: PASS.

---

## Rules

- This registry is the closed tool list. Nodes never call external systems except through these tools.
- Signature changes require: update this file → update graph-design.md node spec → change code → re-run tool eval → update status. In the same commit.
- Every tool: Pydantic args, explicit timeout, explicit error behavior — the four columns above are mandatory per tool.
- Eval status values: `UNTESTED` → `PASS (vN)` / `FAIL`. A tool with `UNTESTED` may not be consumed by a phase-2 gate run.
