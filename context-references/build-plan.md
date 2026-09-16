# Build Plan

> REFERENCE EXAMPLE — format for `context/build-plan.md`. Written and extended under the `planning-and-task-breakdown` skill. Format is authoritative; content is the example project.

---

## Core Principle

Design top-down, build bottom-up. The full graph exists as stubs before any real logic — the shape is proven in Studio on day one, then real tools, then real node logic, then real wiring, each layer gated by tests and evals. Every feature is complete, tested, recorded, and pushed before the next begins. Skills: `planning-and-task-breakdown` governs how this file's features are sliced and ordered; `ponytail` governs all code written for every feature below.

---

## Phase 0 — Skeleton

### 01 Project Scaffold
**Logic:**
- pyproject.toml with the closed dependency list; ruff + mypy strict configured
- Folder tree exactly per architecture.md; empty `__init__.py` files
- config.py with all constants; .env.example with every variable, no values
- langgraph.json pointing at `deep_research.graph:graph` (stub graph initially)
**Gate:** `ruff check`, `mypy --strict`, `pytest` (empty suite) all green.

### 02 Stub Graph End-to-End
**Logic:**
- state.py: full ResearchState with reducers exactly per graph-design.md
- All 4 nodes as stubs returning hardcoded realistic values matching schemas (plan → 5 fixed sub-questions; research → 2 fixed notes; synthesize → fixed briefing; critique → pass)
- Interrupt stubbed to auto-approve via a `DEV_AUTO_APPROVE` flag in config (test-only)
- graph.py wired per topology; AsyncSqliteSaver checkpointer
**Verification:** run in LangGraph Studio and via `pytest tests/e2e/test_stub_run.py` — stub input flows START→END, briefing lands in final state.
**Gate:** stub e2e green + Studio screenshot recorded in progress-tracker.

---

## Phase 1 — Tools

### 03 tavily_search
**Logic:** implement per tool-registry.md contract (args model, timeout, retry, ToolError); unit tests with respx incl. empty/429/timeout cases.
**Gate:** tool eval layer 1 (search) ≥ threshold; registry status → PASS.

### 04 fetch_page
**Logic:** implement per registry (8-fixture contract: healthy/404/blocked/non-HTML/redirect/oversized/slow/https); trafilatura extraction; size cap.
**Gate:** tool eval layer 1 (fetch) 8/8; registry status → PASS.

### 05 save_note
**Logic:** implement per registry (append-only log, disk-error fallback); unit tests.
**Gate:** tool eval layer 1 (notes) 3/3; registry status → PASS.

---

## Phase 2 — Subgraphs

### 06 Research Loop Subgraph
**Logic:**
- research.py: per-sub-question loop search → should_fetch? → fetch → take_notes → next; own ResearchSubState with explicit boundary mapping
- take_notes node: real gpt-4o-mini call, NOTE_TAKER_V1, with_structured_output; save_note called per note; notes/sources appended via reducers
- Routers per graph-design.md; unanswerable sub-question marked done with zero notes
**Tests:** layer 2 dataset with recorded fixtures — all 5 assertions on all 5 cases.
**Gate:** layer 2 (research) green.

### 07 Plan Node + Approval Gate
**Logic:**
- plan node: real gpt-4o call, PLANNER_V1, DEPTH_MAP count enforcement
- Replace DEV_AUTO_APPROVE with real interrupt(payload); resume contract per HITL table (approve/edit/reject); route_after_gate router
- api.py minimal: POST /research, POST /research/{thread_id}/resume
**Tests:** layer 2 plan dataset — 7/7 assertions (edit fidelity, reject cancellation).
**Gate:** layer 2 (plan + gate) green.

---

## Phase 3 — Main Graph

### 08 Real Wiring + Critique Loop
**Logic:**
- All stubs now real; synthesize (SYNTHESIZER_V1 + citation post-validation) and critique (CRITIQUE_V1) live
- route_after_critique: pass/retry routing with retry_count guard; recursion_limit from config
- DEV_AUTO_APPROVE flag removed entirely
**Tests:** e2e golden cases G1–G3 (structure validity, citation coverage, plan fidelity).
**Gate:** layer 3 (non-LLM-judge graders) green.

### 09 Resume Integrity
**Logic:**
- Kill-and-resume test harness: start G1, interrupt at gate, restart process, resume from checkpoint
- Assert briefing fields match uninterrupted run modulo logged tool nondeterminism
**Gate:** layer 3 resume-integrity grader green.

---

## Phase 4 — Evals + Hardening

### 10 Groundedness Grader + Full Suite
**Logic:**
- evals/graders/groundedness.py: gpt-4o temp 0.0 judge per rubric in eval-plan.md
- run_evals.py: all three layers runnable via flags; results table printed + saved
**Gate:** groundedness ≥ 0.8 on G1–G3; full suite green.

### 11 Regression Wiring + API Polish
**Logic:**
- Regression mapping per eval-plan.md wired into a make target (`make check`: lint + mypy + unit + affected eval layers)
- api.py final: run_meta reporting, error mapping, LangSmith metadata per run
**Gate:** `make check` fully green — this is the standing pre-push command.

---

## Feature Count

| Phase | Features |
| --- | --- |
| Phase 0 — Skeleton | 2 |
| Phase 1 — Tools | 3 |
| Phase 2 — Subgraphs | 2 |
| Phase 3 — Main Graph | 2 |
| Phase 4 — Evals | 2 |
| **Total** | **11** |

---

## Standing Rules for This File

- Written and every future extension follows the `planning-and-task-breakdown` skill.
- Every feature lists exactly one Gate. No gate, no next feature.
- Feature order is the ladder: no skipping phases, no reordering without a recorded decision.
