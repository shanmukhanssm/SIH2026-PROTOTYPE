---
name: langgraph-builder
description: >-
  Writes correct single-graph LangGraph Python code: StateGraph scaffolding,
  typed state schemas with reducers, nodes, static and conditional edges, Send
  fan-out and fan-in, Command route-and-update, subgraph wiring, checkpointer
  setup, time travel, and streaming modes. Encodes the engine's sharp edges
  (static-plus-conditional double-fire, LastValue data loss on parallel fan-in,
  Command misuse) as enforced gotchas.
  Trigger: "build a LangGraph graph", "add a reducer to my state",
  "wire conditional edges", "fan out to N workers with Send",
  "Command vs conditional edges", "set up a checkpointer for my graph",
  "stream LLM tokens from a graph", "replay or fork a LangGraph thread",
  "why do my parallel workers lose results".
  Do NOT use for: choosing architecture or topology patterns
  (agent-architecture-advisor), multi-agent supervisor and handoff topologies
  (multi-agent-builder), tool schema design (agent-tool-designer), interrupt()
  approval flows (hitl-builder), Store memory taxonomy (agent-memory-builder).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# langgraph-builder

## Overview

This skill writes production-correct LangGraph code for a single graph: state schema, nodes, edges, fan-out/fan-in, checkpointing, and streaming. It assumes the architecture is already chosen (which pattern, which framework) and that any tools mentioned are already designed. It belongs to the BUILD stage. All guidance derives from one non-negotiable mental model: LangGraph executes as Pregel super-steps — nodes run in parallel and in isolation, writes merge through channel reducers only after the super-step completes, and a checkpoint commits after every super-step. Nearly every "why did my graph do THAT?" bug is a super-step, reducer, or edge-wiring misunderstanding, and every template and gotcha here encodes one of those. Never invent APIs: use only `StateGraph`, `add_node`, `add_edge`, `add_conditional_edges`, `compile`, `Send`, `Command`, `Annotated` reducers, `add_messages`, `get_state`, `get_state_history`, `update_state`, `invoke`/`stream` with `stream_mode`, and the checkpointer classes shown in the references.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| `references/templates.md` | Always, at Step 3 — copy the closest runnable template (ReAct tool loop, Send map-reduce, Command routing, checkpointer + time travel). |
| `references/graph-primitives.md` | Choosing Send vs fixed parallel edges vs subgraphs, Command vs conditional edges, subgraph wiring, recursion budgets, or streaming modes. |
| `references/state-and-reducers.md` | Designing the state schema, picking or writing reducers, auditing multi-writer keys, migration, or writing state-layer tests. |
| `references/checkpointing-timetravel.md` | Attaching a checkpointer, durability modes, thread ID design, replay/fork/update operations, custom checkpointers. |

## Execution Checklist

- [ ] 1. Map the task to a topology: nodes, transitions, and failure transitions.
- [ ] 2. Design the state schema: four-way split per key; reducer on every multi-writer key.
- [ ] 3. Scaffold the graph from `references/templates.md`; run `compile()` (it validates the graph).
- [ ] 4. Wire edges: per node use a conditional edge OR Command — never both, never mixed with a static edge.
- [ ] 5. Add Send fan-out/fan-in where N is runtime-determined; reducers on every merged key.
- [ ] 6. Attach a real checkpointer, design thread IDs, and add a step budget check.
- [ ] 7. Pick one or two streaming modes by consumer — not all seven.
- [ ] 8. Verify: compile clean, one test run, state-diff assertions, race and resume tests.

## Step-by-Step Workflow

### Step 1 — Map the task to a graph topology [GUIDED]

List what the agent does, then write three tables: nodes (one unit of observable, resumable work each), transitions (including loop-backs such as agent-to-tools), and failure transitions (where does the graph go when a node fails or the budget runs out — because a graph with no failure transition runs until `GraphRecursionError` and burns money). Split any node that does three logically distinct things (query, LLM call, format): granular nodes get their own checkpoint, streaming events, and retry policy.

- You need a graph the moment you need a loop, dynamic fan-out, shared state, or persistence; a purely linear pipeline without interruption risk can stay a chain.
- Verification: for each node you can name its output keys and its possible next nodes, including the failure path.

### Step 2 — Design the state schema and reducers [GUIDED]

Run the four-way split per datum (graph state / tool parameter / database record / cross-thread Store) and keep only working memory in state, because every key is checkpointed each super-step, re-serialized on every resume, and can be raced by parallel writers. Type everything (`TypedDict` + `NotRequired`, or Pydantic for cross-team write contracts). Then audit writers: any key written by more than one node that can overlap in a super-step gets an explicit merge reducer (`operator.add`, `add_messages`, or a custom one) — the default `LastValue` overwrite silently drops all but the last write.

- Verification: writer audit table — every key has exactly one writer, or a reducer, or a serial edge between its writers; the schema is readable aloud in under a minute.

### Step 3 — Scaffold the StateGraph from a template [EXACT]

Copy the closest template from `references/templates.md` (ReAct tool loop, Send map-reduce, Command routing, checkpointer-wired). Keep the established shape: `StateGraph(Schema)` + one `add_node` per unit + edges + `compile()`. Never skip `compile()` in production because it is the last line of defense that catches orphaned nodes, edges to nonexistent nodes, and missing reducers.

- Customize `{{PLACEHOLDER}}` values (they are quoted strings, so the file parses before you edit).
- Verification: `python3 -c "import ast; ast.parse(open('graph.py').read())"` passes, and `builder.compile()` raises nothing.

### Step 4 — Wire edges and routing [EXACT]

For each node pick exactly one outgoing mechanism: a static `add_edge` (always goes there), a conditional `add_conditional_edges` (routing function returns a node name, a list of names, `END`, or `Send` objects), or a node that returns `Command(goto=..., update=...)` when routing and state-update must be atomic. Never mix static and conditional edges from the same source node — both fire and two nodes run in the next super-step when you expected one.

- Prefer conditional edges for pure routing; prefer Command when the decision and the state it records must land together.
- Verification: print the adjacency; every node has exactly one routing mechanism; every name a router returns is a registered node or `END`.

### Step 5 — Add fan-out/fan-in with Send [GUIDED]

When the number of parallel units is known only at runtime, return `[Send("worker", payload) for item in items]` from the conditional-edge function, because static edges cannot express runtime-N parallelism. Give workers a small worker-scoped input schema; have each worker write into a reduced key of the graph schema. Fan-in by adding a static edge from the worker node to the aggregator — the aggregator is scheduled in the super-step after all workers complete, which is the structural guarantee that makes map-reduce correct.

- Rate limits are the real ceiling: cap concurrency (semaphore) at what the weakest dependency has been load-tested to survive.
- Verification: run with N at least 3 and assert the merged key holds all N results (assert the invariant, never the order).

### Step 6 — Attach checkpointer and configure recursion limits [EXACT]

Choose the checkpointer per the table in `references/checkpointing-timetravel.md` (PostgresSaver for production; a real checkpointer even in staging because serialization bugs surface only on real storage). Every persisted run passes `config={"configurable": {"thread_id": ...}}` — no thread_id means no resume, no HITL, no time travel. Add the budget pattern: route to a wrap-up node when `get_remaining_steps()` (from `langgraph.config`) is low, because the default `recursion_limit` of 1000 lets an infinite loop make hundreds of LLM calls before failing.

- Verification: a second `invoke` on the same thread continues from the latest checkpoint; a killed process resumes without re-running completed nodes.

### Step 7 — Pick streaming modes [FREEFORM]

Pick by consumer, not by curiosity: chat UI gets `messages` (filter tokens by `metadata["langgraph_node"]`), progress UI gets `updates` plus `custom`, HITL state editor gets `values`, dashboards get `tasks`, debugging gets `debug`. Two well-chosen modes beat five half-configured ones because every active mode adds per-event overhead. For batch jobs nobody watches, stream nothing.

- Verification: run `graph.stream(..., stream_mode=[...])` and confirm the events your consumer needs actually arrive (subgraph tokens require `subgraphs=True`).

### Step 8 — Verify with a test run and state assertions [EXACT]

Node tests cannot catch cross-node state bugs, so assert at the state layer: (1) state-diff test — record node outputs and assert exactly which keys change per super-step; (2) race test — run a fan-out N times and assert invariants ("results has 3 entries"), never ordering; (3) resume test — interrupt or crash a node, resume, and assert completed nodes did not re-run and state equals the no-interrupt run; (4) read `get_state_history()` and `metadata.writes` when anything looks wrong — the checkpoint history shows the culprit in minutes that code reading hides for days.

- Verification: all four assertion classes exist for any graph with reducers, fan-out, or interrupts, and pass in CI.

## Examples

**Simple — summarize three URLs.** Input: "summarize each of these 3 pages, then merge." Output: map-reduce template — a planner node writes `urls`, a conditional edge returns three `Send("summarize", {"url": u})`, each worker appends to `summaries: Annotated[list, operator.add]`, a static edge from `summarize` to `merge` guarantees merge runs after all three. Assert `len(summaries) == 3` after the run.

**Typical — support agent with tools and persistence.** Input: "answer billing questions, call the invoice API, survive restarts." Output: ReAct template — `agent`/`tools`/`respond` cycle, `route_after_agent` includes a `get_remaining_steps()` budget guard routing to `respond`, `PostgresSaver` checkpointer, thread ID `user:42:session:7f3a`, `stream_mode=["updates", "messages"]`.

**Edge-case — route and update atomically.** Input: "classify intent and remember which specialist owns the case in one step." Output: Command template — `triage` returns `Command(goto="billing", update={"intent": "billing"})`; no static or conditional edge is added from `triage` because Command replaces them (adding one makes both fire).

## Known Gotchas

1. **Symptom: a parallel worker's result silently disappears from final state.** Cause: the merged key has the default `LastValue` (overwrite) reducer, so concurrent writes in one super-step reduce to last-write-wins, chosen by task-completion order. Response: put an explicit merge reducer (`operator.add`, `add_messages`, `collect`, or custom) on every multi-writer key and add a race test asserting the count, not the order.
2. **Symptom: a node runs twice, or two branches execute when you expected one.** Cause: a static edge and a conditional edge (or Command `goto`) were both added from the same source node — both fire. Response: exactly one outgoing routing mechanism per node; delete the static edge and encode the always-case inside the router.
3. **Symptom: invoke does not continue an interrupted run, or restarts from scratch.** Cause: a plain dict as input starts a new turn, while `Command(resume=...)` continues from the latest checkpoint; `Command(goto=...)` passed as input has no effect. Response: resume with `graph.invoke(Command(resume=value), config)` on the same thread_id; deep approval flows belong to hitl-builder.
4. **Symptom: every thread's state vanishes after a deploy or restart.** Cause: `InMemorySaver` in a production config — state dies with the process. Response: grep configs for `InMemorySaver`/`MemorySaver` before every deploy; use `PostgresSaver` (call `setup()` once on a fresh DB).
5. **Symptom: `GraphRecursionError`, or a runaway loop that makes hundreds of paid LLM calls first.** Cause: the loop never reaches a terminal condition and the default `recursion_limit` (1000) is high. Response: add the `get_remaining_steps()` budget guard routing to a wrap-up node; treat the recursion limit as a backstop, not the budget.
6. **Symptom: state looks fine in dev but is corrupted after a resume.** Cause: a node mutated state in place (`state["items"].append(x)`); the checkpoint captured the pre-mutation value and resume replays from there. Response: nodes return new values only; lint against in-place mutation of state objects.
7. **Symptom: duplicate side effects (emails, rows) after resume or crash recovery.** Cause: a node's work before an interrupt (or before a crash) re-executes from the node's beginning on resume. Response: side effects must be idempotent (idempotency keys, upserts) or moved after the resumable point.
8. **Symptom: sub-agent tokens never appear in the stream while the supervisor streams fine.** Cause: `stream_mode="messages"` on the parent drops subgraph tokens unless `subgraphs=True` is passed. Response: enable `subgraphs=True` and filter with `metadata["langgraph_node"]`; tag internal calls `nostream`.
9. **Symptom: `InvalidUpdateError` or a serialization failure after a node succeeded.** Cause: the returned update does not match the schema/reducer, or an exotic object (Pandas frame, open connection) hit the serializer at checkpoint time. Response: return schema-conforming partial updates; keep bulk/exotic data out of state (store a reference instead); `pickle_fallback=True` is a last resort.
10. **Symptom: `TypeError` raised on a channel after all nodes succeeded.** Cause: a reducer raised during the update phase — the error names the channel, not the writers, so parallel-write type conflicts are hard to attribute. Response: audit that channel's writers, make the reducer total and type-safe, and add a state-diff test naming the expected writers.
