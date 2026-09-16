---
name: agent-debugger
description: >-
  Wire agent observability and respond to live incidents: instrumentation contract,
  structured logging, span schemas, four core metrics, trace-based alerting,
  dashboards, checkpoint replay debugging, the 10-minute triage protocol, symptom
  router, 16 incident runbooks, and the ten-minute latency audit. Trigger: agent
  loops forever, cost spiked overnight, empty or truncated responses, mysterious
  timeout, state corruption or lost messages, streaming breaks behind the load
  balancer, parallel results lost, interrupt won't resume, memory pollution,
  hallucinated tool calls, model got worse without deploy, injection attempt
  detected, HITL queue overflow, set up LangSmith or OpenTelemetry tracing, add
  trace-based alerts, run a latency audit. Do NOT use for: upfront reliability
  design like retries and circuit breakers (agent-reliability-hardener), authoring
  eval suites (agent-eval-builder), writing graph code (langgraph-builder),
  choosing architecture (agent-architecture-advisor).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# agent-debugger

## Overview

This skill covers the OPERATE stage of agent building: seeing inside a running agent and fixing it when it breaks. One user message detonates into a tree of nodes, LLM calls, tool calls, retrieval lookups, and checkpoint writes — any one of which can cause a failure five hops from the symptom. Workflow A (instrument) wires the tracing, metrics, alerts, and replay-debugging setup that makes incidents answerable; Workflow B (incident) is the live response path: contain, triage in 10 minutes, route the symptom to one of 16 runbooks, fix, and retro. A third mini-workflow runs the ten-minute latency audit. Load this skill when something is broken, when a bill looks wrong, when quality quietly sagged, or when observability is being wired for the first time. It assumes a LangGraph-style agent with checkpointing; it does not redesign for reliability upfront or author eval suites.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/triage-protocol.md | An incident is live right now: 10-minute triage steps, symptom router, error message dictionary, reading a trace in 60 seconds, incident retro template. |
| references/runbook-library.md | The symptom router pointed at a runbook number: all 16 runbooks with ranked causes, investigation steps, fixes, prevention, field notes. |
| references/observability-setup.md | Wiring observability: instrumentation contract, log/span schemas, metrics, LangSmith vs OpenTelemetry, alerts, dashboards, replay debugging, maturity model. |
| references/latency-audit.md | The agent is slow or unfamiliar: latency budget anatomy, the ten-minute latency audit, measure-first optimization workflow. |
| references/templates.md | Copy-paste Python: structured logging setup, span instrumentation, trace-based alert condition, checkpoint replay snippet, latency budget tracker. |

## Execution Checklist

- [ ] 1. Route the problem: broken now, Workflow B; wiring observability, Workflow A; slow or costly, Workflow C.
- [ ] 2. B: CONTAIN in minute 0 — kill switch per model/tool/tenant or rollback last deploy; never investigate first.
- [ ] 3. B: SCOPE by metrics (error rate, cost/min, latency p95, eval delta), pick 3 failing traces, state the incident sentence.
- [ ] 4. B: route the symptom via the router table, then execute that runbook's ranked-cause investigation.
- [ ] 5. B: fix, verify against the baseline metric, and fill the 5-section incident retro; add one permanent artifact.
- [ ] 6. A: apply the instrumentation contract — 6 span types, full tool I/O with redaction, config_hash on every run.
- [ ] 7. A: structured JSON logs with run_id/thread_id correlation, secret-free, retries at WARNING.
- [ ] 8. A: dashboard built around the four core metrics, alerts on sustained baseline deviations with runbook links, replay-from-checkpoint tested in a sandbox, weekly observability review scheduled.

## Step-by-Step Workflow

### Step 1 — Route the problem to a workflow [FREEFORM]

Ask one question: is the system on fire, unobservable, or slow?

| Situation | Workflow | Because |
|-----------|----------|---------|
| Users/money affected right now, alert fired | B (incident) | Containment beats understanding; a looping agent burns budget while you read its trace. |
| No incidents, observability thin or absent | A (instrument) | The most expensive trace is the one you want during an incident and don't have. |
| Latency or cost complaints, no error | C (latency audit) | Ten questions find 80% of latency waste without a profiling project. |

Verification: you can name the workflow and the first file to open from the table above.

### Step 2 — Contain first, before any investigation (Workflow B) [EXACT]

Minute 0-1 of the 10-minute triage protocol (references/triage-protocol.md): decide if money or customer data is at risk. If yes, hit the kill switch (per model / per tool / per tenant) or roll back the last deploy. Because a looping refund agent is actively burning budget and trust while you think. Do not investigate first.

Verification: either you contained (state what and how in the channel) or you explicitly recorded "no containment needed" with a one-line reason.

### Step 3 — Triage in 10 minutes (Workflow B) [EXACT]

Follow the protocol exactly, in order: MIN 1-4 scope via metrics in this order — error rate (which hop?), cost/min (looping?), latency p95 (slowness?), eval delta (semantic regression?); MIN 4-7 pick 3 representative failing runs and find each one's last completed node in the trace; MIN 7-10 state the incident in one sentence in the channel ("runs fail at the refund gate with schema-validation errors on invoices created after Tuesday; suspected invoice schema drift; mitigated by X; owner Y"). Because an unmitigated, unowned incident is how a 20-minute fix becomes a 5-hour outage.

Verification: a dated incident sentence exists in the channel with owner and mitigation state.

### Step 4 — Route the symptom and execute the runbook (Workflow B) [GUIDED]

Find the first-thing-you-saw row in the symptom router (references/triage-protocol.md) to get a runbook number; also check its "but also check" column. Open that runbook in references/runbook-library.md and work its investigation steps in the given cheapest-first order — the order is a cost function: traces answer "what happened", metrics answer "how widespread", logs answer "what did the code say", state answers "what does the system believe". Because each step narrows the search space before you invest in the next, more expensive step. Read the ranked causes with their probabilities before theorizing; the runbooks encode hundreds of real incidents.

Verification: you can name the failing hop, the owning subsystem, and which ranked cause matches the evidence — or you have proven no runbook matches (that is a new runbook to write in the retro).

### Step 5 — Fix, verify, retro (Workflow B) [EXACT]

Apply the runbook's fix for the identified cause. Verify by re-measuring the metric that fired the alert (cost/run returns to baseline, error rate by node drops, interrupt-to-resume latency falls) — not by "it seems fine now". Then fill the 5-section retro template (references/triage-protocol.md): factual timeline, failed hop and matched runbook, root cause stated twice (technically AND systemically), one detection-gap metric converted into one alert, and one permanent artifact (eval case, CI test, runbook edit, or kill switch). Because a retro that produces no permanent artifact produced nothing.

Verification: baseline metric re-measured to normal; retro written with exactly one new alert and one permanent artifact.

### Step 6 — Apply the instrumentation contract (Workflow A) [EXACT]

Wire the six mandatory span types with correct parentage (run parents node; node parents LLM/tool/retrieval): run span with thread_id, run_id, tenant, graph_version, config_hash, outcome, cost_usd; node span; LLM span with full resolved prompt, tokens, latency, cost, finish_reason; tool span with full arguments and result (truncated+hash only if redaction policy demands); retrieval span with query, top-k, scores; checkpoint-write span. Because correct parentage is what lets you navigate from "this run cost $4" down to "the third iteration's billing-API call did it", and full tool I/O is the only way to ever answer audit questions like "did the agent send a refund over $400 on June 3rd". Exclude secrets and unlicensed PII by design before the first production run — retrofitting redaction always leaks. Set config_hash (hash of resolved prompt + graph version) from day one.

Verification: one real run produces a complete trace tree in LangSmith (or OTel backend) where every question — cost, latency, retrieval quality, tool behavior — is answerable cold.

### Step 7 — Wire metrics, alerts, dashboard, replay, and the weekly review (Workflow A) [GUIDED]

Follow references/observability-setup.md: (a) dashboard built around the four core metrics — cost per run grouped by intent/tenant, iteration count, error rate by node, judged success rate — one screen, every tile linked to its drill-down trace filter; (b) alerts on sustained baseline deviations (e.g. 2x over 2 windows for cost, 3x over 2 windows for errors, 1.3x over 4 windows for p95 latency), each alert bound to a saved trace-filter URL, plus an online judged-eval alert for silent quality drops; (c) replay debugging via checkpoints — diff adjacent checkpoints, fork, fix, re-run — tested against sandboxed tool stubs; (d) the 30-minute weekly observability review as standing enforcement. Because grouping is free and the surprise is not, absolute thresholds are wrong within a week of traffic shifts, and review rituals are the only thing that keeps instruments pointed at reality.

Verification: dashboard answers "is the system healthy, and if not, what's broken?" in under 10 seconds; one alert fires a link that opens the exact trace filter; a replay from a mid-run checkpoint runs in a sandbox without side effects.

### Step 8 — Run the ten-minute latency audit (Workflow C) [GUIDED]

Answer the ten questions in references/latency-audit.md against real traces (100+ runs): LLM calls per run, independent adjacent steps, prompt-prefix stability, first 500ms visibility, slowest tool p95, model routing, dead-weight tool results, context at 60%+, speculative writes, last before/after measurement. Then apply the mechanized optimization workflow: DEFINE target, MEASURE 100+ runs, BREAK DOWN per-node p50/p95, RANK latency vs cost contributors (two different lists), HYPOTHESIZE the mechanism, VERIFY it in traces before changing anything, CHANGE one thing, RE-MEASURE on the same pinned dataset, REPEAT. Because the bottleneck is usually the slowest tool or the iteration count — not the LLM — and optimizations without numbers are superstitions.

Verification: a filled audit sheet naming the top contributor, and (if optimization proceeded) an optimization-log entry with before/after numbers on the same benchmark dataset, including quality scores.

## Examples

1. **Typical incident — "users see blank chat bubbles."** Router row "Blank replies, half sentences" → Runbook 3 (empty/truncated). Trace shows the model's response was complete but the API returned nothing → cause 4: finishing node wrote `final_reply`, API serialized `messages` (renamed in a cleanup deploy). Fix the field mapping, add a contract test asserting the field the API reads exists in state. Retro converts it into an eval assertion "every successful run has a non-empty final message over N tokens".
2. **Cost alert — "cost per run is 2.3x baseline for the last hour."** Runbook 5. Dashboard MONEY tile → cost by intent → one intent at 6x → trace filter → five traces all show the loop node at 14 iterations (baseline 5) → identical tool call returning a "transient" error contract → root cause: the tool docstring says "use format X" but the API changed to format Y, so the model corrects forever. Fix docstring, deploy, verify cost/run returns to baseline, add an arg-accuracy eval.
3. **Edge case — "evals sagged 3 points overnight, zero deploys."** Runbook 14. Re-run evals 3x (stable = real), diff traces: same inputs, same config_hash, different raw model output → provider shipped a new snapshot under the same name. Pin the previous model snapshot, record provider model fingerprint on every span, add a daily canary input diff.

## Known Gotchas

1. **Setting up tracing after the first outage.** Symptom: every incident is a from-scratch re-investigation because there is nothing to read. Cause: observability treated as a big-bang platform project instead of a ladder with a one-hour first rung. Response: auto-instrument now (LangSmith is one environment variable); instrument before you need it.
2. **Replaying against production tools.** Symptom: a debugging session mints real refunds and emails. Cause: replay from a checkpoint re-executes every side effect in the resumed steps. Response: replay only against sandboxed tool stubs or a shadow environment.
3. **Trusting green dashboards during a silent failure.** Symptom: weeks of incomplete or confidently-wrong answers with zero errors. Cause: parallel result loss and memory pollution are the two silent failure classes — they fail without failing, so no alert exists to fire. Response: prevent with eval suites (fan-out completeness, memory hygiene), not alerts; you cannot alert on what produces no error.
4. **Starting forensics at the model.** Symptom: hours spent re-reading prompts while the bug sits in the stack. Cause: the model is the most visible layer, but user environments, gateways/proxies, provider updates, and tool dependencies each break more often than a tested graph. Response: eliminate layers in order — user env, gateway, provider, dependency, then agent; the trace eliminates each in minutes.
5. **Catching GraphRecursionError and returning whatever state exists.** Symptom: runs "succeed" with half the work done and no error to page on. Cause: treating the recursion limit as noise instead of a tripwire. Response: let it fail loudly; investigate what triggered the loop, not the error itself.
6. **Over-broad exception handlers returning empty strings.** Symptom: blank replies with a perfectly healthy trace. Cause: `context_length_exceeded` or a parse failure swallowed into `""`. Response: raise visibly; a clear 500 beats a silent empty string every time.
7. **Alerting on instant spikes and absolute thresholds.** Symptom: flapping pages; on-call ignores pages. Cause: no trailing baseline and no sustained-deviation requirement. Response: alert on K x baseline for N consecutive windows, aggregate per incident, and delete any alert that fired 30 times without requiring action.
8. **Global averages for cost and latency.** Symptom: the one tenant whose runs cost 40x everyone's is discovered from a billing escalation, not a dashboard. Cause: grouping not applied first. Response: group cost by intent and tenant, error rate by node — grouping is free; the surprise is not.
9. **Sampling individual spans inside a run.** Symptom: broken trajectory trees; attribution impossible. Cause: mid-run sampling drops parts of the tree. Response: sample per-run only (1-5% full fidelity, light attributes for the rest), never mid-run.
10. **Secrets or customer PII in logs, prompts, or traces.** Symptom: an un-deletable PII goldmine; credential leak. Cause: redaction policy decided after the first production run. Response: structured redaction middleware at the logging boundary, decided before launch; never log full prompts containing customer data to unbounded stores.
