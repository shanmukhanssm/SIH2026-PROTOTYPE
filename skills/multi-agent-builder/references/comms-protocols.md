# Inter-Agent Communication Protocols — Shared State, Messaging, Event Bus, A2A, MCP

**Load this when:** deciding how agents exchange data (shared state vs message passing vs event bus), designing message envelopes, or integrating across process/org boundaries (A2A, MCP, an agent bus).

## 1. Why Shared State Stops Working

Inside one LangGraph application, agents communicate the lazy way: they mutate shared state. It works because LangGraph guarantees ordering (nodes execute in graph order, state is checkpointed after each super-step, every node sees a consistent snapshot). Four failure modes emerge as the system grows:

1. **Implicit coupling** — Node B must know the exact key Node A writes; the contract lives in nobody's code. Rename the key and B silently reads None — discovered by the LLM, which hallucinates a fallback answer.
2. **No delivery semantics** — "did A write before B read?" holds only by trusting graph order; one parallel branch and the ordering assumption quietly dies.
3. **No history** — state is a snapshot, not a log; you cannot see the sequence of intents that produced a decision.
4. **No independent evolution** — two teams cannot version a shared dict; every consumer deploys in lockstep with every producer.

Shared state also fails completely the moment an agent moves out of process — you cannot share a Python dict over HTTP.

**The incident shape to remember:** a two-agent "research and report" app ran fine for months until a parallel branch was added. The reducer for `research_notes` was never specified — default replace, not concatenate — so the writer got only the last source's notes; and the reviewer read a stale draft because the super-step timing changed. Nothing crashed, no error surfaced; a customer found the thin report. Silent wrongness is the worst failure class. Every mechanism below converts that kind of incident into an explicit, observable failure.

**Pro tip:** treat LangGraph state keys as memory, never as a message queue. If two agents communicate by appending to a list that both also read for reasoning, the list becomes a chaotic shared notepad — half-written reasoning re-read as input, prompts bloated with self-referential cruft. Private state namespace per agent; explicit, small, typed payloads across node boundaries.

## 2. Three Topologies Compared

| | Shared State (LangGraph) | Direct Message Passing | Event Bus (Pub/Sub) |
|---|---|---|---|
| Coupling | Tight (key names, types) | Loose (envelope schema) | Loosest (topics) |
| Latency | Zero (in-process) | 1-10ms in-process, 10-100ms over HTTP | 1-100ms + broker overhead |
| Delivery | Guaranteed by graph | At-least-once with ack | Depends on broker config |
| Debugging | Checkpoint replay | Message logs | Message logs + topic tap |
| Fan-out | Manual (parallel branches) | Manual | Built-in (N subscribers) |
| Ordering | Graph order | Per-pair FIFO | Per-topic, per-partition |
| New consumers | Code change + redeploy | Code change + redeploy | Subscribe, no producer change |
| Ops cost | None | None | Run a broker (Redis Streams, Kafka, NATS) |

Decision rules: shared state wins when everything is one process, one graph, one deploy, and one-or-two writers per key (~90% of LangGraph apps — do not add infrastructure you don't need). Message passing wins the moment a boundary appears: service-to-service, Python-to-Node, your-team-to-their-team, or when you need a durable audit log of intents. Event bus wins with dynamic topology: N consumers that come and go, fan-out to unknown future consumers, or load-leveling between a bursty producer and many slow consumers.

**Pro tip:** the most expensive decision in multi-agent systems is adopting Kafka "because we'll need it at scale." An agent turning over in seconds means a handful of messages per second per user — Redis Streams or even Postgres SKIP LOCKED queues handle it at a thousandth of the operational cost. Choose the bus for fan-out and replay, not imagined throughput.

### The Communication Decision Tree

```text
Do the agents share a process and a deploy?
  |-- YES: shared state (LangGraph). Done.
  |-- NO: do they share a trust domain (same org/team)?
        |-- YES: do you need fan-out or unknown future consumers?
        |       |-- NO:  direct messages (HTTP + envelope),
        |       |        log to Postgres for replay. Done.
        |       +-- YES: event bus (Postgres queue / Redis Streams /
        |                Kafka at scale).
        +-- NO (different orgs/vendors):
                |-- is the other agent opaque by requirement?
                |      |-- YES: A2A (agent cards, tasks, artifacts).
                |      +-- NO:  direct messages with negotiated
                |               envelopes + shared correlation IDs.
```

Every "NO" branch adds infrastructure — a real cost you should be able to name. If you cannot articulate what the added message layer buys you, the tree sends you back to the top.

## 3. The Envelope

Every message between agents carries an envelope — a structured header that turns an opaque blob into a traceable, versioned, debuggable unit:

```text
Envelope:
  envelope_id: UUID            # unique per message
  correlation_id: UUID         # same for all messages in one logical task
  sender: "agent.researcher.v2"
  receiver: "agent.writer.v1"  # or a topic like "events.reports.ready"
  type: "report.ready"         # machine-readable, namespaced
  schema_version: 3            # what payload layout follows
  sent_at: 2026-08-12T14:03:11Z # sender clock; NEVER trust as ordering
  idempotency_key: string      # for retries on side-effectful consumers
  payload:
    # schema-versioned body; a frozen dataclass / JSON Schema
```

| Field | What breaks without it |
|-------|------------------------|
| envelope_id | Cannot dedupe retries or reference "that message" in a bug report |
| correlation_id | Cannot slice the log by user task; a 40-agent fan-out is undebuggable soup |
| sender / receiver | Cannot filter logs per agent or find "who sent this malformed payload" |
| type | Consumers parse free-form payloads; every new type breaks every consumer |
| schema_version | Producer upgrades silently; consumers parse v3 fields as v2 and corrupt data |
| sent_at | No latency measurement per hop (never use it to order — clocks skew) |
| idempotency_key | Retries double-execute side effects (the classic double-charge) |
| ttl (optional, add it) | Stale messages ("investigate the current outage") get executed against a changed world |

Minimal forward-compatible handler: dispatch on `type`, ignore unknown types; if `schema_version` exceeds what you support, ignore or route to an upgrade queue — do not guess.

## 4. Message Design Patterns: Commands, Events, Queries

| Pattern | Example | Contract |
|---------|---------|----------|
| Command | `cmd.review.requested` | Directed at one agent, expects ack/result; needs a deadline + idempotency because the receiver acts (twice if retried naively) |
| Event | `evt.report.ready` | Past-tense fact, broadcast, no recipient expectations, self-contained because any consumer may join late |
| Query | `qry.refund.status` | Request for information, expects a reply, no side effects; safe to retry and cache |

Mismatch incidents: treating an event as a command (a consumer must act, nothing guarantees delivery or tracks completion); treating a query as a command (the queried agent takes an irreversible action "because it was asked"); commands without deadlines (the classic deadlock). Label the pattern in the type namespace — `cmd.`, `evt.`, `qry.` — so the semantics travel with the schema.

## 5. Delivery Guarantees

- **At-most-once:** send once, no retry, loss acceptable — telemetry, logs, suggestions.
- **At-least-once:** retry until acknowledged; requires idempotent consumers. The default for real work (email, DB writes, report generation).
- **Exactly-once:** not achievable end-to-end. What people call exactly-once is at-least-once plus idempotency — "deduplication under a better brand name."

Effectively-once consumer: dedupe on `idempotency_key` with a persistence window (72 hours covers every realistic retry, DLQ replay, and manual re-drive; forever-storage grows unboundedly for zero benefit). The key must be generated by **deterministic code** (e.g., user + receipt id), not by the model and not a random UUID — a retry of the same logical operation must produce the same key. Never implement retries without idempotency: the LLM may even rephrase the retried side effect ("send again, politely this time").

## 6. Ordering

Prefer unordered, idempotent, self-contained messages: make each message carry the full context it needs instead of referring to earlier messages — this makes ordering irrelevant, the single best ordering trick in the book. LLM agents tolerate disorder better than databases; exploit it.

If ordering is unavoidable: per-pair FIFO (a queue per sender-receiver pair — enough for almost everything), or a monotonic `seq_no` per (sender, receiver) with consumer-side buffering and resend-on-gap. Total order across agents is almost never needed; when someone asks for it, they are usually describing a database.

## 7. Backpressure

The fundamental dynamic: a fast producer talking to a slow consumer (LLM calls are seconds per turn). An unbounded queue against a 0.2/sec consumer fed at 50/sec grows ~175,000 items/hour, then memory death.

| Strategy | Mechanism | When | Failure mode |
|----------|-----------|------|--------------|
| Drop | Bounded queue, discard oldest/newest | Status updates, metrics, retryable work | Silently lost data — log the drops |
| Block | Bounded queue, producer blocks | Correctness matters, low latency | Cascading stalls up the chain |
| Degrade | Consumer sheds load: batch, summarize, "come back later" | Expensive per-message work | Quality loss, delayed results |

Options for the 50/sec → 0.2/sec shape: parallelize the consumer with N workers; coalesce (drain every 60s, one summarization call instead of 300 — cost drops 300x, backlog bounded); drop-with-stats for advisory notes (keep last value per key, count drops, alert when drop rate crosses a threshold — dropping 60% of notes means the pipeline is lying to the downstream agent).

**Warning:** backpressure that silently drops messages converts a performance problem into a correctness problem with no visible symptom. Every drop path needs a counter, and every counter needs an alert.

### Token-Level Flow Control
An agent's context window is a bounded queue; every tool result is a producer pushing items into it. A 200KB tool result in a small window silently evicts the instructions — the model continues fluently, acting without its instructions ("confident amnesia" — looks like a reasoning failure, gets debugged as a prompt problem for days). Telltale: late-run behavior contradicts the system prompt, and end-of-run token counts are dominated by tool results. Fixes: compress at ingestion (tool wrapper summarizes before injecting), truncate with structure (keep the head + a pointer, fetch more on demand), budget the window explicitly (reserve N tokens for instructions, cap tool results at the remainder).

## 8. State Divergence: Messages vs State

The message says "approved," the state's review key says "pending" — because the state update crashed after the message published, or vice versa. The fix is a rule, not a technology: **state is the system of record; messages are notifications of intent.** Design each message payload to include a state reference (like a report_id) so consumers re-read the truth rather than trusting the notification. After such a bug, never "make messages authoritative too" — two authoritative copies drift, doubling every write path and consistency question.

## 9. A2A Protocol (Agent2Agent) — Cross-Vendor Agent Interop

Open protocol for communication between **opaque** agents (no shared code, memory, tools, or framework). Announced by Google April 2025, donated to the Linux Foundation, v1.0. HTTP(S) with JSON-RPC 2.0 payloads; SDKs in Python, JavaScript, Java, .NET, Go, Rust. Design insight: MCP standardizes agent-to-tool; A2A standardizes agent-to-agent — complementary, not competing.

Core concepts:
- **Agent Card** — JSON at a well-known URL: name, description, URL, capabilities (streaming, push notifications), skills (typed, with input/output modes), auth requirements, extensions. Clients fetch it first and decide whether the agent can do the job.
- **Task** — stateful unit of work with an ID and lifecycle: `submitted → working → completed | failed | canceled | rejected`, plus `input-required` (the remote agent asks you a question mid-task — a client that treats input-required as an error hangs the task forever; it's also where cross-org HITL naturally lives).
- **Message / Part / Artifact / Context** — one turn (role user/agent) made of Parts (exactly one of text, file, or data); Artifacts are named deliverables built from Parts; contextId groups related tasks into one conversation.

Interaction patterns: request/response (poll with tasks/send + tasks/get), streaming (SSE via tasks/sendSubscribe), push notifications (client webhook; best-effort at spec level — always keep a polling fallback, and note webhooks need ingress, so behind NAT/firewalls use SSE or polling).

**Adopt A2A when:** two+ orgs (or hard team boundaries) must cooperate without sharing internals; interop across frameworks by contract; discovery from Agent Cards; long-running tasks across trust domains with structured artifacts. **Do NOT adopt when:** agents share one process and one state dict (adds HTTP/JSON-RPC/polling to something LangGraph state does natively — the most common over-adoption), for sub-agent control inside one application (use LangGraph subgraphs or function calls — A2A explicitly does not do sub-agent or tool-call protocols), or for human-facing chat UI messaging.

A2A failure modes: **agent card drift** (card says v2, deploy is v1 — treat card content as code: version it, deploy it with the service, verify claims with a tiny probe task on onboarding); **push webhook reliability** (fire-and-forget; run a polling safety net); **no standardized auth enforcement** (federation means real identity work — mTLS, OAuth2 client credentials, scoped tokens).

**LangGraph mapping:** expose a LangGraph agent through an A2A server by mapping one A2A task to one LangGraph thread — you inherit time-travel debugging, interrupt/resume, and state migration; the polling loop becomes "poll = threads.get + latest state." In federation, wrap every remote task in your own local task record (timeout, retry policy, fallback path) before integrating the remote endpoint, and assume the remote agent is at best 95% reliable. Negotiation sequence: check protocolVersion, required skill, input/output modes, auth scheme; send a bounded probe task; cache the result with a TTL and re-negotiate when the remote card version changes.

## 10. MCP in Multi-Agent Context

MCP is Anthropic's open protocol for connecting AI applications to context and tools (announced Nov 2024). Three participants: **Host** (your application — one host, many clients), **Client** (one per server connection), **Server** (exposes primitives, locally via stdio or remotely via streamable HTTP).

| | stdio | Streamable HTTP |
|---|---|---|
| Model | Subprocess spawned by client, JSON-RPC over stdin/stdout | HTTP POST client-to-server; SSE for server-to-client streams |
| Best for | Local tools (filesystem, git, local DB) | Remote/cloud services, multi-tenant servers |
| Latency | Near zero | Network RTT + HTTP overhead |
| Scaling | One client per process | Many clients per server (stateless) |
| Auth | None (local trust) | Bearer tokens, API keys, OAuth recommended |
| Lifecycle | Tied to host process | Long-lived service |

Server primitives: **Tools** (executable actions, name + description + JSON Schema inputSchema — the description is prompt engineering: vague or over-claiming descriptions cause tool misselection, the dominant MCP failure class), **Resources** (read-only data addressed by URI, with templates for parameterized access), **Prompts** (reusable server-side templates — treat as optional sugar, not load-bearing). Client-side: notifications (opt-in, best-effort — do not build correctness on them).

**Spec-version warning:** MCP moved fast — the June 2025 revision replaced the HTTP+SSE transport with Streamable HTTP; current spec uses `server/discover` with per-request version negotiation; sampling is deprecated. Old tutorials using `initialize`/`initialized` handshakes and SSE GET streams will fail against current servers ("server never responds to initialize"). Check the protocol version on any snippet older than 2025.

Security: **tool poisoning** (a benign-looking tool whose implementation exfiltrates — tool descriptions and behavior are decoupled), **server trust asymmetry** (treat every remote MCP server as untrusted code: least privilege, sandboxing, egress allowlists, pin versions, audit tools/list diffs), **prompt injection via tool results** (treat tool results as data, never instructions; gate irreversible actions behind human approval), **data exfiltration** (any tool call can send context out — deny-by-default egress policy). Never pass secrets as tool arguments.

**The silent tool failure trap:** a server's credential expires, `tools/call` returns an error result, and the LLM — receiving the error as tool output — treats it as data and improvises ("0 in stock" taken as fact). Client-side tool wrappers must check for protocol-level errors and raise a retryable exception instead of feeding error text to the model unfiltered; prefix with `TOOL_ERROR:` so the model knows it's an error, not an answer.

**When MCP beats plain function tools:** one tool surface across multiple hosts; tools owned/versioned by a different team; remote multi-tenant tool hosting with its own auth; contract-first opaque integration. **Overkill when:** a few Python functions in the same repo called by one application — plain tools have zero protocol overhead and full type checking. Default to plain tools; adopt MCP when a second consumer or an external boundary appears.

## 11. Interop Decision Table

| Protocol | Solves | Mechanism | Use in YOUR system when |
|----------|--------|-----------|------------------------|
| MCP | How a model talks to tools and data | Client-server: tool/resource/prompt servers | Standardizing your agents' tool layer across vendors and internal services |
| A2A | How agents talk to agents across orgs/vendors | Agent cards + task/message primitives over HTTP/SSE | Your agent needs to call an external vendor's agent as a capability |
| In-app transfer (Command) | In-app agent-to-agent control | transfer_to_* tools + state | Internal multi-agent conversations — you don't need a standard for this |

MCP and A2A are complementary and neither replaces your internal architecture. A LangGraph topology is internal control flow, invisible to outside agents — that's fine. Adopt standards at the edge: expose your agent through A2A for partners, consume MCP tool servers for third-party tools, keep the interior on the internal patterns. Standards are for interop, not design quality: a badly-designed internal topology doesn't improve by speaking A2A. The failure pattern to avoid: re-architecting internal control flow into A2A tasks "for future-proofing" — you inherit the protocol's abstraction costs to solve a problem you didn't have.

## 12. Building Your Own Agent Bus

Roll your own when messages stay inside your organization, you need full control over the envelope and replay, and A2A's opacity is a drawback (you want richer internal state than A2A allows).

```python
class AgentBus:
    def publish(self, envelope): ...        # append to log/stream/table
    def subscribe(self, type_filter, handler):
        # consumer group: ack after handler succeeds; retry with backoff;
        # dead-letter after N failures
        ...
    def replay(self, correlation_id):
        # fetch all envelopes for a task in order -> debug and replay
        ...
```

| | Postgres (SKIP LOCKED) | Redis Streams | Kafka | NATS JetStream |
|---|---|---|---|---|
| Comfortable throughput | ~1-5k msg/s | ~50-100k msg/s | ~100k+ msg/s per topic | ~10-100k msg/s |
| Consumer groups | DIY (SELECT ... FOR UPDATE SKIP LOCKED) | Built-in | Built-in | Built-in |
| Replay | Full (it's a table) | Bounded (retention) | Full (log retention) | Bounded by storage |
| Ops burden | None (your DB) | Low | High (clusters, rebalancing) | Low-mid |
| Best when | Already have Postgres, <5k msg/s | In-memory speed, modest retention | Multi-team replayable logs | Lightweight delivery guarantees |

Postgres is the boring right answer: an atomic, transactional queue where the query is the consumer group, a DLQ is another table, and replay is a SELECT. Agents consume at LLM speeds, not database speeds — its ceiling covers nearly every agent bus on earth. Adopt Kafka only when multiple teams consume the same streams, retention-by-terabytes matters, or you genuinely push six figures of messages per second.

Design decisions you will re-make:
- **Schema registry vs schemas-in-code:** one language, one repo → code; multiple languages/teams with independent deploys → a registry (or at minimum JSON Schema files in a shared repo with CI validation).
- **The DLQ contract:** dead-letter after N failures — then the DLQ has owners, alerting, and a weekly drain ritual (inspect, fix handler, re-drive by correlation ID). A DLQ nobody reads is a message-silent-loss device with a reassuring name.
- **Message log retention and privacy:** the log is gold for debugging and radioactive for privacy — user content, tool outputs, decisions. Set retention windows, restrict access; the replay endpoint is gated like a database of user conversations, because it is one.

**Build the replay endpoint on day one.** `replay(correlation_id)` turns every production incident from "we think the orchestrator sent X" into a two-minute investigation, and enables the most powerful agent-debugging move: replaying the same messages into a fixed version of the agent to isolate model drift from logic bugs.

Bus security checklist: service identity, not just network position (mTLS or signed tokens); least-privilege per consumer — capability-scoped credentials per (sender, receiver, type) triple, not per service; egress/ingress allowlists on the bus so a compromised service cannot spoof the orchestrator's message types; secrets never in envelopes (payloads get logged, replayed, dead-lettered — use references, not credentials); the audit log is security-relevant (append-only where possible, access-controlled always).

**When the bus is the wrong answer:** a bus inside a LangGraph application ("agents communicate via Redis Streams" where a graph edge would do) costs latency (every hop crosses serialization + a broker), lost atomicity (transactional checkpoints replaced by message-level semantics), and debugging difficulty (state and messages diverge). The bus earns its keep only across deploy or trust boundaries. If both agents restart together, checkpoint together, and get traced together, they should communicate through state, together.

## 13. Testing the Communication Layer

- **Contract tests per message type:** the producer serializes a sample envelope and asserts the schema; the consumer deserializes the same fixtures; share fixtures through a versioned schema package — when the producer bumps schema_version, the consumer's contract tests fail in CI, not in production.
- **Simulation harness — one fake peer, five behaviors:** (a) replies correctly, (b) replies late, (c) replies with the wrong schema version, (d) never replies, (e) floods. The never-replies case finds deadlock bugs; the floods case finds missing backpressure.
- **Replay tests:** capture a production conversation, re-run it through a changed agent version, assert on the outcomes that mattered — the regression suite for inter-agent behavior.
- **Property-based envelope fuzzing:** thousands of valid-but-weird envelopes (huge payloads, unicode, missing optional fields, schema_version=999); assert the consumer degrades gracefully — logs and ignores, or rejects, but never crashes.

If you remember one sentence: communication failures in agent systems are diagnosed, not guessed — but only if the messages exist to be read. Every envelope field, every log line, and every replay endpoint exists so the evidence is there when you need it.
