# Retry Semantics and Idempotency

**Load this when:** you wire any retry (Step 2-3), design idempotency keys, configure backoff and budgets, or need DLQ/async retries and saga compensation.

## 1. Idempotency First

Retrying is only free if the call is idempotent: calling it twice has the same effect as calling it once. Retries are an amplifier — if the operation charges a credit card, retries charge it multiple times. The cardinal rule: **retry policy is a property of the operation, not of the code path.** Classify every tool and node before wiring retries.

| Operation type | Retryable? | Mechanism |
|---|---|---|
| Pure read (search, GET, embedding) | Yes, freely | Plain retry |
| Write with natural key (upsert user by ID, put file at path) | Yes | Idempotent by design |
| Write needing idempotency key (charge, create order, send email) | Yes, with care | Client generates `idempotency_key`, server dedupes |
| Non-idempotent, no key support (legacy internal API) | Only if you can verify | Pre-check state before retry |
| Side effect with human audience (SMS, push, Slack) | No | Deliver via outbox; never retry the call |

The pattern for the key row — generate a UUID at the top of the run, store it in graph state, pass it to every money-moving tool, and have the backend store the key with the result so a repeat call returns the stored outcome (Stripe `Idempotency-Key` / AWS SDK are the models to copy):

```python
def charge_tool(amount, currency, idempotency_key):
    # server-side dedupe: if key seen before, return stored outcome
    existing = payment_store.lookup(idempotency_key)
    if existing:
        return existing.result
    result = payment_gateway.charge(amount, currency, key=idempotency_key)
    payment_store.save(idempotency_key, result)
    return result

# in graph state, one key per run, reused by every write tool
state["idempotency_key"] = str(uuid4())   # set once at run start
```

Three sharp edges:

- **Key stability.** The key must be stable across retries of the same logical action. A node that generates a new UUID per attempt makes the server see two different "first" calls — generate per run or per task ID, never per attempt.
- **Keys come from the graph, not the model.** A resume that replays a node is a feature of durable execution; make replaying the tool safe by construction.
- **Blanket client retries are a time bomb.** Teams add retries at the HTTP client level and discover the tool that "sends a confirmation email" now sends four. Scope generic retries to read endpoints or push the dedupe server-side — you cannot bolt it on later; the duplicated charge happened the day you shipped the retry.

## 2. Exponential Backoff with Jitter

Instant retries convert one slow request into a thundering herd. Backoff spaces attempts; jitter desynchronizes a fleet. Deterministic backoff makes 100 workers retry at exactly t+1, t+3, t+7, t+15 seconds — synchronized spikes that often keep a recovering service down. Full jitter turns the spikes into a smooth hum:

```text
attempt      delay
1            sleep(random(0, 500ms))          # base
2            sleep(random(0, 1s))
3            sleep(random(0, 2s))
4            sleep(random(0, 4s))             # capped by max_interval
```

Retry budgets — never allow unlimited retries:

| Budget | Rule | Because |
|---|---|---|
| Per-call | Max 3 attempts, total elapsed cap ~30s; then fail and let the error handler decide | Unbounded attempts on a downed dependency are a stall, not a strategy |
| Global (run-level) | Retries may add at most ~2x the node's nominal latency | Prevents a degraded provider from turning every run into a 2-minute stall |
| Interactive `max_interval` | 4-8s | 3 retries at 0.5/1/2s are invisible; at 16/32/64s it is "why does the app hang for two minutes" — push long backoff to background work |

The math worth doing once: with per-call success probability p and n independent attempts, success becomes 1 - (1-p)^n. At p=0.95 (a bad day), n=3 reaches 0.999875 — retries are a huge lever on flaky calls. At p=0.5 (a downed dependency), n=3 reaches only 0.875 — the right answer is a circuit breaker, not more attempts. Retries fix transient failure; they cannot fix persistent failure. Knowing which you have is the whole game.

## 3. When NOT to Retry (Descending Importance)

1. Non-idempotent write without a key — duplicate charges.
2. Deterministic errors — schema validation, 401 auth failures, 404 on a write path: retrying is guaranteed to fail again.
3. Contention failures — 429s, DB deadlocks at high load: retries actively worsen them.
4. Interactive latency — the user is waiting and every retry adds user-facing latency.
5. Failure as signal — "insufficient permissions" should stop the agent, not be hammered three times.

LangGraph's default `retry_on` already encodes the core wisdom: it retries any exception except a blocklist (ValueError, TypeError, RuntimeError, OSError, SyntaxError and friends), and for httpx/requests errors retries only 5xx status codes. `NodeTimeoutError` is retryable by default.

## 4. LangGraph's Built-In Retry Machinery

```python
from langgraph.types import RetryPolicy, TimeoutPolicy

builder.add_node(
    "call_api",
    call_api,
    retry_policy=RetryPolicy(
        max_attempts=3,        # includes the first attempt
        initial_interval=0.5,  # seconds before first retry
        backoff_factor=2.0,    # exponential multiplier
        max_interval=128.0,    # cap in seconds (set 4-8 for interactive agents)
        jitter=True,           # randomize intervals (default True)
        retry_on=...,          # exception types or predicate
    ),
    timeout=TimeoutPolicy(run_timeout=120, idle_timeout=30),
)
```

Timeouts and retries compose cleanly: when `run_timeout` or `idle_timeout` fires, LangGraph raises `NodeTimeoutError` with structured fields (node, elapsed, kind), discards any partial writes from the failed attempt, and lets the retry policy decide. The timeout clock resets per attempt — this "clear writes on timeout" behavior is what makes retrying stateful nodes safe.

Useful extras:

- `runtime.execution_info.node_attempt` (1-indexed) lets a node switch to a fallback API after the first failed attempt.
- `Send` accepts a per-dispatch timeout override for dynamic fan-out, without changing the node definition.
- For long silent work, call `runtime.heartbeat()` per batch so `idle_timeout` does not kill progress-making nodes.

The exception-matching trap: SDKs wrap errors in their own types (`httpx.TimeoutException` vs `requests.Timeout` vs the provider's `APITimeoutError`); a predicate that does not match the wrapped type silently never fires. Test each retry policy against the actual exception the tool raises in a fault-injection unit test — the test is five lines and catches this every time.

## 5. Dead Letter Queues and Async Retries (Background Agents)

For background agents — digests, crawlers, scheduled research — the user is not waiting and "retry later" is legitimate. Pattern: the run fails → serialize state + failure metadata into a DLQ with a topic per failure class → a worker consumes and applies delayed retries measured in minutes-to-hours → budget exhausted moves the entry to a terminal topic for human alert and manual replay.

The DLQ entry must carry: `thread_id` (so the checkpoint is the resume state), failed node name, exception class, attempt count, original timestamp. Replaying a fixed run needs no state reconstruction: `graph.invoke(None, {"configurable": {"thread_id": t}})`.

```python
def on_run_failure(thread_id, node, exc, attempt):
    entry = {"thread_id": thread_id, "node": node,
             "exc": type(exc).__name__, "attempt": attempt,
             "first_failed_at": now()}
    if attempt >= MAX_ATTEMPTS or is_poison(entry):
        terminal_dlq.send(entry)              # human review
    else:
        retry_queue.send(entry, delay=backoff(attempt))   # e.g. 5m, 30m, 2h
```

Distinguish retryable-later from poison: a run that fails identically 5 times is poison — replaying it at 6 AM changes nothing. Detect poison by fingerprint (hash of node + exception class + first 100 chars of message) and route straight to human review, or the DLQ fills with permanently-broken runs retried daily forever.

## 6. Error Handlers and Saga Compensation

Retries and fallbacks are for continuing; error handlers are for recovering. LangGraph 1.2+ lets a node carry an `error_handler` — a function that runs after all retries are exhausted, receives state plus a typed `NodeError` (`error.node`, `error.error`), and can update state or reroute. This is the machinery for the Saga pattern — compensating a partially-completed multi-step action:

```python
def payment_error_handler(state, error: NodeError) -> Command:
    # charge failed after 3 retries; compensate the earlier reservation
    release_inventory(state["reservation_id"])
    return Command(
        update={"status": f"compensated after {error.node}: {error.error}"},
        goto="finalize")

graph = (StateGraph(State)
    .add_node("reserve_inventory", reserve_inventory)
    .add_node("charge_payment", charge_payment,
              retry_policy=RetryPolicy(max_attempts=3, retry_on=ConnectionError),
              error_handler=payment_error_handler)
    .add_node("finalize", finalize)
    .compile())
```

Composition order to memorize: **attempt → timeout → retry policy → error handler.** The retry policy answers "is it transient?"; the handler answers "what do we undo now?"

Handler rules, each verified against the docs:

| Rule | Because |
|---|---|
| One handler per node — compose inside it if you need more | No handler chains exist |
| Keep handlers simple and idempotent | If the handler itself raises, the exception propagates as if the node had no handler |
| `interrupt()` bypasses handlers | Interrupts use the pause mechanism, not the error mechanism, by design |
| Failure provenance is checkpointed | If the process crashes after the node fails but before the handler completes, the handler sees the same `NodeError` on resume — resume-safe compensation |
| Subgraph failures surface to the parent | An unhandled subgraph exception fires the parent node's handler |

`set_node_defaults` scales this graph-wide — default retry policy, timeout, and error handler in one call, with per-node values overriding:

```python
graph = (StateGraph(State)
    .set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=3),
        error_handler=mark_process_failed,      # graph-wide default
        timeout=TimeoutPolicy(run_timeout=30))
    .add_node("fetch_data", fetch_data)          # uses defaults
    .add_node("charge_payment", charge_payment,
              error_handler=refund_payment)      # overrides the default
    .compile())
```

Applicability matrix rules: error-handler nodes get retry and timeout defaults applied but **never** an `error_handler` default (handlers must never catch themselves), and `cache_policy` is not applied to handlers (caching a compensation is unsafe). Defaults do NOT propagate into subgraphs — each graph owns its own.

Placement: per-node compensation goes in per-node handlers; cross-cutting bookkeeping (mark the external process row "failed", emit the alert, attach the `NodeError` to the trace) goes in the graph-wide default. Teams that put both in one handler build a monster function that becomes the failure point of the failure path.

## 7. Common Mistakes

| Mistake | Why it's bad | Fix |
|---|---|---|
| Retrying everything | Duplicate charges, emails, side effects | Idempotency classification first; retry policy per operation |
| Retrying 4xx errors | Deterministic failures retried deterministically fail | Exclude 401/404/validation (LangGraph's default already does) |
| New idempotency key per attempt | Server sees N distinct "first" calls | Key per logical operation, stable across retries |
| Raw tracebacks to the LLM | Context waste + hallucinated recovery | Structured error contract with category + retry_hint |
| No idempotency keys on write tools | Crash + resume = double side effects | UUID key per run, dedupe server-side |
| Retry predicate not matching the wrapped exception | Retries silently never fire | Fault-injection unit tests against real exception types |
