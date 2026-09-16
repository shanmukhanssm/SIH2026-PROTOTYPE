# Failure Taxonomy, Error Catalog, and Recovery Paths

**Load this when:** you run the taxonomy pass (Step 1) or need to handle a specific error class, a partial completion, or a context overflow.

## 1. What Actually Breaks, Per Layer

A request crosses API gateway → orchestration graph → LLM provider → tools/databases/third-party APIs, and every arrow is a network boundary with its own failure domain:

| Failure | Typical cause | Symptom | Layer that owns it |
|---|---|---|---|
| LLM API failure | Provider outage, 500s, auth expiry | Exception in the model node | Retry + fallback |
| LLM timeout | Long generations, provider overload | Request hangs 60-300s | Per-node timeout |
| Rate limit (429) | Burst traffic, free-tier quotas | Throttling errors; naive retries make it worse | Client-side throttling + backoff |
| Tool failure | DB down, third-party API broke, bug in your code | Tool returns error string or raises | Error contract + retry |
| Tool timeout | Slow query, deadlock | Tool hangs | Per-tool timeout |
| Context overflow | Conversation too long, tool returned 40k tokens | Provider rejects request (400) | Context pruning, truncation |
| Partial completion | Process dies mid-run, pod evicted, deploy during run | Run vanishes, user sees nothing | Checkpoint resume |
| State corruption | Bad merge in a fan-out, non-serializable state | Weird downstream behavior | Checkpoint inspection, schema validation |
| Poisoned input | Injection in tool results or user input | Agent does something dangerous | Guardrails (agent-guardrails-builder) |

Failure rates are not uniform: an LLM call fails at maybe 0.5-2% for retryable 5xx-class failures, a legacy internal SOAP endpoint fails at 3-8%, a cold-cache vector search times out at 1%, and your own deploys kill in-flight requests weekly. Harden the whole path — if you harden only the LLM call, you harden the most reliable link in the chain. And in every agent postmortem, the "LLM bug" is usually a tool bug on inspection: "model hallucinated" is often "tool contract was ambiguous." Replay the trace and check whether the tool input was even valid before blaming the model.

## 2. Tool Failure Classes and the Four-Field Error Contract

Three classes of tool failure, each demanding different agent behavior:

| Class | Examples | Agent should |
|---|---|---|
| Transient | Network blip, timeout, 429, deadlock | Retry or try a different approach |
| Permanent input | Invalid argument, not found | Fix its input and re-call |
| Permanent capability | No permission, feature unsupported | Stop trying and tell the user |

Return failures to the model as a structured message with four fields — what failed, is it worth retrying, what should change, should it stop:

```python
return json.dumps({
    "error": "timeout",
    "category": "transient",
    "detail": "search engine did not respond in 10s; try a narrower query",
    "retry_hint": "retry with a more specific query, or reduce limit"
})
```

Why it matters — the two transcripts:

```text
BAD CONTRACT:
assistant:  call search(query="refund policy")
tool:       Error: something went wrong
assistant:  call search(query="refund policy")   <- same call, hallucinated fix
tool:       Error: something went wrong
assistant:  call search(query="refund policy")   <- spiral; 3 wasted iterations

GOOD CONTRACT:
assistant:  call search(query="refund policy")
tool:       {"error":"timeout","category":"transient",
            "detail":"engine busy; try narrower query",
            "retry_hint":"retry with more specific query or reduce limit"}
assistant:  call search(query="refund policy 30 days limit:3")  <- corrected, succeeds
```

A raw Python traceback answers none of the model's questions, wastes ~1,500 tokens of context, and invites the model to invent a fix (an agent told `KeyError: 'user_email'` will hallucinate an entire fake error-handling plan). Keep the machine-facing and model-facing contracts separate: the LLM sees the JSON string, your retry policy sees the exception, and the exception is logged outside the message with stack + correlation ID. Adding `category` + `retry_hint` measurably cuts the retry-same-broken-call spiral.

## 3. The Error Message Catalog

Each error maps to exactly one strategy — retry, degrade, or stop-and-escalate — decided in advance, never during the incident:

| Error you will see | What it actually means | Correct response |
|---|---|---|
| HTTP 429 / "rate limit exceeded" | Provider or tool quota exceeded | Back off longer than usual; throttle client-side; never retry immediately |
| HTTP 401 / "invalid api key" | Auth expired or misconfigured | DO NOT retry; alert a human; switch to fallback provider |
| HTTP 500 / 502 / 503 from provider | Provider internal failure | Retry with jitter; circuit-break after threshold; escalate to fallback |
| HTTP 400 "context length exceeded" | Prompt too large | Truncate/prune context and re-run — fixable in code |
| `NodeTimeoutError(kind="run")` | Node exceeded wall-clock budget | Retry (LangGraph default) or degrade; investigate the node |
| `NodeTimeoutError(kind="idle")` | Node stopped making progress | Same, but suspect a deadlock or hung socket, not slowness |
| Connection reset / broken pipe | Network or server died mid-request | Retry (idempotent only); log for pattern detection |
| `GraphDrained(reason="sigterm")` | Graceful shutdown fired | Expected; resume from checkpoint after redeploy |
| Tool returns `category: "permanent_capability"` | Tool capability missing | Agent should stop and inform user; escalate |
| Deadlock / lock timeout in a tool | DB contention | Back off with jitter; reduce concurrency (bulkhead) |

## 4. Partial Completion: The Failure Nobody Plans For

The failure class between success and crash: the run produced some output, wrote some state, then died or degraded. Four variants, each with specific handling:

| Variant | What happened | Handling |
|---|---|---|
| Crash-before-answer | Process died after tool work, before the final answer | Checkpoint resume re-runs from the last boundary — tool work is preserved, only the final synthesis re-runs. Resume silently for sub-second gaps; notify the user for hours-long ones |
| Crash-after-answer, before delivery | Answer computed and checkpointed, but delivery to UI/email/webhook failed | Outbox problem, not a graph problem: write the answer to an outbox table in the same transaction as the checkpoint, let a delivery worker retry. Re-running the graph to regenerate the answer costs money and yields a different answer to the same question |
| Degraded partial output | Run completed but a tool failed; answer knowingly incomplete | Say so explicitly ("I could not fetch live prices; here is yesterday's"). The silent partial answer is the worst outcome — the user cannot distinguish it from a full answer |
| Interrupted by human or policy | Human rejected an approval, or a policy gate killed the run | Surface the interrupt state honestly: "stopped before the refund step — nothing was charged" |

Partial completion is a communication problem as much as an engineering one: the system knows what it did and did not finish — make that visible at the right granularity ("your order lookup finished, but the payment step did not run").

## 5. Context Overflow Recovery

The provider rejects with 400 "context length exceeded" — the one failure class fixable inside the run. Do not retry, degrade, or escalate; shrink the context and re-run.

Four-layer handling stack:

1. **Pre-flight estimation.** Count tokens before the call; truncate before the provider rejects to save a round-trip. Characters / 4 is a rough lower bound; use the provider's tokenizer for the final check.
2. **Progressive truncation ladder** (each step loses information — prefer the least-lossy step that fits):
   - Drop the oldest messages (conversation history is the usual culprit).
   - Trim the largest tool result — keep the head, add an "N items omitted" marker.
   - Summarize the middle of the conversation into a short note.
   - Drop tool results entirely and re-run the relevant tool calls.
3. **Budget-aware state design.** State fields that can grow unbounded (message lists, accumulated tool results) get caps in their reducers — a reducer keeping only the last N results prevents overflow from ever reaching the provider.
4. **Tell the model what was dropped.** "Earlier messages summarized; tool results truncated to 2k tokens" — otherwise the model invents context for the gap. A one-line truncation note costs ~15 tokens and prevents a class of hallucination.

Catch the overflow inside the loop and continue — agents usually hit overflow mid-loop, and if it crashes the run, every completed iteration's work is wasted. Treating overflow as fatal is a choice, and it is the expensive one. Also cap tool result size at the tool boundary, not at the prompt: a RAG tool returning 40k tokens pays for 40k input tokens on every subsequent call.

## 6. The Failure Signature Library

Reliability matures when failures stop being incidents and become known signatures with rehearsed responses. Seed entries — fingerprint, diagnosis, rehearsed fix:

| Signature | Diagnosis | Rehearsed response |
|---|---|---|
| Identical tool call 4+ times in a row | Error contract missing or wrong | Fix the contract; short-term: cap identical retries in the loop |
| Error rate step-function at a specific time | Deploy, or a dependency changed under you | Diff the deploy; check dependency changelogs; roll back |
| Slow 500s from provider, no retries firing | Retry predicate does not match the wrapped exception | Fault-injection test to find the real exception type |
| All runs fail with auth errors | Credential expired/rotated | Rotate via secrets manager; circuit-open the tool; alert, don't retry |
| Runs die at exactly N minutes | Total-run deadline firing, or upstream gateway timeout shorter than your run | Align the deadlines; make the outer timeout the outermost |
| Cost doubles with flat traffic | Prompt-cache breakage or a loop regression | Diff request payloads; check iteration metric |
| Interleaved state corruption | Double-texting with no strategy | Pick reject/enqueue/interrupt; never interleave |
| p95 latency spike, p50 flat | A rare path (heavy intent, large tenant) or a cold cache | Filter p95 traces; find the path; fix the one thing |
| Retry rate rising for a week | Dependency slowly degrading | Check dependency health; pre-scale; prepare the breaker |
| Checkpoint writes slowing down | Unbounded checkpoint table | Retention job; index review |

The retry-rate trend is the most underrated signal: a dependency almost never dies suddenly — it degrades, and retry rate is the seismograph that registers the tremor days early. Alert on "retry rate > 2x baseline for 4 hours."

Feed the library with the postmortem format — every incident pays rent by adding one entry:

```text
POSTMORTEM: <title> — <date>, <duration>, <severity>
1. TIMELINE (mechanical reconstruction from traces + logs + audit, with links)
2. WHAT HAPPENED (the mechanism, named)
3. WHY THE DEFENSES DID NOT HOLD (each failed defense named)
4. FIXES (each maps to a missing mechanism, not a person)
5. SIGNATURE LIBRARY ENTRY (fingerprint + rehearsed response)
```

Discipline: mechanisms not people; traces not memories; one library entry per incident so the next one is a lookup, not an investigation.

## 7. The Reliability Interview: Ten Questions Before Ship

Ask these in the design review, so reliability work happens before the incident instead of as its aftermath:

1. What happens when the LLM provider is down for an hour? (Is there a rung that keeps users served?)
2. Which tools are idempotent, and how do the others dedupe? (The charge and email tools, specifically.)
3. Where is the total-run deadline, and what does the user see when it fires?
4. If the process dies after the charge but before the confirmation, what does resume do?
5. Which tool failures are transient and which are permanent, and does the model get told the difference?
6. What is the retry budget, and who pays for it in latency?
7. Where are the bulkheads — can a batch job starve the interactive path?
8. What is the success-rate SLO, and how is "success" measured?
9. When was the last chaos drill, and what did it find?
10. How does the system behave at 2x traffic? At 10x?

An agent design that cannot answer these ten questions has been designed for the demo, not for production.
