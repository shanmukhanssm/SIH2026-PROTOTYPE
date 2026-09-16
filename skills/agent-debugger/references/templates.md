# Templates — Runnable Python for agent-debugger

**Load this when:** implementing Workflow A instrumentation or Workflow C tooling: structured logging, span emission, baseline alerts, checkpoint replay, and latency budget tracking. Python 3.10+, LangGraph python idioms. Replace every {{PLACEHOLDER}} before running.

---

## Template 1 — Structured logging setup (JSON lines, correlation IDs, redaction)

Every log line carries the thread_id + run_id correlation pair, an event from a finite vocabulary, and passes through redaction before the serializer sees it. Retries log at WARNING — a rising retry rate is your earliest outage signal.

```python
"""Structured JSON logging for a LangGraph agent. Wire once at process start."""
import contextvars
import hashlib
import json
import logging
import sys

RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")
THREAD_ID: contextvars.ContextVar[str] = contextvars.ContextVar("thread_id", default="")

# Finite event vocabulary so dashboards can group on `event`.
EVENTS = {"node_enter", "node_exit", "tool_call", "tool_retry",
          "checkpoint_write", "degrade", "fail"}

# {{PLACEHOLDER: list every field your compliance policy forbids in logs}}
REDACT_KEYS = {"api_key", "authorization", "password", "email", "phone", "ssn"}


def _redact(value):
    """Mask secrets at the logging boundary, never inside business code."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in REDACT_KEYS else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and len(value) > 256:
        return f"[len={len(value)} sha={hashlib.sha256(value.encode()).hexdigest()[:12]}]"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = record.__dict__.get("fields", {})
        line = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "thread_id": THREAD_ID.get(""),
            "run_id": RUN_ID.get(""),
            "node": record.__dict__.get("node", ""),
            "event": record.__dict__.get("event", ""),
            "message": record.getMessage(),
        }
        line.update(_redact(payload))
        return json.dumps(line, default=str)


def setup_logging() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]      # single structured pipeline
    root.setLevel(logging.INFO)
    return handler


def log_event(event: str, *, level: int = logging.INFO, **fields) -> None:
    assert event in EVENTS, f"unknown event {event!r} — extend EVENTS deliberately"
    logging.getLogger("agent").log(level, event, extra={"event": event, "fields": fields})


# Usage inside a node:
#   log_event("node_enter", node="loop", state_size_bytes=24576)
#   log_event("tool_retry", node="loop", tool="fetch_order", attempt=2,
#             reason="ConnectionError", latency_ms=341, level=logging.WARNING)
#   log_event("checkpoint_write", node="loop", duration_ms=23)
```

---

## Template 2 — Span instrumentation per the instrumentation contract

Emits the six-span contract (run > node > llm/tool/retrieval) as JSON lines with correct parentage. If you use LangSmith, auto-instrumentation covers most of this — this recorder is for OTel/custom pipelines and for keeping the contract testable. For sub-agents, set `parent_agent` at delegation time.

```python
"""Minimal span recorder implementing the instrumentation contract.

Span tree: run(root) -> node -> {llm | tool | retrieval}; checkpoint_write at run level.
"""
import contextvars
import hashlib
import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

_RUN_STACK: contextvars.ContextVar[list] = contextvars.ContextVar("run_stack", default=[])


class SpanSink:
    """Where finished spans go. Swap for an OTel exporter or HTTP post."""

    def __init__(self, path: str) -> None:
        self._path = path  # {{PLACEHOLDER: path or endpoint for your span store}}

    def write(self, span: dict[str, Any]) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(span, default=str) + "\n")


class SpanRecorder:
    def __init__(self, sink: SpanSink) -> None:
        self.sink = sink

    @contextmanager
    def span(self, kind: str, name: str, **attrs: Any) -> Iterator[dict]:
        stack = _RUN_STACK.get()
        parent = stack[-1]["span_id"] if stack else None
        span = {
            "kind": kind, "name": name, "span_id": f"{kind[:2]}_{int(time.time()*1e6)}",
            "parent_span_id": parent, "start_ms": time.time() * 1000, **attrs,
        }
        stack.append(span)
        error_class = None
        try:
            yield span
        except Exception as exc:  # record, then re-raise: spans never swallow errors
            error_class = type(exc).__name__
            raise
        finally:
            stack.pop()
            span["end_ms"] = time.time() * 1000
            span["latency_ms"] = round(span["end_ms"] - span["start_ms"], 1)
            span["error_class"] = error_class
            self.sink.write(span)


# recorder = SpanRecorder(SpanSink("{{SPAN_LOG_PATH}}"))  # instantiate once at startup

# --- usage inside a LangGraph node -----------------------------------------
# def loop_node(state):
#     with recorder.span("node", "loop", attempt=state.get("attempt", 1)):
#         with recorder.span("llm", "plan", provider="{{PROVIDER}}",
#                            model="{{MODEL}}", tokens_in=..., tokens_out=...,
#                            cost_usd=..., finish_reason="tool_calls",
#                            prompt_hash=hashlib.sha256(prompt).hexdigest()[:16]):
#             ...
#         with recorder.span("tool", "fetch_order", args_hash=...,
#                            result_size=340, latency_ms=210):
#             ...
# --- run root + sub-agent delegation ---------------------------------------
# with recorder.span("run", "customer_refund_inquiry", thread_id=..., run_id=...,
#                    tenant="{{TENANT}}", graph_version="{{SEMVER}}",
#                    config_hash=hash_of_resolved_prompt_plus_graph_version):
#     ...  # node spans nest under the run span automatically
# # When delegating to a sub-agent, stamp the delegator on the child root span:
# with recorder.span("run", "researcher", parent_agent="orchestrator", ...):
#     ...
```

> NOTE: the recorder never swallows exceptions — errors propagate after the span is marked with `error_class`. For sub-agent fan-out, pass the recorder down (or re-create per process) so child spans keep the parent linkage; in OTel, propagate the parent span ID manually.

---

## Template 3 — Trace-based alert condition (baseline deviation, sustained)

Alert template: "metric X for scope S exceeds K times the baseline for N consecutive windows of W minutes." Metrics must be computed at trace completion and exported — never queried from raw traces at alert time. Starting points: cost 2x/2 windows, errors 3x/2 windows, p95 latency 1.3x/4 windows.

```python
"""Sustained baseline-deviation alert evaluator.

metrics: list of {"ts": epoch_minutes, "scope": str, "value": float} sampled per window.
baseline: trailing 7-day median for the same scope (computed offline and passed in,
or maintained in a dict). One page per incident: dedupe on scope, not on runs.
"""
from statistics import median
from typing import Callable

# Tuning knobs per alert class (from the runbook of pain):
ALERT_CLASSES = {
    "cost_per_run":  {"k": 2.0, "windows": 2},
    "error_rate":    {"k": 3.0, "windows": 2},
    "p95_latency":   {"k": 1.3, "windows": 4},
    "iterations":    {"k": 2.0, "windows": 2},
    "escalation":    {"k": 2.0, "windows": 2},
}


def baseline_7d(history: list[float]) -> float:
    """Trailing 7-day median — the alert's denominator."""
    return median(history) if history else 0.0


def evaluate_alert(
    series: list[float],
    history: list[float],
    metric: str,
    *,
    on_fire: Callable[[str, float, float], None],
) -> bool:
    """Fire only when the deviation is SUSTAINED for the class's window count."""
    cfg = ALERT_CLASSES[metric]
    base = baseline_7d(history)
    if base <= 0:
        return False  # no baseline -> no alert; absolute thresholds are wrong within a week
    recent = series[-cfg["windows"]:]
    sustained = len(recent) == cfg["windows"] and all(v > cfg["k"] * base for v in recent)
    if sustained:
        latest = recent[-1]
        # {{PLACEHOLDER: attach the saved trace-filter URL + escalation owner to this page}}
        on_fire(
            f"{metric} {latest:.3f} > {cfg['k']}x baseline {base:.3f} "
            f"for {cfg['windows']} consecutive windows",
            latest,
            base,
        )
    return sustained


# Wiring:
#   def page(message: str, value: float, base: float) -> None:
#       alerting.send(subject=f"[agent] {message}", body=RUNBOOK_URLS[metric])
#   evaluate_alert(cost_series, cost_history_7d, "cost_per_run", on_fire=page)
#
# Two alerts most teams miss (both are silent failures):
#   - retry rate at WARNING: rising retries precede outages by hours.
#   - online judged-eval score drop: error and latency metrics stay flat
#     while quality quietly regresses.
```

---

## Template 4 — Checkpoint replay debugging (sandbox only)

Diff adjacent checkpoints to find the poisoned field, then fork and replay with the fix. Replay re-executes real side effects for every step you re-run — always point the graph at sandboxed tool stubs or a shadow environment first.

```python
"""Replay debugging via LangGraph checkpoints.

RUN IN A SANDBOX ONLY: resuming re-executes charges, emails, and API calls
contained in the re-run steps. Bind every tool to a stub before invoking.
"""
from typing import Any


def diff_states(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Fields written (or changed) by the step between two checkpoints."""
    changed = {}
    for key, new_value in after.items():
        old_value = before.get(key, "<absent>")
        if old_value != new_value:
            changed[key] = {"before": old_value, "after": new_value}
    return changed


def replay_from(config: dict, step_index: int, patches: dict[str, Any] | None = None) -> Any:
    """Fork the thread at `step_index` checkpoints back and re-run with `patches`."""
    history = graph.get_state_history(config)      # all checkpoints, newest first
    target = history[step_index]                   # {{PLACEHOLDER: the checkpoint BEFORE the failure}}

    current = graph.get_state(config)              # where the run ended
    changed = diff_states(target.values, current.values)
    print("poisoned fields written by the failing step:", changed)

    if patches:
        # e.g. {"tools": {"search": stub_search}} — swap in the sandboxed fix
        graph.update_state(target.config, patches)  # {{PLACEHOLDER: patch dict}}
    return graph.invoke(None, target.config)        # re-runs from that checkpoint


# Incident flow:
#   1. tools = build_sandbox_stubs(PROD_TOOLS)   # stub every side-effecting tool
#   2. replay_from(config={"configurable": {"thread_id": "{{FAILED_THREAD_ID}}"}},
#                  step_index={{FAILING_STEP_MINUS_1}},
#                  patches={"tools": tools})
#   3. diff the replayed output against the failed run's trace
#   4. promote the fix to code, then re-run the FULL graph on the eval set
```

---

## Template 5 — Latency budget tracker

The budget spreadsheet as code: per-component budget lines, per-node p95 measured from traces, breach report with total vs SLO. Re-pin budgets deliberately, not automatically.

```python
"""Latency budget tracker: hold every component to its budget line.

Feed per-node p95 (ms) from your trace pipeline (LangSmith or OTel metrics).
Alert when a component's p95 exceeds its line for 2+ hours.
"""
from dataclasses import dataclass


@dataclass
class BudgetLine:
    component: str
    budget_ms: float
    p95_ms: float          # measured at p95 over the pinned benchmark dataset
    p50_ms: float = 0.0


# {{PLACEHOLDER: fill budgets from the measured breakdown; anchor: TTFT <= 2s, total <= 15s}}
def ms(placeholder: str) -> float:
    """Convert a measured-p95 placeholder into a float; fail loudly until filled."""
    return float(placeholder)


BUDGET: list[BudgetLine] = [
    BudgetLine("gateway+auth", budget_ms=200, p95_ms=ms("{{MEASURED_P95_MS}}"), p50_ms=ms("{{MEASURED_P50_MS}}")),
    BudgetLine("checkpoint_read", budget_ms=50, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("classify", budget_ms=800, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("retrieve", budget_ms=400, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("loop_iteration_1", budget_ms=2500, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("loop_iteration_2", budget_ms=2500, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("final_answer", budget_ms=2500, p95_ms=ms("{{MEASURED_P95_MS}}")),
    BudgetLine("post_processing", budget_ms=200, p95_ms=ms("{{MEASURED_P95_MS}}")),
]

SLO_TOTAL_MS = 15000  # user-experience anchor for interactive agents


def audit(budget: list[BudgetLine] = BUDGET) -> list[str]:
    breaches, total_p95 = [], 0.0
    for line in budget:
        total_p95 += line.p95_ms
        if line.p95_ms > line.budget_ms:
            over = (line.p95_ms / line.budget_ms - 1) * 100
            breaches.append(
                f"BREACH {line.component}: p95 {line.p95_ms:.0f}ms "
                f"> budget {line.budget_ms:.0f}ms (+{over:.0f}%)"
            )
    print(f"total p95 {total_p95:.0f}ms vs SLO {SLO_TOTAL_MS}ms "
          f"({'OK' if total_p95 <= SLO_TOTAL_MS else 'SLO VIOLATION'})")
    for b in breaches:
        print(b)
    if not breaches:
        print("all components within budget — re-rank before optimizing anything")
    return breaches


if __name__ == "__main__":
    audit()

# Discipline reminders:
# - Rank latency contributors and cost contributors separately (two lists).
# - Optimize the percentile that violates its line (often p95, not p50).
# - After each one-change optimization, re-measure on the SAME pinned dataset
#   and append an entry to the optimization log with before/after numbers.
```
