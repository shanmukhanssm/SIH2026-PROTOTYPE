# Latency Audit

**Load this when:** the agent is slow or expensive, when you have ten minutes on an unfamiliar agent, or before optimizing anything — because optimizations without numbers are superstitions.

## Where the Seconds Go: Latency Budget Anatomy

Before optimizing anything, name every second. Not estimate — name, with real numbers. Typical support-agent run (mid-tier model, 2025-2026):

```
TTFT   = time-to-first-token (first streamed token reaches user)
TOTAL  = time from user message to final answer token

user message
  -> API gateway, auth, routing                        50-150 ms
  -> graph startup + checkpoint read                   10-30 ms
  -> node: classify (small LLM call)                   400-900 ms
  -> node: retrieve (embedding + vector search)        80-250 ms
  -> node: agent loop
       iteration 1: LLM call (plan)                    600-1200 ms
                    tool: search_internal              150-400 ms
       iteration 2: LLM call (synthesize)              800-2000 ms
                    tool: fetch_customer               100-300 ms
       iteration 3: LLM call (final answer)            1000-3000 ms
  -> post-processing, logging                          20-50 ms
TOTAL                                                  ~3.5s to ~9s
TTFT (first token of final answer)                     ~2.5s to ~6s
```

Three non-obvious truths that trip up almost every team:

1. **TTFT is not dominated by the final answer generation.** It includes every LLM call and tool call before the answer begins streaming. Users perceive "the agent is slow" as the time until something — anything — appears on screen. Streaming just the final answer barely helps if the user spent six seconds staring at a blank screen first.
2. **Sequential LLM calls dominate the budget.** A three-iteration agent pays three LLM latencies; a seven-iteration agent pays seven. Iteration count is the single biggest latency lever — not model speed, not embedding efficiency.
3. **Tools are usually not the bottleneck.** A 200ms vector search is noise next to a 1.5s LLM call. Teams spend weeks optimizing retrieval while their agent makes six sequential model calls that account for 80% of the wall time. Tools matter when one is genuinely slow (a 4-second legacy SOAP endpoint), but that's the exception.

Quick arithmetic example: four LLM calls at 1.2s each plus three tool calls at 0.3s each = 5.7s sequential. Merging two independent middle LLM calls into parallel fan-out: ~3.6s. Eliminating one iteration by having the planner emit tool calls directly: ~4.5s while staying sequential. Caching the classify step at a 30% hit rate: another ~0.35s saved on average. Latency optimization is accounting: you cannot spend what you cannot see.

## Latency Budgets as an Engineering Discipline

Convert the breakdown table into a budget: allocate before building, then hold every component to its line item.

1. **Pick the user-experience anchor.** Interactive agents: TTFT <= 2s and total <= 15s are defensible 2026 targets. Research agents get longer budgets because the user sees progress. Voice tightens brutally to TTFT <= 800ms.
2. **Allocate down the chain.** From a 15s total: gateway 200ms, checkpoint read 50ms, classify 800ms, loop iterations 3 x 3s = 9s, tools 1.5s, final answer streaming 2.5s, post-processing 200ms — sums to 14.25s, leaving 5% headroom.
3. **Enforce per-node.** Each node gets a TimeoutPolicy matching its budget line. The sum of budgets must be less than the total deadline — a run where everything degrades simultaneously must still terminate.
4. **Alert on budget breaches by node.** When the classify node's p95 crosses its line, that's the node to optimize. The budget table tells you which optimization matters before you start profiling.
5. **Re-budget quarterly.** Model upgrades and traffic-mix shifts move the numbers. The budget is a living contract, reviewed like an SLO.

The complete budget spreadsheet (filled example — every line has a measured justification; budgets are not wishes):

| Component | Budget | How the number was set |
|-----------|--------|------------------------|
| Gateway + auth + routing | 200ms | Measured overhead floor; alert if > 2x |
| Checkpoint read | 50ms | PostgresSaver on warm cache; cold penalty goes to cold-start budget |
| Classify (tiny model) | 800ms | p95 of the tiny tier's generation + queueing |
| Retrieve (embed + search + rerank) | 400ms | Embed 50ms + search 150ms + rerank 200ms at p95 |
| Loop iteration 1 (plan + tools) | 2.5s | One LLM call p95 + two parallel tools p95 |
| Loop iteration 2 (synthesize + tools) | 2.5s | Same, with larger context |
| Final answer generation | 2.5s | Mid-tier output at ~60 tokens/s for a 150-token answer |
| Post-processing + logging | 200ms | Fixed overhead |
| **Total budget** | **9.1s** | Sums to the 15s SLO with headroom for retries and the tail |

The same spreadsheet becomes the alerting configuration — alert when a component's p95 exceeds its line for 2+ hours. Build the sheet before optimization begins; afterward it tells you what was achieved. Its real value is political: "we cannot add this tool call" becomes an arithmetic statement ("it breaks the 15s budget by 2.3s") instead of a taste argument.

## The Ten-Minute Latency Audit

The quick pass that finds 80% of latency waste without a full profiling project — ten questions, each pointing at a specific fix:

| # | Question | Points at |
|---|----------|-----------|
| 1 | How many LLM calls does the average run make? (> 4 = candidate) | Iteration reduction: merge steps, better tool contracts |
| 2 | Are any two adjacent steps independent? | Parallelize them (Send fan-out or asyncio.gather) |
| 3 | Is the stable prompt prefix actually stable across iterations? | Prompt-cache check; volatile content at the end |
| 4 | What does the user see in the first 500ms? | Streaming/visibility: show the plan, stream progress |
| 5 | Which tool has the highest p95, and does anything cache it? | Tool-result cache with per-tool TTL |
| 6 | Is the small model used for classification/routing, or the big one for everything? | Model routing / cascading |
| 7 | What fraction of tool results are never used by the answer? | Dead-weight pruning (often 20-35% of calls) |
| 8 | Is the context at 60%+ of the window on typical runs? | Window management: summarize/trim before it grows |
| 9 | Is anything executed speculatively that writes state? | Reads only — speculative writes corrupt state |
| 10 | When was the last before/after measurement for a latency change? | If you can't remember, the optimization log is dead |

Ten questions, ten minutes, and the answers name the top of the optimization list. Run it quarterly — the answers drift as traffic and prompts change, and last quarter's healthy agent is this quarter's iteration-bloated one.

## Measure Before Optimizing

The most important trap in performance work: developers optimize the model because it's the visible part, while the numbers say otherwise. Roughly four times in five, profiling shows a 4-second legacy endpoint or a 9-iteration loop — the LLM is the most visible latency, which makes it the most blamed and least responsible. Four steps:

1. **Profile node-level latencies first.** Per-node p50/p95 latency and per-node token counts for 100+ real runs — from tracing spans, not estimates. Build the actual breakdown.
2. **Rank the spend** — sort nodes by latency contribution AND by cost contribution separately. They are different lists with different fixes: a cheap-but-slow database call and an expensive-but-fast large-model call live on different lists.
3. **Attack the top of the right list.** Usual winners, in order: reduce iterations (merge steps, better tool contracts), parallelize independent middle steps, cache repeat work, route easy traffic to small models. Usual losers: swapping embedding models (~15% of one step's time), fine-tuning "for speed," rewriting the vector store.
4. **Re-measure against the same workload and publish the before/after.** Optimizations without numbers are superstitions; numbers without before/after are anecdotes.

A profiling session is not "look at one slow run" — slow runs are anomalies by definition. Profile the distribution: which node contributes most to the p50, which to the p95, which to cost. The node that dominates the p95 is usually a different node than the one that dominates the mean — and the p95 one is the one users complain about. Optimizing the wrong percentile is the classic failed optimization project.

## The Performance Debugging Workflow, Mechanized

The repeatable procedure behind every optimization:

```
1. DEFINE: pick the metric and the target ("p95 total < 8s for intent X")
2. MEASURE: instrument; collect 100+ runs of the target workload
3. BREAK DOWN: per-node p50/p95 latency + tokens + cost table
4. RANK: latency contributors vs cost contributors (two different lists)
5. HYPOTHESIZE: for the top contributor, name the mechanism
   (iteration count? slow tool? serial spine? cache miss? cold start?)
6. VERIFY the hypothesis against traces (find the runs that prove it
   before changing anything — a hypothesis you cannot see in traces
   is a guess)
7. CHANGE one thing
8. RE-MEASURE against the same workload; log the entry
9. REPEAT from step 2 (the budget moved — re-rank, don't assume)
```

The two steps teams skip most often, and the cost of skipping: step 6 (changing things without confirming the mechanism — the "swap the embedding model" move) and step 9 (re-optimizing the old bottleneck — after the first fix, the second bottleneck is a different node than the first). The workflow is deliberately boring. Boring is what makes optimization converge instead of wander. Refuse optimization PRs that skip step 4.

**Worked case (9 seconds to 3):** p50 5.8s / p95 9.1s support agent, profiled over 200 runs. Step order: merge planner + tool-caller (removed 1 iteration, −1.1s) → parallelize iteration 2's independent tool calls (−0.4s) → cache the classify step (−0.23s avg) → stream everything (perceived TTFT ~4s → ~1.5s) → route 40% of intents to a tiny model (−0.8s). Result: p50 3.6s (−38%), p95 5.2s (−43%), cost −31%. Iteration reduction and parallelization delivered two-thirds of the win with no new infrastructure; the biggest perceived win barely touched the measured metric; every step was chosen from the profiled table, never from intuition.

## Tail Hunting: The p95 Project

Three latency regimes, three different causes: cold start (first request after deploy — imports, pools, prompts: 1-3s; mitigate with warm-up requests on deploy, lazy tool clients, compile-once), warm path (the median run), and the tail (cold caches, retries, provider hiccups, rare intents).

The p95 trap with real numbers: p50 = 3.5s feels healthy, but 5% of users are at 9s — and those are the users writing the negative reviews. Dissatisfaction concentrates in the tail. Track p50, p95, and p99; optimize the one that violates its budget line.

Tail-hunting procedure: filter traces for p95+ runs only, read them individually, find the shared property. Composite case: 100% of tail runs shared one property — a `fetch_history` tool call returning 10k+ tokens for long-term customers, bloating context and slowing every subsequent call. Fix targeted the mechanism: paginate by default (limit 20, explicit opt-in for more), trigger window management before 60% context, dedicated fast path for the heavy-history intent. p95: 8.9s → 4.1s; p50 barely moved. The tail is a specific path, not a global property — find the path, fix the path. Median-optimization does not move the tail.

## Anti-Optimizations: The Moves That Look Like Progress

| Move | Why it fails |
|------|--------------|
| "Just use a bigger model for everything" | Buys quality (sometimes) at 5-50x cost and 2-10x latency; quality gain saturates |
| Fine-tuning "for speed" | Changes behavior, not speed; same tokens/second. Speed comes from architecture |
| Swapping the embedding model | Touches ~3% of the latency budget; only correct if profiling shows retrieval is the bottleneck |
| Micro-batching everything | Interactive paths have nothing to batch; batch APIs add hour-scale latency |
| Aggressive streaming of everything | Streaming is a UI strategy, not a compute optimization; leaks unverified content |
| Trimming prompt words | Saves tenths of a second, often costs quality; the real lever is structure (fewer iterations) |
| Parallelizing the serial spine | Violates the dependency graph and produces wrong answers faster; the serial fraction is serial |
| Cache-everything | Wrong keys/TTLs/semantics = a wrong-answer generator with a nice latency number |

The pattern: anti-optimizations are what happens when optimization starts from technique instead of measurement. If the optimization pitch does not begin with a profiled number, it is an anti-optimization in costume.

## The Optimization Log

Every optimization gets one entry:

```
date: {{DATE}}
change: {{ONE_CHANGE_DESCRIBED_IN_ONE_LINE}}
baseline: p50 {{X}}s / p95 {{Y}}s / cost ${{Z}}/run  (dataset v{{N}}, n=200 runs)
after:    p50 {{X2}}s / p95 {{Y2}}s / cost ${{Z2}}/run
verdict:  shipped / rejected. p50 -{{A}}%, cost -{{B}}%.
```

Four rules that make the log work: (1) one change per entry — two changes confound attribution; (2) the same pinned benchmark dataset every time (~200 real runs representing the traffic mix) — optimizations are comparable only against a fixed workload; (3) negative results are logged too — "swapped embedding model: no measurable change" is a finding, and the log of failed optimizations keeps the team from re-trying them; (4) quarterly rollups — the log becomes the evidence and the map of where the remaining budget lives. Include the quality column on every entry (judge scores before/after): teams without it optimize straight off the cliff — fast, cheap, and measurably dumber, and nobody knows which change did it.

One asymmetry worth exploiting: some techniques add quality while cutting cost — iteration reduction via better tool contracts, prompt caching, unused-tool pruning. Exhaust those first; only then touch the quality-negative ones, and only with judge scores in the same report.
