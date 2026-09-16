# The Supervisor Prompt, Complete — Plus the Annotated Handoff

**Load this when:** writing or adapting the supervisor routing prompt, wiring the done-tracker, or composing a handoff briefing.

## 1. The Complete Supervisor Prompt (copy, don't paraphrase)

```text
# ---- Supervisor system prompt ----
# Role: You are the coordinator of a report-production team. You do not do the
# work; you decide who does it next and when the work is finished.
#
# Workers (route to EXACTLY one name, or FINISH):
#   - researcher: gathers sources and facts. Use when facts are missing or
#     unverified. NOT for writing prose.
#   - writer: drafts and revises the report from verified facts. Use when the
#     report needs drafting or revision.
#   - reviewer: checks the draft against the requirements list. Use after
#     every draft. NOT before the first draft exists.
#
# Reply format: exactly one token from {researcher, writer, reviewer, FINISH}.
# FINISH only when: the draft exists AND reviewer returned "approved".
#
# Evidence you can see each round (assembled by the system, do not invent it):
#   goal: <task verbatim>
#   done: <completed subtasks list>
#   last: <previous worker + one-line result>
#
# ---- Supervisor context, assembled fresh each round ----
# goal: Write a 500-word market summary of the EV industry in Germany.
# done: ["sourced: EV sales data 2024-2025", "drafted: outline approved"]
# last: writer -> draft v1 complete (620 words)
```

Three details separate this from a naive supervisor prompt:

1. **Negative routing hints** ("NOT for writing prose") prevent the number-one misroute — the researcher being asked to write.
2. **A checkable FINISH precondition** ("reviewer returned approved") is a state field, not a vibe.
3. **A system-assembled evidence block** from the done-tracker, which the prompt forbids the model from inventing — a supervisor that hallucinates progress evidence re-dispatches endlessly.

The prompt is not the supervisor; the prompt plus the done-tracker in state is the supervisor.

## 2. The Operational Shape (what to assemble each round)

```text
# SYSTEM (routing contract):
# "You are the coordinator. Workers:
#   - researcher: gathers sources and facts (use for: unknown facts, open questions)
#   - writer: drafts the deliverable (use for: drafting, editing)
#   - reviewer: critiques against the requirements (use for: quality checks)
#  Reply with EXACTLY one of: researcher | writer | reviewer | FINISH.
#  FINISH only when the deliverable exists AND reviewer approved it."
#
# CONTEXT (assembled fresh each round — this is the part that matters):
# goal: <the original task, verbatim, always>
# done: <completed subtasks, from the done-tracker>
# remaining: <inferred remaining subtasks>
# last: <last worker + one-line result>
```

Two design notes that make this work:
- The worker registry replaces the workers' system prompts with one-line capability descriptions — the supervisor routes on roles, not implementation.
- The done-tracker (an append-only `done` list in state, written by workers, shown to the supervisor) is the difference between a supervisor that finishes in 3 rounds and one that re-dispatches forever.

## 3. Adaptation Guide

| Change | Adapt like this | Because |
|--------|-----------------|---------|
| Add a worker | One registry line: name + capability + "use when" + one negative hint ("NOT for X") | The supervisor routes on the registry text; stale text = wrong decomposition |
| Change a worker's capabilities | Regenerate the registry line as part of the worker's release process | Registry drift: capabilities changed but the registry text didn't |
| Tighten termination | Make the FINISH condition a state field ("reviewer approved" = a boolean in state) | Threshold ambiguity: a subjective FINISH condition routes forever |
| Cut cost | Cheaper supervisor model; done-tracker to cut rounds; supervisor synthesizes on FINISH | Routing is a cheap problem; rounds are the cost driver (N agents x M rounds) |
| Force progress | Add "supervisor must name what improved each time it re-dispatches" + a max-rounds cap | Round inflation: routes a worker to "improve" forever |
| Fix "done" without content | Workers return structured results into shared channels, not just a status word | Evidence starvation: workers return "done" so the supervisor can't judge completion |
| Add synthesis | Supervisor synthesizes on FINISH (one extra call) OR the final worker is the synthesis step (route to writer last) | Lost synthesis: all the pieces and no document |
| Reduce knowledge bottleneck | Keep evidence in state channels; give the supervisor references + conclusions plus a `read_detail(ref)` tool | Deep evidence flattened to summaries at each level loses the detail decisions need |

## 4. Loop Control, Ranked by Reliability

1. **Explicit FINISH in the routing contract** — the baseline.
2. **Done-tracker in state** — a list of completed subtasks; the supervisor sees it and must justify re-dispatch. The one that actually fixes the forgotten-work failure mode.
3. **Iteration caps** — max supervisor rounds; on breach, synthesize best-effort from whatever exists.

Implement all three before production. Loop-control failures look like model stupidity but are missing-evidence bugs.

## 5. The Annotated Handoff, Step by Step

The transfer of a conversation from support to billing, exposing every payload decision:

```text
 user: "I want a refund for order 88213"
    |
[support_agent] (has tools: order_lookup, transfer_to_billing)
    |  -> order_lookup("88213")            # gather before transferring
    |  <- "status: delivered, $42, 14 days ago"
    |  -> decides: refunds are billing's domain, user is eligible-looking
    |  -> calls transfer_to_billing(briefing=...)
    |
[state update]  (what the handoff tool writes)
    |  active_agent = "billing"
    |  messages += [ AIMessage(transfer tool call),           # the pair
    |                ToolMessage("Refund request: order 88213, $42,
    |                             delivered 14d ago. User cites wrong size.
    |                             Policy check needed: 30-day window.
    |                             Tone: user is calm but firm.") ]
    |
[billing_agent]  (reads: user msg + transfer pair + briefing)
    |  -> refund_policy_check("88213")      # has the context it needs
    |  <- "within window: yes"
    |  -> issues refund, replies to user directly
```

Four annotation notes:
1. **The sender gathered first** — the briefing contains the order facts, so billing doesn't re-fetch.
2. **The briefing is three parts** — task, facts, and context (policy question plus tone) — matching the handoff payload schema exactly.
3. **The receiver sees a complete conversation** (user message, transfer pair, briefing) with no dangling tool calls — avoiding the malformed-history failure.
4. **The final reply goes to the user from billing** — the last agent speaks to the user in one hop, no ping-pong back.

One transfer, zero re-asking, zero lost context — the whole value of the handoff pattern in four steps.

## 6. Handoff Payload Checklist

| Field | Content | Why |
|-------|---------|-----|
| Tool-call pair | AIMessage + matching ToolMessage (tool_call_id) | Malformed history otherwise — verified docs requirement |
| Active-agent marker | `active_agent: "sales"` in state | The routing signal the graph reads |
| Task status | One line: what was decided/done | Receiver doesn't redo work |
| Artifacts by reference | IDs/refs, not content | Content lives in DB/state; refs are cheap and durable |
| Constraints | Tone, deadline, legal limits the receiver must respect | The number one thing lost in naive handoffs |
| Open question | What the receiver must answer/produce | Makes the handoff a contract with a deliverable |

T28: handoff with an agenda beats handoff with a summary — send the task, the user's goal, and what the sender already ruled out. A bare conversation summary forces the receiver to re-derive context, and re-derivation is where multi-agent systems lose coherence.

## 7. The Handoff as a Structured State Handover

The OpenHands pattern generalizes: every agent-to-agent handoff carries a summary with
- the goal restated,
- completed work with verifiable outcomes,
- current state,
- blockers and risks,
- recommended next actions.

Two properties matter: it is **verifiable** (claims reference state the receiver can check) and **lossy-by-design** (deliberate compression, not accidental truncation). Use this shape whether the handoff is a ToolMessage, a state field, or an event payload.

## 8. Choosing the Handoff Style

| Style | Mechanism | Choose when |
|-------|-----------|-------------|
| Handoff-as-tool | The model calls a transfer tool that returns `Command(goto=..., graph=Command.PARENT, update=...)` | The transfer decision needs judgment; transfers can happen mid-turn |
| Deterministic handoff | The graph routes on `active_agent`; no tool, the model never decides | The transfer condition is a rule ("always hand off after collecting billing info") |
| Policy-in-description | The tool exists; its description encodes the policy ("transfer only when the user asks about X") | You want cheaper than policy-in-routing-call and more flexible than policy-in-code |
