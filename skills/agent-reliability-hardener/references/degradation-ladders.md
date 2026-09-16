# Fallback Chains, Degradation Ladders, Circuit Breakers, and SLOs

**Load this when:** you design fallback chains (Step 4), add timeouts/breakers/bulkheads (Step 5), or define SLOs, error budgets, and availability math (Step 8).

## 1. The Graceful Degradation Ladder

Retries buy time; fallbacks buy survival. Design the ladder before the outage, one rung per capability cut:

```text
rung 0: primary model, full toolset                 full experience
rung 1: fallback model (different provider), same tools   same answers, different cost/latency
rung 2: same provider, smaller/faster model         slightly worse answers
rung 3: no tools, retrieval-only mode               answers from pre-indexed docs
rung 4: static/canned responses + escalation        "a human will reply shortly"
```

Each rung has a trigger (error rate over threshold, provider health check), a specific capability trade, and — critically — a user-visible difference. Users forgive a slower answer; they do not forgive an answer that silently pretends to be as good as the primary. Rung 3 must tell the user "live data unavailable, showing cached knowledge from June 2026." The ladder must be honest at every rung.

Model fallback chain in practice:

```python
def call_model_with_fallback(state):
    for model, budget in [(primary, 30), (secondary, 15), (small_fast, 8)]:
        try:
            return model.invoke(prompt, timeout=budget)
        except (ProviderError, TimeoutError):
            log.warning("falling through from", model.name)
    # rung 4: degrade to canned reply
    return {"answer": CANNED_APOLOGY, "escalated": True}
```

Provider-fallback gotchas:

| Gotcha | Consequence | Fix |
|---|---|---|
| Tool-calling schemas differ subtly across providers (JSON strictness, argument formatting, enums) | Fallback breaks tool calls in ways a smoke test misses | Test the fallback with the same eval suite, not just a smoke test |
| System prompt tuned on one provider underperforms on another | Degraded answer quality | Keep the fallback prompt simpler |
| Fallback calls without their own budgets | A degraded primary returning slow 500s eats the fallback's time too | Give each rung its own timeout budget |
| No fail-back | You run degraded for a week without noticing | Track primary health (rolling error rate) and restore rung 0 on recovery |

Economics: OpenAI and Anthropic publish availability on the order of 99.5-99.9% annually — a single-provider agent is down 4-40+ hours a year from the provider alone, before counting your own bugs. A second provider costs one extra integration and a routing decision; it is one of the cheapest reliability wins available. If dual-provider is too expensive at your scale, at minimum keep a small fast model for the "apology + escalation" rung.

## 2. Degraded Mode Engineering: Designing the Rungs

A ladder only saves you if the rungs are engineered, not improvised:

| Rule | Detail | Because |
|---|---|---|
| Flag propagation | `state["degraded"] = True`, `state["degradation_reason"] = "search_unavailable"` — the final answer carries the honesty clause, and audit/metrics/downstream agents know the answer is a degraded artifact | The flag is the difference between degradation and silent lying |
| Rung-specific prompts | A dedicated lifeboat prompt: "you are in degraded mode: no live tools, be conservative, cite the knowledge cutoff" | Reusing the primary prompt produces an agent that calls missing tools and apologizes in loops |
| Auto-recovery | Breaker re-closes on recovery; provider fallback re-tests the primary on a schedule via a small health-check probe, not user traffic | Degradation that requires manual intervention becomes permanent within a week — nobody remembers to flip the flag back |
| Budget the degraded path | Degraded runs get tighter, not looser, latency and cost budgets | A degraded run that retries dead tools for 2 minutes is worse than the failure it survived |
| Measure the degraded experience | Track degraded-run share; judge the degraded answers with evals | A ladder whose rungs produce garbage answers is a ladder into the basement |

## 3. Circuit Breakers

A circuit breaker stops calling a dependency that is known-down, so you fail fast instead of slow. Three states: closed (normal), open (fail immediately for N seconds), half-open (let one probe through; success closes, failure reopens). In agent terms it wraps a tool or external API:

```python
breaker = CircuitBreaker(failure_threshold=5, recovery_window=30)

def guarded_search(query):
    if breaker.open:
        return json.dumps({"error": "unavailable",
                           "category": "transient",
                           "retry_hint": "try later or use cached index"})
    try:
        result = engine.search(query)
        breaker.record_success()
        return result
    except Exception:
        breaker.record_failure()
        raise
```

The payoff is enormous: when the search API is down, without a breaker every run burns 10s of timeout plus retries before failing; with one, runs fail in milliseconds and fall to a cached or degraded path. At 50k tool calls per hour, that is the difference between "slow" and "outage." The half-open probe is also the recovery mechanism — it re-enables the dependency automatically when it heals, which manual toggles never do.

## 4. Timeouts at Every Level

Timeouts must exist at every level of the stack, not just the outer edge:

| Level | Mechanism | Typical value |
|---|---|---|
| Total run | Outer `asyncio.wait_for` around the invoke | 60-120s interactive, hours for background |
| Per node | `TimeoutPolicy(run_timeout=, idle_timeout=)` on `add_node` | run 30-120s, idle 15-30s |
| Per tool call | Timeout inside the tool function | 5-30s |
| Per LLM call | Model client timeout / request_timeout | 30-60s |

Why a stuck agent must be killable: an agent loop that never terminates — model stuck calling a tool whose error it ignores — burns money continuously, tokens on every iteration. Every production agent needs a hard total-run deadline, full stop; the alternative is a bug that spends your entire monthly API budget over a weekend. Budget-derived timeouts beat generous ones ("timeout-everything" with values so large they never fire still lets the stuck agent burn money).

`idle_timeout` models reality better than a wall clock: it fires only when the node stops making observable progress (stream chunks, state writes, LLM callbacks) for N seconds. A batch download emitting progress every 10s survives `idle_timeout=30`; a deadlock dies after 30s. For work with no natural progress signal, call `runtime.heartbeat()` manually per batch. Note the sharp edge: node timeouts apply only to async nodes — a sync node with `timeout=` is rejected at compile time, so wrap blocking I/O in `asyncio.to_thread` inside an async node.

## 5. Bulkheads and Client-Side Throttling

Bulkheads isolate failure domains so one sinking compartment does not sink the ship:

- Separate pools and quotas for the hot path (user-facing agent) vs the cold path (batch reindexing, nightly jobs).
- Separate API keys or rate-limit tiers per workload.
- Separate DB connection pools per tool family.

Without bulkheads, a runaway batch job exhausts the shared rate limit and takes the interactive service down with it — a classic incident that is trivially prevented.

Client-side throttling is the proactive version: a token bucket or sliding-window limiter per provider and per tool, applied before the provider enforces it for you:

```python
limiter = TokenBucket(rate=50, burst=10)      # 50 calls/sec sustained, 10 burst

async def throttled_model_call(prompt):
    await limiter.acquire()                    # blocks briefly under burst
    return await model.ainvoke(prompt)
```

The subtlety: throttling trades latency for reliability, so the limiter's wait must count against the node's timeout budget. A node that waits 20s for a permit and then times out needs a bigger-picture fix (fewer calls or a bigger quota), not a bigger timeout.

## 6. SLOs for Agents

An SLO is a promise with teeth: a target, a measurement window (28 days), and an error budget (the allowed misses). "Availability" for an agent is not "the API returned 200":

| Metric | Definition | Typical target |
|---|---|---|
| Success rate | Runs ending in a correct final answer / all runs | 90-98% |
| p95 end-to-end latency | User message to final token | 30-60s interactive |
| TTFT p50/p95 | Time to first streamed token | 2-4s p50, 8s p95 |
| Correctness | Judged by evals | 85-95% |
| Escalation rate | Fraction routed to human | 5-20% (a dial, not a failure) |

Worked error-budget math (the arithmetic is the discipline): at 10,000 runs per day and a 95% success SLO over 28 days, the budget is 5% x 280,000 = 14,000 bad runs per month — roughly 500 per day. A 2-hour outage at 100 runs per minute burns 12,000 bad runs, about 85% of the monthly budget. "Budget remaining" is the number that tells the ops person whether they may deploy on Friday. When the budget burns down, freeze feature work and fix reliability.

The trap: LLM quality is not binary, so "success" must be defined by measurement (eval scoring, user feedback), not by exception-free completion. An agent that finishes 100% of runs with wrong answers has "100% availability" and zero value — a success-rate SLO always needs an automated judge. Start with a loose SLO you can already meet and tighten quarterly: teams that start at 99.9% spend their first month permanently in violation, learn the alert means nothing, and ignore the SLO forever. An honest 93% that triggers real budget discussions beats an aspirational 99% that trains everyone to mute alerts.

## 7. Availability Math

- **Composite availability multiplies.** A chain (gateway x provider x tools): 0.999 x 0.995 x 0.99 = ~0.984 — a 1.6% failure rate, about 140 hours per year of some-component-down, from components that each look "fine". Per-component availability is misleading; the chain is the product. This is why the degradation ladder matters more than any single component's uptime.
- **Run-level is worse than call-level.** At p=0.95 per call, 3 attempts reach 99.99% per call. But a run has ~10 calls: 0.9999^10 = ~99.9% per run. Set SLOs at run level, or you keep promising call-level numbers and under-delivering run-level experience.
- **Silent regressions eat the budget invisibly.** A silent quality regression consumes the error budget without raising errors — the meter must be judge-measured to be honest.
- **MTTR over MTBF.** Agent reliability gains come mostly from mean-time-to-recovery (faster detection, replay, revert), not from fewer failures — LLM providers and third parties will keep failing. Invest in detection and recovery before attempting failure elimination.

## 8. Capacity Planning Under Failure

- **Concurrency model.** A run holds one thread/connection per in-flight LLM and tool call: 1,000 concurrent runs at 4 parallel calls = 4,000 concurrent outbound calls. Know provider rate limits (per-key RPM/TPM) and tool-service limits; compute the binding constraint — usually the provider's tokens-per-minute on the mid-tier model, or one legacy tool.
- **Queue vs reject.** Queueing hides the problem and builds latency debt; rejecting is honest but user-visible. Hybrid wins: queue briefly (~30s) with visible progress, then reject with an apology — never unbounded queues.
- **The degraded-mode multiplier.** At 90% load, drill the degradation ladder: the fallback provider has its own (usually lower) rate limits, and a provider outage that pushes all traffic to the fallback will 429 the fallback within minutes unless the fallback route also throttles and queues. Capacity-plan the degraded mode, not just the happy mode.
- **Load testing.** Agents vary wildly in cost and latency, so synthetic uniform inputs lie. Replay a corpus of ~1,000 recorded production runs at increasing concurrency, measure p95 latency and error rate at each step, and find the knee — the mix is the test.

## 9. Reliability Anti-Patterns

| Anti-pattern | What it looks like | Why it fails | The fix |
|---|---|---|---|
| Retry-everything | Blanket retries at the HTTP layer | Duplicate side effects, 4xx hammering | Per-operation retry classification |
| The untested ladder | A 5-rung ladder nobody has drilled | Rung 2 is broken and rung 4 was never implemented | Quarterly drill per rung |
| Timeout-everything | Timeouts so generous they never fire | The stuck agent still burns money | Budget-derived timeouts |
| Checkpoint-everything | Checkpointing every trivial state change | Checkpoint writes become the bottleneck | Checkpoint at superstep boundaries; watch write latency |
| Heroic on-call | Incident response as improvisation | Response quality varies with who is awake | Playbooks + signature library |
| Fallback-theater | A fallback provider added, never evaluated | The fallback fails differently in production | Same eval suite on both providers |
| SLO-by-vibes | "We aim for 99%" with no budget, no meter | The number means nothing; alerts are noise | Honest measured SLO + budget |
| Resilience-shopping | Buying a resilience tool before knowing the failure | Machinery without diagnosis | Failure taxonomy first, tools second |

The common thread: reliability work is diagnosis-shaped; the anti-patterns are all tool-shaped — reaching for a mechanism before identifying a failure. The correct sequence: classify the failures you actually have, pick the mechanism for each, verify it works, and let postmortems feed the next cycle.
