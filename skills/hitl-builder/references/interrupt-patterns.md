# Interrupt & Approval Patterns

**Load this when:** choosing static vs dynamic pauses, picking a pattern from the catalog,
writing an interrupt payload, or resuming a paused run (including parallel fan-out).

## Static vs Dynamic Breakpoints

| | `interrupt()` (dynamic) | `interrupt_before/after` (static) |
|---|---|---|
| Where it lives | Inside node code | Graph structure (compile-time or per-invocation) |
| Conditionality | Any code condition (amount, flag, role) | None — pauses at that node every run |
| Payload | Arbitrary JSON ("show this question and these fields") | None (human sees "paused before node X") |
| Resume | `Command(resume=value)` | `invoke(None, config)` — no value carried |
| Intended use | Human workflows (approval, review, edit) | Debugging, stepping, inspection |

- The decision in one question: is the pause a **product feature** (a human must see rich
  context and decide) or a **developer tool** (you want to freeze execution and inspect)?
  Product feature -> `interrupt()`. Developer tool -> static breakpoints. The docs say it
  plainly: static breakpoints are "not recommended for human-in-the-loop workflows".
- Static breakpoints are configured via `compile(interrupt_before=[...],
  interrupt_after=[...])`, require a checkpointer, and resume with a `None` input. One
  legitimate hybrid: static breakpoints as temporary debugging scaffolding on a live graph
  (no code changes to add or remove).
- Common mis-design: "approval" via `interrupt_after("draft_node")` because it is one line
  of config. The human then sees nothing about the draft — they must query `get_state` to
  find it. That is a breakpoint someone forgot to productize. **Breakpoints freeze;
  interrupts communicate.** If the human needs context to decide — they always do — the
  context must travel in the interrupt payload.

## interrupt() Internals (what dictates every rule)

`interrupt(payload)` raises a special internal exception, caught by the LangGraph runtime
(not by your code). The runtime then: (1) saves graph state to the checkpointer — a
checkpointer is mandatory, no checkpointer no interrupt; (2) surfaces the payload to the
caller via `stream.interrupts` under `stream_events(version="v3")`, or
`result["__interrupt__"]` under plain invoke; (3) waits indefinitely — no timeout, no
polling, no compute. A suspended run costs nothing until resumed.

Resume: invoke the graph again on the same thread_id with `Command(resume=value)`. The
value becomes the *return value of the interrupt() call* inside the node. Two non-obvious
mechanics:

1. **The node restarts from its beginning.** Everything before the `interrupt()` call
   re-runs. This single fact drives all five rules below.
2. **Resume matching is index-based** for sequential interrupts in one node. Parallel
   fan-out interrupts must resume with the ID map (see below).

### The Five Rules (all verified, all commonly broken)

1. **Never wrap interrupt() in a bare try/except.** The suspension is an exception;
   catching `Exception` swallows it, the runtime never sees the interrupt, and the node
   runs past the approval point. Catch specific exception types.
2. **Don't conditionally skip or reorder interrupt calls across executions.** If the
   node's second run executes interrupts in a different order because state changed, the
   human's answers land on the wrong questions — the nastiest HITL bug there is.
3. **Don't loop interrupts inside a node.** A loop means each resume replays the loop from
   the top: resume 1 replays 1 iteration, resume 2 replays 2 — exponential re-execution of
   everything in the loop body, including side effects. Sanctioned validation pattern: call
   `interrupt()` exactly once per node invocation, store the re-prompt question in state,
   and use a conditional edge to loop back to the node.
4. **JSON-serializable payloads only.** Functions, class instances, and other live objects
   cannot survive the checkpoint round-trip.
5. **Side effects before the interrupt must be idempotent.** Anything executed before
   `interrupt()` runs at least twice. Upsert, don't insert. Place new-record creation and
   notifications after the interrupt, or in a separate downstream node.

Additional mechanics worth internalizing:

- **Determinism on resume:** anything computed before the interrupt re-computes; if it is
  non-deterministic (timestamp, random value, API fetch), the resumed node sees a different
  value. Don't compute things you need to be stable before an interrupt unless checkpointed.
- **Interrupts inside tools:** a tool function can call `interrupt()` directly — the
  natural gate for dangerous tools like `send_email` or `execute_query`. The approval logic
  lives with the tool, reusable across every graph that binds it; the resume value can even
  edit the tool's arguments before execution. The subtlety: the interrupt suspends the node
  executing the tool, so node-restart semantics apply to the whole agent step.
- **Subgraph propagation:** an interrupt deep in a subgraph pauses the whole parent run;
  the payload surfaces on the parent's stream; one `Command(resume=...)` resumes
  everything. `checkpointer=False` subgraphs cannot interrupt at all. Put approvals in the
  worker subgraph that owns the action, and standardize the payload schema so the parent's
  UI renders any worker's approval identically.
- **Timers:** there is no built-in interrupt timeout. "Auto-resume after N minutes" is your
  scheduler logic resuming with a default or escalating. Design the default before you need
  it: auto-expire to "reject" is safe; auto-expire to "approve" is how you end up in an
  incident review.

### Parallel Fan-Out Resume (the ID map)

When parallel branches each hit `interrupt()`, the run surfaces N interrupts, each with its
own ID. Resume with a map:

```
Command(resume={interrupt.id: answer for interrupt in stream.interrupts})
```

The runtime matches each answer to its interrupt by ID, so parallel approvals resolve
independently. Without the map you are back to index matching across parallel tasks — which
interleaves non-deterministically and binds answers to the wrong questions. The ID map is
the only correct way to resume concurrent interrupts.

## The Approval Pattern Catalog (16 patterns)

Every pattern is a point on a tradeoff between safety and throughput. Patterns 1-6 put the
human before the action; 10-16 put the human after or beside it; 7-9 are the queueing
machinery that keeps the before-patterns alive under load. Compose points on this spectrum;
do not default to pattern 1 everywhere.

| # | Pattern | Mechanism | Use When | Caution |
|---|---------|-----------|----------|---------|
| 1 | Approve/reject/edit | Interrupt + three-verb verdict | Single-action gating | Edit path must apply cleanly to state |
| 2 | Plan approval (approve-once) | Interrupt on the plan; autonomous steps after | Risk concentrated in the plan | Flag irreversible steps; log divergence |
| 3 | Dual control (maker-checker) | Two sequential interrupts, second sees first's rationale | Above a severity tier | Two humans = two latencies; batch when volume is high |
| 4 | Interrupt-in-tool | `interrupt()` inside the dangerous tool | Reusable gating across graphs | Node restart semantics apply |
| 5 | Question loop (validation) | One interrupt per invocation + conditional edge re-prompt | Collecting required human input | Never while-loop interrupts (exponential replay) |
| 6 | Edit-in-place | Resume value replaces a draft channel | Human refinement of generated content | Preserve non-draft context on continue |
| 7 | Batch review | Queue N payloads, one review session | High volume of homogeneous approvals | "Approve all" on heterogeneous items = click-through |
| 8 | Deadline auto-resolve | Scheduler resumes with a default at SLA expiry | Bounded human latency | Default must be the safe action; state it in the payload |
| 9 | Escalation ladder | Unanswered interrupts escalate owner -> team -> auto | Review capacity < demand | Escalation timeouts, not vibes |
| 10 | Shadow review | Humans review logged auto-decisions without blocking | Deciding whether to gate | Produces data, not safety — don't count it as control |
| 11 | Judge pre-filter | Cheap model scores; low-confidence goes to human | Cutting human load on cheap decisions | Calibrate judge vs human agreement; log disagreements |
| 12 | Annotate-not-block | Agent acts, flags risky items for later human review | Latency-critical, low-error-rate actions | Residual risk is priced, not eliminated |
| 13 | Human-as-tool | Agent calls an `ask_human(question)` tool mid-task | Clarification, not approval | Humans become a latency sink; cap calls per task |
| 14 | Teleoperation | Human drives steps; agent suggests each | High-stakes unfamiliar workflows | The human is the bottleneck — surface only decisions |
| 15 | Sandbox-gated execution | Dry-run/staged execution; promote on approval | Reversible containment beats gating | Promotion path must re-validate |
| 16 | Audit sampling | Post-hoc stratified review of auto-decisions | Statistical safety at low human cost | Tighten thresholds on rising sample error rate |

## The Three Decision Verbs

Every human approval reduces to approve, reject, or edit:

- **approve** — proceed unchanged.
- **reject** — stop the path; route to a cancel node, driven by the interrupt's return
  through a conditional edge.
- **edit** — the human changes the proposal (the action's arguments, or the generated
  text) and approves the edited version. The resume value carries the edits; the node
  applies them. Raw approve/reject on a bad draft wastes the round-trip; edit makes one
  human pass do real work.

```python
def review_node(state):
    verdict = interrupt({
        "instruction": "Review the email draft. Edit the body in place or reject.",
        "draft": state["draft"],                     # what is being judged
        "context": state["customer_summary"],        # why it exists
        "options": ["approve", "edit", "reject"]
    })
    if verdict.action == "approve":  return {"draft": state["draft"], "status": "approved"}
    if verdict.action == "edit":     return {"draft": verdict.edited_draft, "status": "revised"}
    return {"status": "rejected"}
```

## Approve-Each-Step vs Approve-Once

- Approve-each-step (interrupt before every irreversible action) is safest and most
  fatiguing; per-step approvals become rubber-stamps that train the human to click approve
  without reading.
- Approve-once (human approves a plan or batch, then the agent executes autonomously until
  the next checkpoint) is the right default where risk is concentrated in the plan. The
  human's leverage is highest at the plan level — usually where irreversibility and
  ambiguity peak.

```python
def plan_review_node(state):
    verdict = interrupt({
        "instruction": "Approve this 5-step plan, edit it, or reject.",
        "plan": state["plan"],                        # rendered steps, with tool names
        "irreversible_steps": [2, 4],                 # flagged: step 2 sends, step 4 deletes
        "estimated_cost": state["cost_estimate"],     # humans weigh cost well
        "options": ["approve", "edit", "reject"]
    })
    if verdict.action == "edit":
        state["plan"] = verdict.edited_plan           # human-edited plan is authoritative
    return {"status": "approved" if verdict.action != "reject" else "rejected"}
# downstream: execute steps autonomously with per-step status tracking,
# and a final summary interrupt if any step diverged from the approved plan.
```

Design rules: flag the irreversible steps (the human's real question is "can I live with
steps 2 and 4"), state the cost, and record plan-vs-execution divergence — the divergence
log is your best data on which steps should have been their own interrupt.

## Approval Criteria Design

Decide, in writing, when an interrupt fires. The criteria should be code (deterministic),
not the model's judgment.

```python
def should_interrupt(state) -> bool:
    return (state["amount"] > POLICY["max_auto_transfer"]        # deterministic threshold
            or state["risk_score"] > POLICY["risk_threshold"]    # cheap-model score
            or state["user_role"] == "unverified")               # identity signal

def transfer_node(state):
    if should_interrupt(state):
        decision = interrupt(approval_payload(state))            # human path
        if not decision.approved: return {"status": "cancelled"}
    transfer.execute(state["amount"], state["recipient"])        # auto path (audited)
    return {"status": "transferred"}
```

A risk-score gate is the scalable middle ground: a cheap model scores the action, scores
above threshold trigger interrupts, everything below auto-executes with the audit trail.
The two numbers to own: the risk-score threshold (tune against labeled historical actions)
and the false-interrupt rate (if >90% approval on a class, the gate is too tight or the
class doesn't need gating).

Criteria that FAIL in production: "the model will ask when unsure" (variable interrupt
volume, random friction); "always approve transfers" (no gating — fatigue); "interrupt on
high value" (undefined — what's high?); "let the user decide when signing up" (user-set
thresholds get set once and never revisited). Criteria that work: deterministic thresholds
(amount > $500), model-risk-score above a calibrated cutoff, identity signals (new account,
changed device), action-class allowlists. Write them in code, version them in config, audit
every interrupt against its criterion.

## The Approval Payload, Fully Specified

The payload is the entire human decision surface. This is the complete specification for a
transfer approval — the template for every payload you will write:

```python
approval_payload = {
    # WHAT will happen, concretely — the action and its arguments, rendered
    "action": {
        "name": "transfer",
        "args": {"amount_usd": 1250.00, "to_iban": "DE89 ... 5300",
                 "currency": "USD", "reference": "INV-2044"},
        "reversible": False,
    },
    # IMPACT — the numbers humans weigh well
    "impact": {
        "account_balance_after_usd": 3140.75,
        "limit_remaining_usd": 3750.00,
        "policy_note": "Approval required: amount exceeds $1,000 auto threshold",
    },
    # WHY the agent chose this — one line + evidence references (not the evidence)
    "rationale": "Invoice INV-2044 matches the purchase order PO-881 and was "
                 "approved by the vendor portal.",
    "evidence_refs": ["po://PO-881", "inv://INV-2044", "vendor://approval/2044"],
    # ALTERNATIVES the human might prefer
    "alternatives": [
        {"name": "partial_pay", "args": {"amount_usd": 625.00}},
        {"name": "hold_for_review", "args": {}},
    ],
    # DECISION CONTRACT — what the resume value must contain (the UI enforces this)
    "options": [
        {"verb": "approve", "resume": {"decision": "approve"}},
        {"verb": "reject",  "resume": {"decision": "reject", "reason": "{{REQUIRED_REASON}}"}},
        {"verb": "edit",    "resume": {"decision": "edit",
                                        "edited_args": "{{PARTIAL_ARGS_OVERRIDE}}"}},
    ],
    # PROCESS FACTS — the fatigue and audit machinery
    "deadline": "2026-08-12T18:00:00Z",   # stated in the payload, honored by the queue
    "timeout_default": "reject",          # safe default, never "approve"
    "tier": "maker",                      # "maker" | "checker" (dual control)
    "payload_hash": "sha256:9f2c...",     # proves what was shown
}
```

The design logic: the payload carries everything the human needs and nothing else.
Evidence is by reference (the reviewer can fetch; the context stays small). Alternatives
are pre-computed (the human edits a menu, not invents options). The decision contract is
machine-checkable (the UI cannot submit a resume value the node cannot handle). The process
facts (deadline, default, tier, hash) make the payload self-auditing. Every field answers
one of the four auditor questions or reduces one fatigue mechanism. **Approval payloads
decide your false-accept rate, not your prompts** — the team that shows "Approve?
[yes/no]" on an opaque action ID sees ~100% click-through and zero actual review; the team
that shows a rendered before/after diff with a dollar figure sees humans rejecting real
bugs. Budget the payload UI like a product feature — it is the product, for that human.

## Interrupt Payload Anti-Patterns

| Anti-Pattern | What the Human Sees | What It Produces |
|---|---|---|
| The opaque ID | "Approve action 4f9c21?" | Blind click-through |
| The raw state dump | 40 keys of JSON, 3 relevant | Skimming, missed details, false confidence |
| The missing impact | Action with no cost/blast-radius | Approvals of things the human never understood |
| The no-alternatives form | "Yes / No" on a bad proposal | Rejections that should have been edits |
| The unexplained rationale | Action with no "why" | The human re-derives context (or doesn't) |
| The vague deadline | "Please respond soon" | Stale decisions and unplanned escalations |
| The default-approve timeout | "Auto-approves in 4h if unanswered" | The postmortem incident, in miniature |
| The untyped resume | Free-text "ok sure thanks!" | The node cannot parse it; the human must re-answer |

Each anti-pattern is a fatigue amplifier and an audit gap in one object. The cheapest
review: take one real payload from production, sit next to one real reviewer, and watch
them decide. The friction you observe is the anti-pattern, localized.

## The Resume Sequence, Annotated

```
 [caller]  graph.stream_events(input, config={"configurable": {"thread_id": "t-77"}}, version="v3")
     |
 [graph]   node runs ... reaches interrupt(payload)
     |     -> special exception raised, caught by the RUNTIME (not by user code)
     |     -> state checkpointed (thread "t-77"; the checkpoint has tasks[0].interrupts)
     |     -> stream reports: stream.interrupted == True,
     |        stream.interrupts == (Interrupt(value=payload, id="i-1"),)
 [caller]  renders payload in the review UI; enqueues {thread_id: "t-77", interrupt_id: "i-1"}
     |       ... 35 minutes pass; the run costs nothing (no polling, no compute) ...
 [reviewer] approves with edits
 [caller]  graph.stream_events(Command(resume={"decision": "edit", ...}),
                               config={"configurable": {"thread_id": "t-77"}}, version="v3")
     |
 [graph]   the NODE re-runs from its start — everything before interrupt() re-executes
     |     -> interrupt() returns the resume value (the edits)
     |     -> node applies edits, executes the action once, writes state
     |     -> next checkpoint; the thread continues to completion
```

Four moments worth pausing on:

1. The suspension is invisible to user code — the node has no idea it "stopped"; it simply
   re-runs from the top later with `interrupt()` returning a value.
2. The thread ID is the entire resume handle — the queue stores one string, and the whole
   suspended computation is recoverable from it.
3. The pause costs nothing — the checkpoint waits, which is the economic asymmetry.
4. Resume is also a stream — the caller loops on `stream.interrupted`, because a resumed
   run can hit another interrupt.

This sequence, in full, is the HITL contract — everything else elaborates one of its steps.
