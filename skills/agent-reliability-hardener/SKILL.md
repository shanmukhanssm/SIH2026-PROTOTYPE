---
name: agent-reliability-hardener
description: >-
  Harden a working LangGraph agent for production failure BEFORE incidents:
  failure taxonomy pass, idempotency keys on mutating tools (set by the graph,
  not the model), retries with jittered backoff and caps, fallback chains and
  degradation ladders, circuit breakers, timeouts, bulkheads, durable execution
  via checkpoint crash recovery, partial-completion and context-overflow
  handling, SLOs with error budgets, CI fault-injection tests.
  Trigger: "harden my agent for production", "add retries with backoff",
  "make this tool idempotent", "add a circuit breaker", "fallback provider
  chain", "graceful degradation", "crash recovery and checkpoint resume",
  "define SLOs for the agent", "chaos drills or fault injection",
  "handle context overflow".
  Do NOT use for: debugging a live incident (agent-debugger), safety guardrails
  like PII or prompt-injection defense (agent-guardrails-builder), building
  eval suites (agent-eval-builder), designing tool schemas (agent-tool-designer),
  writing the graph itself (langgraph-builder).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# agent-reliability-hardener

## Overview

This skill hardens an already-working LangGraph agent against failure, before the first incident. It belongs to the HARDEN stage of agent building. It assumes a compiled graph with nodes and tools exists (built by langgraph-builder and agent-tool-designer); it does not design the graph, design tool schemas, debug live incidents, or add security guardrails. In production agents, well over half of outages trace to tool and infrastructure failures, not model quality — the LLM call fails at roughly 0.5-2% while a legacy internal API fails at 3-8% and your own deploys kill in-flight runs weekly. So hardening is distributed-systems work (idempotency, retries, breakers, bulkheads, durable execution) applied to the whole agent path, with the LLM treated as one flaky dependency among several. Reliability for agents is infrastructure work first, model work second — the reverse of the order most teams reach for.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/failure-taxonomy.md | Starting the taxonomy pass: per-layer failure table, error message catalog, failure signature library, partial completion, context overflow recovery |
| references/retries-idempotency.md | Before wiring any retry: idempotency classification, key design, backoff/jitter, retry budgets, DLQ, saga compensation via error handlers |
| references/degradation-ladders.md | Designing fallback chains, circuit breakers, timeouts, bulkheads, degraded-mode rungs, availability math, SLOs |
| references/durable-execution.md | Verifying crash recovery and replay safety, chaos drills, fault injection in CI, capacity planning |
| references/templates.md | Ready to write code: runnable retry wrapper, fallback node, circuit breaker, idempotent tool wrapper, checkpointer crash-recovery pattern |

## Execution Checklist

- [ ] 1. Run the failure taxonomy pass: for every node, tool, and dependency, name what breaks, the symptom, and the pre-decided response (retry / degrade / stop-and-escalate).
- [ ] 2. Classify every tool as idempotent, idempotent-with-key, or non-idempotent; add graph-generated idempotency keys to every mutating tool.
- [ ] 3. Wire retry policies per operation: jittered backoff, attempt caps, latency budgets — never blanket client-level retries.
- [ ] 4. Design the degradation ladder per capability: rungs, triggers, user-visible honesty clause, degraded flag propagation.
- [ ] 5. Add timeouts at every level, circuit breakers on flaky dependencies, bulkheads between workloads, and a hard total-run deadline.
- [ ] 6. Verify durable execution: production checkpointer, resume-on-crash, replay-safe tools, SIGTERM drain handler.
- [ ] 7. Define partial-completion handling (4 variants) and the context-overflow recovery path.
- [ ] 8. Define run-level SLOs with error budgets and do the composite-availability math.
- [ ] 9. Fault-inject in CI (stubbed failures + resume tests), schedule chaos drills, and add signature-library entries.

## Step-by-Step Workflow

### Step 1 — Run the failure taxonomy pass over the graph [GUIDED]

Enumerate every node, tool, and external dependency on the request path. For each, fill the taxonomy table: failure, typical cause, symptom, layer that owns it. Load references/failure-taxonomy.md for the per-layer table and the error message catalog. Cover the whole path, because if you harden only the LLM call you harden the most reliable link in the chain — vector DBs, payment APIs, and your own deploys fail more often than the provider.

Map every error to exactly one of three strategies — retry, degrade, or stop-and-escalate — as a decision made in advance, because the on-call engineer must never invent a response to a 429 at 3 AM. Distinguish failure classes as named types (retryable-provider, validation, budget-exceeded, tool-timeout), each with a policy; a graph that catches bare `Exception` has no policy, only hope.

**Verify:** every arrow between components has a named failure mode and a pre-decided response; no node is marked "nothing can go wrong" (those are where everything goes wrong first).

### Step 2 — Make every mutating tool idempotent [EXACT]

Classify every tool before wiring any retry (full table in references/retries-idempotency.md):

| Operation type | Retryable? | Mechanism |
|---|---|---|
| Pure read (search, GET, embedding) | Yes, freely | Plain retry |
| Write with natural key (upsert by ID, put file) | Yes | Idempotent by design |
| Write needing key (charge, create order, send email) | Yes, with care | Graph generates `idempotency_key`, server dedupes |
| Non-idempotent, no key support (legacy API) | Only if verifiable | Pre-check state before retry |
| Side effect with human audience (SMS, email, Slack) | No | Deliver via outbox; never retry the call |

Then implement the key discipline:

- Generate a UUID once per run at the graph entry node (`state["idempotency_key"] = str(uuid4())`) and pass it to every money-moving tool — keys are passed by the graph, not generated by the model, because a resume that replays a node must replay the same key.
- One key per logical operation, never per attempt, because a fresh key per attempt makes the server see N distinct "first" calls and the dedupe is worthless.
- The server stores key-with-result so a repeat call returns the stored outcome (Stripe `Idempotency-Key` / AWS SDK pattern).

**Verify:** a fault-injection unit test calls the tool twice with the same key and asserts one side effect and an identical result; a resume test kills the process after the tool call and asserts no duplicate effect.

### Step 3 — Implement retry policies [EXACT]

Set `RetryPolicy` per node — retry policy is a property of the operation, not of the code path:

```python
from langgraph.types import RetryPolicy, TimeoutPolicy
builder.add_node("fetch_order", fetch_order,
    retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.5,
                             backoff_factor=2.0, max_interval=8.0, jitter=True))
```

- Full jitter always, because deterministic 1-2-4-8 backoff synchronizes a fleet into thundering-herd spikes that keep a recovering service down.
- Cap `max_interval` at 4-8s for interactive agents (a chain of retries at 16/32/64s is "why does the app hang for two minutes"); push long backoff to background work and DLQs.
- Budget retries: per-call (3 attempts, ~30s elapsed cap) plus a run-level rule that retries add at most ~2x the node's nominal latency.
- Never retry: non-keyed writes, 401/404/validation errors, programming errors (LangGraph's default `retry_on` already excludes these and retries only 5xx for httpx/requests); 429s get backoff-longer-than-usual plus client-side throttling, not immediate retries.
- Unit-test each retry predicate against the actual exception the tool raises, because SDKs wrap errors (`httpx.TimeoutException` vs the provider's `APITimeoutError`) and a non-matching predicate silently never fires.

Background agents: failures go to a dead letter queue with delayed retries measured in minutes-to-hours and poison detection by fingerprint (references/retries-idempotency.md).

**Verify:** fault-injection test per policy asserting the retry count and that exhaustion reaches the error handler; sanity-check the math — 3 attempts at p=0.95 gives 99.99% per call, but at p=0.5 you need a circuit breaker, not more attempts.

### Step 4 — Build the fallback/degradation ladder per capability [GUIDED]

Design the ladder before the outage, one rung per capability cut (engineering rules in references/degradation-ladders.md):

```text
rung 0: primary model, full toolset          full experience
rung 1: fallback model (different provider), same tools
rung 2: same provider, smaller/faster model  slightly worse answers
rung 3: no tools, retrieval-only mode        answers from pre-indexed docs
rung 4: static/canned responses + escalation "a human will reply shortly"
```

Each rung needs a trigger (error-rate threshold, provider health check), a specific capability trade, and a user-visible honesty clause — rung 3 must say "live data unavailable, showing cached knowledge", because users forgive slower but not silent degradation. Per rung: propagate the flag (`state["degraded"]`, `state["degradation_reason"]`), use a rung-specific lifeboat prompt (the primary prompt in degraded mode calls missing tools and apologizes in loops), budget the degraded path tighter than normal, and wire auto-recovery back up (breaker re-close, primary health probe) because manual un-degrading is forgotten within a week.

A second provider is one of the cheapest reliability wins available (single-provider agents lose 4-40+ hours/year to the provider alone); test the fallback with the same eval suite or you shipped fallback-theater.

**Verify:** drill each rung; assert the degraded flag reaches the final answer, the honesty clause is user-visible, and the system returns to rung 0 when the primary heals.

### Step 5 — Add timeouts, circuit breakers, bulkheads [EXACT]

Timeouts at every level, because a stuck agent burns money on every loop iteration and needs to be killable:

| Level | Mechanism | Typical value |
|---|---|---|
| Total run | Outer deadline around `invoke` | 60-120s interactive; hours for background |
| Per node | `TimeoutPolicy(run_timeout=, idle_timeout=)` on `add_node` | run 30-120s, idle 15-30s |
| Per tool call | Timeout inside the tool function | 5-30s |
| Per LLM call | Model client request timeout | 30-60s |

- `idle_timeout` fires when the node stops making observable progress; for long silent batches call `runtime.heartbeat()` per batch. Node timeouts apply to async nodes only — a sync node with `timeout=` is rejected at compile time, so wrap blocking I/O in `asyncio.to_thread` inside an async node.
- Circuit breakers (closed/open/half-open) on every flaky dependency: without one, a downed search API costs every run 10s of timeout plus retries; with one, runs fail in milliseconds and fall to a cached rung. Half-open probing is the auto-recovery mechanism.
- Bulkheads: separate pools, quotas, and API keys for hot path (user-facing) vs cold path (batch jobs), because a runaway reindex that exhausts the shared rate limit takes the interactive service down with it.
- Add client-side throttling (token bucket) per provider/tool, and count the limiter's wait against the node's timeout budget.
- Timeout composition order to memorize: attempt, then timeout, then retry policy, then error handler. Attach error handlers for compensation (saga pattern) — see references/retries-idempotency.md.

**Verify:** kill the dependency in staging; runs must fail fast and degrade to a cached path, not stall 10s+ per call; confirm the breaker auto-recovers.

### Step 6 — Verify durable execution (crash recovery via checkpoints) [GUIDED]

- Use a production checkpointer: PostgresSaver (call `.setup()` once) or SqliteSaver for single-node — never MemorySaver, which is RAM-only and loses everything on restart. Cap thread_id at 255 chars and run a retention job, because checkpoints accumulate forever and slow every write.
- Internalize at-least-once semantics: a node that started but did not checkpoint re-executes on resume — this is exactly why Step 2 exists. Resume is `graph.invoke(None, {"configurable": {"thread_id": t}})`.
- Add graceful shutdown (LangGraph 1.2+): `RunControl` + `request_drain()` on SIGTERM so deploys stop between supersteps and resume after; nodes can read `runtime.drain_requested` to skip expensive work. Drain does not cancel in-flight tasks — pair it with per-node timeouts.
- Choose a double-texting strategy deliberately: reject, enqueue, or interrupt-resume. Never interleave, because interleaved state corruption is the failure mode of picking none.
- Pin running threads to the graph version they started on; checkpoints are not portable across versions.

**Verify:** run the graph, SIGKILL mid-run, resume in a fresh process, and assert the continuation matches the uninterrupted run modulo expected at-least-once re-execution. Test the resume path like it owes you money — it is the feature your reliability story is betting on.

### Step 7 — Handle partial completion and context overflow [GUIDED]

Partial completion is the failure class between success and crash. Handle all four variants (details in references/failure-taxonomy.md):

| Variant | Handling |
|---|---|
| Crash-before-answer | Checkpoint resume; re-runs only the final synthesis. Notify the user for hours-long gaps; resume silently for sub-second ones |
| Crash-after-answer, before delivery | Outbox problem: write the answer to an outbox table and let a delivery worker retry. Re-running the graph yields a different answer to the same question |
| Degraded partial output | Say so explicitly ("could not fetch live prices; here is yesterday's") — the silent partial answer is the worst outcome |
| Interrupted by human/policy | Surface the interrupt state honestly ("stopped before the refund step — nothing was charged") |

Context overflow (400 "context length exceeded") is the one failure fixable inside the run: pre-flight token estimate (chars/4 is a rough lower bound; provider tokenizer for the final check), then the progressive truncation ladder — drop oldest messages, trim the largest tool result (keep the head, add "N items omitted"), summarize the middle, drop tool results and re-run the calls. Tell the model what was dropped, because a 15-token truncation note prevents a class of hallucination. Catch overflow inside loops, not at the run edge, so iteration work is not wasted. Cap unbounded state fields in their reducers so overflow never reaches the provider.

**Verify:** force a 40k-token tool result and a too-long history; the run must finish with a truncation note, not a 400 and not a dead run.

### Step 8 — Define SLOs + availability math [FREEFORM]

An SLO is a target + measurement window + error budget. Set them at run level, because run-level numbers are always worse than call-level numbers (0.9999 per call across 10 calls is 99.9 per run):

| Metric | Definition | Typical target |
|---|---|---|
| Success rate | Runs ending in a correct final answer / all runs | 90-98% |
| p95 end-to-end latency | User message to final token | 30-60s interactive |
| TTFT p50/p95 | Time to first streamed token | 2-4s p50, 8s p95 |
| Escalation rate | Fraction routed to a human | 5-20% (a dial, not a failure) |

- Define success by measurement (eval scoring, user feedback), never by exception-free completion — an agent that finishes 100% of runs with wrong answers has 100% availability and zero value.
- Work the budget: at 10k runs/day and a 95% SLO over 28 days, the budget is ~14,000 bad runs (~500/day); a 2-hour outage at 100 runs/min burns ~12,000 — that "budget remaining" number is the deploy-on-Friday signal.
- Composite availability multiplies: 0.999 x 0.995 x 0.99 is ~0.984, so the degradation ladder matters more than any single component's uptime. Invest in MTTR over MTBF — providers will keep failing; detection and recovery are where the math points.
- Start with a loose SLO you can already meet and tighten quarterly; an aspirational 99.9% on day one trains everyone to mute the alerts.

**Verify:** every SLO has a meter, a dashboard tile, and an error budget; the ten reliability-interview questions (references/failure-taxonomy.md) are answered in writing.

### Step 9 — Fault-inject in CI to verify the stack [EXACT]

A reliability mechanism that is not tested is a belief, not a mechanism. Implement the verification ladder (drill catalog and CI patterns in references/durable-execution.md):

1. Unit fault injection: stubbed tools raise each catalog error (timeout, 429, 500, permanent) and you assert the correct strategy fired — retry count, handler invoked, degrade path taken.
2. Resume tests: kill mid-run, resume from the checkpoint in a fresh process, assert the continuation (catches unserialized state fields and double-fired side effects).
3. Scheduled chaos drills, monthly: mid-run SIGKILL is the highest-value drill (it finds which side effects re-execute); poisoned-tool-result is second (it reveals blind trust in tool output).
4. Shadow replay of production thread IDs in a sandbox with the mechanisms active, diffed against recorded outcomes.

Every drill becomes a runbook entry plus an automated test; every incident adds an entry to the failure signature library. Implement the reliability table in cost order — idempotency first, fallback provider last — because teams that buy dual-provider fallbacks while their refund tool double-charges are optimizing for the outage they imagine instead of the one they are having.

**Verify:** CI fails when a reliability mechanism breaks; no "we added retries" ships without the matching fault-injection test, and a scheduled first chaos drill is on the calendar before launch.

## Examples

**Example 1 — simple: "add retries to my order lookup tool."** Output: classify `fetch_order` as a pure read (freely retryable), attach `RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0, max_interval=8.0, jitter=True)` plus `TimeoutPolicy(run_timeout=60, idle_timeout=20)` to the node, and add a fault-injection test asserting 3 attempts on a stubbed 500 and 0 retries on a stubbed validation error. ~95% of transient tool failures disappear for 10 minutes of work.

**Example 2 — typical: harden a support agent (search_kb, fetch_order, refund, send_email).** Output, in implementation order: idempotency keys on refund and send_email (1 day, kills double refunds/emails); retry policy with 4s cap on fetch_order (10 min); run/idle timeouts on the loop node (10 min, kills infinite spend); four-field error contracts in all tools (half day, kills retry spirals); second provider at rung 1 with the same eval suite; circuit breaker on search_kb (10s stalls become 5ms degradations); PostgresSaver + drain handler; 90s total-run deadline. The composite effect is structural: the agent can now fail small.

**Example 3 — edge-case: "the run crashed after charging the user but before storing the confirmation."** Output: resume re-executes the charge node (it started but did not checkpoint — at-least-once), so without a key the customer is charged twice. Fix: `idempotency_key` set once at run start, passed by the graph; the charge tool's backend stores key-with-result and returns the stored outcome on the repeat; the confirmation step then completes from checkpoint. Exactly-once is a lie; idempotent retry is the truth.

## Known Gotchas

1. **Duplicate charges or emails after a crash+resume or an outage.** → Cause: blanket HTTP-client retries, or an idempotency key regenerated per attempt — retries amplify whatever the operation does. → Response: classify every tool (Step 2), one graph-generated key per logical operation, server-side dedupe; human-audience side effects go through an outbox, never retried.
2. **The retry predicate silently never fires and every call fails on attempt one.** → Cause: the SDK wraps the real error (`httpx.TimeoutException` inside `APIConnectionError`) and your policy watches for the wrong class. → Response: five-line fault-injection test per policy against the actual raised exception type.
3. **Identical tool call 4+ times in a row, then a hallucinated "fix".** → Cause: a bare "Error: something went wrong" result — the model cannot learn from a failure with no category or hint, and each spiral iteration is a full LLM call plus a tool call. → Response: four-field error contract (`error`, `category`, `detail`, `retry_hint`) in every tool; cap identical retries in the loop.
4. **An unbounded loop burned the monthly API budget over a weekend.** → Cause: no hard total-run deadline and timeouts so generous they never fire. → Response: outer run deadline (60-120s interactive), per-node run/idle budgets, per-run cost cap that makes exceeding it a loud event.
5. **The app hangs for two minutes on every transient failure.** → Cause: `max_interval` left at 128s — backoff correct for a cron job is wrong for a chatbot. → Response: cap interactive backoff at 4-8s; move long backoff to background DLQ retries.
6. **`builder.compile()` rejects the graph after adding timeouts.** → Cause: node timeouts apply only to async nodes; a sync node with `timeout=` fails at compile time. → Response: convert to async and wrap blocking I/O in `asyncio.to_thread`.
7. **Every checkpoint vanished on restart; resumes restart from zero.** → Cause: MemorySaver in production. → Response: PostgresSaver with `.setup()`, a retention job, and a tested resume-on-startup path.
8. **The degraded agent loops calling missing tools and apologizing.** → Cause: rung-3 mode reusing the primary prompt. → Response: rung-specific lifeboat prompt, degraded flag in state, and tighter (not looser) budgets in degraded mode.
9. **The fallback provider fails differently in production than the primary ever did.** → Cause: fallback added but never evaluated — fallback-theater. → Response: run the same eval suite on both providers and keep the fallback prompt simpler.
10. **Runs return "I've processed your refund!" without the refund existing.** → Cause: trusting the model's final token instead of the tool result — the last token is not success. → Response: verify side effects from the tool result (or a read-back) before reporting them; the tool result is the verification.
11. **Checkpoint writes get slower every week until everything does.** → Cause: unbounded checkpoint table. → Response: retention job deleting checkpoints older than N days plus index review; watch write latency.
