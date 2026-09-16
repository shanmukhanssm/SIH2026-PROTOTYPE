# Graph Design

> REFERENCE EXAMPLE — format for `context/graph-design.md`. This file is the CONTRACT: written before any real code, changed only when the topology legitimately changes (and then tool-registry/prompt-registry are updated in the same commit).

---

## Topology

```
                    START
                      │
                      ▼
                  ┌───────┐
                  │ plan  │  (gpt-4o) decompose question → sub_questions
                  └───┬───┘
                      │
                ◆ INTERRUPT ◆   human approval gate: approve / edit / reject
                      │
                      ▼ reject → END (status: cancelled)
                      ▼ approve/edit
              ┌───────────────┐
              │ research      │  SUBGRAPH (compiled, added as one node)
              │ (loop per     │    ├─ search      → tavily_search
              │ sub-question) │    ├─ fetch?      → fetch_page (conditional)
              │               │    └─ take_notes   → save_note + gpt-4o-mini extraction
              └───────┬───────┘
                      ▼
                ┌────────────┐     unsupported claim
                │ synthesize │──────┐  (max 1 retry)
                │  (gpt-4o)  │◄─────┤
                └──────┬─────┘      │
                       ▼            │
                 ┌──────────┐       │
                 │ critique │───────┘ routes back to research
                 └────┬─────┘
                      ▼ pass
                     END (briefing in state)
```

---

## Topology Table

| Node | Type | Model | Tools | Consumes from state | Writes to state |
| --- | --- | --- | --- | --- | --- |
| `plan` | node | gpt-4o (temp 0.4) | — | `question`, `depth` | `sub_questions`, `plan_approved=False` |
| *(interrupt)* | HITL gate | — | — | `sub_questions` | `plan_approved`, (edited) `sub_questions` |
| `research` | compiled subgraph | gpt-4o-mini inside | tavily_search, fetch_page, save_note | `sub_questions` | `notes`, `sources`, `tool_call_counts` |
| `synthesize` | node | gpt-4o (temp 0.3) | — | `notes`, `sources`, `question` | `briefing`, `retry_count` |
| `critique` | node | gpt-4o (temp 0.2) | — | `briefing`, `notes` | `critique_pass`, `unsupported_claims` |

| Conditional edge | From | Condition | To |
| --- | --- | --- | --- |
| `route_after_gate` | *(interrupt)* | `action == "reject"` | END |
| `route_after_gate` | *(interrupt)* | approve / edit | `research` |
| `should_fetch` | `search` (inside subgraph) | ≥ 1 result with score ≥ 0.7 and unread URL | `fetch` |
| `should_fetch` | `search` | otherwise | `take_notes` |
| `next_sub_question` | `take_notes` | unprocessed sub-questions remain | `search` |
| `next_sub_question` | `take_notes` | all processed | outer graph (return) |
| `route_after_critique` | `critique` | `critique_pass == True` or `retry_count >= 1` | END |
| `route_after_critique` | `critique` | unsupported claims and `retry_count == 0` | `research` |

---

## State Schema

```python
from typing import Annotated, Literal
from operator import add
from pydantic import BaseModel, Field

class SubQuestion(BaseModel):
    id: str
    text: str
    status: Literal["pending", "done"] = "pending"

class Note(BaseModel):
    sub_question_id: str
    content: str
    source_url: str
    confidence: float = Field(ge=0.0, le=1.0)

class Source(BaseModel):
    url: str
    title: str
    fetched: bool
    search_score: float

class Briefing(BaseModel):
    summary: str
    findings: list[dict]        # {sub_question_id, finding_text, citations[list[url]]}
    contradictions: list[str]
    sources: list[str]

class ResearchState(BaseModel):
    # inputs
    question: str
    depth: Literal["quick", "standard", "deep"] = "standard"
    # plan
    sub_questions: Annotated[list[SubQuestion], lambda a, b: b]        # overwrite — plan is atomic
    plan_approved: bool = False
    # research accumulation
    notes: Annotated[list[Note], add]                                  # append — many nodes contribute
    sources: Annotated[list[Source], add]                              # append, deduped in take_notes
    tool_call_counts: Annotated[dict[str, int], lambda a, b: {**a, **b}]  # merge dicts
    # synthesis + critique
    briefing: Briefing | None = None                                   # overwrite
    retry_count: int = 0
    critique_pass: bool = False
    unsupported_claims: Annotated[list[str], add] = []                 # append
```

### Reducer Rules

- **Appends** (`notes`, `sources`, `unsupported_claims`): `operator.add` — contributions accumulate across the loop and retries.
- **Overwrites** (`sub_questions`, `briefing`, `plan_approved`): last-writer-wins — these are atomic decisions owned by exactly one node.
- **Merges** (`tool_call_counts`): dict-merge reducer.
- If a new field needs a reducer not listed here, add the reducer AND this schema section in the same commit.

---

## Node Specs

### `plan`
- **Responsibility:** decompose `question` into 3–8 sub-questions per `depth` map in config; each sub-question self-contained and researchable via web search.
- **Input:** `question`, `depth`. **Output:** `sub_questions` (overwrite), `plan_approved=False`.
- **Structured output:** `PlanOutput { sub_questions: list[SubQuestion] }` via `with_structured_output`.
- **Failure:** model/validation failure after one retry → state gets `sub_questions=[]`; router sees empty plan → END with status `failed`. Never raises.

### *(interrupt — approval gate)*
- **Contract:** `interrupt(payload={"sub_questions": [...], "question": ...})`. Resume value: `{"action": "approve"} | {"action": "edit", "sub_questions": [...]} | {"action": "reject"}`.
- On `edit`: state overwrites `sub_questions` from resume payload, sets `plan_approved=True`. On `reject`: `plan_approved=False` → router ends run as `cancelled`.
- **Invariant:** this is the only `interrupt()` call in the entire codebase.

### `research` (subgraph)
- **Responsibility:** for each pending sub-question: search → (conditionally fetch best result) → extract cited notes → mark done; loop until none pending.
- **Sub-state:** own `ResearchSubState` (sub-question pointer, per-question search results) — mapped to/from parent state at the boundary; only `notes`, `sources`, `tool_call_counts` flow back.
- **Tools used:** `tavily_search` (every iteration), `fetch_page` (only when `should_fetch` fires), `save_note` (per note, before appending to state).
- **Failure:** a sub-question whose search + fetch both yield nothing is marked done with zero notes and logged — the loop never stalls on one bad sub-question.

### `synthesize`
- **Responsibility:** draft the Briefing from all notes: summary, per-sub-question findings with citations, contradictions between sources.
- **Input:** `notes`, `sources`, `question`. **Output:** `briefing` (overwrite), `retry_count` incremented only on re-synthesis.
- **Structured output:** `Briefing` schema above. Citation URLs must come from `sources` — the node drops any invented URL before returning (post-validation).
- **Failure:** validation failure after one retry → briefing with `summary` only and `contradictions=["synthesis degraded"]`; run still ends with a usable, honest artifact.

### `critique`
- **Responsibility:** verify every finding's citations resolve to real notes; list unsupported claims.
- **Input:** `briefing`, `notes`. **Output:** `critique_pass`, `unsupported_claims`.
- **Routing:** pass (or `retry_count >= 1`) → END; fail first time → back to `research` with the unsupported claims as priority re-checks.

---

## HITL Contract

| Aspect | Value |
| --- | --- |
| Interrupt count | Exactly 1 per run |
| Payload | `sub_questions` + original `question` |
| Resume actions | `approve` / `edit` (with edited list) / `reject` |
| Resume API | `POST /research/{thread_id}/resume` |
| Timeout | None — run waits indefinitely at the gate (checkpointed) |

---

## Run Limits

| Limit | Value | Defined in |
| --- | --- | --- |
| `recursion_limit` | 50 | config.py |
| Max sub-questions | quick 3 / standard 5 / deep 8 | config.py DEPTH_MAP |
| Max fetches per sub-question | 2 | config.py |
| Synthesis retries | 1 | config.py |
