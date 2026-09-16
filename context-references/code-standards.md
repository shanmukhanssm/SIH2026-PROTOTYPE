# Code Standards

> REFERENCE EXAMPLE — format for `context/code-standards.md`. Implementation rules for every session. Format is authoritative; content is the example project. NOTE: all code below is written under the `ponytail` skill — this file shows the standard, the skill governs the practice.

---

## Engineering Mindset

- Think before implementing — verify against graph-design.md before writing a line.
- Scope is sacred: only the current feature, nothing "while we're here".
- Every feature testable immediately after implementation, before the next begins.
- Clean over clever: a junior developer must be able to follow the graph by reading graph.py and the node files top to bottom.
- Failures are expected and handled — never a crash path.

---

## Python

- Python 3.12. Full type hints — every function signature complete; `mypy --strict` clean.
- Never `Any` — use `object` and narrow, or proper generics.
- Pydantic v2 models for: state, tool args, structured outputs. Never raw dicts where a model applies.
- `async` throughout the graph path (`astream`, `ainvoke`, async tools). Sync only in pure helpers.
- `const`-style module constants in `config.py` — UPPER_SNAKE, typed.
- No default mutable args; no star-imports; ruff clean.

---

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Nodes | lower_snake verb-or-role | `plan`, `take_notes`, `synthesize`, `critique` |
| Tools | lower_snake verb | `tavily_search`, `fetch_page`, `save_note` |
| State models | PascalCase + role suffix | `ResearchState`, `ResearchSubState`, `PlanOutput` |
| Files | lower_snake, one node/tool per file | `plan.py`, `search.py` |
| Tests | mirror the source tree | `tests/tools/test_search.py` |
| Constants | UPPER_SNAKE in config.py | `RECURSION_LIMIT`, `DEPTH_MAP` |

---

## Node Template

Every node follows this exact shape:

```python
# src/deep_research/nodes/plan.py
from deep_research.config import DEPTH_MAP
from deep_research.prompts.planner import PLANNER_V1
from deep_research.state import PlanOutput, ResearchState
from deep_research.models import get_model  # central model factory, reads config

async def plan(state: ResearchState) -> dict:
    """Decompose the question into sub-questions. Returns state UPDATE only."""
    n = DEPTH_MAP[state.depth]
    model = get_model("plan").with_structured_output(PlanOutput)
    try:
        result = await model.ainvoke(PLANNER_V1.format(n_sub_questions=n, depth=state.depth))
    except Exception:
        return {"sub_questions": []}  # router ends run as failed — never raise past a node
    return {"sub_questions": result.sub_questions, "plan_approved": False}
```

Rules: typed state in, partial-dict state out. No mutation of `state`. Prompt text imported from `prompts/`, model from the factory — zero literals. One try/except at the boundary, structured fallback in state.

---

## Tool Template

Every tool follows this exact shape:

```python
# src/deep_research/tools/search.py
import httpx
from pydantic import BaseModel, Field

class SearchArgs(BaseModel):
    query: str = Field(..., min_length=3)
    max_results: int = Field(5, ge=1, le=10)

class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str
    score: float

SEARCH_TIMEOUT_S = 10.0

async def tavily_search(args: SearchArgs) -> list[SearchResult]:
    """One web search. Empty list on no results. ToolError on service failure."""
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S) as client:
        try:
            resp = await client.get(
                "https://api.tavily.com/search",
                params={"api_key": TAVILY_KEY, "query": args.query, "max_results": args.max_results},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 503):
                await asyncio.sleep(0.5)  # single retry for rate/transient
                resp = await _retry_once(args)
            else:
                raise ToolError("search_unavailable") from exc
    return [SearchResult(**r) for r in resp.json().get("results", [])]
```

Rules: Pydantic args in, Pydantic model out. Explicit timeout constant. Empty/None results are valid returns; only service-level failure raises `ToolError` — and nodes catch it.

---

## State Update Rules

- Nodes return partial dicts of ONLY the keys they own.
- Appends go through `Annotated[..., operator.add]` fields — a node returning a full rebuilt list is a bug.
- Overwrite fields are owned by exactly one node each (see graph-design.md topology table).
- Subgraph boundaries: explicit in/out key mapping — never share the parent state object by reference.

---

## Error Handling

- `ToolError(message_code)` — tools raise only this, with a stable code string.
- Nodes catch everything at their boundary and translate to state (empty plan, degraded briefing, skipped source) — a run degrades, never dies.
- Logging: `structlog`, one line per significant event, prefix `[node-or-tool-name]`. Never `print`.
- User-facing surfaces (API) never see raw tracebacks — mapped to `{status, error}` responses.
- No empty `except:` blocks — ever.

---

## Config Constants

Single source in `config.py`; import everywhere; never redeclare:

```python
RECURSION_LIMIT: int = 50
DEPTH_MAP: dict[str, int] = {"quick": 3, "standard": 5, "deep": 8}
MAX_FETCHES_PER_SUBQUESTION: int = 2
SYNTHESIS_RETRIES: int = 1
FETCH_TIMEOUT_S: float = 15.0
SEARCH_TIMEOUT_S: float = 10.0
```

---

## Dependencies (closed list)

`langgraph`, `langchain-openai`, `langgraph-checkpoint-sqlite`, `openai`, `tavily-python`, `httpx`, `trafilatura`, `pydantic`, `fastapi`, `uvicorn`, `structlog`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

Nothing else without updating this list and `pyproject.toml` in the same commit.

---

## Comments and Docstrings

- One-line docstring per node/tool stating responsibility — matches its registry entry.
- Comments only for WHY (a non-obvious decision), never WHAT.
- No TODO comments in pushed code — TODOs become build-plan features or are dropped.

---

## Testing Standards

- pytest + pytest-asyncio; external services mocked (respx for httpx, recorded fixtures for Tavily).
- Every tool: contract tests per its registry error behavior (timeout, empty, failure fixture).
- Every node: state-in → state-update-out tests, including the failure path.
- E2E: golden cases from eval-plan.md, marked `@pytest.mark.e2e`, excluded from unit runs.
- Coverage target: every node and tool has at least its happy path + one failure path tested. No percentage chasing.
