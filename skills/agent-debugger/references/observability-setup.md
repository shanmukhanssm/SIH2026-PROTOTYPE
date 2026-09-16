# Observability Setup

**Load this when:** wiring observability for an agent (Workflow A): what to trace, how to trace it, which metrics matter, alerting, dashboards, and replay debugging.

## The Instrumentation Contract

A single user message detonates into a branching trajectory — routing decision, tool calls, retrieval lookups, LLM invocations, retries, checkpoint writes. Any one could be where things went wrong. Without a trace that captures the full tree structure, you're holding a bag of events instead of a causal story — you can't answer "which node made this LLM call?" from a flat log, and that question is behind every cost investigation you'll ever run.

Mandatory spans for a LangGraph agent:

| Span | Must capture | Why |
|------|--------------|-----|
| Run (root) | thread_id, run_id, user/tenant, graph version, config hash | Correlation + attribution |
| Node | Node name, start/end, input state size, output delta, error | The unit of failure attribution |
| LLM call | Provider, model, prompt (full), tokens in/out, latency, cost, finish_reason | Cost + quality forensics |
| Tool call | Tool name, arguments, result (full or truncated+hash), latency, error | Side-effect audit |
| Retrieval | Query, top-k docs, scores | RAG quality forensics |
| Checkpoint write | checkpoint_id, size, duration | Durability health |

Tree structure matters as much as the fields: the run span parents the node spans; node spans parent LLM and tool spans. Parentage is what lets you navigate from "this run cost $4" down to "the third iteration's tool call to the billing API did it." Without it you have a flat list with no causality.

Two fields teams most often forget and then deeply regret:

1. **Full tool inputs and outputs.** When finance asks "did the agent ever send a refund of $400+ on June 3rd?", only stored tool payloads can answer. Truncated logs are not sufficient for audit. Store the full payload with appropriate redaction.
2. **The prompt as actually sent to the model** — the resolved template with all variables filled in, not a reference like `system_prompt_v3`. When the agent says something toxic, you need the exact bytes that produced it.

What NOT to trace: secrets — API tokens, encryption keys, customer PII you're not licensed to store in your tracing vendor's cloud. Exclude by design, not by hope. Decide the redaction policy before the first production run, because retrofitting redaction onto an existing trace pipeline always leaks — and the field you miss will be the one you can't afford to expose.

## Logging Conventions

Traces tell you what happened inside a run; logs tell you what happened around it.

- **Structured logs — JSON lines, not prose.** Every line needs run_id and thread_id, the node name, the event type, and a message. Across gateway, graph, tools, and background workers, the same run_id must thread through every log line. In LangGraph, pass a run_id in the config and use it (with thread_id) as your correlation keys everywhere. Without it, "why did this run take 40 seconds?" becomes a cross-service archaeology dig.
- **Log state transitions, not just errors.** "node entered with state size 12k tokens," "checkpoint written (23ms)," "tool search returned 42 results." Reconstructing a run from error logs alone is impossible; from transition logs it's a grep. The difference between a five-minute and a two-hour investigation is often just whether you logged the transitions.
- **Never log secrets.** No API keys, auth headers, or full user PII in plaintext logs. Redact at the logging boundary with structured redaction middleware — don't rely on developers remembering, because they won't. Never log entire prompts containing customer data into an unbounded log store: that's how you build a PII goldmine you cannot delete.
- **Right verbosity.** INFO for transitions, WARNING for retries and degradations, ERROR for run failures. Every retry is a WARNING — a rising retry rate is your earliest outage signal, often appearing hours before error rates spike.

## Concrete Schemas

A structured log line:

```json
{"ts": "2026-08-12T09:14:03.112Z", "level": "WARNING",
 "thread_id": "thr_9f2e", "run_id": "run_77ab",
 "node": "loop", "event": "tool_retry",
 "tool": "fetch_order", "attempt": 2, "reason": "ConnectionError",
 "latency_ms": 341}
```

Rules embodied: every line carries the thread_id + run_id correlation pair; `event` comes from a finite vocabulary (node_enter, node_exit, tool_call, tool_retry, checkpoint_write, degrade, fail) so dashboards can group on it; anything secret is redacted before the serializer ever sees it; WARNING is used for retries and degradations.

Span attributes (OTel / LangSmith contract):

```
run span:    {thread_id, run_id, tenant, graph_version, config_hash, outcome, cost_usd}
node span:   {node, attempt, input_tokens, state_size_bytes, error_class}
llm span:    {provider, model, tokens_in, tokens_out, latency_ms, cost_usd, finish_reason,
              prompt_hash, cache_hit}
tool span:   {tool, args_hash, args_redacted, result_size, result_hash, latency_ms, error_class}
retrieval:   {query, top_k, corpus_version, hit_scores}
```

Three fields that pay for themselves repeatedly:

- **config_hash** — hash of the resolved prompt plus graph version; lets you filter traces by deployment. "Is this failure on the new prompt or the old one?" is the first question in every regression investigation; without the hash the answer requires archaeology. Set it once in the run-factory code; require it in every trace-filter conversation.
- **prompt_hash** — links LLM spans to exact prompts without storing full prompt text twice.
- **corpus_version** — answers the question that ends every RAG argument: "did this wrong answer use the old or new documentation?"

## The Metrics That Actually Matter

| Metric | Definition | Why it matters |
|--------|------------|----------------|
| TTFT | Time from user message to first streamed token | Perceived latency; the metric users actually feel |
| Total latency p50/p95/p99 | End-to-end run duration percentiles | Latency SLOs; p99 is where the pain lives |
| Per-node latency p50/p95 | Duration per node type | Bottleneck attribution |
| Iteration count | Agent-loop LLM calls per run | Dominant cost/latency driver; spike = loop bug |
| Tokens per run (in/out) | Sum across all calls | Cost predictor; context bloat detector |
| Cost per run | Tokens x model prices | The bill, per run — groupable by intent, tenant, tier |
| Success rate | Judged-successful runs / total | The agent's actual availability |
| Error rate by node | Failures / executions per node | Which component is sick |
| Tool failure rate by tool | Tool failures / tool calls | Tool health |
| Escalation rate | Runs handed to humans / total | Too high = not trusted; too low = maybe silent wrong answers |
| Correctness score | Judge/eval score distribution | Quality trend, separate from success rate |
| Cache hit rate | Cached LLM/tool calls / total | Cost lever health |

Four of these earn their keep in every incident and form the foundation of the dashboard: **cost per run** (a bug that doubles cost is visible in an hour; "latency is a bit high" is not), **iteration count** (loop bugs show as a step-function in iterations before they show as errors), **error rate by node** (attribution without digging), and **success rate** (your actual SLO). Build the dashboard around these four first; everything else is a drill-down.

Track cost per run by intent and tenant, not globally. A global average hides the one customer whose runs cost 40x everyone else's because their data triggers a pathological tool path — you'll discover that customer from a billing escalation, not from your dashboard, unless you group first. Grouping is free; the surprise is not.

## LangSmith vs OpenTelemetry: The Decision

| Situation | Choice |
|-----------|--------|
| Entirely in the LangChain/LangGraph ecosystem, want deepest agent visibility | LangSmith native — it understands nodes, checkpoints, tool calls; keeps prompt payloads; feeds evaluations. OTel generic spans lose that structure. |
| Already run OTel across dozens of services | Export agent spans to your existing OTel backend so agent traces join your distributed traces in one waterfall. |
| Multi-vendor freedom or compliance needs | OTel as base layer + LangSmith for agent-specific depth via dual export. Cost: two pipelines to maintain — real overhead. |
| Strict data residency (regulated, sovereign cloud) | Self-host OTel or LangSmith. Evaluate before choosing cloud tracing — migrating trace data across jurisdictions after the fact is painful and sometimes illegal. |

Architecture options:

| Architecture | What it looks like | Choose when |
|--------------|--------------------|-------------|
| LangSmith cloud | Traces, evals, dashboards in the SaaS | Default for non-regulated traffic |
| LangSmith self-hosted | Same product, your infrastructure | Data residency, strict DPA needs |
| OTel-only | Your existing stack, custom dashboards | Mature OTel org; willing to build agent-specific views |
| Hybrid | LangSmith for agent forensics/evals + OTel root spans to central | The common enterprise pattern |

The pragmatic stack for most teams: LangSmith native for agent forensics and evaluations, plus OTel export of just the root run span with minimal attributes (thread_id, run_id, cost, status) so ops dashboards can correlate agent health with the rest of the system. Full dual export of every inner span is rarely worth the pipeline complexity.

**Sampling and retention — decide in week one, both are hard to change later.** Multi-agent runs generate 10-50 spans each; at 10,000 runs/day with 30 spans/run that's 9 million spans/month. Sampling must be per-run — never sample individual spans within a run, or the trajectory tree breaks and attribution is lost. Pragmatic answer at scale: full fidelity for sampled runs (1-5%), light attributes for the rest, retention tiers — 30 days full, 90 days light, aggregate metrics forever. Teams that keep everything at full fidelity for a year build the world's most expensive debugging museum.

## Trace-Based Alerting

Alert on behavior change, not absolute thresholds — absolute thresholds break every time your traffic pattern shifts, every time you add a new intent, every time the model gets cheaper.

| Alert | Condition | Why |
|-------|-----------|-----|
| Error-rate spike | Node error rate > 3x trailing 7-day baseline | Outages of tools/providers |
| Cost spike | Cost per day > 1.5x baseline, or cost per run > 2x | Loop bugs, token leaks, prompt-cache breakage |
| Latency regression | p95 latency > 1.3x baseline for 2+ hours | Provider degradation, slow tool, queue buildup |
| Iteration explosion | Mean iterations > 2x baseline | Model regression on tool use |
| Success-rate drop | Judged success below SLO target | Quality regressions |
| Escalation spike | Escalation rate > 2x baseline | Agent losing trust, possibly silent wrong answers |
| Feedback sentiment | Thumbs-down ratio spike | User-visible quality dip |

Rules from painful experience:

- **Alert on sustained deviations** — two or more consecutive evaluation windows — to avoid flapping. A single spike that resolves in 15 minutes is noise; a sustained shift is signal.
- **Every alert needs a runbook entry** that points at the right trace filter ("Error rate by node: click through to charge node traces"). An alert that just says "something is wrong" is a page without a plan.
- **Distinguish "our bug" from "provider down."** Provider outages are not your fault, but they are your problem. A provider status check link in the runbook saves 20 minutes at 3 AM.
- **The alert most teams miss is the silent quality drop.** Error rates and latencies stay flat while the model quietly gets worse — a provider model update, a bad prompt change that still completes, retrieval corpus drift. The only detector is the online judged eval: sample live traces, score with an LLM judge, alert when the score distribution shifts. Wire the judge into alerting, or learn about regressions from customer churn — the most expensive monitoring system available.

Engineering pieces:

1. **Metric source:** per-run metrics computed at trace completion and exported as metrics (LangSmith dashboards/rules, or an OTel metrics pipeline to Prometheus/Grafana) — NOT ad-hoc queries over raw traces at alert-evaluation time, which is slow at scale and breaks under the very load spike you're alerting on.
2. **Baselines:** trailing 7-day median per metric, per intent or tenant where it matters. Cost grouped by intent; error rate per node. The baseline is the alert's denominator.
3. **Alert template:** "metric X for scope S exceeds K times the baseline for N consecutive windows of W minutes." Starting points: 2x over 2 windows for cost, 3x over 2 windows for errors, 1.3x over 4 windows for p95 latency (latency moves more slowly; longer windows avoid flapping).
4. **Runbook binding:** every alert carries a URL to a saved trace filter plus the escalation owner. Without it you spend the first five minutes of every incident figuring out what to look at.
5. **Silence budget:** aggregate per incident — one page, not forty. Deduplicate on the root scope (alert on the tool that's failing, not the thousand runs that hit it). Tune or delete noisy alerts monthly: an alert that fired 30 times and never required action is training your on-call to ignore pages.

## The Run Dashboard

The on-call person must answer "is the system healthy, and if not, what's broken?" in under ten seconds. One screen, no scrolling:

```
+------------------+  +------------------+  +------------------+
| TRAFFIC          |  | QUALITY          |  | MONEY            |
| runs/min         |  | success rate     |  | $ today (vs 7d)  |
| active threads   |  | correctness      |  | $ per run p50/p95|
| queue depth      |  | escalation rate  |  | $ by intent      |
+------------------+  +------------------+  +------------------+
+------------------+  +------------------+  +------------------+
| LATENCY          |  | HEALTH           |  | LOOP             |
| TTFT p50/p95     |  | errors by node   |  | iterations p50   |
| total p50/p95/p99|  | tool failures    |  | max iterations   |
| per-node p95     |  | retry rate       |  | cache hit rate   |
+------------------+  +------------------+  +------------------+
```

Four design principles: (1) every tile has a baseline line — a trailing 7-day trend — because a number without context is meaningless; (2) clicking any tile leads to the trace filter that explains it — a dashboard without drill-down is a wall decoration; (3) show the SLO status bar with error budget remaining — the ops person's real question is often "can I ship on Friday?"; (4) keep it to one screen. If the run dashboard needs scrolling, the run dashboard is wrong.

## Replay Debugging: Checkpoints as a Time Machine

Because every superstep is persisted as a checkpoint, you can do things impossible in traditional debugging:

- **Replay from any checkpoint.** A run failed at step 5 of 9: fork the thread at checkpoint 4, patch the tool, re-run steps 5-9 with identical state. This is a real debugger — you're testing the fix against the exact state that caused the failure, not "re-run from scratch and hope."
- **State history diffing.** `graph.get_state_history` returns every checkpoint, newest first. Diff checkpoint N versus N-1 to see exactly what one step changed — the single most useful forensic tool when an agent goes off the rails: was it the tool result that was wrong, or the model misreading a correct result?
- **Time travel.** Roll a thread back to checkpoint 3 and continue from there — "what would have happened if step 4 had been different?" LangGraph exposes this via `update_state` on a historical checkpoint; LangGraph Studio gives a clickable UI.

```python
# replay debugging workflow
history = graph.get_state_history(config)          # all checkpoints, newest first
step4 = history[4]                                  # the checkpoint before the failure
bad_state = graph.get_state(config)                 # where the run ended
# diff: what did the failing node actually write?
diff = compare(step4.values, bad_state.values)      # pinpoints the poisoned field
# fork and replay with a fixed tool:
graph.update_state(step4.config, {"tools": {"search": fixed_search}})
graph.invoke(None, step4.config)                    # re-runs from step 4 with the fix
```

**WARNING: replay re-executes real side effects.** Resuming from checkpoint 4 re-runs steps 5 and beyond, including any charges, emails, or API calls they contain. Replay in production must run against sandboxed tool stubs or a shadow environment, or your debugging session mints real refunds.

Replay converts "it said something weird, maybe try changing the prompt?" into "step 4 wrote price: -1, here is the state before and after, here is the replay with the fix."

**LangGraph Studio vs other tools:** Worth it for understanding one weird routing decision, stepping through HITL interrupts, exploring fan-out state, or teaching the graph's behavior to a new teammate. Not worth it for production forensics at scale (traces and dashboards scale to ten thousand runs statistically), automated evals, or multi-service debugging (use OTel). Studio answers "what happened in this specific run?"; LangSmith answers "what's happening across all runs?" The `debug` stream mode (all events including checkpoints, tasks, metadata) is the programmatic emergency exit when you need the full firehose in code.

## Multi-Agent Debugging: Attribution and Stitching

The genuinely hard problem multi-agent systems add: which agent failed? A supervisor delegates to three sub-agents; one returns a wrong answer the supervisor incorporates; the final wrongness is five hops from the root cause.

1. **Trace stitching:** every sub-agent invocation must be a child span of the delegating agent's span, carrying the delegator's trace context. This happens automatically when sub-agents are compiled graphs invoked as nodes; the stream v2 `ns` namespace shows which subgraph emitted which event; LangSmith's tree view stitches automatically. In custom/OTel setups you must propagate the parent span ID manually — without it, you get flat traces where "three LLM calls happened" but nobody knows whose, and you spend an hour re-deriving the tree by timestamps.
2. **Attribution by contract:** give each agent a defined output schema — researcher returns {claim, sources}, writer returns {draft, citations}. When the final answer lacks sources: did the researcher return zero sources (researcher's fault) or did the writer drop them (writer's fault)? Without contracts, multi-agent failures are blame-free and therefore unfixable.
3. **Delegation attribute:** add the delegating agent's name to every sub-agent's root span at delegation time (`parent_agent="orchestrator"`). Without it, a flat vendor UI renders 40 identically-named spans.

The debugging loop, in order: (1) stitch the trace so you can see the tree, (2) inspect each agent's final output object, (3) identify the boundary where correctness was lost, (4) replay that agent alone with its exact input, (5) fix locally, (6) re-run the full trace. The most common mistake is jumping straight to step 5 for the supervisor's prompt — almost never where the loss happened.

## The Five Trace Reads

Most trace investigation falls into one of five patterns. Recognize which one you're in and the moves become mechanical.

1. **The cost spike read.** Sort runs by cost; the top decile shares a signature — unusual iteration count, giant tool result, model escalation. Group the expensive runs, then diff against a normal sample on tokens-in, tokens-out, iterations. Usually a specific tool-result shape triggering a loop or context explosion.
2. **The wrong-answer read.** Start from the judge/feedback verdict and walk backward: which tool result did the final answer depend on? Which was wrong or poisoned? Where was correctness lost? The checkpoint diff pins the exact node.
3. **The slow-run read.** Pick p95+ latency traces, overlay per-node latency bars against a median trace — one bar sticks out. It's rarely the LLM: usually a cold cache, a retry chain, or a slow tool on a rare code path.
4. **The loop read.** Iteration count > 2x baseline. Read consecutive tool calls: identical calls = the model isn't learning from the error contract (retry loop that won't converge); near-identical calls = the retry_hint is steering wrong; wild new calls each iteration = the model is lost (missing context, too many tools, unsolvable task). Each signature has a different fix — never treat "it looped" as one bug.
5. **The injection read.** A run did something alarming. Read every untrusted input in the context before the alarming action: user text, retrieved documents, tool results. The injected instruction is usually in the least obvious channel — a search snippet, an email footer, an innocuous document. This is why full tool I/O must be in traces.

Keep a shared document of "trace signatures we have seen" — one entry per distinct failure with its fingerprint (e.g. "refund-loop: 6+ identical refund calls, tool returns permanent_capability, model re-calls anyway") and its fix. The second time a failure appears, the doc turns a 40-minute investigation into a 4-minute pattern match. After a year, this document is the most valuable observability artifact you own.

## Trace Query Cookbook

Ten queries worth saving as named filters — the operational vocabulary of on-call:

```
1. errors in the last hour, grouped by node          -> which component is sick right now
2. cost > $2 AND outcome=success                     -> expensive-but-working runs (audit targets)
3. iterations > 10                                   -> the loop suspects (this hour's bugs)
4. feedback = thumbs-down, last 7 days               -> this week's failure set for curation
5. node=loop AND tool=fetch_order AND error != null  -> one tool's failure mode, isolated
6. tenant=<X> AND cost > $5                          -> the expensive tenant's runs
7. latency p95+ runs, last 24h                       -> the tail hunt
8. config_hash != current                            -> runs on stale configs (post-deploy drift)
9. retrieval score < 0.4 AND success                 -> answers that succeeded DESPITE bad retrieval
10. guardrail_blocked = true, last 7 days            -> the safety filter's week
```

Query 1 is the first thing on-call runs when paged. Query 4 feeds dataset curation. Query 7 starts the latency tail hunt. Query 9 finds RAG runs that only worked by luck — and will fail next week when the data shifts.

## Long-Running and Background Agents

Background agents (hours-long research jobs, scheduled crawls, batch pipelines) break interactive debugging: nobody is watching when they fail; failures surface days later.

- **Checkpoint-first forensics:** when a 6-hour run "got weird at hour 3," `get_state_history` with timestamps jumps you to hour 3's state directly. For long runs, checkpoints beat traces because they capture full state per step.
- **Heartbeats and liveness:** an agent emitting nothing for two hours is either working hard or dead, and you can't tell without heartbeats. Emit progress events at every milestone; the ABSENCE of heartbeats is itself the alert. A background agent that silently dies at hour 2 of 6 produces the worst outcome: a bill for 2 hours of compute, zero delivered value, and a trust deficit.
- **Interruption and resume as first-class:** long runs WILL be interrupted (deploys, quota limits, restarts). Resume-on-startup from the last checkpoint, idempotent tools so re-executed steps don't duplicate, partial-progress publication so a killed run still yields value — if a 6-hour research agent dies at hour 3, you should have the first 3 hours of results.
- **Cost traceability:** per-hour cost rollups per run. A run that doubles its burn rate at hour 4 is a bug — the per-hour cost curve surfaces it.

## When the Bug Is Not in the Agent: Cross-Stack Order

The diagnostic order that saves hours of misdirected investigation — eliminate layers, each takes minutes with traces and hours without:

1. **User's environment** (most common, most ignored): stale client, network stripping the streaming protocol. Signal: the trace shows the run succeeded and the answer was delivered — the complaint is about their rendering.
2. **Gateway/proxy layer:** a 30s proxy timeout killing your 45s run; header stripping dropping correlation IDs; body limits truncating tool payloads. Signal: the trace is missing or cut off at a suspicious boundary.
3. **Model provider:** silent model update, regional routing, provider bug. Signal: same run behaves differently across hours with an identical config_hash.
4. **Tool's dependency:** the database, third-party API, search index. Signal: the tool span shows the error class and the agent's behavior is actually correct — the error contract worked; the "agent bug" is a faithful report of someone else's bug.
5. **The agent itself** — model, prompt, graph logic. Only after eliminating 1-4. Most teams start here, which is why most debugging sessions take hours instead of minutes.

The "identical config, different behavior" signature is the provider-update tell. Record the provider-reported model fingerprint on every span and run a daily canary input: a fixed request whose output you diff against yesterday's. A provider update shows up as a canary diff before it shows up as a user complaint — the cheapest drift detector available.

## Observability Maturity Model

Levels are sequential — each assumes the previous exists. The whole ladder is about a month of part-time work, not a quarter-long platform project.

| Level | What exists | What breaks | Next investment |
|-------|-------------|-------------|-----------------|
| 0: None | Print statements, provider logs | Every incident is a re-investigation | LangSmith auto-instrumentation (1 hour) |
| 1: Traces | Full runs visible, tool I/O captured | Can see runs, cannot see trends | Dashboards + the four core metrics (1 day) |
| 2: Metrics + dashboards | Cost/latency/error trends, grouping | Can see trends, cannot see quality | Feedback collection + judged online evals (1 week) |
| 3: Feedback + evals | Success rate, judge scores, SLOs | Can see quality, cannot act fast | Alerting with runbooks, replay debugging (1 week) |
| 4: Alerting + forensics | Alert-to-trace-filter paths, checkpoint replay | Can react, cannot prevent | Failure-signature library (ongoing) |

Teams stuck at level 0-1 are stuck because they believe observability is a big-bang migration, not a ladder with cheap first rungs. Instrument before you need it: the most expensive trace is the one you want during an incident and don't have.

## The Weekly Observability Review

All conventions decay without enforcement. The enforcement mechanism is a 30-minute standing meeting:

1. **The four tiles (5 min):** cost/run, iterations, errors-by-node, success rate — each vs its 7-day baseline. Anything drifted → owner assigned on the spot.
2. **The failure sample (10 min):** 5-8 traces picked by the filter "failed or degraded this week" — read together, one finding each.
3. **The feedback sample (5 min):** the week's thumbs-downs with comments — the user's words on top of the traces.
4. **The dataset handoff (5 min):** the week's notable failures curated into dataset rows by the person who owns the fix.
5. **The dashboard hygiene (5 min):** dead alerts tuned or deleted; new tiles if a question went unanswered this week.

Grouping happens because the review demands it; feedback gets collected because the review reads it; alert hygiene happens because the review owns it. This meeting is the cheapest observability feature in existence.

## Observability Scorecard

Score each line 0-3 (0-60 total), quarterly, with evidence per line (a link, a screenshot, a run) — not from memory. Most teams believe they're at 50 and score 25 on their first honest fill.

```
TRACES
[ ] full trajectory captured (nodes, LLM calls, tools, retrieval, checkpoints)
[ ] full tool I/O stored, with a redaction policy
[ ] config_hash + corpus_version on every run
METRICS
[ ] the four core metrics on a dashboard (cost/run grouped, iterations,
    errors-by-node, success rate)
[ ] TTFT and p95 tracked separately from totals
[ ] SLO status + error budget visible
FEEDBACK AND QUALITY
[ ] user feedback collected, attached to traces, with comments
[ ] judged online evals sampling live traffic (the silent-drop detector)
ALERTING
[ ] alerts fire on sustained baseline deviations, with runbook links
[ ] cost-spike and retry-rate alerts (the earliest outage signals)
FORENSICS
[ ] replay-from-checkpoint procedure tested (in a sandbox)
[ ] multi-agent trace stitching verified end-to-end
[ ] failure-signature library exists and is consulted during incidents
HYGIENE
[ ] logs structured, correlation-ID'd, secret-free
[ ] sampling + retention policy written and enforced
[ ] the weekly observability review actually runs
```

Scoring: below 20 = incidents are archaeology. 20-35 = you can react, but slowly. 35-50 = you react fast and learn from each incident. 50+ = instrumented like a mature service. The gaps between what exists in your head and what exists as a linked runbook, saved filter, or working dashboard are exactly where the next incident will hide.
