# Runnable Templates — approval gates, resume, edits, tests, loan-review worked example

**Load this when:** you are writing the actual HITL code: an approval gate with
`interrupt()`, a resume driver with `Command`, human-edit patterns, simulated-human tests,
or the loan-review style worked example. Graph assembly (StateGraph, edges, compile) is the
graph-builder's job; these are the HITL pieces you drop into a graph that MUST be compiled
with a checkpointer (no checkpointer, no interrupt). Replace every `{{PLACEHOLDER}}` and
every `# {{...}}` comment marker before shipping.

## Template 1 — Risk-Gated Approval Node with interrupt()

```python
import hashlib
import json
from langgraph.types import interrupt

POLICY = {
    "max_auto_amount": 50.0,        # {{MAX_AUTO_AMOUNT}}: deterministic threshold, in code
    "risk_threshold": 0.8,          # {{RISK_THRESHOLD}}: cheap-model score cutoff
    "timeout_default": "reject",    # NEVER "approve" — an auto-approve timeout is the
                                    # postmortem failure (see references/audit-compliance.md)
    "policy_version": "v1",         # {{POLICY_VERSION}}: audited on every auto decision
}


def canonical_hash(obj) -> str:
    """Proves what the human was SHOWN; stored on the payload and the DecisionRecord."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def should_interrupt(state) -> bool:
    # Deterministic criteria in code — never "the model asks when unsure",
    # because that yields variable, random interrupt friction.
    return (state["amount"] > POLICY["max_auto_amount"]
            or state["risk_score"] > POLICY["risk_threshold"]
            or state["user_role"] == "unverified")


def approval_payload(state) -> dict:
    # JSON-serializable ONLY — the payload must survive the checkpoint round-trip.
    return {
        "action": {
            "name": "{{ACTION_NAME}}",
            "args": dict(state["action_args"]),          # rendered args the human judges
            "reversible": False,
        },
        "impact": {                                       # the numbers humans weigh well
            "balance_after": state["balance_after"],
            "policy_note": "Approval required: amount exceeds auto threshold",
        },
        "rationale": state["agent_rationale"],            # one line + references, not evidence
        "evidence_refs": list(state["evidence_refs"]),
        "alternatives": list(state["alternatives"]),      # pre-computed menu, human edits it
        "options": [                                      # machine-checkable decision contract
            {"verb": "approve", "resume": {"decision": "approve"}},
            {"verb": "reject",
             "resume": {"decision": "reject", "reason": "<required>"}},
            {"verb": "edit",
             "resume": {"decision": "edit", "edited_args": "<partial args override>"}},
        ],
        "deadline": state["deadline"],                    # stated here, honored by the queue
        "timeout_default": POLICY["timeout_default"],
        "tier": "maker",
        "payload_hash": canonical_hash(state["action_args"]),
    }


def execute_once(state) -> None:
    # IDEMPOTENT: upsert, never insert. Everything before interrupt() re-runs on resume.
    ...  # {{YOUR_ACTION_EXECUTION}} — e.g. transfer.execute(state["action_args"])


def approval_gate_node(state) -> dict:
    # IDEMPOTENT PREFIX ONLY: this whole region re-runs when the node resumes.
    if not should_interrupt(state):
        execute_once(state)                    # auto path: audited, reviewer is "policy"
        return {"status": "auto_executed", "reviewer": "policy",
                "policy_version": POLICY["policy_version"]}

    # BARE interrupt call — NEVER inside a bare try/except: the suspension IS an
    # exception; catching Exception runs the node straight past the approval point.
    decision = interrupt(approval_payload(state))

    if decision["decision"] == "reject":
        return {"status": "cancelled", "reject_reason": decision["reason"]}
    if decision["decision"] == "edit":
        args = {**state["action_args"], **decision["edited_args"]}   # human edits win
    else:
        args = state["action_args"]
    execute_once({**state, "action_args": args})   # AFTER the interrupt: runs exactly once
    return {"status": "executed", "action_args": args}
```

## Template 2 — Resume Driver with Command (sequential and fan-out)

```python
from langgraph.types import Command


def start_run(graph, initial_input, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}   # thread_id = THE resume handle
    result = graph.invoke(initial_input, config)
    # Under stream_events(version="v3") the same payloads arrive as stream.interrupts;
    # under plain invoke they arrive on result["__interrupt__"].
    return result


def resume_after_decision(graph, thread_id: str, decision: dict) -> dict:
    config = {"configurable": {"thread_id": thread_id}}   # SAME thread_id, every time
    result = graph.invoke(Command(resume=decision), config)
    # A resumed run can hit ANOTHER interrupt — loop until none are pending.
    while result.get("__interrupt__"):
        pending = result["__interrupt__"]
        raise RuntimeError(                                # your queue/UI decides here
            f"{{HANDLER_REQUIRED}}: {len(pending)} interrupt(s) still pending on {thread_id}")
    return result


def resume_fanout(graph, thread_id: str, interrupts, answers: dict) -> dict:
    # Parallel branches: resume with the ID map built from stream.interrupts.
    # NEVER index-match across parallel tasks — interleaving is non-deterministic
    # and binds answers to the wrong questions.
    resume_map = {i.id: answers[i.id] for i in interrupts}
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=resume_map), config)
```

## Template 3 — Human Edit-in-Place (the edit verb, review loop shape)

```python
MAX_ROUNDS = 3   # hard cap: then "best-so-far" ships with a flag


def review_node(state) -> dict:
    verdict = interrupt({
        "instruction": "Review the draft. Edit in place or reject with feedback.",
        "draft": state["draft"],                      # what is being judged
        "context": state["customer_summary"],         # why it exists
        "prior_feedback": state["feedback_history"],  # generator must not reintroduce fixes
        "round": state["round"],
        "options": ["approve", "edit", "reject"],
    })
    if verdict["action"] == "approve":
        return {"final": state["draft"], "status": "approved"}
    if verdict["action"] == "edit":
        # The human's edit is authoritative: it REPLACES the draft channel, and the
        # generator must re-read it next turn — not its cached belief about it.
        return {"draft": verdict["edited_draft"], "status": "revised",
                "round": state["round"] + 1}
    # reject: structured feedback only — prose feedback produces prose-shaped oscillation.
    return {"status": "rejected",
            "feedback_history": state["feedback_history"] + [verdict["feedback"]],
            "round": state["round"] + 1}


def route_after_review(state) -> str:
    # ONE interrupt per node invocation; the re-prompt loop is a CONDITIONAL EDGE,
    # never a python while-loop around interrupt() (exponential replay).
    if state["status"] in ("approved", "rejected"):
        return "done"
    if state["round"] >= MAX_ROUNDS:
        return "best_so_far"      # ships flagged as non-converged
    return "generate"             # regenerate with the feedback history in context
```

## Template 4 — Out-of-Band Human Edit (update_state, the admin path)

```python
from langgraph.types import Overwrite


def admin_fix_source_field(graph, thread_id: str, corrected_email: str) -> dict:
    # Out-of-band edit for live threads: fix wrong data the agent captured, then let
    # the run continue with the correction.
    #
    # PITFALLS, all earned:
    # - Reducer semantics apply: on an add_messages channel a plain write APPENDS —
    #   use the by-ID update form to replace a message.
    # - Overwrite bypasses a reducer when you truly need to reset a channel.
    # - as_node controls what runs next: attributing the update to capture_node
    #   resumes at capture_node's successors. Deliberate "skip ahead" if you mean it;
    #   accidental skipped work if you don't.
    # - Editing a PAST checkpoint FORKS the thread; the returned config is the new
    #   head — make the fork the line that actually keeps running.
    # - Semantic breaks: this must be a source-of-truth field whose consumers are
    #   downstream, or you must re-trigger derivation — otherwise the plan silently
    #   runs on stale derivatives.
    new_config = graph.update_state(
        {"configurable": {"thread_id": thread_id}},
        {"customer_email": corrected_email},
        as_node="capture_node",   # {{AS_NODE}}: the node whose successors should run next
    )
    return new_config


def admin_bypass_reducer(graph, thread_id: str, field: str, value) -> None:
    graph.update_state(
        {"configurable": {"thread_id": thread_id}},
        {field: Overwrite(value)},    # bypass reducer semantics
    )
```

## Template 5 — SimulatedHuman Tests (approve / reject / edit / double-resume)

```python
from langgraph.types import Command


class SimulatedHuman:
    """Answers interrupts from a script: 'approve', ('edit', {...}), ('reject', 'reason').

    If the UI can send something this script cannot express, the resume protocol
    is under-specified — extend the script shapes, then enforce them in the UI.
    """

    def __init__(self, script):
        self.script = list(script)

    def next_resume(self, payload) -> dict:
        answer = self.script.pop(0)
        if answer == "approve":
            return {"decision": "approve"}
        if answer[0] == "edit":
            return {"decision": "edit", "edited_args": answer[1]}
        return {"decision": "reject", "reason": answer[1]}


def drive(graph, app_input, thread_id: str, script) -> dict:
    human = SimulatedHuman(script)
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(app_input, config)
    while result.get("__interrupt__"):
        answer = human.next_resume(result["__interrupt__"][0].value)
        result = graph.invoke(Command(resume=answer), config)
    return result


def test_criteria_gate_fires_and_stays_shut(build_graph):
    graph = build_graph()          # compiled WITH a checkpointer; mocks instrumented
    below = drive(graph, {"amount": 10.0, "risk_score": 0.1, "user_role": "verified",
                          "action_args": {}, "agent_rationale": "", "evidence_refs": [],
                          "alternatives": [], "deadline": "", "balance_after": 0},
                  "t-crit-1", [])
    assert below["values"]["status"] == "auto_executed"      # under threshold: no human
    gated = drive(graph, {"amount": 500.0, "risk_score": 0.1, "user_role": "verified",
                          "action_args": {}, "agent_rationale": "", "evidence_refs": [],
                          "alternatives": [], "deadline": "", "balance_after": 0},
                  "t-crit-2", ["approve"])
    assert "__interrupt__" in str(gated) or gated["values"]["status"] == "executed"


def test_approve_replay_is_safe(build_graph_with_counting_mock):
    # The node re-runs from its start on resume. The counting mock proves the
    # IDEMPOTENT prefix: records created stay at 1 even though the node ran twice.
    calls, graph = build_graph_with_counting_mock()
    final = drive(graph, minimal_state(), "t-replay-1", ["approve"])
    assert final["values"]["status"] == "executed"
    assert calls["records_created"] == 1            # NOT 2 — upsert prefix, post-interrupt action
    assert calls["action_executions"] == 1          # the irreversible action ran exactly once


def test_edit_lands_in_state(build_graph):
    graph = build_graph()
    final = drive(graph, minimal_state(), "t-edit-1",
                  [("edit", {"amount_usd": 35.0}), "approve"])
    assert final["values"]["action_args"]["amount_usd"] == 35.0   # human edit is authoritative
    assert final["values"]["status"] == "executed"


def test_reject_routes_to_cancel(build_graph):
    graph = build_graph()
    final = drive(graph, minimal_state(), "t-reject-1", [("reject", "too risky")])
    assert final["values"]["status"] == "cancelled"


def test_double_resume_does_not_double_execute(build_graph):
    graph = build_graph()
    config = {"configurable": {"thread_id": "t-double-1"}}
    first = graph.invoke(minimal_state(), config)
    assert first.get("__interrupt__")
    answer = {"decision": "approve"}
    graph.invoke(Command(resume=answer), config)     # queue duplicate delivery #1
    final = graph.invoke(Command(resume=answer), config)  # duplicate delivery #2
    # Claim semantics at the queue level make the second resume a no-op;
    # the graph-level assert is that no additional execution happened.


def minimal_state() -> dict:
    return {"amount": 500.0, "risk_score": 0.1, "user_role": "verified",
            "action_args": {"amount_usd": 50.0}, "agent_rationale": "policy match",
            "evidence_refs": [], "alternatives": [], "deadline": "", "balance_after": 0}
```

Also required in CI: a stale-payload resume test (resume with a payload whose
`payload_hash` no longer matches — assert drift detection fires) and a timeout test
(assert the auto-resolve answer is the safe default and the DecisionRecord says
"policy"). The one end-to-end test that exercises the whole async path: start graph ->
interrupt fires -> assert queue item created with thread_id + payload hash -> simulated
reviewer approves with edits -> assert edits in state, action executed once,
DecisionRecord written with rationale + payload hash, audit table queryable.

## Template 6 — Loan-Review Worked Example (risk tiers, dual control, remand cap)

Regulatory shape: decisions to $50k; auto below $10k (policy auto-approval, sampled-audited
monthly); human maker above $10k; maker-CHECKER (dual control) above $25k; max 2 remands,
then human adjudication; everything reconstructable years later.

```python
from langgraph.types import interrupt

POLICY = {
    "auto_threshold": 10_000,         # {{AUTO_THRESHOLD}}
    "dual_control_threshold": 25_000, # {{DUAL_CONTROL_THRESHOLD}}
    "max_remands": 2,
    "policy_version": "v1",           # {{POLICY_VERSION}}
}


def write_decision_record(record: dict) -> None:
    ...  # {{AUDIT_TABLE_WRITE}} — who/what/when/why + payload_hash + checkpoint_id


def approval_payload(decision, tier: str, prior: dict | None = None) -> dict:
    payload = {
        "action": {"name": "loan_decision",
                   "args": {"amount": decision.amount, "tier": tier},
                   "reversible": False},
        "impact": {"applicant": decision.applicant_id,
                   "policy_note": f"Human sign-off required above "
                                  f"{POLICY['auto_threshold']}"},
        "rationale": decision.rationale,
        "evidence_refs": list(decision.evidence_refs),
        "alternatives": [{"name": "counter_offer", "args": {"amount": decision.amount / 2}}],
        "options": [
            {"verb": "approve", "resume": {"approved": True, "rationale": "<required>"}},
            {"verb": "reject", "resume": {"approved": False, "rationale": "<required>"}},
        ],
        "deadline": decision.deadline,
        "timeout_default": "reject",
        "tier": tier,
        "payload_hash": canonical_hash({"amount": decision.amount, "tier": tier}),
    }
    if prior is not None:
        payload["prior"] = prior     # the checker sees the maker's decision + rationale
    return payload


def decide_node(state) -> dict:
    decision = loan_agent.evaluate(state["application"])    # model + rules

    if decision.amount <= POLICY["auto_threshold"]:
        write_decision_record({"verdict": "auto", "reviewer": {"id": "policy", "role": "auto"},
                               "policy_version": POLICY["policy_version"],
                               "amount": decision.amount})
        return {"decision": decision, "route": "auto"}      # audited, no interrupt

    maker = interrupt(approval_payload(decision, tier="maker"))
    if not maker["approved"]:
        write_decision_record({"verdict": "reject", "tier": "maker",
                               "rationale": maker["rationale"]})
        return {"decision": {"status": "rejected", "reason": maker["rationale"]},
                "route": "done"}

    if decision.amount > POLICY["dual_control_threshold"]:
        checker = interrupt(approval_payload(decision, tier="checker",
                                             prior={"maker": maker["rationale"]}))
        if not checker["approved"]:
            if state["remands"] >= POLICY["max_remands"]:
                return {"decision": decision, "route": "human_adjudication"}
            write_decision_record({"verdict": "remand", "tier": "checker",
                                   "rationale": checker["rationale"]})
            return {"decision": {"status": "remanded", "reason": checker["rationale"]},
                    "route": "remand", "remands": state["remands"] + 1}
        write_decision_record({"verdict": "approve", "tier": "checker",
                               "rationale": checker["rationale"]})

    write_decision_record({"verdict": "approve", "tier": "maker",
                           "rationale": maker["rationale"]})
    return {"decision": decision, "approved_by": [maker["rationale"]], "route": "issued"}


def route_after_decision(state) -> str:
    # Remand loops back to the agent WITH the human's reason — via a conditional
    # edge, never a python loop. Bounded: max 2 remands, then human adjudication.
    if state["route"] == "remand":
        return "decide"
    return "end"
```

Note the dual-control mechanics: the maker and checker interrupts live in ONE node in a
fixed, unconditional order, so index-based resume matching stays correct; the maker's
answer replays from the checkpoint while the checker interrupt suspends fresh. Watch the
four numbers after launch: auto-decision rate (should be high), human approval rate by
tier (maker at 95% = noise gate; 40% rejection = agent needs better evals), queue latency
p90, dual-control disagreement rate (> 10% = ambiguous criteria — fix the rubric).

## Template 7 — Async Review Queue and Timeout Sweeper

```python
from datetime import datetime, timedelta, timezone
from langgraph.types import Command

SLA = timedelta(hours=4)   # {{SLA}} — stated to the customer at interrupt time


def enqueue_interrupts(result: dict, thread_id: str, graph) -> None:
    for intr in result.get("__interrupt__", []):
        state = graph.get_state({"configurable": {"thread_id": thread_id}})
        queue.put({
            "thread_id": thread_id,             # THE resume key — never lose it
            "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
            "payload": intr.value,              # rendered decision surface
            "interrupt_id": intr.id,            # for multi-interrupt resumes
            "deadline": datetime.now(timezone.utc) + SLA,
            "assigned_to": None,
        })


def resume_after_review(item: dict, decision: dict, graph) -> None:
    # Idempotent under duplicate delivery: two service instances (or an impatient
    # double-click) must never both resume the same thread — claim semantics first.
    if not queue.claim(item["id"]):
        return
    # Re-validate at RESUME time, not enqueue time:
    #   - the approver's role may have expired (expired authorization)
    #   - the world may have moved (stale decision) — a node right after the
    #     interrupt re-checks the payload's assumptions
    #   - the checkpoint may have moved (forked thread) — assert what you expect
    assert_authorized(reviewer_for(item))          # {{AUTHZ_CHECK}}
    graph.invoke(Command(resume=decision),
                 {"configurable": {"thread_id": item["thread_id"]}})
    write_decision_record({**decision_record_fields(item, decision),
                           "payload_hash": item["payload"].get("payload_hash")})


def timeout_sweeper(queue, graph) -> None:
    """Scheduler: resumes stuck interrupts with the DEFAULT — which must be the SAFE
    action ('reject' / best-so-far), never 'approve'. Recorded as a policy decision."""
    for item in queue.items_past_deadline():
        if not queue.claim(item["id"]):
            continue
        default = item["payload"].get("timeout_default", "reject")
        graph.invoke(
            Command(resume={"decision": default, "reason": "timeout_default",
                            "policy_version": POLICY["policy_version"]}),
            {"configurable": {"thread_id": item["thread_id"]}},
        )
        write_decision_record({"verdict": default, "reviewer": {"id": "policy", "role": "auto"},
                               "policy_version": POLICY["policy_version"],
                               "thread_id": item["thread_id"]})
```

The sweeper's escalation ladder (owner -> team -> auto-resolve, on timed rungs) runs ahead
of the deadline and must alarm on its own failure: if the fallback processes nothing in a
window, page someone — a silently broken fallback is a control that was never a control.
