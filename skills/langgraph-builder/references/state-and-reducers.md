**Load this when:** designing the state schema, picking or writing reducers, auditing multi-writer keys, modeling workflow phases as state machines, planning schema migration, or writing state-layer tests.

# State and reducers — schema design, races, anti-patterns, tests

## The four-way split (where each datum lives)

| Data | Where it lives | Why |
|------|----------------|-----|
| What the agent is working on right now (conversation, plan, status) | Graph state (checkpointed per thread) | Working memory; checkpointed for resume and HITL. |
| What a single tool call needs (arguments, per-call config) | Tool parameters | Never round-trip through state — tools receive args directly. |
| Ground-truth records (users, orders, documents) | Your database / object storage | State holds references, not the data; the DB is the system of record. |
| What should survive across threads (preferences, facts) | Store (long-term memory) | Cross-thread, namespaced, searchable. |

Test per key: "would this value be needed if the process crashed and resumed tomorrow?" If it is derivable from other keys, delete it — derived fields drift. Minimal state wins because every key is checkpointed each super-step, serialized on every resume, re-sent to the model when it lands in context, and raceable.

State-key audit (run before shipping):

1. Who writes it — one node or many? Many means you need a reducer or a serial edge.
2. Does it need to survive crash/resume? If not, it may be a tool argument.
3. Is it derivable from other keys? Delete it and compute on the fly.
4. Is it bulk content? Store it externally and hold a reference.
5. Is it cross-thread knowledge? Move it to the Store.
6. Could it be a secret? Get it out of state immediately.

## Schema types: TypedDict vs dataclass vs Pydantic

| Aspect | TypedDict | dataclass | Pydantic BaseModel |
|--------|-----------|-----------|--------------------|
| Runtime validation | none | none | recursive, on every update |
| Defaults | no | yes (`field(default_factory=list)`, never `=[]`) | yes |
| Performance | fastest | fast | slower (validation per super-step) |
| Contract enforcement across teams | by convention | by convention | enforced at write time |
| Framework support | universal | universal | NOT supported by `create_agent` |

- TypedDict wins on hot paths and simple schemas; Pydantic wins when writes come from code you don't control (multi-team graphs): an invalid write fails fast at the boundary with the channel name and offending value, instead of corrupting state and crashing three nodes later.
- The decision is usually about who writes your nodes, not micro-benchmarks — validation cost is microseconds against second-long LLM calls.

## Channel types (what each state key compiles to)

| Channel type | Behavior | When to use |
|--------------|----------|-------------|
| `LastValue` (default) | Overwrites on every write | Single-writer keys like `current_step` |
| Topic | Pubsub-style accumulation, all writes kept | Broadcasting events to multiple consumers |
| `BinaryOperatorAggregate` | Folds values with a binary function | Summing scores, custom collection |
| `EphemeralValue` | Pass-through, not persisted to checkpoint | Temporary coordination signals |
| `DeltaChannel` (1.2+ beta) | Per-step deltas with replay on read | Large append-heavy state; adopt deliberately |

## Reducer catalog

Contract: `new = reducer(current_value, new_write)`. It runs once per write (three writers in a super-step = three chained applications), must handle `None`/empty initialization, and should be order-insensitive (associative and commutative) because parallel application order follows task-completion order.

| Reducer | Behavior |
|---------|----------|
| default (none) | Overwrite — last applied write wins (the race-prone default) |
| `operator.add` | Concatenates lists |
| `add_messages` | Append new-ID messages; REPLACE existing-ID messages in place; deserializes plain dicts into Message objects |
| `collect` (custom, below) | Append with None-initialization |
| `merge_dicts` (custom, below) | Shallow dict merge, right wins per key |
| `Overwrite(value)` | Escape hatch to bypass a channel's reducer for ONE write (state surgery, time-travel fixes) |

```python
import operator
from typing import Annotated, TypedDict

def collect(left, right):
    """Append to a list, handling None initialization."""
    if left is None:
        return [right]
    return left + [right]

def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}

class State(TypedDict):
    log: Annotated[list[str], operator.add]   # append-only
    config: Annotated[dict, merge_dicts]      # shallow merge
    current_step: str                          # single writer: overwrite is fine
```

`add_messages` discipline: editing a message mid-run means reusing its ID — a copy with a new ID leaves both versions in the transcript and the model sees both, silently. Treat message IDs as a data contract (like primary keys) wherever you programmatically edit or regenerate messages.

Custom reducer checklist — must be: total (works for any input pair), order-tolerant (parallel application order must not change the result meaningfully; dedupe-by-ID before appending is robust, plain concatenation is not), cheap (it runs per write per channel — an O(n) reducer on an append channel makes super-steps O(n²)), and side-effect-free (reducers can run more than once across retries and resumes; a reducer that increments an external counter double-counts).

## The race catalog (five canonical shared-state races)

| Race | What happens | Design response |
|------|--------------|-----------------|
| Lost update | Two writers overwrite one key; one value disappears silently | Merge reducer on the channel, or serial edges, or one channel per writer |
| Read-modify-write | A reads status=="running", acts, but B already set "done" (TOCTOU inside your graph) | Single-writer ownership; route transitions through a state machine |
| Interleaved list mutation | Appends interleave nondeterministically; positional consumers break | Key results by writer/item ID and order at read time |
| Checkpoint divergence | In-place mutation invisible to the checkpointer; resume replays the pre-mutation state | Return new values; never mutate state objects in place |
| Reducer double-application | Side-effectful reducer runs twice (retry + resume) | Reducers must be side-effect-free |

Three legitimate fixes for any multi-writer key: an order-insensitive reducer (e.g., dict keyed by task/agent ID), serial edges between the writers, or separate channels per writer merged by a third node.

## Anti-patterns (seven ways to corrupt state)

| Anti-pattern | Looks like | Fix |
|--------------|-----------|-----|
| Monster state | 30+ keys, half unused per node | Four-way split; private subgraph state for scratchpads |
| Raw content in state | Full PDFs, base64 images, 100k-token docs | Bytes in object storage; state holds ref + metadata |
| Tokens/secrets in state | API keys in fields or thread_id | Env/secret manager injected via runtime context, never state |
| State as a database | User table, order history in channels | DB is the system of record; state holds references |
| Unbounded growth | Append-only channels with no pruning | Prune checkpoints; summarize/trim history |
| In-place mutation | `state["items"].append(x)` in a node | Return new values; lint the pattern |
| Non-idempotent nodes | DB row inserted every run | Idempotency keys, upserts, read-before-write |

## State machines in state

Make the workflow's outer phases a state machine: a `Literal`-typed status with a legal-transition table, enforced in the router — because an invalid state that is validated after the fact has already been checkpointed, read by parallel nodes, and shown to the model.

```python
from typing import Literal, TypedDict

STATUS_FLOW = {
    "draft": {"submit"},
    "submitted": {"approve", "reject"},
    "rejected": {"revise"},
    "revised": {"submit"},
    "approved": set(),  # terminal
}

class TaskState(TypedDict):
    status: Literal["draft", "submitted", "rejected", "revised", "approved"]
    # ... other keys

def transition(state: TaskState, new_status: str) -> bool:
    return new_status in STATUS_FLOW.get(state["status"], set())
```

Rules: the table lives in code, not in the model's prompt (the model proposes, the graph disposes; rejections go back as corrective messages); one status field per workflow concern; do not over-machine genuinely exploratory inner work — statuses for coarse phases, agent freedom inside a phase.

## Versioning and migration (schema changes vs old threads)

| Change | Old threads | Action |
|--------|-------------|--------|
| Add key (NotRequired/default) | see default | Ship it; lazy adoption |
| Remove key | harmlessly ignore it | Ship; delete the writer in the same release |
| Rename key | lose saved values | Dual-write one release, then drop the old name |
| Type change (compatible-ish) | may hold old-typed values | Coerce on read for one release |
| Type change (breaking) | can break | New key + migration node, or fork old threads |
| Prompt/tool behavior change | mid-conversation semantics shift | Pin `behavior_version` in state; route old threads on the old path |
| Topology change while a thread is interrupted | breaks only if you rename/remove the node it is about to enter | Never rename the node a pending thread's `next` points at |

## Serialization notes

- Default `JsonPlusSerializer` (ormsgpack-based) round-trips LangChain messages, Pydantic models, dataclasses, datetimes, enums, numpy arrays.
- `pickle_fallback=True` handles exotic types (Pandas frames) but stores pickles — a code-upgrade landmine. Prefer not putting exotic objects in state at all; store a reference.
- Beware round-trip drift: types that serialize but deserialize differently (a set becoming a list; a datetime losing timezone) pass tests and break after resume. Use round-trip-stable types only.
- A reducer that raises fails the WHOLE super-step at update time, after nodes succeeded — the error names the channel, not the writers.

## State testing (the minimum viable suite)

State bugs are cross-node bugs; unit tests cannot find them.

- Reducer property tests: apply writes in different orders and assert equivalent results; assert identity for empty updates; assert wrong-typed updates raise rather than corrupt.
- State-diff tests: run the graph with recorded node outputs and assert exactly which keys change per super-step — catches "node X unexpectedly writes key Y".
- Race tests: run fan-out graphs N times; assert the invariant ("results contains all 3 worker entries, in any order") — never the order.
- Resume/replay tests: interrupt or crash mid-graph, resume, assert completed nodes did not re-run (side-effect counters), the paused node re-ran from its start, and final state equals the no-interrupt run.
- Migration tests: freeze a checkpoint fixture with old-schema state, resume with new graph code, assert old keys migrate as designed.

## State bugs by symptom (build-time diagnostic)

| Symptom | Likely cause | First check |
|---------|--------------|-------------|
| A worker's result disappears | Default-overwrite race | `metadata.writes` across history; multi-writer keys without reducers |
| Result order varies between runs | Parallel writes to an append channel | Reducer order-tolerance |
| State fine in dev, wrong after resume | In-place mutation | Lint for `state[...]` mutation; replay test |
| "Bot forgot everything" after deploy | InMemorySaver in prod | Grep checkpointer configs |
| Old threads break after deploy | Key rename or type change | Migration matrix; version pinning |
| TypeError on a channel after nodes succeeded | Reducer raised at commit time | Custom reducer; conflicting writer types |

When state misbehaves: read `get_state_history()` and `metadata.writes` BEFORE changing code — the history identifies which of these you have in minutes; the code hides it for days.
