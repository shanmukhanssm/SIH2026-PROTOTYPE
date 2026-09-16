# Eval Plan

> REFERENCE EXAMPLE — format for `context/eval-plan.md`. Updated when gates, datasets, or thresholds change. Format is authoritative; content is the example project.

---

## Philosophy

Evals are layered to match the build ladder: every layer has its own datasets, graders, and gate. A phase gate = the corresponding layer passing at threshold. Evals run via `python -m evals.run_evals --layer tools|subgraphs|graph` and are recorded in `progress-tracker.md` on every run.

Golden datasets are small and hand-picked — 12 search queries, 8 fetch fixtures, 3 end-to-end questions — not synthetic bulk. Quality over volume; every case must have a knowable right answer.

---

## Layer 1 — Tool Evals (gate for Phase 1)

### `tavily_search`
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/tool_search.jsonl` — 12 known-answer queries |
| Assertions | ≥ 1 result per query; ≥ 10/12 queries contain the known-relevant domain in top 5; empty never raises |
| Threshold | 10/12 relevance + 12/12 no-crash |
| Run cost | ~12 Tavily calls — safe to run on every tool change |

### `fetch_page`
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/tool_fetch.jsonl` — 8 fixture URLs (healthy, 404, blocked, non-HTML, redirect, oversized, slow, https) |
| Assertions | Healthy pages: `fetched_ok=True`, text > 500 chars, boilerplate-free. Failure fixtures: `fetched_ok=False`, zero raises, < 16 s each |
| Threshold | 8/8 contract compliance |

### `save_note`
| Aspect | Value |
| --- | --- |
| Dataset | unit-level (3 cases: single append, ordering, disk-error fallback) |
| Assertions | file well-formed; disk error → returns False, state note survives |
| Threshold | 3/3 |

---

## Layer 2 — Subgraph Evals (gate for Phase 2)

### research loop
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/subgraph_research.jsonl` — 5 sub-questions with mocked search/fetch (recorded fixtures, not live web) |
| Assertions | (1) every sub-question reaches `done`; (2) unanswerable sub-question yields zero notes and does not stall; (3) `should_fetch` fires only for results scoring ≥ 0.7; (4) notes all carry valid URLs and confidence; (5) loop terminates ≤ configured max fetches |
| Threshold | 5/5 assertions on all 5 cases |

### plan node + gate routing
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/subgraph_plan.jsonl` — 4 question/depth pairs + 3 resume actions |
| Assertions | plan count matches DEPTH_MAP; `edit` resume researches exactly the edited list; `reject` ends with cancelled; edited sub-questions are non-overlapping |
| Threshold | 7/7 |

---

## Layer 3 — Graph Evals (gates for Phase 3 + Phase 4)

### Golden cases

| ID | Question | Depth | Key property that must hold |
| --- | --- | --- | --- |
| G1 | "What are the tradeoffs of SQLite as an agent checkpoint store?" | standard | briefing names ≥ 3 concrete tradeoffs, all cited |
| G2 | "How does Tavily's pricing model compare to SerpAPI?" | quick | contains at least one contradiction entry or explicit "no disagreement found" |
| G3 | "What changed in LangGraph's checkpointer API in the last year?" | deep | every finding cites a source; zero invented URLs |

### Graders

| Grader | Method | Threshold |
| --- | --- | --- |
| Structure validity | Pydantic validation of Briefing + all citations resolve to `sources` | 100% on all 3 |
| Citation coverage | script: claims with ≥ 1 citation / total claims | ≥ 0.9 per case |
| Groundedness | LLM-as-judge (gpt-4o, temp 0.0, rubric: is each claim entailed by its cited note?) | mean score ≥ 0.8 per case |
| Plan fidelity | script: executed sub-questions == post-gate approved list | 100% |
| Resume integrity | run G1, kill process at gate, restart, resume | briefing identical fields to uninterrupted run, modulo tool nondeterminism logged |

### Gate mapping

| Ladder phase | Required layer | Threshold |
| --- | --- | --- |
| Phase 0 — skeleton | none (manual Studio check) | — |
| Phase 1 — tools | Layer 1 | per-tool thresholds above |
| Phase 2 — subgraphs | Layer 2 | per-subgraph thresholds above |
| Phase 3 — main graph | Layer 3: structure + citation coverage + plan fidelity + resume integrity | per grader |
| Phase 4 — evals | Layer 3: full, incl. groundedness; regression mode on | all thresholds |

---

## Regression Policy

- Any change to a tool → rerun its Layer 1 set + Layer 3 golden cases.
- Any prompt version bump → rerun Layer 3 full (groundedness included).
- Any state schema or routing change → rerun Layer 2 + Layer 3 full.
- A golden case that regresses blocks push until fixed or the case is explicitly re-scoped with user sign-off (recorded in progress-tracker.md).

---

## Flakiness Rules

- LLM-judged graders run once; borderline (within 0.05 of threshold) results trigger exactly one rerun; the second result stands and is recorded either way.
- Live-web cases (G1–G3) may shift as the web changes: a failure re-ran within 24 h that passes is recorded as environment-drift, not regression — with both runs noted.
