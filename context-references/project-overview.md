# Project Overview

> REFERENCE EXAMPLE — this file demonstrates the exact FORMAT for `context/project-overview.md`. Content describes the example project (DeepResearch). When generating for a new project: keep every section, in this order, filled with the new project's truth.

---

## About the Agent

DeepResearch is an autonomous research assistant built on LangGraph. The user submits one research question; the agent decomposes it into sub-questions, gets human approval on the plan, researches each sub-question with web search and page fetching, extracts cited notes, and synthesizes a single grounded briefing where every claim carries a citation. Runs end when the briefing is delivered — the agent does not act on the research, only produces it.

The entire run is traced in LangSmith and checkpointed in SQLite, so any run can be paused at the human-approval gate, resumed later, inspected, or replayed.

---

## The Problem It Solves

Manual research on an unfamiliar topic takes hours: decomposing the question, running many searches, skimming low-quality pages, and stitching findings into something coherent with sources. Existing chatbots answer in one shot, without depth control, without verifiable citations, and without a human checkpoint on what will be researched.

DeepResearch removes the grunt work but keeps the human in charge of direction: the user approves the research plan before execution, and receives a briefing where every claim is traceable to a source. It turns a two-hour manual process into a five-minute review-and-approve process.

---

## Trigger Points

```
POST /research       → API run (body: { question, depth })
LangGraph Studio     → manual run (graph: deep_research)
```

The API accepts a question and a depth parameter (`quick` = 3 sub-questions, `standard` = 5, `deep` = 8). Studio runs use `standard`.

---

## One Perfect Run

1. User submits: "What are the tradeoffs of SQLite as an agent checkpoint store?"
2. `plan` node decomposes into 5 sub-questions (durability, concurrency, latency, ops burden, alternatives)
3. Graph **interrupts** — human reviews the plan, edits one sub-question, approves
4. `research` loop runs per sub-question: `tavily_search` → conditional `fetch_page` → `take_notes` (cited notes extracted)
5. `synthesize` node drafts the briefing from all notes
6. `critique` node checks every claim has a citation; finds one unsupported claim → routes back to research once
7. `synthesize` regenerates; critique passes
8. Briefing returned: summary, findings per sub-question, contradictions found, source list

---

## Inputs and Outputs

| Direction | Field | Type | Notes |
| --- | --- | --- | --- |
| Input | `question` | string | The research question. Required. |
| Input | `depth` | enum | `quick` / `standard` / `deep`. Default `standard`. |
| Output | `briefing` | Briefing (jsonb) | Full structured briefing — schema in graph-design.md |
| Output | `run_meta` | object | Sub-questions used, tool call counts, tokens spent, thread_id |

---

## Scope: In

- Question decomposition into 3–8 sub-questions (depth-dependent)
- Human approval interrupt on the research plan — edit + approve + reject
- Web search via Tavily; conditional full-page fetch for promising results
- Cited note extraction per sub-question (gpt-4o-mini)
- Briefing synthesis (gpt-4o) with mandatory per-claim citations
- One automatic critique → re-research loop for unsupported claims
- SQLite checkpointer — resumable, inspectable runs
- LangSmith tracing on every run
- REST API endpoint + Studio runnability
- Golden-case eval suite (3 cases) with groundedness grading

## Scope: Out

- Multi-agent debate or self-consistency voting
- PDF / document upload as research sources — web only
- Cross-run memory between different research questions
- Streaming partial briefings to the client — full briefing on completion only
- Scheduled / recurring research runs — manual trigger only
- Authentication or multi-tenancy — single-user internal tool
- Vector store integration
- Non-English research questions (v1)
- Slack / email delivery of briefings
- Cost-ceiling circuit breaker (logged as future work, not built)

---

## Human-in-the-Loop

Exactly one interrupt: after `plan`, before `research`. The human may approve, edit sub-questions, or reject. Rejection ends the run with a cancelled status. No other node pauses.

---

## Success Criteria

- A `standard` run completes in under 3 minutes end-to-end (excluding approval wait)
- Every claim in a briefing maps to at least one source URL — zero uncited claims on golden cases
- Groundedness score (LLM-graded, rubric in eval-plan.md) ≥ 0.8 on all 3 golden cases
- Edited plans are respected verbatim — the loop researches exactly the approved sub-questions
- An interrupted run resumes from the approval gate with zero lost state
- Tool failures (search timeout, fetch 404) degrade gracefully — the run completes with fewer sources, never crashes
- Every run appears in LangSmith with full trace
- Sub-50-line briefings: tight, skimmable, no filler

---

## Target User

A developer or analyst who needs fast, verifiable background research on technical topics and who wants to steer the scope of that research before it runs — not a fully hands-off agent.
