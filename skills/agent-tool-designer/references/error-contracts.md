**Load this when:** a tool can fail (all of them) — designing what the model receives on failure, classifying retryable vs fatal errors, writing remedy text, or adding idempotency keys to mutating tools.

# Error Contracts and Idempotency

## Tool errors are prompts

When a tool fails, the model receives the failure as text in the tool result. What you put in that text determines whether the agent recovers, hallucinates, or spirals. The remedy text is the difference between one retry and a five-step death spiral — there is no human in the loop to interpret anything else.

Two contracts, kept separate (this split is the single most common design fix made in agent reviews):

- **Machine-facing errors**: real exceptions, typed, caught by your code — feeding retry policies, fallbacks, and error handlers.
- **Model-facing errors**: strings in tool results — feeding the LLM's next decision.

The LLM never sees the Python exception; the retry policy never sees the JSON string. Log the traceback OUTSIDE the message you send the model — your logging system gets the stack and correlation ID; the model gets "what happened + what to try next". Never invert that: an agent told `KeyError: 'user_email'` will invent an entire fake error-handling plan.

Two hard prohibitions:
- Never let a tool exception propagate as the tool result — tracebacks are full of confusing tokens that trigger another bad call; models also copy them into user-facing answers.
- Never return an error string as a successful result ("error: timeout" as data) — the model will happily build its answer on top of that string. Raise internally; format at the boundary.

## The four-field error object

Answer the four questions the model actually needs — what failed, is it worth retrying, what should change, should I stop:

```python
def _tool_error(error: str, category: str, detail: str, retry_hint: str) -> str:
    import json
    return json.dumps({"error": error, "category": category,
                       "detail": detail, "retry_hint": retry_hint})
```

| Class | Examples | Agent's correct action |
|---|---|---|
| `transient` | timeout, 429, deadlock | Retry once (with corrected args if hinted); then report |
| `permanent_input` | invalid argument, not found, bad format | Fix the input and re-call — or stop if unfixable |
| `permanent_capability` | 403, missing scope, feature unsupported | STOP. Do not retry. Tell the user / escalate |

Contrast the transcripts:

```
BAD:   tool -> "Error: something went wrong"
       model -> same call again (hallucinated "fix") -> spiral, 3+ wasted iterations

GOOD:  tool -> {"error":"timeout","category":"transient",
                "detail":"engine busy; try narrower query",
                "retry_hint":"retry with more specific query or reduce limit"}
       model -> corrected call with narrower query -> succeeds
```

Empirically, adding `category` + `retry_hint` measurably cuts the "retry the same broken call 5 times" spiral — each spiral iteration is a full LLM call plus a tool call, one of the top real-world cost multipliers.

## Remedy text catalog

Write the remedy the way you write a prompt, because that is what it is:

| Situation | Model-facing remedy text |
|---|---|
| Validation | "'date' must be in YYYY-MM-DD format, got 'next tuesday'. Retry with an ISO date." |
| Transient (timeout/429) | "Retry once after a short wait; if it persists, tell the user." |
| Authorization (403, missing scope) | "You are not allowed to do this. Do not retry. Explain the limitation to the user." |
| Not found (404, empty result) | "The resource does not exist. Do not retry with variations of this ID." |
| Deleted resource | "ACCOUNT_DELETED: this account was closed on 2025-11-02. Do NOT retry. Offer account-recovery options instead." |
| Capability missing | "This capability is not available. Do not retry. Stop and inform the user." |

The fatal-class remedies are the underrated half. A model told "403" but not "do not retry" will retry with different arguments five times, burning five calls to learn the lesson three words could have taught in one.

Worked debugging lesson (the $4.10 run): a support agent called `lookup_account` 12+ times with the same customer_id because the remedy said "check the customer_id and retry" — the ID looked right, but the account was deleted (fatal class, not input class). The fix was made at the error contract, not the prompt: return `ACCOUNT_DELETED ... Do NOT retry. Offer account-recovery options instead.` One tool-level change fixed every caller forever; the regression case ("deleted_account → no retries, offers recovery, 1 call") keeps it fixed.

## Error message catalog

Production agents die by a small vocabulary of concrete errors — map each to retry / degrade / stop-and-escalate IN ADVANCE, never during an incident:

| Error you will see | What it actually means | Correct response |
|---|---|---|
| HTTP 429 / rate limit exceeded | Quota exceeded | Back off longer than usual (jitter); throttle client-side; never retry immediately |
| HTTP 401 / invalid api key | Auth expired or misconfigured | DO NOT retry; alert a human; switch to fallback provider |
| HTTP 500 / 502 / 503 | Provider internal failure | Retry with jitter; circuit-break after threshold; escalate to fallback |
| HTTP 400 "context length exceeded" | Prompt too large | Truncate/prune context and re-run — fixable in code |
| Node timeout (wall-clock) | Node exceeded its budget | Retry or degrade; investigate the node |
| Node timeout (no progress) | Deadlock or hung socket | Same, but suspect a hang, not slowness |
| Connection reset / broken pipe | Network or server died mid-request | Retry (idempotent ops only); log for pattern detection |
| Tool returns `permanent_capability` | Tool capability missing | Agent stops and informs the user; escalate |
| Deadlock / lock timeout | DB contention | Back off with jitter; reduce concurrency |

The SDK gotcha: the exception you think your tool raises is not the exception it raises — SDKs wrap errors in their own types, and a retry predicate watching `TimeoutError` silently never fires on `APIConnectionError`. Fault-inject each error class in a unit test; the test is five lines and catches this every time.

## Retryable vs fatal — the operation decides

Retry policy is a property of the OPERATION, not of the code path. Retrying an error is only free if the call is idempotent: calling it twice has the same effect as calling it once.

When NOT to retry, in descending order of importance:
1. The operation is a non-idempotent write without a key (duplicate charges).
2. The error is deterministic — schema validation, 401, 404 on a write path — retrying is guaranteed to fail again.
3. The failure is contention-related (429, deadlocks at load) — retries actively worsen it.
4. The user is waiting — every retry adds user-facing latency.
5. The failure is a signal, not noise — "insufficient permissions" should stop the agent, not be hammered three times.

## Idempotency for mutating tools

Crash recovery re-runs nodes, retries re-run calls, resumes re-run pre-interrupt code. Every external side effect therefore needs an idempotency strategy — this is not an optimization; it is the difference between "the system recovered" and "the system recovered, having emailed everyone twice."

| Operation type | Retryable? | Mechanism |
|---|---|---|
| Pure read (search, GET, embedding) | Yes, freely | Plain retry |
| Write with natural key (upsert by ID, put file at path) | Yes | Idempotent by design |
| Write needing a key (charge, create order, send email) | Yes, with care | Client supplies `idempotency_key`; server dedupes |
| Non-idempotent write, no key support (legacy API) | Only with verification | Pre-check state before retry |
| Side effect with human audience (SMS, push, Slack) | NEVER | Deliver via outbox; never retry the call |

The pattern for the key row — the one that matters most in production:

```python
def charge_tool(amount: float, currency: str, idempotency_key: str) -> str:
    # server-side dedupe: if key seen before, return stored outcome
    existing = payment_store.lookup(idempotency_key)          # {{PLACEHOLDER}} your store
    if existing:
        return existing.result
    result = payment_gateway.charge(amount, currency, key=idempotency_key)  # {{PLACEHOLDER}}
    payment_store.save(idempotency_key, result)
    return result
```

Rules:
- Generate the key ONCE per run, store it in graph state, pass it to every money-moving tool. The key must be stable across retries of the same logical action — if the node generates a new UUID per attempt, the server sees two different "first" calls and the key is useless.
- The key is passed by the graph, not generated by the model — models cannot know what "the same logical action" means across retries and resumes.
- WARNING: blanket retries at the HTTP-client level are how tools "that send a confirmation email" send four. Scope generic client retries to read endpoints, or move the dedupe server-side. You cannot bolt this on later — the duplicated charge happened the day you shipped the retry.
- Verification question for every mutating tool: "what happens if this executes twice?" If the answer is not "nothing", the key is missing.

## Loop detection — the last line of defense

Even with perfect contracts, keep a circuit breaker: hash the last K tool calls as `(name, sorted-args)` — argument order is not identity — and if the same signature repeats 3x in a row, route to a forced-answer node ("Stop calling tools. Answer with what you have, or say what is missing."). Log the forced-answer rate: a rising rate is the early-warning signal that a tool's contract or error text changed underneath you.
