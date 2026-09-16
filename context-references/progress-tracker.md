# Progress Tracker

> REFERENCE EXAMPLE — format for `context/progress-tracker.md`. LIVING FILE: update after every completed feature — same commit as the feature. This example shows the bootstrap state right after intake, BEFORE any feature is built.

---

## Current Status

**Phase:** Phase 0 — Skeleton
**Last completed:** none (bootstrap)
**Next:** 01 Project Scaffold

---

## Progress

### Phase 0 — Skeleton

- [ ] 01 Project Scaffold
- [ ] 02 Stub Graph End-to-End

### Phase 1 — Tools

- [ ] 03 tavily_search
- [ ] 04 fetch_page
- [ ] 05 save_note

### Phase 2 — Subgraphs

- [ ] 06 Research Loop Subgraph
- [ ] 07 Plan Node + Approval Gate

### Phase 3 — Main Graph

- [ ] 08 Real Wiring + Critique Loop
- [ ] 09 Resume Integrity

### Phase 4 — Evals + Hardening

- [ ] 10 Groundedness Grader + Full Suite
- [ ] 11 Regression Wiring + API Polish

---

## Decisions Made During Intake

- **Language:** Python 3.12 (user requirement; LangGraph-first ecosystem).
- **Checkpointer:** SQLite via `langgraph-checkpoint-sqlite` — single-user internal tool; Postgres deferred until multi-user is in scope.
- **Search provider:** Tavily (user-provided key); REST called over httpx instead of the SDK to keep the dependency surface minimal.
- **Models:** gpt-4o for plan/synthesize/critique, gpt-4o-mini for note extraction — cost split recorded in prompt-registry.md model policy.
- **HITL:** exactly one interrupt, after plan. User explicitly rejected per-sub-question approvals as too much friction.
- **Retry design:** one critique→research retry max, enforced by retry_count — unbounded loops rejected at intake.
- **Eval judging:** LLM-as-judge for groundedness only; structural properties (citation coverage, plan fidelity, resume integrity) are scripted checks — no LLM.
- **No UI in v1** — Studio + REST only. UI trio of context files intentionally absent.

---

## Notes

*(append one block per completed feature, newest first — example of the expected format from a hypothetical feature:)*

- **Feature 02 (example format):** stub run green in Studio; stub e2e `tests/e2e/test_stub_run.py` passes in 1.8 s. Caveat: `DEV_AUTO_APPROVE` added in config.py for stub-only — must be removed in feature 08. Gate: stub e2e. Registries updated: tool-registry (stub statuses → UNTESTED note).
