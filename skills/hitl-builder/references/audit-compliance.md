# Audit Trails, Compliance Mapping, and HITL Metrics

**Load this when:** you operate in a regulated domain or anyone asks for "an audit trail of
human decisions"; you need the DecisionRecord schema, compliance evidence mapping, or the
metrics/dashboards that tell you the HITL system is healthy.

## What to Record — the Four Questions

Every human decision must answer: **Who** (reviewer identity, role), **What** (the exact
payload shown — provable via hash), **When** (interrupt time AND resume time — the decision
latency itself is auditable), **Why** (verdict: approve/edit/reject + free-text rationale +
any edits). Plus **against what**: thread ID + checkpoint ID of the reviewed state, plus a
hash of the payload so "what was shown" is provable.

The checkpoint system gives you most of this for free. The gap is **why** — the rationale —
which only the human can provide. Require it in the decision form: a required rationale
field changes decision quality measurably.

## The DecisionRecord Schema

```python
DecisionRecord = {
    "decision_id": "{{UUID}}",                 # correlatable everywhere
    "thread_id": "...", "checkpoint_id": "...", # the exact state decided against
    "payload_hash": sha256(canonical(payload)), # prove what was SHOWN
    "reviewer": {"id": "...", "role": "..."},   # who, and in what capacity
    "requested_at": "...", "decided_at": "...", # the decision latency itself is auditable
    "verdict": "approve|edit|reject",           # one of the three verbs
    "edits": {...},                             # the edit diff, if any
    "rationale": "...",                         # required free text
    "policy_version": "...",                    # which criteria permitted this decision
    "resume_checkpoint_id": "...",              # where the run continued (drift detection)
}
```

Implementation split: write DecisionRecords to a dedicated audit table at resume time
(cross-thread queries are trivial), while the thread keeps its own copy in a state channel
(per-thread replay shows decisions in context). **Both, not either** — the table for
auditors, the state for debuggability.

Auto-decisions are recorded as POLICY decisions: `reviewer: "policy"`,
`policy_version: ...` — never as human approvals. An approval record with a human-shaped
field filled by a machine is an audit-trail forgery.

## The Postmortem: The Approval That Approved Itself

A payment agent gated transfers over $1,000 with an interrupt. The queue's escalation rule:
"if unapproved after 4 hours, resume with the default." Someone set the default to
`{"action": "approve"}` "so legitimate urgent payments don't stall," intending to review
escalated items manually. Two weeks later a $9,000 transfer to a compromised account
executed at the 4-hour mark; the queue's manual-review step had silently broken (its DB
view errored) and nobody noticed — the system looked healthy: every transfer had an
approval record, every record had a reviewer ("auto"), every metric was green. The auditor
asked the four questions: who ("auto" — a reviewer that doesn't review), what (payload hash
— valid), when (decided exactly 4h later — the escalation fired), why (rationale: the
default string, not a human judgment). Conclusion: "a control that was never a control."
The regulatory finding was worse than the fraud loss.

Lessons, numbered:

1. **Timeout defaults must be the safe action.** "Auto-approve on expiry" is delayed
   execution with extra steps. If reject-on-expiry breaks a legitimate flow, the flow needs
   a faster human, not a looser default.
2. **"Auto" is not a reviewer.** Auto-decisions are recorded as policy decisions
   (`reviewer: "policy"`, `policy_version`), never as human approvals.
3. **Escalation paths are load-bearing and must alarm.** Every escalation path gets a
   heartbeat: if the fallback doesn't process anything in a window, page someone.
4. **The audit trail is for auditors.** It must answer the four questions without
   engineering assistance, three months later, in a format auditors recognize.

Post-incident changes (each one line of policy or code): default flipped to
reject-with-escalation; auto-decisions relabeled as policy decisions with a distinct record
type; a dead-letter alert on the review queue; and a CI test: "a decision record with
reviewer=auto must never carry a human rationale field."

## Compliance Realities

Auditors ask three questions:

1. Can you prove the human saw the same data the agent acted on? (payload hash +
   checkpoint ID)
2. Can you prove the approver was authorized at approval time? (capture role at resume)
3. Can you reproduce the decision? (thread + checkpoint + replay, minus side effects)

If your answer to any is "the logs probably have it," you don't have an audit trail — you
have logs. Compliance-grade extras: tamper-evidence via append-only storage or
hash-chaining; retention aligned with the regulator's policy, not engineering convenience
(two different lifecycles, two different stores); and capturing who saw what before
deciding — the payload_hash guards exactly this.

**WARNING — "audited" and "compliant" are different claims.** The mechanisms below give the
technical substrate (record structure, hashes, replay path). Whether the system meets a
specific regulation is a legal-and-controls determination involving the whole organization.
Do not let a technically-excellent audit trail be sold as regulatory certification by
itself; and do not let the absence of a certificate justify skipping the audit trail. The
mechanisms are necessary either way.

## Compliance Mapping

| Requirement (Typical) | The Mechanism | The Evidence |
|---|---|---|
| Human sign-off on class X decisions | Risk-gated interrupt with criteria in code | DecisionRecord with reviewer + role |
| Segregation of duties (no single approver above a tier) | Dual control (maker-checker) | Two DecisionRecords, maker != checker |
| Reproducible decisions | Checkpoint + payload hash | Thread ID + checkpoint ID + hash; replay |
| "Show what the human saw" | Payload hash + rendered payload archive | Hash match proves unaltered post-decision |
| Right to erasure (GDPR) | Tenant-first threads + retention pruning | Per-user deletion runbook, executed and logged |
| Retention schedules | Separate lifecycles: checkpoints vs audit records | Retention policy + enforced pruning |
| Change management for criteria | Policy versioning | `policy_version` on every auto-decision record |
| Incident reconstruction | Full audit chain | Decision -> rationale -> evidence refs -> thread replay |

Most compliance requirements are data-hygiene requirements in disguise. Build the audit
chain with the first interrupt and the compliance conversation becomes "here is the
evidence," not "here is a project plan."

## The Five Numbers Every HITL System Must Chart

| Metric | What It Tells You | Healthy Signal | The Alert |
|---|---|---|---|
| Interrupt rate (% of tasks) | Gating too much / too little? | Matches risk model's prediction | Drift > 2x predicted = criteria rot |
| Interrupt-to-resume latency (p50/p90) | Queue health, human capacity | p50 minutes, p90 < SLA | p90 > SLA = capacity problem |
| Approval rate by class | Is the gate real? | 60-95% (varies by class) | > 98% = click-through; < 50% = noise |
| Auto-decision sample error rate | Is the policy still right? | Below tolerance | Rising = tighten thresholds |
| Escalation path heartbeats | Are the fallbacks alive? | Each path processed N/hour | Zero = the postmortem failure, real-time |

The dashboard's second job — the queue view: reviewers need queue depth, age-of-oldest-item,
per-tier counts (maker vs checker), and per-item SLAs. Depth growing at constant arrival
rate means the review-capacity ceiling is hit; the answer is fewer interrupts or more
reviewers — never "faster clicking."

## Review-Loop Convergence Metrics

Four metrics tell you whether a generate -> review -> revise loop is actually working:

1. **Rounds-to-approval distribution** — median > 2 means the generator isn't using
   feedback or the criteria are ambiguous.
2. **Feedback-incorporation rate** — for each reviewer comment, does the next draft address
   it? Below 70% means the feedback isn't reaching the generator's context or isn't
   actionable.
3. **Reviewer agreement** — two reviewers on the same draft agreeing below 80%? Your rubric
   is the problem.
4. **Rejection reason distribution** — "wrong facts" vs "wrong tone" vs "missing section"
   tells you which stage to fix.

Run all four on 100 reviews and you'll know exactly where the loop is leaking. Convergence
fixes: structured feedback (score + required changes as a list, not prose), the same
reviewer across rounds (criteria drift between humans is the #1 convergence killer),
feedback history fed to the generator, and hard caps (3 rounds, then "best-so-far" ships
with a flag). Reviewer consistency beats reviewer expertise: two rotating reviewers with
slightly different standards oscillate forever — pin the reviewer to the thread, or give
both reviewers the same rubric text in the payload. The rubric is the spec; the human is
the executor. An LLM-as-judge inner loop (cheap judge scores against the rubric before the
human; low-confidence or below-threshold drafts go to the human) gets human eyes on 10-30%
of drafts; the judge's verdict must never silently override the human's.

## Loan-Review Worked Example: The Numbers That Matter

The lending system (risk-gate under $10k auto; maker above $10k; maker-checker above $25k;
max 2 remands) is only "done" when these four numbers are watched and acted on:

- **Auto-decision rate** — should be high; if low, thresholds are too conservative.
- **Human approval rate by tier** — maker approves 95%? The gate is noise. Rejects 40%?
  The agent needs better evals.
- **Queue latency p90** — the SLA the customers were promised.
- **Dual-control disagreement rate** — > 10% means the criteria are ambiguous; fix the
  rubric.

## The Instrumentation Checklist

Build the instrumentation with the first interrupt, not after the first audit:

- Every **interrupt** emits: thread ID, interrupt ID, payload hash, timestamp, and the
  criterion that fired it.
- Every **resume** emits: who, when, verdict, rationale.
- Every **auto-decision** emits: policy version + inputs.

With those three streams, every metric above is a SQL query away, the audit is free, and
the quarterly HITL review has data instead of anecdotes.
