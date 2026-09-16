---
name: hitl-builder
description: >-
  Adds human approval, review, and edit points to LangGraph agents: risk-based interrupt
  placement, interrupt() calls with reviewer-ready payloads, Command(resume=...) resume
  sequences, human state edits mid-run, async review queues for background agents, audit
  trails of human decisions, and HITL test flows including a loan-review style worked
  example. Stage: BUILD.
  Trigger: "add human approval to my agent", "where should the agent ask a human",
  "implement interrupt() in LangGraph", "approval gate for dangerous tool calls",
  "resume a paused graph after human review", "maker-checker dual control",
  "audit trail for human decisions", "review queue for background agents",
  "interrupt payload design", "test approve reject edit flows".
  Do NOT use for: building the surrounding graph code (langgraph-builder), choosing
  overall architecture or topology (agent-architecture-advisor), building eval suites
  (agent-eval-builder), or retries and reliability hardening (agent-reliability-hardener).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# hitl-builder

## Overview

This skill adds the human control surface to LangGraph agents: where humans approve, edit,
or review before the agent acts, how `interrupt()` and resume work, how decisions get
audited, and how the whole flow is tested. Stage: BUILD (applied while wiring graph nodes;
assumes a graph design exists or is being written alongside). The core economics: HITL
converts errors from "shipped" to "blocked" only where a human has an information advantage,
so interrupts must be few, well-placed, and decided from rich payloads — because fatigued
humans click through, and click-through approval has the cost of HITL with the safety of
autonomy.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/interrupt-patterns.md | Choosing static vs dynamic pauses, picking a pattern from the 16-pattern catalog, writing the interrupt payload, or resuming (incl. parallel fan-out) |
| references/async-hitl.md | The reviewer is hours/days away: review queues, timeout defaults, escalation, multi-agent gating, approval UX, or deciding whether HITL is worth it at all |
| references/audit-compliance.md | Regulated domain or "we need an audit trail": DecisionRecord schema, compliance mapping, HITL metrics and dashboards |
| references/templates.md | Writing the actual code: approval gate, resume caller, human-edit pattern, loan-review worked example, simulated-human tests |

## Execution Checklist

- [ ] 1. List irreversible/expensive/uncertain action classes; place interrupt points BEFORE building the happy path.
- [ ] 2. Pick a catalog pattern per class (approval / edit / review / collaboration); justify against alternatives.
- [ ] 3. Implement `interrupt()` nodes obeying the 5 mechanics rules; payload meeting the full spec.
- [ ] 4. Implement resume: `Command(resume=...)` on the same thread_id; approve/reject/edit all land in state.
- [ ] 5. Write a DecisionRecord for every human (and auto) decision; require rationale.
- [ ] 6. If the agent runs in background: wire the async review queue with thread_id, deadline, safe default.
- [ ] 7. Test approve / reject / edit / timeout / double-resume paths with a simulated human.
- [ ] 8. Run the 15-point pre-launch HITL review; re-run it whenever an interrupt class is added.

## Step-by-Step Workflow

### Step 1 — Decide what needs human approval; place interrupts BEFORE the happy path [FREEFORM]

Score every action class the agent can take against the error-cost ledger:

| Error class without HITL | Typical cost | One approval would catch it? |
|---|---|---|
| Auto-refund at scale (prompt-injected "refund everyone") | Refunds x users + churn | Yes — threshold gate |
| Auto-publish hallucinated price/content | Trust + legal exposure | Yes — content diff review |
| Auto-delete in migration ("archive" misread as "delete") | Days of recovery | Yes — plan review |
| Auto-answer wrong policy detail | Low, per-query | Sometimes only |

Rules:

- Design interrupt points before the happy path. Retrofitting approval into a built flow
  means re-plumbing state, resuming, and UI — roughly 3x the cost of designing it in,
  because every node boundary and state channel downstream of the interrupt is shaped by it.
- Gate where irreversibility begins, not at agent boundaries. Only the send/publish/commit
  is usually irreversible; everything before it is reversible internal state.
- HITL pays only where the human's information advantage overlaps the error class. If human
  catch-rate on the class is ~10%, spend on evals instead.
- Prefer alternatives in this order before adding any human step: (1) sandboxes (budget
  caps, dry-run modes, allow-listed arguments), (2) policy-based auto-approval with sampled
  post-hoc audit, (3) LLM-as-judge pre-filter, (4) human review as rare exception. Because
  each of these preserves latency and human attention for the classes that truly need it.
- Write approval criteria as deterministic code (amount thresholds, risk scores, roles) —
  never "the model will ask when unsure", because that yields variable, random friction
  (observed: ~40 interrupts per task).
- Do the throughput math before committing: if fraction P of tasks interrupt with human
  latency L, median latency goes from seconds to L and throughput is capped at
  reviewers x decisions/hour. Most teams find the gate belongs on ~2% of tasks, not 20%.

Verification: a written table of interrupt classes, each with a coded criterion, expected
fire rate, timeout default, and the P x L figure; a stakeholder has seen the ledger.

### Step 2 — Choose the pattern for each interrupt class [GUIDED]

Every human decision is one of three verbs — approve, reject, edit — composed into patterns.
Pick from the catalog in references/interrupt-patterns.md; the core four:

| Pattern family | Use when | Caution |
|---|---|---|
| Approval gate (approve/reject/edit on one action) | Single irreversible action | Edit path must apply cleanly to state |
| Plan approval (approve-once) | Risk concentrated in the plan, not each step | Flag irreversible steps; log plan-vs-execution divergence |
| Review loop (generate, review, revise, cap) | Quality checkable by humans but not automatable | Median rounds > 2 means feedback is broken |
| Collaboration (edit-in-place, agent continues) | Human refines generated content | Agent continues from edited state; preserve non-draft context |

Composition rules:

- Always offer the edit verb, not just approve/reject. Because without edit, a fixable
  proposal ($50 refund that should be $35) forces reject + full restart, wasting the
  round-trip; with edit the resume value carries the correction and the run continues.
- Dual control (maker-checker) above a severity tier; the checker sees the maker's decision
  and rationale. Two sequential interrupts = two latencies: batch when volume is high.
- Interrupt-in-tool for dangerous tools (send_email, execute_query): the gate travels with
  the tool across every graph that binds it. Node-restart semantics apply to the agent step.
- High-volume homogeneous approvals: batch review (queue-based, not ping-based) — one
  5-minute session of 20 items beats 20 context-switches. Batch only genuinely homogeneous
  items, because "approve all" on mixed items is click-through by another name.

Verification: each interrupt class names one catalog pattern plus its honest tradeoff, and
a one-line reason the cheaper alternatives (Step 1) were rejected.

### Step 3 — Implement interrupt() with a spec-compliant payload [EXACT]

Mechanics that dictate every rule: `interrupt(payload)` raises a runtime-caught exception;
the runtime checkpoints state (a checkpointer is MANDATORY — no checkpointer, no interrupt),
surfaces the payload via `stream.interrupts` under `stream_events(version="v3")` or
`result["__interrupt__"]` under plain invoke, then waits indefinitely at zero compute.

The five rules — each exists because the node RESTARTS FROM ITS BEGINNING on resume:

1. Never wrap `interrupt()` in a bare try/except. The suspension IS an exception; catching
   `Exception` swallows it and the node runs past the approval point. Catch specific types.
2. No conditional skips or reordering of interrupt calls across executions. Resume values
   match by execution-order index; reordered interrupts bind answers to wrong questions.
3. No loops around interrupts. `while True: interrupt(...)` replays all prior iterations on
   every resume (exponential re-execution + duplicate side effects). Sanctioned validation
   pattern: exactly ONE interrupt per node invocation; store the re-prompt question in
   state; loop via conditional edge back to the node.
4. JSON-serializable payloads only — the payload must survive the checkpoint round-trip.
5. Side effects before the interrupt must be idempotent (upsert, never insert); move
   new-record creation and notifications after the interrupt or into a downstream node.

The payload is the entire human decision surface. Every payload carries: action (name +
rendered args + reversibility), impact (balance after, blast radius, policy note), rationale
(one line + evidence REFERENCES, not the evidence), alternatives (pre-computed menu),
options (the machine-checkable decision contract), deadline, timeout_default ("reject" —
never "approve"), tier (maker/checker), payload_hash. Full spec: references/interrupt-patterns.md.
Show the diff — email text, migration preview, amount + recipient + balance-after. A payload
saying "approve action?" with a truncated ID is a liability waiver, not an approval; opaque
payloads produce ~100% click-through and zero review.

Verification: sit next to one real reviewer with one real payload and watch them decide —
a good payload is a 15-second decision; observed friction IS the anti-pattern, localized.

### Step 4 — Implement resume and mid-run human edits [EXACT]

Resume = invoke the graph again on the SAME thread_id with `Command(resume=value)`; value
becomes `interrupt()`'s return value inside the node. The three verbs wire like this:

- approve: node proceeds unchanged (conditional edge or in-node branch).
- reject: node returns a rejected status / conditional edge routes to a cancel node.
- edit: the resume value carries the edits; the node applies them to state and continues
  from the edited content (the human's edit is authoritative).

Parallel fan-out: N branches each interrupting surface N interrupts with IDs — resume with
`Command(resume={interrupt.id: answer ...})` built from `stream.interrupts`. Index matching
across parallel tasks interleaves non-deterministically and mis-binds answers.

Out-of-band edits (admin fixes a live thread via `update_state`) follow four pitfalls:

- Reducer semantics apply: on an `add_messages` channel writes APPEND — use the by-ID form
  to replace a message; use `Overwrite` to bypass a reducer.
- `as_node` controls what runs next: an update attributed to node B resumes at B's
  successors — deliberate "skip ahead", or accidental skipped work.
- Editing a past checkpoint FORKS the thread; the returned config is the new head. Make the
  fork the line that keeps running.
- Semantic breaks: fixing a source field silently strands stale derivatives — re-trigger
  derivation, confine edits to fields whose consumers are downstream, and serialize edits
  (one review owner per high-stakes thread), because two humans editing while one decides
  makes resume answers land on a state version the human never saw.

Verification: replay test with counting mocks — pre-interrupt side effects execute once per
logical run; edited values land in state; the action executes exactly once; resume twice
with the same answer does not double-execute.

### Step 5 — Wire the audit trail of human decisions [GUIDED]

Every decision must answer: Who (reviewer id + role), What (payload_hash — provably what
was SHOWN), When (requested_at + decided_at — latency is auditable), Why (verdict + required
rationale + edit diff), against what (thread_id + checkpoint_id). Write the DecisionRecord
to a dedicated audit table at resume time AND keep a copy in a state channel — the table
for auditors' cross-thread queries, the state for per-thread replay. Require the rationale
field in the decision form: it is the one thing the checkpoint cannot supply, and it
measurably changes decision quality.

- Auto-decisions are policy decisions: record `reviewer: "policy"`, `policy_version: ...` —
  NEVER a human-shaped approval, because "auto" as a reviewer is an audit-trail forgery
  (the postmortem in references/audit-compliance.md).
- Full schema, compliance mapping table, and the five launch metrics are in
  references/audit-compliance.md.

Verification: an auditor (not an engineer) can answer who/what/when/why for a sample
decision three months later, without your help.

### Step 6 — Add async HITL for background agents [GUIDED]

Async HITL = the run pauses (checkpointed, zero cost), the payload lands in a DB-backed
review queue, a reviewer claims and decides, and the service resumes the stored thread_id.
The pieces you must not lose: thread_id (THE resume key), the payload, and the resume
contract. Queue row: `{thread_id, checkpoint_id, payload, interrupt_id, deadline,
assigned_to}`. You need a queue, a UI, and the thread ID — no long-running workers,
websockets, or heartbeats, because the checkpoint IS the process.

Guard the four slow-human failure modes (guards 2 and 4 are non-negotiable for
irreversible actions):

1. Context rot (system changed while paused) — pin behavior versions; never rename a
   node with a pending interrupt.
2. Stale decisions (world moved since drafting) — re-validate the payload's assumptions
   in a node immediately after the interrupt.
3. Lost reviewer context — the queue UI re-renders the full payload from the checkpoint.
4. Expired authorization — re-check the approver's role at RESUME time, not enqueue time.

Timeout policy per interrupt class, stated in the payload: deadline + default, where the
default is the SAFE action (reject / best-so-far). Auto-expiring to "approve" is delayed
execution, not approval — that exact misconfiguration executed a $9,000 fraudulent transfer
in the chapter's postmortem. Escalation ladder (owner -> team -> auto-resolve) with a
heartbeat alarm on the ladder itself. Multi-agent systems: gate at irreversibility, one
uniform payload schema platform-wide, batch parallel approvals into one human decision.

Verification: kill the server mid-pause; restart; resume — the run completes. Simulate
duplicate queue delivery; resume is idempotent (claim semantics at the queue level).

### Step 7 — Test approve / reject / edit / timeout paths [EXACT]

Four testable behaviors beyond normal agent tests: the pause fires when the criterion says
to; the resume does the right thing per verb; the replay is safe; interrupt ordering is
stable across simulated resumes. Use a SimulatedHuman that answers from a script —
`["approve", ("edit", {...}), ("reject", "too risky")]` — because if the UI can send
something your tests cannot express, the resume protocol is under-specified.

Required cases per interrupt node:

- Criteria tests: above/below threshold fires / does not fire.
- Verb tests: approve executes once; reject routes to cancel; edit lands edits in state.
- Replay: assert pre-interrupt behavior identical on resume (counting mocks).
- Double-resume with the same answer: no double execution.
- Stale payload resume: drift detection fires.
- Timeout: default is the safe action, and the DecisionRecord says "policy", not a human.
- End-to-end: interrupt -> queue item with thread_id + payload_hash -> approve-with-edit ->
  edits in state, action once, DecisionRecord written, audit table queryable.

Runnable templates: references/templates.md (Template 5).

Verification: the end-to-end test passes in CI including double-resume and stale-payload
cases.

### Step 8 — Run the pre-launch HITL review [EXACT]

Run all 15 checks; re-run whenever an interrupt class is added:

| # | Check |
|---|-------|
| 1 | Every interrupt class: coded criterion + stated timeout default = safe action |
| 2 | Node rules verified: no bare try/except, stable order, idempotent prefix, JSON payloads |
| 3 | Every payload meets the spec (action/impact/rationale/alternatives/contract/deadline/hash) |
| 4 | Queue stores thread_id + interrupt_id + payload hash; resume idempotent under duplicate delivery |
| 5 | Resume re-validates authorization + payload freshness |
| 6 | Escalation ladders exist and alarm on their own failure |
| 7 | Audit records answer who/what/when/why; "auto" never appears as a human reviewer |
| 8 | Multi-agent gating at irreversibility with one uniform payload schema |
| 9 | Frontend loops on stream.interrupted, persists thread IDs across sessions, re-renders stale payloads |
| 10 | The five metrics charted before launch, alerts defined |
| 11 | End-to-end CI test green incl. double-resume and stale-payload |
| 12 | Rollback lever tested: interrupts disableable via config = full autonomy + full audit |
| 13 | Shadow-review measurement scheduled for month one |
| 14 | Throughput math written: P x L per class, review-capacity ceiling, overflow plan |
| 15 | Reviewer fatigue designed against: batching, prioritization, payload quality |

Verification: every row checked with evidence; the first requested human decision in
production should be boring — that is the pass criterion.

## Examples

1. Simple — refund gate: Input: refund tool, auto-approve threshold $50. Output: node calls
   `should_interrupt(state)` (amount > 50 or risk_score > cutoff); interrupt payload shows
   order, amount, reason, balance impact; reviewer approves -> refund executes once and a
   DecisionRecord with rationale is written; below threshold it auto-executes with
   `reviewer: "policy"` + sampled monthly audit.
2. Typical — background loan review: Input: lending agent, decisions to $50k, policy =
   human sign-off above $10k, dual control above $25k. Output: auto path under $10k;
   $10k-$25k one maker interrupt; above $25k maker interrupt -> checker interrupt (sees
   maker's rationale) -> approve or remand (max 2 remands, then human adjudication); every
   decision reconstructable years later via payload hash + checkpoint ID.
3. Edge — parallel fan-out: Input: supervisor dispatches 3 workers; workers 1 and 3 both
   interrupt. Output: caller collects `stream.interrupts`, batches both payloads into ONE
   review session; reviewer edits worker 1's args and rejects worker 3; caller resumes with
   `Command(resume={id1: edited, id3: rejected})`; worker 2 was never blocked.

## Known Gotchas

1. **Human's answer lands on the wrong question.** → Cause: interrupt calls reordered or
   conditionally skipped between the original run and the resume; resume values match by
   execution-order index. → Response: fixed order, no conditional skips; for parallel
   interrupts always resume with the `{interrupt_id: value}` map.
2. **The node runs straight past the approval point.** → Cause: `interrupt()` wrapped in
   bare `try/except Exception`; the suspension is an exception and was swallowed. →
   Response: keep interrupt() bare; catch specific exception types only.
3. **Side effects execute twice (double inserts, double notifications).** → Cause: resume
   re-runs the node from its start, so pre-interrupt code runs at least twice. → Response:
   idempotent prefix (upsert); move record creation and notifications after the interrupt.
4. **Validation re-prompt gets exponentially slower.** → Cause: `while`-loop around
   `interrupt()`; each resume replays all prior iterations. → Response: one interrupt per
   invocation, re-prompt question in state, conditional-edge loop back to the node.
5. **An approval "executes itself" hours later.** → Cause: timeout default set to
   "approve" ("so urgent payments don't stall"); escalation fired without a human. →
   Response: default = reject / best-so-far, stated in the payload; auto-decisions recorded
   as policy decisions; escalation paths get heartbeats.
6. **Approval rate 98%+, zero real review.** → Cause: opaque payload (truncated IDs, raw
   state dumps) trains click-through; audit trail then falsely documents "reviewed". →
   Response: payload spec (rendered diff, impact numbers, rationale, alternatives);
   risk-gate down to the 2-5% of cases that carry risk; batch homogeneous items.
7. **A suspended run becomes unreachable garbage.** → Cause: the review queue never stored
   the thread_id (or lost it on restart). → Response: persist thread_id + interrupt_id +
   payload hash in the queue row at interrupt time; treat the thread ID as the resume key.
8. **The queued review resumes the wrong branch.** → Cause: someone forked the live thread
   for "what-if" exploration; the queue resumed the branch head. → Response: exploration on
   explicit copies (new thread IDs); the resume path asserts the checkpoint it expects.
9. **Approver was no longer authorized / the price moved.** → Cause: authorization and
   freshness checked at enqueue time, hours before resume. → Response: re-check role and
   re-validate payload assumptions in a node right after the interrupt, on every resume.
10. **Resume value shape differs between dev and prod.** → Cause: the resume contract was
    never enumerated ("ok sure thanks!" free text hits the node). → Response: machine-
    checkable decision contract in the payload options; SimulatedHuman tests pin the shapes.
