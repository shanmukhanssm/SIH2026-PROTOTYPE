**Load this when:** choosing between Send, fixed parallel edges, and subgraphs; deciding Command vs conditional edges; reasoning about superstep ordering, node contracts, recursion budgets, structured outputs for routing, or streaming modes.

# Graph primitives — edges, Send, Command, subgraphs, supersteps

## The Pregel superstep model (why every "mysterious" behavior happens)

Each run proceeds in discrete super-steps with three phases:

| Phase | What happens |
|-------|--------------|
| Plan | Runtime schedules every node that has incoming messages (or START). |
| Execute | All scheduled nodes run in parallel and in isolation — each sees the state as it was at the start of the super-step, never sibling writes. |
| Update | All writes merge into shared state through channel reducers; the checkpoint commits; the next Plan phase sees the merged state. |

Consequences to internalize:

- Two nodes in the same super-step cannot see each other's updates, because writes are invisible until the Update phase.
- A fan-in node (aggregate) is scheduled in the super-step AFTER all workers complete — never concurrently. This ordering is structural, which is what makes map-reduce correct without locks.
- Scheduling is deterministic given the same state and graph structure (this is what makes time travel and replay possible), but within a super-step the order of reducer application follows task-completion order — reducers must therefore be order-insensitive whenever writers can overlap.
- Logical isolation is not physical parallelism: the GIL serializes CPU-bound nodes, and provider rate limits serialize LLM-bound ones. Design for logical correctness; physical parallelism is a deployment concern.

## Node contract and injection table

A node is a plain (sync or async) function that receives the current state and returns a PARTIAL update — only the keys it changes. The runtime merges the partial update through each channel's reducer.

| What the node needs | Where it comes from |
|---------------------|---------------------|
| Task data (the work) | `state` parameter |
| Model client, DB pool | `runtime.context` (set at compile time) |
| Thread identity, step count | `config` parameter |
| Cross-thread facts, user prefs | `store` (`InMemoryStore` or Postgres-backed) |
| Progress events, partial output | `get_stream_writer()` callback |

- Never put credentials in state, because they are serialized into every checkpoint, visible in `get_state()`, and may crash the serializer. State is checkpointed working memory; config is ephemeral runtime context; mixing them loses data or leaks it.
- Node caching (`cache` + `CachePolicy(ttl=..., key_func=...)`) is valid for expensive deterministic nodes (retrieval, reference lookups); never cache side-effecting or nondeterministic nodes (LLM calls with temperature greater than 0), because the cache key is the input state slice and stale answers follow.

## Send vs fixed parallel edges vs subgraphs — decision table

| Pattern | Use when | Why |
|---------|----------|-----|
| `Send` | Dynamic N (known at runtime), same node, same logic per item | Static edges cannot express runtime-N; Send spawns one execution per payload in the same super-step. |
| Fixed parallel edges | Known N at build time, different nodes, different logic | The structure is static, so plain edges from one node to several nodes express it without extra machinery. |
| Subgraph as node | Different logic, own state schema, own persistence scope | Each specialist gets encapsulated state; parent only sees shared keys. |

Fan-in rules for all three:

- Every key written by multiple workers needs an explicit reducer (`operator.add`, `add_messages`, or custom). Default overwrite means silent last-write-wins data loss.
- Add a static edge from worker node to the aggregator; the superstep ordering guarantee schedules the aggregator only after all worker writes are merged.
- Send payloads are the worker's input state — give workers a small worker-scoped schema (`{"item": ...}`) instead of handing them the whole graph state.
- Concurrency is capped by reality, not by the graph: N Sends means N simultaneous provider slots. Semaphore-cap at what the weakest dependency has been load-tested to survive.

## Command vs conditional edges

Command has four parameters:

| Parameter | Purpose | Notes |
|-----------|---------|-------|
| `update` | State update (reducers apply) | Like returning a dict from the node. |
| `goto` | Route to a specific node | Do not mix with static/conditional edges from the same node — they all fire. |
| `graph` | `Command.PARENT` for subgraph escape | Hands routing to the parent graph's node (agent-to-agent handoffs across subgraph boundaries). |
| `resume` | Resume an `interrupt()` | Only valid when Command is used as INPUT to invoke/stream. |

Two usage contexts — mixing them up is a one-line bug that costs hours:

1. **Returned from a node** (route-and-update): `return Command(goto="tools", update={"messages": [result]})`. The update merges (reducers apply), then the graph routes. Use when the decision and the state recording it must land atomically.
2. **Passed as input to invoke/stream** (resume only): `graph.invoke(Command(resume={...}), config)`. This resumes from the LATEST CHECKPOINT, not from START. A plain dict as input starts a new turn from scratch; `Command(resume=...)` continues an interrupted run.

Decision table:

| Situation | Use |
|-----------|-----|
| Pure routing decision (inspect state, return next node name) | Conditional edge |
| Route + record the decision in state atomically | `Command(goto=..., update=...)` returned from the node |
| Dynamic per-item dispatch, runtime N | Conditional edge returning `[Send(...)]` |
| Continue after human pause | `Command(resume=...)` as input (approval payload design is hitl-builder's job) |

A routing function may return: a node name (str), a list of node names, `END`, or a list of `Send` objects. Command's `goto` also accepts a list.

## Edge wiring sharp edges

- Static edge + conditional edge from the same source node = BOTH fire; two nodes run in the next super-step when you expected one. Exactly one routing mechanism per node.
- The same applies to a node returning `Command(goto=...)`: add no edges out of that node.
- `compile()` performs real validation — orphaned nodes (no incoming edges), malformed edges (nonexistent targets), missing reducers. Never skip it; it is your last line of defense against graph-definition bugs.
- Checkpoint a run only if you pass `thread_id`: no thread_id means no persistence, no resume, no time travel.

## Subgraph-as-node pitfalls

A compiled graph used as a node: shared keys flow through automatically; parent-only keys are not passed in; child-only keys are invisible to the parent.

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Key mismatch | Child can't find expected state key | Ensure the parent schema includes all shared keys. |
| Child expects unfilled key | KeyError on access | Add a default or handle None in the child node. |
| Reducer differences | Parent and child define different reducers for the same key | Align reducer definitions or map explicitly. |
| Parent reads mid-run | Parent sees stale child state | Child must complete its full super-step before the parent sees updates. |
| Deep nesting | Debugging becomes impossible | Limit to 2-3 levels; flatten with Send where possible. |
| Plain function call instead of compiled subgraph | State doesn't flow, no checkpointing | Compile the child and add it as a node; never call the function directly. |

Persistence: each subgraph gets its own `checkpoint_ns` prefix. With its own checkpointer it keeps full internal history; without one, the parent sees the whole subgraph execution as a single super-step (no internal checkpoints).

## Recursion limits and budget checking

- `recursion_limit` (default 1000 since v1.0.6; was 25) caps super-steps; exceeding it raises `GraphRecursionError`.
- The higher default means an infinite loop (LLM keeps calling the same tool with the same arguments) burns hundreds of LLM calls before failing. Always implement a budget check:

```python
from langgraph.config import get_remaining_steps

def route_with_budget(state: AgentState) -> str:
    if get_remaining_steps() <= 2:
        return "wrap_up"          # graceful degradation node
    if state.get("final_answer"):
        return "respond"
    return "tools"
```

- Give `wrap_up` a node that returns a partial, honest final answer, and route it to `END`.

## Structured outputs for fast routing nodes

Forcing structured output (`.with_structured_output(Schema)` on any chat model) speeds agents up three ways: shorter outputs (a classification that takes 150 tokens as prose takes ~30 as JSON), fewer iterations (a structured plan eliminates the free-text "think out loud" round), and no parse-retry loops (an unparseable prose output triggers a full re-generation at 1-3 seconds per failure).

- Use for: intent classification, routing decisions, tool-argument drafting, planning steps, extraction.
- Do not force schemas on the final open-ended answer — overhead and rigidity hurt quality there.
- Structured output makes content SHAPED, not correct: a schema-valid `{"action": "refund", "amount": -5}` is still a disaster. Schema validation is a guardrail, not a correctness guarantee.

## Streaming modes — selection and implementation patterns

| Mode | What you get | Best for |
|------|--------------|----------|
| `updates` | Per-node state deltas (the diff) | Progress tracking, debugging node outputs |
| `values` | Full state snapshot after each super-step | HITL editors, state inspection |
| `messages` | LLM tokens as `(message_chunk, metadata)` tuples | Chat UIs, real-time token display |
| `custom` | Arbitrary data via `get_stream_writer()` | Progress bars, partial results, any model |
| `checkpoints` | Checkpoint created/updated events | Audit trails |
| `tasks` | Task start/finish events | Job monitors, dashboards |
| `debug` | Checkpoints + tasks + metadata | Debug sessions, full lifecycle tracing |

Consumer matching: chat UI gets `messages` (+ `custom`); HITL state editor gets `values`; monitor gets `tasks`; audit gets `checkpoints` + `debug`; debugging gets `debug`. Two well-chosen modes beat five half-configured ones because every active mode adds overhead.

Implementation patterns that prevent the classic failures:

- Filter token streams by node: `metadata["langgraph_node"] == "final_answer"` — otherwise users see reasoning tokens from every LLM call (including tool-call drafting). Tag internal calls `nostream` so their tokens never reach the UI.
- `updates` shows each node's contribution (a diff); `values` shows the world after each step. Debugging is usually easier with `updates`.
- Subgraph tokens are silently dropped unless you pass `subgraphs=True` — the classic "supervisor streams, sub-agents are silent" bug.
- Pair every slow tool with a `custom` progress event (`{"tool": "search", "done": 12, "total": 40}`), because a token stream that pauses during a tool call reads as a dead agent.
- Batch/background jobs: stream nothing — per-token callback overhead and lost provider optimizations for nobody's benefit.
- Interrupts surface in the stream (the `interrupts` field of values parts in v2); a UI that ignores them shows a frozen spinner where an approval button belongs.
- Python below 3.11: asyncio lacks task context propagation — pass `RunnableConfig` explicitly into `ainvoke()` calls and do not use `get_stream_writer()` in async nodes (inject the writer as a parameter instead). Most "streaming just doesn't work" reports trace here.
- v2 stream parts are uniform dicts: `{"type": ..., "ns": ..., "data": ...}`; multiple modes at once via `stream_mode=["updates", "messages"]`.

## Runtime error quick map (build-relevant)

| Error | Most common cause |
|-------|-------------------|
| `GraphRecursionError` | Loop never terminates; implement budget checking. |
| `InvalidUpdateError` | Node return doesn't match schema, or write type incompatible with reducer. |
| `KeyError` on state access | Key missing from schema, or subgraph key mismatch. |
| Node ran twice | Static + conditional edge from the same node. |
| Serialization failure | Non-serializable object in state. |
| Streaming nothing | Wrong `stream_mode`, missing `subgraphs=True`, or graph ended first. |
| Command stuck / no effect | `Command(goto=...)` passed as input instead of `Command(resume=...)`. |
| Async code running serially | Missing await, or a sync node blocking the event loop. |
