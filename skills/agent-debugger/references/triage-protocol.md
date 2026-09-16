# Triage Protocol

**Load this when:** an incident is live right now and you need to get from "something's wrong" to "open this runbook" in under sixty seconds.

## The 10-Minute Triage Protocol

Run this sequence in strict order. If you cannot complete it in ten minutes, the missing piece is almost always observability, not intelligence — fix the tracing before you fix the code.

```
MIN 0-1:  CONTAIN. Is money or customer data at risk? Kill switch (per model /
          per tool / per tenant) or rollback the last deploy. Do not
          investigate first — a looping refund agent is burning budget AND
          trust while you think.
MIN 1-4:  SCOPE. Metrics in order: error rate (which hop?), cost/min
          (looping?), latency p95 (slowness?), eval delta (semantic
          regression?). This tells you which runbook you're in.
MIN 4-7:  PICK A TRACE. Find 3 representative failing runs. The trace is the
          narrative: which node last completed, which tool was called, what
          the model saw and said. The failing hop names the owning subsystem.
MIN 7-10: STATE THE INCIDENT. One sentence in the channel: "runs fail at the
          refund gate with schema-validation errors on invoices created after
          Tuesday; suspected invoice schema drift; mitigated by X; owner Y."
          Unmitigated + unowned is how incidents become outages.
```

Why the order is rigid:

| Rule | Because |
|------|---------|
| Contain before investigating | A looping agent is actively burning money while you read its trace. |
| Metrics before logs | Metrics answer "how widespread is this?" in seconds; logs require knowing what to grep for. |
| Trace before state | The trace tells you which state to look at. |
| State the incident | An unmitigated, unowned incident turns a 20-minute fix into a 5-hour outage. |

## The Investigation Order Is a Cost Function

Traces answer "what happened" (per-run), metrics answer "how widespread" (aggregate), logs answer "what did the code say" (per-process), state answers "what does the system believe" (ground truth). In that order. Checking the checkpointer first is like reading the map before you know which country you're lost in.

**Investigation cheatsheet (paste into on-call docs):**

```
TRACES (LangSmith or equivalent):
  - filter runs by: time window + error status + node name
  - open 3 failing runs; find the LAST completed node in each
  - check the model call spans: token counts, model version, prompt hash
  - check the tool call spans: args (what the model believed), result
    (what was real)
  - check the run's state snapshot at the failure point (get_state)
METRICS:
  - error rate by node (which hop?)        - cost/min (looping?)
  - p95/p99 latency by node (which hop?)   - provider 429 rate (tier cap?)
  - eval score deltas (semantic?)          - cache hit rate (silent cost?)
LOGS (structured, correlation_id):
  - grep the run's correlation_id across services
  - gateway logs: 429/backoff sequences, routing decisions, budget refusals
  - orchestrator logs: GraphRecursionError, serialization errors, lock timeouts
  - sandbox logs: OOM, timeout, egress denials
STATE (the ground truth, read-only):
  - get the checkpoint payload for the failed run; diff against schema
  - check metadata.source == "update" in state history (did a human touch it?)
  - check thread_id consistency between run creation and resume records
ONE COMMAND THAT ANSWERS MOST INCIDENTS:
  "show me three failing runs, their last completed node, and the state
   at failure" — a trace filter + the state snapshot; the answer to
   "which hop, which subsystem" is visible in 90 seconds.
```

## The Symptom Router

Map the first thing you see to the runbook to open, plus the secondary check that catches the less obvious cause.

| First thing you see | Start with runbook | But also check |
|---------------------|--------------------|----------------|
| Same node repeating in the trace | 1 (loops) | 5 (cost), 10 (fallback not firing) |
| Users get wrong-but-confident answers | 2 (wrong tool) | 11 (memory), 13 (hallucinated tools) |
| Blank replies, half sentences | 3 (empty/truncated) | 6 (state corruption) |
| Runs stop at the same duration | 4 (timeout) | 7 (streaming), 10 (retry/fallback) |
| Bill changed, nothing else did | 5 (cost spike) | 14 (model changed) |
| Agent forgets things it just said | 6 (state corruption) | 11 (memory pollution) |
| Works in dev, broken behind the LB | 7 (streaming) | 4 (timeouts) |
| Some results missing, no errors | 8 (parallel loss) | 6 (reducers) |
| Pending approvals can't be applied | 9 (interrupt/resume) | 6 (checkpointer) |
| Fallback code exists but never fires | 10 (fallback) | 4 (timeouts) |
| Agent acts on old/wrong facts | 11 (memory pollution) | 14 (model changed) |
| Evals dropped after "just a wording change" | 12 (eval regression) | 14 (model changed) |
| Calls to tools that don't exist | 13 (hallucinated tool calls) | 2 (wrong tool) |
| Quality sagged with zero deploys | 14 (model changed) | 11 (memory), 12 (evals) |
| Rejected calls to tools like admin_refund_tool | 15 (injection attempt) | 13 (hallucinated tools) |
| Approval queue grows day over day | 16 (HITL queue overflow) | 9 (interrupt/resume) |

## Error Message Dictionary

Error strings are the fastest signal-to-runbook router in your system. Pin this table where on-call can see it.

| Error you see | What it really means | Runbook |
|---------------|----------------------|---------|
| GraphRecursionError: Recursion limit of N reached | The loop detector fired; the run executed N supersteps without terminating | 1 |
| context_length_exceeded / prompt is too long | Input + requested output exceeds the context window; usually history or tool-result bloat | 3 |
| 429 Too Many Requests / rate_limit_error | Provider tier cap or shared-account contention; backoff helps only if transient | 4, 10 |
| Object of type 'datetime.datetime' is not JSON serializable | Non-serializable value reached the checkpointer at persist time | 6 |
| PicklingError: Could not pickle ... | Same disease, different serializer (thread/lambda/cursor captured in state) | 6 |
| thread not found / checkpoint not found | thread_id mismatch, wrong checkpointer, or checkpoint GC'd | 9 |
| KeyError: 'resume' / interrupt resume mismatch | Resume value passed under the wrong key or the run is not actually interrupted | 9 |
| Tool ... not found (registry) | Hallucinated tool call — the model invented a name the registry rejects | 13 |
| validation error: field 'amount' expected number | Model produced args that fail the schema; usually free-text fields that should be enums/ranges | 13 |
| BrokenPipeError / client disconnected in stream logs | Client dropped mid-stream; run continues server-side — check re-attach logic, not the graph | 7 |
| connection pool timeout (async gateway) | Concurrency exceeds pool size; usually retry storms during a provider slowdown | 4, 10 |
| idempotency key conflict (your own API) | A replayed tool call hit dedup — correct behavior; verify the original result is being returned | 6 |
| connection reset by peer on the model call | Provider closed mid-stream (often during long generations or provider deploys); retry is usually safe if no tool side effects | 4, 10 |
| invalid_request_error: messages.1: all messages must have non-empty content | A node wrote an empty message into state (often a tool result with no body); the model call never happened | 6 |
| max retries exceeded with url: ... (NewConnectionError) | The gateway cannot reach the provider — DNS/egress/TLS issue, not the provider itself; check your own network first | 4, 10 |
| JSONDecodeError inside the streaming assembler | A chunk boundary split a token or the stream emitted malformed SSE — client-side, not model-side | 7 |
| Recursion limit reached inside an except log from a successful run | The loop cap fired, the error handler recovered, and the run "succeeded" with degraded output — loop protection worked, quality didn't | 1 |
| unsupported parameter: 'tools' (fallback model) | The fallback chain routed to a model without tool support — the hidden cost of heterogeneous fallbacks | 10 |
| checkpoint channel X not found in state schema | Resume hit a checkpoint written by an older schema — the version-pinning gap, made visible | 6, 9 |
| InvalidRequestError: tool results must include tool_call_id | A manually-constructed ToolMessage lost its pairing ID — the model cannot correlate results to calls | 6 |
| TimeoutError: timed out waiting for lock on thread | Two runs on one thread are serializing; either legitimate (fan-out) or a duplicate-submit bug | 4, 6 |

## Reading a Trace in 60 Seconds

Read pattern by pattern, not line by line. Annotated failing run:

```
RUN 8f2c...  (thread T-10421, user 9917, graph v12, 3m42s, FAILED)
 |
 +-- NODE ingest ........... 12ms   OK        <- everything before this is plumbing
 +-- NODE triage ........... 1.9s   OK
 |     +-- LLM call (small model, pin 2026-06)
 |           in: 3,412 tok / out: 41 tok      <- small out = a decision, not prose
 |           output: {category: "billing", confidence: 0.84}
 +-- NODE billing_specialist  FAILED
 |     +-- LLM call #1 (mid model, pin 2026-05)
 |           in: 3,988 tok / out: tool call get_invoice(customer_id="9917")
 |     +-- TOOL get_invoice ......... 14.2s    <- 14s for a DB read? SUSPECT
 |           result: "error: timeout talking to ticket-db"
 |     +-- LLM call #2: in 4,212 tok / out: tool call get_invoice (SAME args)
 |     +-- TOOL get_invoice ......... 14.1s    <- loop seed
 |           result: "error: timeout talking to ticket-db"
 |     +-- LLM call #3: in 4,436 tok / out: "I'm sorry, I couldn't..."
 +-- NODE draft_reply (never ran)
 +-- STATE at failure: specialist_findings.billing = None, attempts = 0
```

What an expert sees in thirty seconds: the tool was the failure (14-second timeouts on a DB read = the DB or its client is the problem, Runbook 4). The model did the rational thing twice and gave up gracefully. The design gap: no no-progress detector — a third identical call should never have happened (Runbook 1). And `attempts = 0`: the loop counter the design promised was never wired into this path. The trace told the whole story: root cause (ticket-db), amplification (two pointless retries), missing armor (no-progress detector). Three fixes: DB pool config, tool timeout classification, the detector — none of them in the model.

## Incident Retro Template

Thirty minutes, five sections, filled in while it's fresh. The retro converts one team's pain into the whole platform's immunity.

1. **Timeline** (factual, not narrative): hh:mm what was observed, by whom, by what alert. The gaps in the timeline ARE the observability gaps — if you can't say when something happened, you can't alert on it.
2. **Failed hop:** which hop failed, and which runbook matched. If no runbook matched, you owe the library a new one — every incident that doesn't match an existing runbook is a runbook you need to write.
3. **Root cause, stated twice:** once technically ("the reducer overwrote the billing branch's result") and once systemically ("we had no test asserting parallel results survive"). The technical cause tells you what broke; the systemic cause tells you why it stayed broken — and generates the permanent fix.
4. **Detection gap:** how long between cause and page, and what metric would have cut that time. Convert exactly ONE metric into an alert. Not five — one.
5. **One permanent artifact:** one of — a new eval case, a new CI test, a runbook edit, a kill-switch addition. If the retro produces none of these, it produced nothing.

## Runbook Maturity Ladder

Each rung is a capability; you cannot skip rungs.

```
RUNG 1: REACTIVE   — incidents discovered by users; fixed by whoever is awake.
RUNG 2: ALERTED    — metrics catch hard failures (errors, timeouts) automatically.
RUNG 3: DIAGNOSED  — traces exist; the failed hop is findable in < 30 min.
RUNG 4: GATED      — evals in CI prevent the regressions you've already met.
RUNG 5: DRILLED    — on-call rehearses: kill switches work, rollback is one
                     command, provider outage is survivable, resume drills pass.
RUNG 6: MEASURED   — every incident has detection-gap + permanent-artifact data;
                     mean-time-to-diagnose is tracked and falling.
```

Rungs 1-2 are monitoring work. Rung 3 is tracing. Rung 4 is evaluation. Rung 5 is rehearsal. Rung 6 is culture. The fastest way up is honestly filling the retro template every single time.

## Quarterly Drill Calendar

Rehearsal beats documentation. Four drills a year, ~90 minutes each — they test the team's response, which is the part that rots when unused.

| Quarter | Drill | Pass condition |
|---------|-------|----------------|
| Q1 | Loop drill: deploy a loop-inducing prompt into staging | Detect (alert), contain (kill switch), prevent (step cap) within 30 minutes |
| Q2 | Provider drill: block the primary provider in staging | Fallback fires within 30s, degrades gracefully, bills correctly (Runbook 10) |
| Q3 | Resume drill: kill worker pods mid-interrupt on a money flow | Exactly-one side effect after restart (Runbooks 9, 6) |
| Q4 | Regression drill: deploy a prompt that drops an eval slice 5 points | Gate blocks it, or alert fires and rollback completes in one command (Runbook 12) |

Drills that "pass" because the system was working are still valuable — they prove the alerting and rollback paths. Log drill results next to the retro template.
