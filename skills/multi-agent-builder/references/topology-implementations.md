# Topology Implementations — LangGraph Mechanics per Architecture

**Load this when:** you have chosen a topology and need its concrete implementation guidance — graph shape, LangGraph primitives, sharp edges, and when the topology disqualifies itself.

Runnable code for every topology lives in references/templates.md. This file is the mechanics and decision detail.

## How to Read Any Architecture

Three columns predict everything:
- **Authority** — who decides the next speaker. No authority means no termination; single authority means SPOF plus ping-pong.
- **Context** — what each agent sees. Full transcript means O(total steps) per step; isolated means re-derivation cost per call.
- **Canonical failure** — and note: every architecture's fix for its own failure is "add the missing column." Swarm adds authority (hop caps + completion owner); supervisor adds memory (done-tracker); handoff adds structure (payload schema); event-driven adds order (correlation IDs).

## 1. Network / Shared-Scratchpad

All agents write into one shared state — a `messages` channel with an `add_messages`-style reducer, or a structured scratchpad — plus a `next` field saying which agent speaks next. Coordination is protocol, not authority: a single conditional edge routes on `next`.

```
        +--------------------------------------------------+
        |             SHARED STATE (one thread)            |
        |  messages: [user, A1, A2, A1, ...]   next: "A2"  |
        +----------+----------------+----------------+-----+
                   |                |                |
               [Agent 1]        [Agent 2]        [Agent 3]
```

- Wins because: total transparency (every agent sees everything), simplicity (one state, no subgraphs), emergent back-and-forth (researcher/writer/critic loops).
- Fails because: context grows with every speaker (O(total steps) per step, multiplied by agent count); message collision — two agents writing `messages` in one super-step merge in task-completion order, which is non-deterministic; double answers because two agents can respond to the same message simultaneously (no mutual exclusion); isolation is a discipline, not a property.
- Use when: 2-4 agents, short tasks, total visibility matters more than cost (demos, creative loops, prototypes). Never for long-running, many-agent, or production cost-sensitive work — the shared transcript is an unbounded token sink.
- **Disqualified when:** the transcript must not be shared (privacy/tenancy), or the task runs long enough for context to blow up.

### Scratchpad Variant
Replace raw messages with a structured scratchpad: `{"facts": [...], "hypotheses": [...], "open_questions": [...], "next": ...}`. Agents write conclusions to typed fields, not prose to a transcript. Context stays small, writes merge by ID without races, and the transcript is reserved for user-facing text. This is the difference between a network that scales to 10 rounds and one that dies at 5. Cost: someone must design the schema up front, and agents must be prompted to write to it faithfully.

### The Four Consistency Rules
1. Append-only channels for anything multiple agents emit; merge by ID when order doesn't matter.
2. Single-writer channels for decisions — `next` is written only by the routing function, or only by the agent currently holding a token.
3. Never trust last-message-author for routing — same-super-step completion makes it ambiguous.
4. If two agents can read the same state and write overlapping channels simultaneously, you need a token-passing protocol (an explicit turn field where only the named agent may write) or you accept emergent behavior. Token-passing is what a supervisor automates.

**Pro tip:** network failures look like agent stupidity but are scheduling issues. "The researcher re-ran a search we already did" is usually two agents reading pre-write state in the same super-step. Instrument (log each agent's input-state hash and write time) before rewriting prompts. The fix is usually a token field or a serial edge, not a better prompt.

## 2. Supervisor

A dedicated supervisor node receives results from worker agents (nodes or subgraphs) and routes to the next worker via a conditional edge on a `next` field. Workers never talk to each other.

```
                    +------------+
                    | SUPERVISOR |  (routing prompt + optional synthesis)
                    +-----+------+
        +------------------+---+------------------+
        v                  v                      v
   [Researcher]         [Coder]               [Planner]
        |                  |                      |
        +---------------------+----------------------+
                  results back to supervisor
```

- The supervisor's job is one decision: who is next, or are we done. Its prompt needs the worker registry (names + one-line capabilities + when to use each), the routing contract ("reply with exactly one worker name, or FINISH"), and evidence of progress (see references/supervisor-prompt.md).
- Loop control, in order of reliability: (1) explicit FINISH in the routing contract; (2) a done-tracker in state — a list of completed subtasks the supervisor sees, so it must justify re-dispatch; (3) iteration caps — max supervisor rounds, then synthesize best-effort from whatever exists. Implement (2) before production: it is the mechanism that actually fixes the forgotten-work failure.
- Cost math: a supervisor call is 1 call per round, and each worker does 1+ calls per round. 3 workers over 4 rounds = 4 supervisor calls + 12+ worker calls = 16+ minimum. Mitigations: cheap model for the supervisor (highest-leverage cut), done-tracking to cut rounds, supervisor synthesizes the final answer itself.
- When the 50% supervisor tax is rational: when the single agent fails often enough and rework is expensive. Cost math without success-rate math is theater — run both, measure both.

| Failure mode | Symptom | Fix |
|--------------|---------|-----|
| Ping-pong | Alternates worker A to B forever | Done-tracker + max rounds; supervisor must explain each re-dispatch |
| Forgotten work | Re-dispatches completed subtasks | Subtask checklist in supervisor context |
| Hallucinated worker | Routes to a worker that doesn't exist | Worker registry with exact names; parse + fallback route |
| Wrong decomposition | Splits work workers can't succeed at | Capability descriptions written by worker owners |
| Lost synthesis | Never merges results | Explicit final synthesis step + output schema |

- **Disqualified when:** the routing is expressible as rules (use a router), or the workers are all the same agent.

**Warning — the SPOF is also the single point of context loss:** if a worker's result is truncated or summarized poorly on the way back to the supervisor, the information is gone — no other agent ever saw it. Explicitly copy worker outputs into a shared results channel, or summarize with care. This is the number one source of "the system knew the answer but gave the wrong one."

## 3. Hierarchical Teams

A supervisor whose workers are themselves supervisor graphs. Team leads route to specialists; leads report to a top-level coordinator. LangGraph expresses this with subgraphs as nodes.

```
                +----------------------+
                |   TOP COORDINATOR    |
                +-----+-----------+----+
             +--------+           +---------+
             v                              v
    +----------------+              +----------------+
    |  RESEARCH TEAM |              |  BUILD TEAM    |
    | (supervisor)   |              | (supervisor)   |
    +---+--------+---+              +---+--------+---+
        v        v                      v        v
    [Web]     [Docs]                [Code]    [Test]
```

### Parent-Child State Mapping — Two Verified Patterns
- **Shared keys:** add the compiled team graph as a node (`add_node("team", team_graph)`) and it reads/writes the parent's channels directly. Simple, but every team key becomes a parent key — state sprawl.
- **Schema-transform:** wrap the team in a node function where you control exactly what crosses the boundary — the wrapper maps parent state to team input and team output back to parent state.

```python
def call_research_team(state: ParentState) -> dict:
    # parent -> team: hand over ONLY what the team needs
    team_input = {"topic": state["topic"], "constraints": state["constraints"]}
    # team -> parent: read back ONLY the conclusions
    team_output = research_team.invoke(team_input)
    return {"findings_ref": team_output["summary_ref"], "team_status": team_output["status"]}
```

### Depth Costs
Each level adds, per round: one routing call, one serialization boundary, one summary step (conclusions upward), one latency hop — and one more place for context to be lost. Checkpoint namespaces nest as `"outer_node:uuid|inner_node:uuid"`.

- Depth 2 (coordinator → teams → specialists) is the sensible ceiling for interactive systems. Depth 3 is usually a sign the task decomposition is wrong, not that the org chart is complex.
- Smell that you've over-nested: a specialist's result must travel up two summary layers before anyone acts on it, and each summary loses the detail the action needed.

### Private Team State — Three Jobs
1. Keeps coordinator context small (only conclusions go up).
2. Prevents write collisions — two teams both writing `draft` into the parent is a race; private channels make it impossible.
3. Makes teams independently testable — the team graph is a unit with a defined input/output contract, so you can eval the research team without running the whole org.

If every team writes to the parent's messages, you have a network pretending to be a hierarchy — and you get network failure modes.

- Wins when: heterogeneous domains with genuine sub-structure; teams need private state; the task has natural nested decomposition. Loses when: few agents (a hierarchy of 2 is ceremony), short tasks, or team results must be cross-joined — hierarchy partitions context, and cross-team joins require the coordinator to ferry data between branches, which is where losses happen.
- **Disqualified when:** depth exceeds 2 for an interactive system, or cross-team joins are the common case.

## 4. Handoff

Control transfers between agents via tool calls. Calling a transfer tool returns `Command(goto="target", graph=Command.PARENT, update={...})` that updates state — crucially `active_agent` — and navigates the graph to the target node. No central router; agents pass the conversation like a baton.

- The critical payload requirement: every tool call in an LLM conversation history must be paired with its ToolMessage response. On handoff, update messages with exactly the AIMessage containing the transfer tool call plus a synthetic ToolMessage acknowledging it (matching `tool_call_id`), plus the `active_agent` change. Do NOT pass the sender's entire internal history — the receiver gets confused by irrelevant reasoning and pays tokens for it. If the receiver needs context, put a summary in the ToolMessage content.
- Why handoff beats supervisor for conversations: the speaker has the context. A supervisor must reconstruct "who should speak now" from the whole transcript each round (1 extra call + context re-read); a handoff encodes the decision at the moment the agent makes it (0 extra calls). Verified docs numbers: one-shot costs 3 calls vs subagent's 4; a repeat request costs 2 calls (the specialist is still active) vs 4. For multi-turn conversations with domain specialists it is the most call-efficient pattern there is.
- The "what MUST be in a handoff" checklist: identity of speaker, task status, artifacts produced (by reference, not by value), constraints the receiver must respect, and the open question. Full payload table in references/supervisor-prompt.md.

### Two Implementation Styles
- **Handoff-as-tool:** the transfer is a tool the model calls; the tool returns a `Command`. The model decides when to transfer — autonomous, flexible, can transfer mid-turn.
- **Deterministic handoff:** routing on `active_agent` with no tool — the graph moves agents on a schedule, the model never decides. Deterministic, cheaper, right when the transfer condition is a rule ("always hand off after collecting billing info").
- **Middle ground:** the tool exists but its description encodes the policy ("transfer only when the user asks about X") — cheaper than policy-in-routing-call, more flexible than policy-in-code.

### Failure Modes
- Missing ToolMessage pairing → malformed history, unexpected behavior.
- Context starvation → too little passed; the receiver re-asks the user what the previous agent already learned.
- Ping-pong handoffs → A transfers to B, B transfers back; no supervisor to break the cycle → transfer count cap + "never transfer back immediately" rule.
- The lost-user problem → after a chain of 4 handoffs nobody addresses the user anymore → cap hop count and force a user-facing response after N hops.

**Pro tip:** the ToolMessage content is the only briefing the receiver gets — write it like a ticket: "Customer: enterprise, 2-year quote, already has pricing PDF, tone: formal, deadline: today." Agents that hand off with an empty "Transferred to sales agent" sentence create systems where each agent re-interviews the user from scratch.

- **Disqualified when:** the transfer condition isn't conversational (use deterministic routing), or no user-facing turns exist.

## 5. Swarm (Handoffs Without Brakes)

Agents transfer to each other freely with no central control; the path is emergent. Honest verdict: it optimizes for flexibility of control flow and nothing else — no termination guarantee (last-speaker-wins), no convergence (A→B→C→A forever with only ad-hoc caps), unbounded context (every hop re-sends the growing transcript), undebuggable trajectories.

Justified for: research prototypes exploring emergent coordination, environments where the interaction dynamics are the point. Pragmatically: it is also the degenerate case of handoffs you already built — if your handoff graph accidentally allows A→B and B→A, you have a swarm, so add the caps before it emerges on its own.

The three fixes if you must: transfer budget (max K hops, then force a final answer), no-immediate-re-transfer rule (can't bounce to the previous speaker), and a completion agent that owns termination. With these, a swarm becomes a handoff graph with emergent paths — you reinvented handoffs with extra steps.

- **Disqualified when:** the task must terminate reliably and auditably — which is most tasks.

## 6. Event-Driven

Agents publish events to a bus and subscribe to events they care about; producer and consumer don't know each other's identities. In LangGraph terms: nodes emit updates to shared state channels (or a Store or external queue like Redis), and downstream graphs are triggered by those writes.

```
 [Agent A] --publish("doc.ready", ref=xyz)--> [EVENT BUS] --> subscriber: [Agent B] starts
 [Agent C] --publish("review.done", ok=true)--> [EVENT BUS] --> subscriber: [Notifier] fires
```

- Wins because: loose coupling (add/remove agents without touching existing ones), failure isolation (a crashed consumer doesn't block producers), scale (fan out one event to many consumers). This is also A2A's philosophy — see references/comms-protocols.md.
- Tradeoffs: at-least-once delivery is the norm, so consumers must be idempotent; ordering is not guaranteed across publishers; the causal chain producer→bus→consumer is invisible in any single trace, so correlation IDs must thread every event; the queue hop adds latency; and the checkpointer's per-thread model doesn't natively span threads — you build the bus (Store or external queue) and trigger new graph runs from consumers.
- Use for: long-running background pipelines (file uploaded → parsed → embedded → indexed), fan-out notifications, cross-system integrations, multi-tenant platforms where teams must not share code. Not for interactive conversations (users feel the queue latency, the decoupling buys nothing) or strict-ordering tasks.
- **Disqualified when:** the consumer must respond in-line (interactive latency), or strict ordering is required.

### LangGraph Integration — Three Shapes
1. **Trigger a graph from an event:** the consumer receives the event and calls `graph.invoke(input, config={"configurable": {"thread_id": event.correlation_id}})` — a fresh run per event, checkpointed per correlation ID, auditable and resumable.
2. **Emit from a node:** a node publishes to the bus as a side effect, respecting idempotency rules — events are the classic double-send on retry.
3. **State-change watcher:** poll `get_state` or stream events for transitions (e.g., "thread entered status=needs_review") and emit. Turns checkpoint state into the event source without coupling nodes to the bus.

**Pro tip:** put a correlation ID in every event and in the graph config metadata of the run it triggers. Six months later, "my document never got indexed" is answered only by searching one ID across every service's logs. Retrofitting IDs into a live system is a weekend project; adding them on day one is one field.

## 7. Market-Based / Auction (Know It, Don't Ship It)

Tasks are posted to a market, agents bid (price, capability, confidence), a market-maker awards and pays from a token budget. Solves allocation under scarcity and capability discovery — useful for multi-tenant metered platforms and research on emergent specialization. Costs: N+1 model calls per task before any work starts, a market-maker SPOF, agents that game bids, probabilistic convergence. As of this writing there is no stable production framework — treat it as conceptual. The transferable idea is bid-as-capability-description: agents self-describe capability, cost, and confidence before dispatch — that's what a worker registry is, minus the price.

## 8. LangGraph Mechanics Reference

| Mechanism | What it does | Sharp edges |
|-----------|--------------|-------------|
| Subgraphs as nodes | Hierarchical teams: `add_node("team", team_graph)` if state keys are shared; invoke inside a wrapper node if schemas differ | A subgraph node updating a key shared with the parent requires a reducer on the parent key |
| Subgraph persistence | `checkpointer=None` (default): per-invocation, inherits parent checkpointer for interrupts within the call. `checkpointer=True`: per-thread state accumulation for multi-turn sub-agent memory. `checkpointer=False`: stateless, no interrupts | Parallel calls to a per-thread sub-agent conflict on the checkpoint namespace — use per-invocation for parallel fan-out, or unique node names |
| Send | Dynamic fan-out: a conditional edge returns `[Send("worker", {"task": t}) for t in tasks]` — each worker gets its own state slice, all run in one super-step, results merge through reducers | Each Send task is its own execution path; checkpoint pending-writes apply per task, so a mid-superstep crash re-runs only unfinished workers |
| Command handoffs | `Command(goto="other_agent", graph=Command.PARENT)` from a tool or node inside a subgraph navigates the parent graph to another agent node | Dynamic edges from Command coexist with static edges (both run — don't mix them on one node) |
| Interrupts across agents | A pause inside a subgraph propagates to the top level; the parent's stream_events surfaces it; `Command(resume=...)` resumes from the paused point; the interrupting node re-runs from its start | Put the approval at the start of the dangerous worker's node, keep side effects after the interrupt |
| Checkpoint namespaces | Each subgraph invocation gets `checkpoint_ns = "node_name:uuid"` (nested: `"outer:uuid|inner:uuid"`) — how per-thread sub-agents keep separate memories | Learn to read namespaces in `get_state(config, subgraphs=True)` output and stream_events paths — this is how you debug multi-agent state |

## 9. Composition: Mixing Architectures

Real systems are composites, and the seams between architectures are where bugs live.

| Composition | Where it wins | The seam to arm |
|-------------|---------------|-----------------|
| Supervisor with handoffs inside a team | Coordination at the top, conversation efficiency at the leaves | The team lead must report up on behalf of whichever specialist finished; the coordinator never sees specialist-level handoffs |
| Event-driven triggering a supervisor workflow | Events kick off workflows; workflows publish completion events | Correlation IDs must flow through the supervisor's state so you can trace the whole chain |
| Delegation tools inside any architecture | Any agent can get sub-agent tools | Sub-agent outputs are untrusted data wherever they re-enter the parent graph |
| Router front door | Deterministic or cheap router classifies, then dispatches to per-domain handoff chains or supervisors | Router rules drift — revisit monthly; log the misroute tax |

The composition rule: each seam needs an explicit contract — who owns state, what gets passed up or down, and who terminates. Every architecture failure mode occurs preferentially at seams.

## 10. Migration: Single to Multi-Agent (Strangler Pattern)

Keep the monolith serving everything; extract domains one at a time into specialist agents behind a router; the router sends the extracted domain's traffic to the specialist and everything else to the monolith. Cut over when the specialist out-performs the monolith on its traffic (eval + cost). Repeat until the monolith serves only the long tail.

- Extraction order: start with the highest-volume domain (biggest isolation win) or the best-understood (easiest eval).
- Dual-run before cutover: shadow the specialist on live traffic — log its output, compare against the monolith — until its eval delta is known. Shadow mode is free and settles every "is it actually better?" argument.
- Context splitting is the real work: defining what crosses the boundary (the handoff payload schema) is 80% of the migration effort, not the graph plumbing.
- Feature-flag the router: the domain-to-agent map lives in config, so cutover and rollback are both config changes. Freeze the monolith's tools during extraction — changing both sides makes every regression unattributable.
- Migration is done when: the monolith serves only its long tail, every domain has its own eval suite in CI, rollback to the previous routing config is tested, and the per-domain before/after numbers (cost, quality, latency) are written down.
