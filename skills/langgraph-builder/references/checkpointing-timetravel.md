**Load this when:** attaching a checkpointer, choosing durability modes, designing thread IDs, implementing replay/fork/update (time travel), pruning checkpoint bloat, or writing a custom checkpointer.

# Checkpointing and time travel

## What a checkpoint stores

One checkpoint (a `StateSnapshot`) per super-step — a git commit for graph state:

| Field | Contents |
|-------|----------|
| `values` | Current channel values (full state at this point) |
| `next` | Nodes scheduled for the next super-step (empty tuple = graph finished) |
| `config` | `thread_id`, `checkpoint_ns`, `checkpoint_id` |
| `metadata` | `source` (`input` / `loop` / `update`), `writes` (which node wrote what), `step` counter |
| `created_at` | Timestamp |
| `parent_config` | Link to the previous checkpoint (the chain is a linked list) |
| `tasks` | Per-task pending work, with per-task interrupts when paused |

Under the hood the checkpointer keeps two tables: `checkpoints` (one row per super-step, merged state) and `writes` (one row per node output within a step, before reducer merging). The writes table is what makes crash recovery precise: if one of three parallel nodes crashes, the successful nodes' writes are already durable and resume re-runs only the failed node.

Field-reading habits: `source == "update"` means a human or script touched state (filter for it first when production state looks weird). `metadata.writes` attributes a transition to a node (the forensic tool for vanished results). `parent_config` is the time-travel handle.

## Checkpointer choice

| Backend | Use when | Watch out |
|---------|----------|-----------|
| `InMemorySaver` | Prototyping, unit tests, notebooks | State dies with the process — never in deployed configs |
| `SqliteSaver` | Local dev, single-server deployments | Single-writer file; not for multi-instance |
| `PostgresSaver` | Production default (multi-server, durable, concurrent) | `thread_id` max 255 chars; call `.setup()` once; prune or it grows forever |
| `CosmosDBSaver` | Azure ecosystems | Azure lock-in, throughput tiers |

- Grep configs for `InMemorySaver`/`MemorySaver` before every deploy — the classic failure is "the bot lost its memory" after a restart, unreproducible.
- Enable a real checkpointer (even SqliteSaver) in staging from day one: serialization bugs, size limits, and latency characteristics differ between in-memory and real storage; find them early.

## Durability modes

| Mode | Behavior | Trade-off |
|------|----------|-----------|
| `sync` (default) | Flush to storage after every super-step | Safest, slowest (~5-30ms per step on networked Postgres) |
| `async` | Background write | Near-zero latency; small crash window; right when redoing one step is cheap |
| `exit` | Write only when the graph finishes | Fastest, least durable — a crash loses everything since the last exit |

Crash-recovery semantics: a node that completed has its write pending-persisted and resume skips it; a node that died mid-execution has uncommitted writes and re-runs from scratch (so node functions must be idempotent or crash-safe). Checkpoints guarantee completed work is never lost and failed work is never silently skipped — they do NOT make side effects transactional.

## Threads and namespaces

- `thread_id` is the primary key of all persistence. Every checkpointed run passes `config={"configurable": {"thread_id": ...}}`; no thread_id means no persistence, no resume, nothing to load after an interrupt.
- Design IDs deliberately — the string is your data model:
  - `user:<uid>:session:<sid>` — assistants; memory follows the user, history per session.
  - `task:<tid>` — workflows; isolation is the point.
  - `user:<uid>:thread:<ulid>` — durable conversations users return to days later.
- Anti-patterns: raw `"1"` (tutorial default, fatal multi-tenant), un-hashed emails (PII as primary key, exposed in logs), IDs longer than 255 chars (documented PostgresSaver failure).
- Never put different tenants in one thread — state bleed across customers is a security incident, not a bug. Do not overload one thread with unrelated tasks — checkpoint growth makes `get_state_history` useless.
- `checkpoint_ns` partitions checkpoints per subgraph invocation (`""` at root, `team:uuid` per subgraph, joined with `|` when nested). Subgraph state is scoped per invocation; `get_state(config, subgraphs=True)` exposes subgraph state via `tasks[].state` — how you debug which agent of a team holds what. Two parallel calls to the same per-thread subgraph can collide in the same namespace.

## Checkpoint bloat

Every super-step stores a full snapshot of every channel: a 100-turn thread with a 100k-token `messages` channel stores ~100 increasingly large snapshots — gigabytes, with resumes and history queries getting slower every turn. Mitigations in order: (1) shrink channels (bulk data out of state — biggest lever, free); (2) prune old checkpoints with a retention cron (deleting checkpoints older than N days is the documented fix); (3) summarize/trim the messages channel; (4) `DeltaChannel` (1.2+ beta) stores per-step sentinels and reconstructs values by replaying writes — promising but changes checkpoint semantics; adopt deliberately.

## Time travel — the four operations

Mental model: checkpoints are commits, `thread_id` is the branch, `update_state` forks, `get_state_history` is the log. History is append-only — replay and fork never modify the past.

```python
# 1. INSPECT — read the latest, or the whole chain
latest = graph.get_state(config)                 # StateSnapshot
chain = list(graph.get_state_history(config))    # newest first

# 2. REPLAY — re-execute from a past checkpoint
past = next(s for s in chain if s.next == ("bad_node",))
graph.invoke(None, past.config)                  # re-runs from bad_node onward

# 3. FORK — branch from the past with corrected state
fork_cfg = graph.update_state(past.config, {"topic": "corrected"})
graph.invoke(None, fork_cfg)                     # continues on the branch

# 4. UPDATE-LIVE — amend the present
graph.update_state(latest.config, {"status": "rejected"}, as_node="review_node")
```

Semantics and invariants:

- Replay re-executes for real — model calls happen again (re-buys tokens), tools run again, interrupts re-fire. It is a re-run, not a recorded playback. Uses: debugging from exact prior state; retry-from-midpoint after a transient failure in node K.
- Fork creates a new checkpoint branching from a past point; the original history is untouched. This is the operator's "fix the wrong value and continue" and the evaluator's what-if branch.
- Update-live rewrites the current checkpoint; `as_node` attributes the edit to a node, so execution resumes from that node's successors — use it to skip a node ("pretend node_b ran") or re-enter before one.
- Never point replay at tools that send money or emails — the checkpointer cannot un-send the first email. Gate side-effect tools behind an environment flag or make them idempotent with idempotency keys.
- Forked threads diverge; track your branches.

## Custom checkpointers

Write one only for an org-mandated database, compliance/retention logic (per-tenant expiry, legal holds), or exotic scale — for most teams, `PostgresSaver` plus a pruning cron beats any custom storage engine.

`BaseCheckpointSaver` requires (sync + async variants):

| Method | Purpose |
|--------|---------|
| `put` | Store a checkpoint; return config with the new checkpoint_id |
| `put_writes` | Store per-task writes linked to a checkpoint |
| `get_tuple` | Fetch checkpoint + pending writes + parent config |
| `list` | History with before/limit |
| `delete_thread` | Remove a thread's chain |

Correctness landmines: `checkpoint_id` lookups must be O(1) direct fetches (time travel and delta reconstruction depend on by-ID reads); never strip unknown metadata keys (LangGraph adds fields in minor releases); validate with the `langgraph-checkpoint-conformance` suite in CI before trusting real threads.

## Encryption at rest

`EncryptedSerializer.from_pycryptodome_aes()` transparently AES-encrypts persisted state (key from the `LANGGRAPH_AES_KEY` env var); reads decrypt transparently — nodes never see ciphertext. It protects at-rest data from DB leaks, nothing else: any node reads decrypted state, so secrets still do not belong in state; losing the key loses every thread's history. Minimization first, encryption as backstop; retention pruning is a compliance feature. (PII policy design belongs to agent-guardrails-builder.)
