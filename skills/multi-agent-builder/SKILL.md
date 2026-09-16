---
name: multi-agent-builder
description: >-
  Implement multi-agent LangGraph systems: verify the split is justified, select and
  implement supervisor, handoff, hierarchical, network, or event-driven topologies;
  write the supervisor routing prompt with its done-tracker; implement handoffs as
  state-carrying Command transitions; wire inter-agent communication (shared state
  vs message passing vs event bus); add detection for ping-pong, lost context, and
  duplicated work; A2A/MCP interop notes. Trigger: "build a supervisor agent",
  "implement handoffs between agents", "multi-agent LangGraph topology",
  "hierarchical agent teams", "how should agents talk to each other", "A2A or MCP
  interop", "how many agents do I need", "supervisor keeps re-routing to the same
  worker", "parallel subagent fan-out". Do NOT use for: single-graph LangGraph code
  (langgraph-builder), advisory architecture decisions (agent-architecture-advisor),
  designing individual agents' tool schemas (agent-tool-designer), HITL interrupts
  and approvals (hitl-builder).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# multi-agent-builder

## Overview
This skill implements multi-agent systems in LangGraph (BUILD stage). Multi-agent is a cost structure, not a feature: it pays only for context isolation, parallelism, or ownership — everything else is cargo cult. The workflow takes a justified split from decision to working graph code: choose the topology from the comparison matrix, define agent roles and boundaries, implement supervisor routing or Command handoffs from templates, write the supervisor prompt with its done-tracker, wire the communication topology with state isolation, and add the failure detection that catches ping-pong, dropped context, and double work before production does. Assumes LangGraph basics (nodes, edges, reducers, checkpointers) and that each agent's own tools are designed elsewhere.

## When to Load Which Reference File
| File | Load when... |
|------|--------------|
| references/topology-implementations.md | Implementing a specific topology (supervisor, handoff, hierarchical, network, event-driven) and its LangGraph mechanics: subgraphs, Send, Command.PARENT, checkpoint namespaces |
| references/supervisor-prompt.md | Writing or adapting the supervisor routing prompt, wiring the done-tracker, or composing a handoff briefing |
| references/failure-modes.md | Adding failure detection, sizing agent count, or reviewing the architecture for anti-patterns and the launch checklist |
| references/comms-protocols.md | Deciding how agents exchange data (shared state vs message passing vs event bus), envelope design, A2A/MCP interop, or building an agent bus |
| references/templates.md | Writing the actual graph code — runnable Python templates for every topology with customization markers |

## Execution Checklist
- [ ] 1. Write the single-agent baseline and run the cargo-cult test (isolation / parallelism / ownership).
- [ ] 2. Select the topology from the comparison matrix; read its disqualification row aloud.
- [ ] 3. Define agent roles: one verifiable deliverable each, capability boundaries, seam contracts.
- [ ] 4. Implement the mechanics from references/templates.md (supervisor or handoff first).
- [ ] 5. Write the supervisor prompt: registry, routing contract, done-tracker evidence block.
- [ ] 6. Wire communication + state isolation (per-agent namespaces, envelopes, reducers).
- [ ] 7. Add failure detection: distinct-agent-visits metric, handoff payload-hash logs, routing diff.
- [ ] 8. Verify with handoff test scenarios and failure injection; run the launch checklist.

## Step-by-Step Workflow

### Step 1 — Verify multi-agent is justified [GUIDED]
Write down the single-agent baseline (calls, tokens, quality), sketch the multi-agent version, and keep the baseline unless multi-agent measurably wins on at least one of: isolation, parallelism, ownership — because on one-shot tasks every multi-agent pattern costs more calls than a single skilled agent, and seams add failure modes a single agent never has.
- Legitimate payoffs: a huge knowledge domain only sometimes relevant (context isolation — the 6k-token policy doc loaded only in the refund worker); independent subtasks (parallelism — 3 research agents finish in ~1/3 the time at ~3x cost); a corpus that cannot fit one context window; different teams/tenants own capabilities (ownership); different models per subtask.
- Cargo-cult red flags (any one = stop): the "supervisor" routes by rules a plain `if` could express; agents differ only in system prompts, share all tools, never run in parallel (one agent in a costume change); nobody can name what Agent B knows that Agent A doesn't; the org chart came before the task; a diagram exists but an eval does not.
- Pre-decode any proposal with four questions: who decides the next speaker (authority / last speaker / state)? What context does each agent see (full transcript / filtered slice / isolated)? Where does the task end (explicit FINISH / handoff exhaustion / nobody knows)? What happens when two agents act at once (reducer merge / race / impossible-by-design)?
- T36: multi-agent latency multiplies, it doesn't add — two agents at 3 sequential calls each is 6 calls plus serialization per hop. Before adding an agent, ask whether a better tool would do.
- Verify: a one-paragraph justification naming the winning dimension and the measured single-agent baseline exists before any graph code is written.

### Step 2 — Select the topology [FREEFORM]
Choose from the matrix (full version with cost tables and disqualification rows in references/topology-implementations.md):

| Topology | Authority | Best for | Canonical failure | Cost |
|----------|-----------|----------|-------------------|------|
| Network / scratchpad | emergent (state) | 2-4 agents, short tasks, total visibility | context blowup, write races | O(steps x agents) |
| Supervisor | central router | heterogeneous workers, clear decomposition | ping-pong, forgotten work | N agents x M rounds + router calls |
| Hierarchical | authority per level | genuine nested domains, private team state | boundary context loss, depth latency | +1 routing call per level |
| Handoff | last speaker | multi-turn conversations with specialists | ping-pong handoffs, re-interviewing | lowest for conversations |
| Event-driven | none (bus) | background pipelines, fan-out | duplicate delivery, lost causality | bus overhead, negligible per event |
| Subagents (delegation) | orchestrator | on-demand domains, parallelism | injection via delegate, depth explosion | delegate invocation rate |
| Router front door | rules | cheapest first hop — always consider it | rule drift, misroute tax | ~0-1 calls |

- Read the candidate's disqualification row aloud and ask "is this us?": supervisor disqualified when routing is expressible as rules (use a router) or workers are all the same agent; hierarchy disqualified above depth 2 for interactive systems; handoff disqualified when the transfer condition isn't conversational; event-driven disqualified when consumers must respond in-line or need strict ordering.
- Pick by traffic row: handoffs win repeat conversational workloads because statefulness amortizes (repeat turn = 2 calls vs 4 for stateless delegates); subagents/router win one-shot multi-domain work; the supervisor pays a permanent routing tax and justifies itself on success rate, not call counts.
- Verify: one topology chosen plus its cost driver named (supervisor → rounds per task; handoff → hop count; router → misroute rate; subagents → delegate invocation rate), because monitoring the cost driver IS the monitoring — a cost alert without the driver is an invoice, not a diagnosis.

### Step 3 — Define agent roles and boundaries [GUIDED]
One agent per business capability, sized by measurable constraints — because agent count grows the total context-engineering surface linearly and every agent adds a checkpoint namespace, a trace subtree, and a failure-mode class.
- Sizing constraints: tool-selection degrades past ~10-20 tools per agent (the real reason to split); ~3 serial hops max per turn against a 10-15s interactive latency budget; past ~7-10 agents in one interactive system, incidents become multi-day archaeology; every agent needs an owner and an eval suite.
- T37: give every subagent a single, verifiable deliverable — "return the last 3 invoices and flag any duplicate charges", not "look into the account" — because undefined deliverables are how subagent trees become runaway cost.
- Capability boundary per agent: which tools, which state keys, which data. The summarizer holding the delete tool is a bug — capability partitioning is a security control.
- Seam contract per boundary: what crosses, in which direction, trusted or treated as data. Sub-agent outputs are always untrusted data wherever they re-enter the parent graph.
- Smell test (any hit = fix before building): two agents share >80% of tools and prompt → merge; an agent invoked by exactly one other agent, always → submerge as node/tool of its caller; an agent has <2 tools → inline it; the supervisor routes one worker >90% of traffic → router + default; every agent speaks in every task → committee, not division of labor; depth 3+ in the agent tree → decomposition error, not an org chart.
- Verify: a role table (agent → deliverable → tools → state keys → owner) where no row fails the smell test and no boundary lacks a contract.

### Step 4 — Implement supervisor or handoff mechanics [EXACT]
Copy the template from references/templates.md (Template 1 supervisor, Template 2/3 handoff). Mechanics detail in references/topology-implementations.md.
- Supervisor: a dedicated supervisor node routes to workers via a conditional edge on a `next` field; workers never talk to each other, because all traffic through one authority makes routing auditable and workers swappable. Loop control in order of reliability: (1) explicit FINISH in the routing contract, (2) a done-tracker in state (append-only completed-subtasks list written by workers, shown to the supervisor — the fix for re-dispatching finished work), (3) iteration caps with best-effort synthesis on breach.
- Handoff: the transfer emits `Command(goto="target_agent", graph=Command.PARENT, update={...})` when agents live in subgraphs. The update MUST pair the AIMessage carrying the transfer tool call with a synthetic ToolMessage of the SAME `tool_call_id` (malformed history otherwise — verified docs requirement) and set `active_agent`. The briefing is a ticket, not a transcript: task, facts already gathered, constraints, open question (T28: an agenda beats a summary).
- Choose handoff-as-tool (the model decides when to transfer — flexible, can transfer mid-turn) vs deterministic handoff (the graph routes on `active_agent` — cheaper, right when the transfer condition is a rule); the middle ground is policy encoded in the tool description.
- Do NOT mix Command dynamic edges with static edges on one node — both run. Do NOT call an agent from inside a tool synchronously — delegate via state + routing, because sync calls deadlock.
- Verify: the graph compiles; a scripted invoke reaches FINISH/END on a golden input; no worker-to-worker edge exists in a supervisor graph.

### Step 5 — Write the supervisor prompt [EXACT]
Copy the complete prompt from references/supervisor-prompt.md — copy, don't paraphrase — because three details separate it from a naive prompt and each prevents a named failure.
- Negative routing hints ("NOT for writing prose") prevent the number-one misroute (the researcher asked to write).
- The FINISH precondition is a checkable state field ("reviewer returned approved"), not an opinion.
- The evidence block (goal / done / last) is system-assembled from the done-tracker and the prompt forbids the model from inventing it, because a supervisor that hallucinates progress evidence re-dispatches endlessly. The prompt is not the supervisor; the prompt plus the done-tracker in state is the supervisor.
- Run the supervisor on the cheapest model that routes correctly — it's a router; a frontier model costs 10x for ~1% better routes.
- Decide synthesis upfront: the supervisor synthesizes on FINISH (one extra call), or the final worker is the synthesis step — otherwise you get "all the pieces and no document".
- Verify: log every routing decision and diff it against a correct-routing label on labeled traffic — supervisor misroutes are quiet and expensive (3-5 wasted worker calls before anyone notices).

### Step 6 — Wire communication and state isolation [GUIDED]
Follow the decision tree (full version, envelope spec, and A2A/MCP notes in references/comms-protocols.md): same process + deploy → shared state; crossed a trust boundary, no fan-out → direct messages with envelopes; fan-out or unknown future consumers → event bus; different orgs → A2A. Every "no" branch adds infrastructure — name what it buys or go back to the top.
- Private state namespace per agent: state keys are memory, never a message queue — agents that read each other's half-written reasoning produce prompt blowup and self-referential cruft. In hierarchies, team-private state with leads reporting conclusions upward; a shared messages channel across teams is a network pretending to be a hierarchy.
- Consistency contract: append-only channels for anything multiple agents emit, merged by ID; single-writer channels for decisions (`next` written only by the routing function or the token holder); never route on last-message-author — same-super-step ambiguity causes flapping.
- T30: parallel subagents each get their own channel with a merge reducer, or run sequentially — there is no third option that survives production, because a default overwrite reducer silently keeps only the last writer's value.
- Cross-process messaging: envelopes (correlation_id first — the single highest-ROI field), at-least-once delivery + idempotency keys generated by deterministic code, commands carry deadlines, events are self-contained past-tense facts, and state stays the single system of record while messages reference it.
- Verify: every multi-writer channel has a reducer (or serial edges); every boundary has a named schema; correlation IDs flow through every event and log line.

### Step 7 — Add multi-agent failure detection [GUIDED]
Instrument the canonical failures (detection + prevention per failure in references/failure-modes.md) — they live between agents, in the seams, so per-agent evals alone never catch them.
- Infinite ping-pong: no progress signal in state → done-tracker + transfer caps + no-immediate-return rule.
- Lost context across handoffs: each transfer drops what wasn't explicitly passed → mandatory handoff payload schema + receiver-can-answer eval.
- Duplicated work: two agents research the same sub-question → shared task registry in state (append-only dispatched/completed log read before starting work).
- Token blowup, deadlocks (sync agent-in-tool calls), partial fan-out failures, supervisor as knowledge bottleneck: see the catalog for the fix of each.
- The one metric to add first: distinct-agent-visits per task with an alert threshold — it catches ping-pong, duplicated work, and most supervisor failures, and ping-pong is the most expensive bug because it looks healthy in monitoring while the bill quietly triples.
- Also: handoff logs with payload-hash ("what did B receive from A" must be answerable retroactively), per-agent cost attribution, and the architecture's cost driver from Step 2.
- Verify: from a single run you can reconstruct who saw what, who decided what, and what was handed to whom — via nested traces (stream_events namespace paths) and `get_state(config, subgraphs=True)`.

### Step 8 — Verify with handoff test scenarios [EXACT]
Run verification in this order, because degradation paths you haven't executed are code you hope exists.
- Failure injection first: force ping-pong (remove the done-tracker), force lost context (empty briefing), kill one worker of a parallel fan-out — assert the recovery path runs (retry with cap, proceed with flag, or fail the subtask explicitly; never silently drop).
- Post-handoff eval: after each transfer, check the receiver can answer questions the sender could — the measurable form of "did context survive the transfer".
- Judge-based end-to-end rubric on isolated threads (`thread_id = "eval:{scenario.id}"` — eval runs sharing production threads corrupt both): task completed (0-3), correct agent involvement (0-2), context preserved across handoffs (0-2), terminated properly (0-1), cost within 1.5x predicted calls (0-1).
- Run the 12-gate launch checklist in references/failure-modes.md — the seam gates (handoff payload schema, ping-pong metric, injection surfaces, subgraph persistence modes) are the ones teams skip.
- Verify: the eval suite covers routing accuracy, handoff quality, termination, cost discipline, and end-to-end outcomes; rollback (revert router config / route back to the baseline) is tested, not assumed.

## Examples
1. Simple — support handoff. Input: "I want a refund for order 88213"; traffic is multi-turn with topic switches. Output: deterministic router front door (keyword rule, 0 LLM calls) → support_agent with transfer_to_billing; support gathers order facts first, transfers with briefing "Refund request: order 88213, $42, delivered 14d ago..."; billing_agent receives the user message + tool-call pair + briefing and answers in 2 calls with zero re-interviewing. No supervisor anywhere, because nothing needs an authority node.
2. Typical — supervisor team. Input: "Write a 500-word market summary of the EV industry in Germany" with researcher/writer/reviewer workers. Output: Template 1; the done-tracker shows `["sourced: EV sales data 2024-2025", "drafted: outline approved"]` so the supervisor never re-dispatches; FINISH requires draft exists AND reviewer approved; supervisor synthesizes on FINISH. Cost check: ~12 calls vs ~7 for a single agent — justified because the single agent failed 20% of runs and rework cost 2x a successful one.
3. Edge case — parallel fan-out. Input: "compare 3 languages", latency-bound. Output: a conditional edge returns `[Send("research_worker", {"task": t}) for t in tasks]` — 3 workers in one super-step, each writing its own `findings` channel with a merge-by-ID reducer (T30), then one synthesis node. A crashed sibling is retried with a cap while completed siblings' pending writes survive resume. Letting all three write `messages` instead makes transcript order task-completion order — non-deterministic run to run.

## Known Gotchas
1. **Ping-pong loops look healthy in monitoring.** → **Cause:** no progress signal in state; the supervisor bounces between workers or two handoff agents bounce each other — every call succeeds, so dashboards stay green while the bill triples. → **Response:** add the distinct-agent-visits-per-task metric and alert on it; fix with done-tracker + transfer caps + no-immediate-return rule.
2. **Missing ToolMessage on handoff corrupts history.** → **Cause:** the transfer updates messages without pairing the AIMessage tool call with a synthetic ToolMessage of the same tool_call_id. → **Response:** always write the pair inside the transfer update; assert the pairing in a contract test.
3. **Passing full transcripts between agents.** → **Cause:** "so nothing is lost" thinking — every hop re-sends everyone's reasoning and tokens double per hop. → **Response:** handoff payload schema — briefing + artifacts by reference; the receiver fetches detail on demand.
4. **Parallel calls to a per-thread sub-agent collide.** → **Cause:** both calls write the same checkpoint namespace, corrupting state. → **Response:** per-invocation sub-agents (checkpointer=None) for parallel fan-out, or unique node names.
5. **Agent calling agent synchronously inside a tool deadlocks.** → **Cause:** A waits on B's output while B waits on A's. → **Response:** delegate via state + routing (Command); design DAGs with explicit completion edges and deadlines on every wait.
6. **Concurrent writes to one channel silently replace instead of merge.** → **Cause:** no reducer specified; the default keeps the last-finished writer's value (the shared-state incident). → **Response:** define a reducer for every multi-writer channel; T30 — own channel + merge reducer per parallel subagent, or run sequentially.
7. **Supervisor on the frontier model.** → **Cause:** routing is a cheap problem; you pay 10x for ~1% better routes. → **Response:** Haiku-class supervisor; upgrade only if routing evals demand it.
8. **Shared transcript for all teams collapses routing.** → **Cause:** the coordinator's context becomes every team's rough drafts; top-level routing quality degrades within a few rounds. → **Response:** team-private state keys or subgraph schemas; leads report conclusions upward, not process.
9. **Routing on last-message-author flaps.** → **Cause:** two agents completing in the same super-step make "last author" ambiguous. → **Response:** route on an explicit `next` field each agent sets in its own update; treat conflicting same-step writes as a bug.
10. **No done-tracker in supervisor loops.** → **Cause:** the supervisor cannot see what finished, re-dispatches completed subtasks, rounds balloon. → **Response:** append-only completed-tasks list in state, rendered into the supervisor's evidence block each round.
