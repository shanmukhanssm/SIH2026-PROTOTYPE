# Asynchronous HITL, Queues, Multi-Agent Gating, and When HITL Hurts

**Load this when:** the reviewer is hours or days away (background agents), you need review
queues / timeout policies / escalation, you are gating a multi-agent system, you are
designing the approval UX, or you are deciding whether HITL is worth it at all.

## The Async Architecture

Async HITL means the human reviews hours or days after the run paused. The architecture:

1. The graph interrupts and stops — checkpointed (durable, zero compute cost).
2. A queueing service (outside the graph) observes the interrupt via `stream_events` and
   puts one row per pending decision into a DB-backed review queue.
3. A reviewer claims items, decides in a form-constrained UI.
4. The service resumes the thread with `Command(resume=decision)` on the stored thread_id.

Nothing polls, nothing idles: the checkpoint is the persistence of the in-flight work.

```python
# At interrupt time (in the graph):
payload = interrupt(approval_request)                # graph pauses; thread checkpointed

# The queueing service (outside the graph) observes the interrupt via stream_events:
queue.put({
    "thread_id": config["configurable"]["thread_id"],  # THE resume key — never lose it
    "checkpoint_id": observed_checkpoint_id,           # what the human is deciding against
    "payload": payload,                                # rendered decision surface
    "interrupt_id": interrupt.id,                      # for multi-interrupt resumes
    "deadline": now() + SLA,                           # escalation/auto-resolve time
    "assigned_to": None,                               # reviewer claim state
})

# Later (hours/days):
queue.assign(item, reviewer)
decision = reviewer_ui(item)                           # form-constrained verdict + rationale
graph.invoke(Command(resume=decision),                 # resume on the SAME thread_id
             config={"configurable": {"thread_id": item.thread_id}})
```

The thread's state is in the checkpointer, not in process memory. Server restarts, deploys,
and crashes do not lose the suspended run. The pieces you must not lose: the thread ID, the
interrupt payload, and the resume contract (what values the node expects back). Async HITL
needs a queue, a UI, and the thread ID — no long-running workers, websockets, or
heartbeats. Teams that internalize "the checkpoint is the process" build async review in a
week; teams that don't build elaborate session state machines and regret it.

## The Four Slow-Human Failures and Their Guards

| Failure | What happens | Guard |
|---|---|---|
| Context rot | The system changed while the run slept (schemas migrated, tools redeployed, prompts updated) | Pin behavior versions; honor interrupted-thread compatibility rules (never rename a node with a pending interrupt) |
| Stale decisions | The human approves a transfer drafted 3 days ago against prices that moved | Re-validate on resume — re-check the payload's assumptions in a node immediately after the interrupt |
| Lost human attention | The reviewer forgets the context of a 3-day-old decision | The queue UI re-renders the full payload + context from the thread state |
| Expired authorization | The human who approves a 4-day-old request may no longer have the role | Re-check authorization at RESUME time, not enqueue time |

Guards 2 and 4 are non-negotiable for anything irreversible.

**WARNING — time travel and async HITL collide:** a human who forks a thread to "see what
would happen" can leave the live thread pointing at a branch, and the queued review resumes
the wrong line. Keep exploration on explicit copies (new thread IDs), and have the resume
path assert the checkpoint it expects.

## Timeout Policies, Escalation, and Notifications

- Every interrupt class gets a deadline AND a default. "Human will review when they get to
  it" means refunds sit pending for a week. Define per class: auto-approve after X (rarely),
  auto-deny after X (money), or escalate to a manager after X (realistic). State the SLA at
  interrupt time — most approval complaints are actually silence complaints.
- The default must be the SAFE action: reject or best-so-far. An approval that silently
  auto-expires to "approve" is delayed execution with extra steps (see Postmortem in
  references/audit-compliance.md).
- Escalation ladder: unanswered interrupts escalate owner -> team -> auto-resolve, on timed
  rungs — and the ladder itself must be monitored. A fallback with no heartbeat that
  silently breaks is a control that was never a control; if the fallback doesn't process
  anything in a window, page someone.
- Notification checklist: (1) notify with the decision context inline (payload summary, not
  just "action required"); (2) notify the right person — role-based routing; (3) escalate on
  silence with stated deadlines; (4) deep-link to the review UI with the thread ID; (5) one
  channel, batched — reviewers tune out channels that notify 30x/day. Every notification is
  a withdrawal from the user's attention budget; spend it like money.
- Expected-wait communication: if the UI says "one moment" while a human spends 4 hours
  reviewing, the product has lied. Surface the human step: "Your request is queued for
  review; median wait is 35 minutes; we'll notify you." Systems that hide the human step
  train users to retry, which creates duplicate interrupts, which lengthens the queue — a
  self-inflicted fatigue spiral.

## Interrupt Fatigue, Batching, and Queue Design

Interrupt fatigue is the #1 HITL killer. Fatigued humans click through; click-through
approval is worse than no approval — it has the cost of HITL with the safety of autonomy,
and it produces an audit trail that falsely documents "reviewed." Mechanisms: too many
interrupts (each one cheapens all), uninformative payloads, interruptions at wrong times.
Combat with approve-once semantics, risk-gating, and payload quality. A good payload is a
15-second decision; a bad one is a 2-minute puzzle.

- Vigilance decays measurably after ~20-40 interrupt decisions per reviewer per day in
  high-stakes settings. Every interrupt you don't need is saved human vigilance for the
  interrupts you do need.
- Batching: group N pending approvals into one review session, sorted by urgency, with
  "approve all similar" for homogeneous batches. One 5-minute session reviewing 20 items
  beats 20 context-switches costing 20x2 minutes. Batch only what is genuinely homogeneous.
- Design the queue like an air-traffic controller's board, not an inbox: sort by urgency
  and irreversibility; never let trivial interrupts crowd out the high-stakes ones.
- Track interrupt-to-resume latency as a first-class metric: p50 at 2 minutes and p90 at 3
  days is a review-queue capacity problem, not an agent problem — the fix is scheduling
  humans, not tuning prompts.

## Multi-Agent HITL

Three placement options:

1. **Per-agent approvals** — best local context, most human burden; failure mode is
   coverage gaps (an action no agent thought to gate).
2. **Single gate before the irreversible action** — one human decision, minimal fatigue;
   failure mode is context thinning (the gatekeeper sees the action but not the chain of
   reasoning). Fix by including the upstream rationale trail in the payload.
3. **Hybrid** — per-agent interrupts for domain-critical actions, a system gate for
   cross-cutting irreversibility. Right for most real systems.

Rules:

- Interrupt where irreversibility begins, not where agent boundaries are. In most systems
  only one action is truly irreversible (the send/publish/commit); everything before it is
  reversible internal state.
- Interrupts inside subgraphs propagate to the top-level run — the pause surfaces on the
  parent's `stream_events` and one `Command(resume=...)` resumes the whole graph.
- For independent parallel workers, one worker's slow approval should not block the others:
  approve-in-parallel by fan-out, resume with the `{interrupt_id: value}` ID map.
- When a supervisor dispatches 3 workers and 2 need approval, batch the two payloads into
  one human decision rather than suffering two sequential waits.
- One uniform payload schema across all agents (action, impact, rationale, alternatives) so
  the review UI renders any agent's interrupt identically. Each team designing its own
  payload fragments the reviewer experience and quietly corrupts the audit trail. Own the
  schema at the platform level, like an API contract, with versioning.

## HITL in Multi-Turn / Chat Products

The interrupt is not a modal dialog — it is a message in the conversation. Design it well
or the chat becomes a CAPTCHA:

- Ask-with-a-rendered-proposal: the approval request renders in-chat with one-tap
  approve/reject/edit controls. If approval requires navigating to another surface, users
  defer and threads stall.
- Edit affordances in the thread: for content approvals, let the user edit inline before
  approving.
- Async awareness: if approval can take hours, say so ("queued for review, median 35 min,
  you'll be notified").
- Re-entry semantics: the user returns days later to an interrupted thread; the resume path
  must re-render the original payload from the checkpoint, not just "continue?".
- Two interrupts per thread are fine; ten are not. Track interrupts-per-thread like any
  other fatigue metric.

The frontend contract (three behaviors that separate demo-grade from production HITL): the
frontend must not assume the interrupt is the last thing that will happen (new interrupts
can follow); must tolerate a resume that lands on a second interrupt (loop until
`stream.interrupted` is false); and must persist the thread ID across user sessions
(losing it orphans the paused run).

## Production Gotchas

1. **Multi-instance resume races:** two service instances both resume the same thread
   (duplicate queue delivery, impatient user double-click). Make resume idempotent at the
   queue level with claim semantics.
2. **Schema/prompt drift while paused:** pin `behavior_version` and honor the
   interrupted-thread compatibility rules (don't rename the pending node).
3. **Checkpointer availability = HITL availability:** an interrupt is an error state if the
   checkpointer is down. Monitor it like a database, because it is one.
4. **Timezone/clock discipline:** deadlines, escalations, and audit timestamps all depend
   on consistent clocks. Store UTC, display local.
5. **Payload size limits:** interrupt payloads ride through the checkpointer and the queue.
   Bound them or the review UI and queue both choke.
6. **PII in review UIs:** the payload is shown to humans who may not be authorized for
   everything in it. Show what the reviewer's role permits.

Platform note: LangGraph Platform stores checkpoints server-side per thread (a suspended
run is a thread whose state the platform keeps; resume is an API call with the thread ID).
For self-hosted LangGraph, the checkpointer, queue, and review UI are your infrastructure
and the patterns here are the blueprint.

## When HITL Hurts — and the Alternatives

The throughput math: if fraction P of tasks hit an approval with median human latency L,
median end-to-end latency goes from ~1s to ~L, and p90 becomes the human queue's p90
(hours). Throughput is bounded by R reviewers x decisions/hour — a hard ceiling no matter
how many GPUs you rent; if review demand exceeds capacity the queue grows without bound.
The idle-cost surprise: suspended runs cost zero compute — HITL burns time and human
attention, which are the scarcer resources. The right fix for "HITL is slow" is never
"more GPUs" and usually "fewer, better-targeted interrupts."

The alternatives, in order of preference:

1. **Sandboxes.** Let the agent act where actions are reversible or contained: staging
   systems, dry-run modes (`dry_run=True` returning the would-be effect), shadow writes,
   budget caps enforced by the tools (caps are enforcement, not prompts), allow-listed tool
   arguments (poka-yoke for blast radius), shadow execution (staging in parallel with
   production; promote on approval). A transfer tool capped at $0 real money until a human
   flips the flag is safer than any approval UI — containment is always on, whereas
   interrupts depend on the model reaching the interrupt.
2. **Policy-based auto-approval with audit.** Below threshold, auto-execute and record
   everything (action, inputs, model confidence, policy version) with
   `reviewer: "policy"`. Post-hoc sampling review (humans review ~5% of auto-approved
   actions) gives statistical safety at ~5% of the human cost — how real fraud/payment
   systems already operate. Honest tradeoff stated for stakeholders: some bad decisions
   will execute, in exchange for near-zero latency and bounded human load. The residual
   risk is priced, not hidden.
3. **LLM-as-judge gate.** A second, cheaper model rejects obvious failures before any human
   sees them; humans review only judge-disagreement and low-confidence cases. Cuts human
   load 70-90%. The judge needs the same rubric text the human sees, calibrated against
   human decisions; the judge's verdict must never silently override the human's —
   disagreement logs are how you find rubric gaps.
4. **Human review as exception handling.** Full autonomy by default; interrupt only on the
   rare high-risk class.

The honest decision rule: HITL pays when human attention is cheaper than the expected cost
of autonomous errors — high irreversibility, ambiguous criteria, or compliance mandates. It
loses when the error cost is low, the criteria are automatable, or review capacity cannot
meet volume. Decision arithmetic per interrupt class: expected errors without gating
(rate x cost), human cost of gating (decisions x minutes x reviewer-hour cost + latency
impact), sandbox cost, auto-approval residual risk. The class goes to HITL where
irreversible-cost x error-rate dominates; sandbox where containment is feasible;
auto-approval where error-rate is low; nothing where the error cost is trivial.

**Shadow review** ends the "should this be HITL?" argument: humans review logged
auto-decisions without blocking, and you count what they would have caught. It takes about
a week to build and one eval period to run, and it replaces opinions with a number — "humans
would have caught 0.4% of these, median cost $12" ends the debate; "9%, median cost $400"
starts the approval-queue project with executive support attached. Most teams discover the
interrupt should fire on ~2% of tasks, not 20%.

**Treat every rejected interrupt as a labeled training example.** A rejection is the model
telling you, for free, where its judgment diverged from the human's — collect rejections in
a dataset and review them monthly; the pattern is always a prompt or policy fix.

Review-loop specifics (generate -> human review -> accept/revise -> regenerate, with a hard
cap): convergence engineering and its four metrics live in the metrics section of
references/audit-compliance.md; the mechanics (one interrupt per invocation, feedback via
conditional edge) are the loop rules of references/interrupt-patterns.md.
