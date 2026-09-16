# Multi-Agent Failure Modes, Sizing, and Anti-Patterns

**Load this when:** adding multi-agent failure detection, sizing agent count, or reviewing an architecture for anti-patterns before or after the build.

Multi-agent failures live between agents, in the seams. A system can score 100% on per-agent evals and still be broken — the evals must target the seams too.

## 1. The Seven Canonical Failures

| # | Failure | Root cause | Detection | Prevention |
|---|---------|-----------|-----------|------------|
| 1 | Infinite ping-pong | No progress signal in state; supervisor bounces between workers, or two handoff agents bounce each other | Search traces for alternating agent names; distinct-agent-visits metric per task | Done-tracker + transfer caps + "no immediate return" rule |
| 2 | Lost context across handoffs | Each transfer drops what wasn't explicitly passed; after the transfer it's unrecoverable | Receiver-can-answer test: can the receiver answer questions the sender could? Post-handoff eval | Mandatory handoff payload schema (status, artifacts by reference, constraints, open question) |
| 3 | Duplicated work | Two agents research the same sub-question — network races, or supervisor forgot what was dispatched | Shared task registry check; distinct-agent metric spikes | Append-only task registry in state: dispatched/completed log every agent reads before starting work |
| 4 | Token blowup | Super-linear growth from every agent re-reading the full transcript (network) or every supervisor round re-sending everything | Tokens-per-hop golden signal; transcript growth per round | Context engineering per agent: each agent gets exactly what it needs, nothing more |
| 5 | Deadlocks | Agent A waits for B's output while B waits for A's — possible when handoff tools call the target synchronously, or in cyclic Send graphs | Silent waits (look healthy in dashboards); response-deadline expiries | Never call an agent from inside a tool synchronously — delegate via state plus routing; DAGs with explicit completion edges; explicit response deadlines |
| 6 | Partial failures | One worker of a parallel fan-out fails; the merge node receives some results | Merge-node logs with expected-vs-received counts | Merge logic handles missing results explicitly — fail the subtask, retry with a cap, or proceed with a flag. Never silently drop. (Checkpoint pending-writes from completed siblings are saved and not re-run on resume.) |
| 7 | Supervisor as knowledge bottleneck | Everything the top needs must fit the supervisor's context; deep evidence flattens to summaries at each level | Boundary summary fidelity check: does the parent's summary match the team's actual conclusions? | Keep evidence in state channels; give the supervisor references plus conclusions; a `read_detail(ref)` tool beats a bigger summary |

**Warning — the most expensive bug in production multi-agent systems:** ping-pong looks healthy in monitoring. Every call succeeds, latency is stable, and the bill just quietly triples. Add one metric — distinct-agent-visits per task — and alert when it exceeds a threshold. This single metric catches ping-pong, duplicated work, and most supervisor failures.

## 2. Observability: What You Lose with More Agents

In a single agent, one trace contains everything. Multi-agent splits the causal chain; rebuild it with five things:

1. **Nested traces** — LangSmith/LangGraph traces nest subgraph runs under their parent run; the trace tree mirrors the agent tree. Read stream_events namespace paths as "which agent am I looking at."
2. **Per-agent cost attribution** — tag each run with agent identity; accumulate tokens and cost per agent. The supervisor-vs-workers cost split is the first number management asks for and the one teams can rarely produce.
3. **The distinct-agent metric** — agent-visits per task, alerting on ping-pong.
4. **Handoff logs** — every transfer with payload-hash, so "what did agent B receive from A" is answerable retroactively.
5. **Checkpoint namespaces** — the durable record of "which agent knew what when," queryable via `get_state(config, subgraphs=True)` even after the run finished.

### Debugging Toolkit (inter-agent systems)
- **Correlation-ID timelines:** every publish and every log line carries the correlation_id; one query produces the full cross-agent timeline of one user task.
- **Conversation replay:** LLMs are non-deterministic — replaying inputs does not reproduce outputs. Either record outputs and replay as fixtures (deterministic simulation of "what did the system do"), or pin temperature=0 / mock the LLM nodes with recorded responses to isolate control flow from model behavior.
- **Checkpoint diffing:** when the same task passed yesterday and fails today, diff the checkpoints at each node — catches the "silently changed a key" class instantly.
- **Golden signals per correlation ID:** duration per hop, tokens per hop, total messages. Pathologies: hop-count explosion (ping-pong / re-prompt loops), token bloat (full-history re-reads), single-agent latency outliers (tool retries stacking).

Walkthrough pattern for "wrong output" incidents: (1) slice by correlation ID — is anything missing? (2) check envelope payloads — was the message right? (3) deterministic replay — was the agent right given its inputs? (4) diff checkpoints — who mutated state after the fact? Each tool isolates a different class: transmission, payload, model behavior, state mutation.

## 3. Sizing: How Many Agents Is Too Many?

| Constraint | Bound | Consequence |
|------------|-------|-------------|
| Tool-selection ceiling | ~10-20 tools per agent | Beyond this, selection errors dominate — the real reason to split |
| Context ceiling per agent | One prompt + history + results budget per agent | N agents = N context budgets to design and N summaries to maintain |
| Latency floor | ~10-15s per-turn interactive budget; ~3 serial hops max | Count your hops; beyond 3 the system is too deep for conversation |
| Ownership map | Every agent has an owner, on-call, eval suite | An agent nobody owns is an unmaintained liability |
| Debugging depth | ~7-10 agents max in one interactive system | Past this, incidents become multi-day archaeology |

**Sizing procedure:** start from the single-agent baseline. Split only when a measurable limit binds: tool-selection error rate rising (split by tool cluster), context-window pressure (isolate the big domain), parallelism demanded by latency (fan-out workers), or ownership boundaries (org map). Stop splitting when the next split wouldn't change any of these numbers. "As few agents as the constraints allow" is the instinct; the constraints are measurable, so the instinct can be checked.

### Smell-Test Table for Over-Engineered Agent Counts

| Smell | Diagnosis |
|-------|-----------|
| Two agents share >80% of tools and prompt | One agent, two names — merge |
| An agent is invoked by exactly one other agent, always | Submerge it as a node/tool of its caller |
| An agent has fewer than 2 tools | It's a prompt with a name — inline it |
| Supervisor routes to one worker >90% of traffic | Router + default, not supervisor |
| Every agent speaks in every task | You built a committee, not a division of labor |
| Depth 3+ in the agent tree | Decomposition error, not an org chart |

**Pro tip:** keep a "deleted agents" list next to your architecture docs. Every agent you remove — merged, inlined, or replaced by a rule — gets a dated entry with the reason. Architectures that never shrink are the ones that eventually drown their teams; the list makes shrinkage a normal, visible activity instead of an admission of failure.

## 4. Anti-Pattern Catalog

| Anti-pattern | Symptom | Fix |
|--------------|---------|-----|
| The agent-as-if-statement | An "agent" whose behavior is one deterministic branch | Replace with a router function — it added a model call and zero capability |
| Echo-chamber agents | Two agents validate each other with no external ground truth; errors survive shared blind spots | Reviewer needs independent evidence (tests, sources, tools the generator didn't use) or a human |
| Token laundering | Full transcript passed between agents "so nothing is lost"; the budget is what's lost | The handoff payload schema — briefing, not transcript |
| The shadow supervisor | A supervisor exists but a downstream rule overrides its routing | One authority per decision; delete the other |
| Capability Christmas trees | Every agent gets every tool "just in case"; blast radius grows, selection degrades | Least-privilege tool sets per agent |
| The handoff daisy chain | A → B → C → D for a task any one could do — four hops of context risk and latency | Hop budgets + a "can you answer directly?" rule in every agent's prompt |
| State couriers | Agents that only move data between two others — no transformation, no decision | Share the channel directly; a courier is a seam with no contract |
| Parallel-identical workers | N copies of the same agent, no differentiation | Distinct slice or distinct rubric per worker — or run one and buy a better model |
| The recursive delegate | Sub-agents calling sub-agents unboundedly; depth 3+ = cost + trace spaghetti | Depth cap at the framework level; flatten to one level |
| Approval theatre | Approvals whose payloads show nothing real; agents "approve" by convention | Approvals only where irreversibility begins, with real payloads |
| The agent-per-team-mirror | Agents mapped 1:1 to the org chart regardless of task structure | Design from task decomposition; ownership maps onto whatever shape results |
| Architecture-driven eval | Evals measure the architecture's virtues (routing accuracy) instead of user outcomes | Outcome evals first; architecture metrics are diagnostics, not targets |

### Inter-Agent Communication Anti-Patterns

| Anti-pattern | Symptom | Fix |
|--------------|---------|-----|
| The telephone game | Outputs drift from the source over 3+ hops; facts untraceable | Pass raw source references with each message; forbid "re-explain in your own words" at internal hops |
| The whisper inbox | Messages read by nobody; backlog grows; work silently never happens | Consumer liveness monitoring; DLQ alerts; message-age dashboards |
| The eager assistant | Actions on 20-minute-old context; double bookings | Freshness contracts: TTLs on context; re-fetch before irreversible actions |
| The ghost sender | Messages from deleted/renamed agents still flowing | Lifecycle discipline: deregister on shutdown; heartbeat liveness per agent |
| The context firehose | One agent forwards everything "just in case"; the consumer drowns | Payload minimalism: references and summaries; the consumer fetches what it needs |
| The trust cascade | B trusts A's conclusions unconditionally; A's one mistake poisons downstream | Trust levels per source; verification before irreversible downstream actions |
| The ping-pong loop | Hop count explodes; cost spikes; no convergence | Convergence criteria: max hops, diminishing-returns check, or an arbiter with authority |

## 5. Evaluating the System (Not Just the Agents)

| Layer | What it tests | Method |
|-------|--------------|--------|
| Per-agent unit | Each agent's task success in isolation | Reuse the single-agent harness per agent |
| Seam/contract | What crosses each boundary | Contract tests: schema + payload-hash + content assertions |
| Routing | Did the request reach the right agent? | Labeled traffic: route accuracy per class, misroute cost |
| End-to-end | Full task success + termination + cost | Judge-based scoring + human sampling |
| Failure injection | Ping-pong, lost context, partial worker failure | Deterministic fault injection: force the failure, assert recovery |

Judge-based end-to-end rubric (run on isolated threads — `thread_id = "eval:{scenario.id}"`; eval runs sharing production threads corrupt both):

```python
def evaluate(scenario, system, judge_model):
    config = {"configurable": {"thread_id": f"eval:{scenario.id}"}}
    result = system.invoke(scenario.input, config)
    verdict = judge_model.invoke(
        "Score this multi-agent run against the scenario rubric: "
        "task completed? (0-3) correct agent involvement? (0-2) "
        "context preserved across handoffs? (0-2) terminated properly? (0-1) "
        "cost within 1.5x predicted calls? (0-1)",
        run_trace=result, rubric=scenario.rubric)
    return {"scenario": scenario.id, "scores": verdict, "calls": count_calls(result)}
```

A judge that only scores "was the answer right" teaches you nothing about the architecture. Metrics per architecture: network — transcript growth per round, agent-visit counts; supervisor — routing accuracy, rounds per task, done-tracker coverage; hierarchical — boundary summary fidelity, depth-penalty latency; handoff — briefing completeness via the receiver-can-answer test. Every architecture's canonical failure has a metric that would have caught it — instrument the metric for your architecture, not all of them. Sample 50-100 judge-scored runs monthly for human recalibration, targeted at the dimensions judges are worst at: nuance, tone, "did the user feel understood."

## 6. Architecture Review: 20 Questions

Ask before implementation, re-ask quarterly — every "we didn't think of that" in a multi-agent postmortem is one of these:

1. What is the single-agent baseline (calls, tokens, success rate) this design must beat?
2. Which of isolation, parallelism, or ownership justifies the split — or is it none of them?
3. Who decides the next speaker: an authority, the last speaker, or state?
4. What context does each agent see — full transcript, filtered slice, or isolated?
5. Where does the task end? Is FINISH checkable (a state field) or an opinion?
6. What happens when two agents write the same channel in one super-step?
7. What crosses each seam, in which direction, and is it trusted or treated as data?
8. What is the handoff payload schema, and does it include status, artifacts, and constraints?
9. How many serial hops per user turn, and does it fit the ~10-15s latency budget?
10. What is the cost driver for this architecture, and is it monitored?
11. Which agent owns termination? Which owns error recovery?
12. What does each agent do when its peer fails or vanishes (partial failure)?
13. Where do interrupts fire — at agent boundaries or at irreversibility?
14. What is the injection surface, and which boundaries are untrusted?
15. Which model runs each agent, and is the tiering evaled or assumed?
16. What is the ping-pong brake (hop caps, done-tracker, no-bounce rule)?
17. How does an incident responder reconstruct "who knew what when"?
18. What is the rollback path — can any one agent be routed around?
19. What would cause this architecture to be deleted next quarter?
20. Is the eval suite measuring user outcomes, or the architecture's own metrics?

## 7. Launch Checklist: 12 Gates

| # | Check |
|---|-------|
| 1 | The single-agent baseline is measured, and the split beats it on isolation/parallelism/ownership |
| 2 | Authority is explicit (supervisor, last-speaker, or state) — no emergent "nobody decides" |
| 3 | Every seam has a contract: what crosses, in which direction, trusted or data |
| 4 | Handoff payloads follow the schema (pair + status + artifacts + constraints + open question) |
| 5 | Termination: FINISH is checkable; hop caps and no-bounce rules exist |
| 6 | Every multi-writer channel has a reducer (or serial edges) |
| 7 | Ping-pong metric (distinct-agent-visits) charted and alerted |
| 8 | Cost attributed per agent; cost driver per architecture monitored |
| 9 | Injection surfaces mapped: delegate outputs untrusted, capability boundaries enforced |
| 10 | Subgraph persistence modes chosen deliberately (per-invocation vs per-thread) |
| 11 | Eval suite covers routing, handoff quality, termination, and end-to-end outcomes |
| 12 | Rollback: router config revert tested; each agent independently disableable |

A multi-agent system is N single agents plus seams, so its launch gate is the single-agent gate plus the seam gates (items 3, 4, 5, 7, 9, 10). Teams that launch with only the single-agent gates green are shipping seams untested — and the seams are where the failures live.
