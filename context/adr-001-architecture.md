# ADR-001: SIH2026-PROTOTYPE — Bus-Dashcam Road-Issue Detector

**Problem ID:** SIH26124 · One dashcam on a public bus + ready-made AI models. We never train anything.

## CONTEXT

Task: turn one bus-dashcam video into a prioritized, verified road-fix list (potholes, kids-crossing hotspots, traffic jams) that a municipal team can open on one map page. Input = one video file (+ optional GPS CSV from the bus logger). Output = verified events in an SQLite store, a Folium heatmap HTML, and a markdown report per run.

- **Quality bar:** prototype for SIH 2026 judging — honest numbers (precision/recall table on hand-labeled clips), zero model training, demo-runnable end-to-end.
- **Volume:** one clip per run; a 10-second clip → ~25 snapshots (2–3 fps) → 3–8 flagged events.
- **Latency tolerance:** minutes per clip — batch processing, no interactive latency requirement.
- **Feasibility verdict:** agent checklist run — decomposition is FIXED (5 stages every run), steps do not depend on model decisions, only the number of verify calls varies at runtime. Verdict: **build a workflow with dynamic fan-out, not an autonomous agent.** LangGraph earns its place solely for Send fan-out, reducer fan-in, and checkpoint/resume — not for open-ended agency.

## DECISION

- **Pattern stack (smallest that meets the bar):** chaining (fixed stages WATCH → SPOT → TRACK & FLAG → DOUBLE-CHECK → SHOW) + **Send fan-out** (one VLM verify call per flagged event, N known only at runtime) + a **deterministic confidence gate** (if-else threshold, not an LLM evaluator loop). No planner, no reflection, no router, no multi-agent — nothing in the run needs them.
- **Single graph, zero agents.** One mono-graph, node-per-call. No role exceeds one loop; no isolation need exists.
- **Agency dial per edge:** ingest L0 · detect L0 (fixed model inference) · track+flag L0 (deterministic if-else rules) · **verify L1** (the only model-driven edge: VLM fills a fixed form, strict JSON) · gate L0 (threshold arithmetic) · publish L0. A wrong verdict costs one wrongly published/dropped event — cheap, reversible, so no HITL interrupt in v1.
- **Framework:** LangGraph (Python) — mandated by the team's operating workflow (AGENTS.md), and its Send + SqliteSaver exactly cover the two real requirements (runtime-N fan-out, crash-resume). Framework-agnostic core kept: tools are plain typed functions, business logic (rules, gate) is pure Python, events live in our own SQLite store, prompts in our own registry.
- **Graph granularity:** mono-graph (one team, < 30 nodes). Loops: verify recheck cycle has a visible budgeted exit — `attempt` field capped at `MAX_VERIFY_ATTEMPTS = 2` in config.
- **Deployment topology:** single machine, CLI runner (`scripts/run_pipeline.py`) + Streamlit dashboard reading the store. SqliteSaver checkpointer at `data/runs/checkpoints.sqlite` (single-writer is fine — one run at a time on a laptop).

## ALTERNATIVES CONSIDERED

- **Autonomous ReAct agent with a tool box:** rejected — steps never vary; agency would add latency, cost, and non-determinism for zero benefit (advisor gotcha #1).
- **Supervisor + specialist agents (detector agent, verifier agent):** rejected — no role overflows one loop; multi-agent would add misrouting failure modes to a pipeline that is already role-clear (advisor gotcha #4).
- **supervision-library ByteTrack over ultralytics built-in tracking:** chosen — we run per-frame batch detection across node boundaries, so a stateless per-frame tracker fed detections arrays fits the node contract; ultralytics `model.track()` assumes a continuous video handle.
- **Evaluator-optimizer loop around the VLM:** rejected — the confidence gate (very sure → publish / half sure → one more look / not sure → drop) already gives bounded re-examination with a named exit; an LLM critic would duplicate it.
- **Deferred:** Postgres checkpointer + Postgres events DB — viable; deferred until multi-user or fleet deployment is in scope (measurable trigger: >1 concurrent run or municipal deployment).
- **Deferred:** HITL review page for half-sure events — viable; deferred until judges/municipal feedback demands it.

## CONSEQUENCES

**+** What this buys: crash-resumable runs (SqliteSaver), parallel verification (one VLM call per event in one super-step), honest precision knob (gate thresholds in config, no model retraining), every event carries snapshot + GPS + time in our own store (not locked in graph state), demo runs on one CPU laptop with stub models and on one GPU with real ones.

**−** What this costs / accepted failure modes: VLM unavailability drops events (unverified ≠ published — honest degradation, logged); frames never live in graph state (paths only — checkpoint size stays KB-scale); LastValue races impossible by design (writer audit: every multi-writer key carries a reducer — `verdicts` uses `operator.add`).

**Budgets (per 10-second clip, 25 frames):**

| Axis | Budget |
| --- | --- |
| Detect (2 YOLO eyes, CPU) | 50 inferences × ~150 ms ≈ 8 s; GPU ≈ 2 s |
| Verify | 3–8 events × 1 VLM call (≤ 2 attempts) ≈ 10–40 s |
| Whole run | < 90 s CPU, < 30 s GPU — minutes tolerance met |
| Cost | YOLO local = ₹0; VLM = 3–8 calls × ~600 tokens ≈ 5k tokens/clip; monthly at 100 clips/day ≈ 15M tokens — within any OpenAI-compatible tier's TPM with room for 5× burst |
| Reliability | per-step p ≈ 0.95 (default), n ≈ 6 sequential steps → e2e ≈ 0.74 raw; raised by: verify retry (1), drop-not-publish gate (errors are cheap and visible), resume from checkpoint (no step re-pays). Accepted for prototype; HARDEN stage explicitly skipped (`prototype: no harden stage`) |
| Safety | no irreversible actions; publishing an event is an upsert into our store, reversible by deletion; snapshots stay local |

## EVIDENCE (filled in after 30 days)

- measured cost/run: *(pending first real-clip runs)*
- termination-reason distribution: *(pending)*
- eval score: *(pending — eval-plan.md Layer 3 golden cases)*
- escalation rate: *(pending)*
