# Runnable Hardening Templates

**Load this when:** you are writing the actual hardening code (Steps 2-6) and want a verified starting pattern instead of prose.

Every block below parses as valid Python 3.10+. Replace `"{{...}}"` quoted markers and `[optional]` comments at the marked points. All LangGraph API usage follows the chapter-documented surface: `RetryPolicy`, `TimeoutPolicy`, `error_handler`, `NodeError`, `Command`, `RunControl`, `GraphDrained`, `PostgresSaver`, `runtime.heartbeat()`, `runtime.execution_info.node_attempt`.

## Template 1 — Retry wrapper with full-jitter backoff and budgets

For plain-Python call sites a node policy cannot reach. For graph nodes, prefer `RetryPolicy` on `add_node` (Template 5 wiring). Only ever applied to operations classified idempotent in Step 2.

```python
import random
import time

RETRYABLE_EXC = (TimeoutError, ConnectionError)  # [optional] extend per catalog; never 4xx/validation

def full_jitter_delay(attempt: int, base_s: float = 0.5, cap_s: float = 8.0) -> float:
    """Full jitter: sleep(random(0, min(cap, base * 2**attempt)))."""
    return random.uniform(0, min(cap_s, base_s * (2 ** attempt)))

def retry_with_backoff(fn, *, max_attempts: int = 3, total_budget_s: float = 30.0,
                       base_s: float = 0.5, cap_s: float = 8.0,
                       retry_on: tuple = RETRYABLE_EXC):
    """Retry a transient, IDEMPOTENT operation with full jitter and a total budget.

    {{OPERATION_NAME}} must be classified idempotent (or carry an idempotency key)
    before this wrapper is applied — a retry of a non-keyed write is a duplicate
    side effect, not resilience.
    """
    deadline = time.monotonic() + total_budget_s
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retry_on:
            if attempt == max_attempts:
                raise                      # fail the call; let the error handler decide
            delay = full_jitter_delay(attempt, base_s, cap_s)
            if time.monotonic() + delay > deadline:
                raise                      # latency budget spent; stop retrying
            time.sleep(delay)
```

Fault-injection test to ship with it (catches the wrapped-exception trap):

```python
def test_retry_matches_real_exception():
    calls = {"n": 0}

    def flaky():                       # stand-in for the SDK's real exception type
        calls["n"] += 1
        raise APITimeoutError("wrapped timeout")   # {{REAL_EXCEPTION_TYPE}}

    try:
        retry_with_backoff(flaky, max_attempts=3, base_s=0.01, cap_s=0.02,
                           retry_on=(APITimeoutError,))
        raised = False
    except APITimeoutError:
        raised = True
    assert raised and calls["n"] == 3, "predicate must match the WRAPPED type"
```

## Template 2 — Fallback chain node with degraded flag (rungs 0/1/2/4)

```python
import logging

log = logging.getLogger(__name__)

# Model clients: primary, secondary (different provider), small_fast (same provider).
# [optional] tune per-rung budgets; each rung needs its OWN timeout budget.
primary = "{{PRIMARY_MODEL_CLIENT}}"      # replace with your model client object
secondary = "{{SECONDARY_MODEL_CLIENT}}"  # different provider, simpler prompt
small_fast = "{{SMALL_FAST_MODEL_CLIENT}}"
CANNED_APOLOGY = "{{CANNED_APOLOGY_TEXT}}"


def call_model_with_fallback(state):
    """Rung 0 -> 1 -> 2 -> 4. Honest degradation: the run state carries the flag."""
    prompt = state["prompt"]
    for model, budget in [(primary, 30), (secondary, 15), (small_fast, 8)]:
        try:
            reply = model.invoke(prompt, timeout=budget)
            return {"answer": reply, "degraded": state.get("degraded", False)}
        except (ProviderError, TimeoutError):
            log.warning("model rung failed; falling through", extra={"model": model})
    # rung 4: canned reply + escalation, flagged and reason-tagged
    return {"answer": CANNED_APOLOGY, "escalated": True,
            "degraded": True, "degradation_reason": "all_model_rungs_exhausted"}
```

Rung-1/2 rules: test the fallback provider with the same eval suite as the primary; keep the fallback system prompt simpler; fail back automatically by probing primary health on a schedule.

## Template 3 — Circuit breaker wrapper for a tool

```python
import json
import time


class CircuitBreaker:
    """closed -> open (fail fast) -> half-open (one probe; success closes)."""

    def __init__(self, failure_threshold: int = 5, recovery_window_s: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_window_s = recovery_window_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.recovery_window_s:
            return False          # half-open: let one probe through
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None    # healed: back to closed

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()   # trip: fail fast for the window


_search_breaker = CircuitBreaker(failure_threshold=5, recovery_window_s=30)


def guarded_search(query: str) -> str:
    """Fail in milliseconds while the dependency is known-down; auto-recovers."""
    if _search_breaker.open:
        return json.dumps({"error": "unavailable", "category": "transient",
                           "retry_hint": "try later or use cached index"})
    try:
        result = search_engine.search(query)     # {{REAL_SEARCH_BACKEND}}
        _search_breaker.record_success()
        return result
    except Exception:
        _search_breaker.record_failure()
        raise
```

## Template 4 — Idempotent mutating tool (graph-generated key, server-side dedupe)

```python
from uuid import uuid4


def init_run_idempotency(state: dict) -> dict:
    """Graph entry node: set the run key ONCE, reused by every write tool.
    The key is passed by the graph, never generated by the model, and is
    stable across retries and resume replays."""
    if state.get("idempotency_key"):
        return {}                                   # double-resume: keep the key
    return {"idempotency_key": str(uuid4())}


class KeyedResultStore:
    """Server-side dedupe store (Stripe Idempotency-Key pattern)."""

    def __init__(self) -> None:
        self._seen: dict[str, object] = {}

    def lookup(self, key: str):
        return self._seen.get(key)

    def save(self, key: str, result) -> None:
        self._seen[key] = result                    # {{PERSISTENT_STORE}} in prod


_payment_store = KeyedResultStore()


def charge_tool(state: dict, amount: float, currency: str) -> dict:
    """Money-moving tool: repeat call with the same key returns the stored outcome."""
    key = state["idempotency_key"]
    existing = _payment_store.lookup(key)
    if existing is not None:
        return existing                             # replay/retry: no second charge
    result = payment_gateway.charge(amount, currency, key=key)  # {{PAYMENT_GATEWAY}}
    _payment_store.save(key, result)
    return result
```

Verify with the two assertions from Step 2: same key twice = one side effect; SIGKILL after the call then resume = still one side effect.

## Template 5 — Durable execution: PostgresSaver + SIGTERM drain + crash resume

```python
import logging
import signal

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph
from langgraph.types import RunControl

log = logging.getLogger(__name__)

DB_URI = "{{POSTGRES_URI}}"        # e.g. "postgresql://user:pass@host:5432/agents"
THREAD_ID = "{{THREAD_ID}}"        # max 255 chars; UUIDs are safe


def build_durable_graph(builder: StateGraph):
    """Compile with a production checkpointer and a drain-on-SIGTERM handler."""
    checkpointer = PostgresSaver.from_conn_string(DB_URI)
    checkpointer.setup()                       # create tables once per deployment
    graph = builder.compile(checkpointer=checkpointer)

    control = RunControl()
    signal.signal(signal.SIGTERM, lambda *_: control.request_drain("sigterm"))

    config = {"configurable": {"thread_id": THREAD_ID}}
    try:
        result = graph.invoke(inputs, config, control=control)
    except GraphDrained as e:                  # graceful stop between supersteps
        log.info("drained: %s", e.reason)      # checkpoint saved; resume after redeploy
        result = None
    return graph, config, result


def resume_after_redeploy(graph, config):
    """Resume a drained or crashed run from its last checkpoint (at-least-once:
    any node that started but did not checkpoint re-executes — keys required)."""
    return graph.invoke(None, config)          # None input = continue from checkpoint
```

`GraphDrained` follows the drained-run exception in the chapter text; nodes may read `runtime.drain_requested` / `runtime.drain_reason` to skip expensive work while draining. Drain does not cancel in-flight tasks — keep per-node timeouts in place.

## Wiring the policies onto nodes (composition order: attempt → timeout → retry → handler)

```python
from langgraph.types import RetryPolicy, TimeoutPolicy

builder.add_node(
    "fetch_order", fetch_order,
    retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.5,
                             backoff_factor=2.0, max_interval=8.0, jitter=True),
    timeout=TimeoutPolicy(run_timeout=60, idle_timeout=20),
)
builder.add_node(
    "charge_payment", charge_payment,
    retry_policy=RetryPolicy(max_attempts=3, retry_on=ConnectionError),
    error_handler=release_and_report,          # saga compensation; keep it simple + idempotent
)
```

## Post-write verification (run after applying any template)

1. `python3 -c "import ast; ast.parse(open('<file>.py').read())"` on every touched file.
2. Unit fault injection: stub each tool to raise timeout / 429 / 500 / permanent; assert retry counts, handler invocation, and the degrade path.
3. Resume test: SIGKILL mid-run, resume in a fresh process, assert continuation matches the uninterrupted run modulo expected re-execution.
4. Duplicate-effect test: call each mutating tool twice with the same key; assert exactly one side effect.
5. Overflow drill: force a 40k-token tool result; assert the run finishes with a truncation note, not a 400.
