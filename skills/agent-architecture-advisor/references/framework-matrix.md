**Load this when:** running Step 5 (framework selection) — the framework comparison matrix, day-60 test, selection questionnaire, scorecard method, and migration/hedging rules.

# Framework Matrix

## The Seven Dimensions That Matter

1. **Abstraction level** — how much the framework does vs what you write. Low (LangGraph core, Pydantic AI, smolagents): control, more code. High (CrewAI crews, AutoGen group chat, LangChain agents): fast start, fighting the framework once you outgrow it.
2. **Graph control** — can branching, looping, parallel execution, and conditional routing be expressed as deterministic, auditable structures? This single dimension predicts more day-60 pain than any other.
3. **State handling** — conversation state, cross-session memory, durable checkpointing with resume. If a process crashes at step 7 of 12 and the run cannot resume, you are running demos, not production agents.
4. **Human-in-the-loop** — interrupts, approvals, and resume as native primitives vs bolt-on.
5. **Observability** — first-party tracing/eval tooling vs "bring your own."
6. **Ecosystem** — integrations, docs, community, and critically the hiring pool.
7. **Production track record** — who actually runs this at scale.

Version-churn reality: framework comparisons are meaningless without version context (this matrix reflects the state at August 2026). Churn is a cost line, not a quality verdict — a stable core costs features; high velocity costs upgrades. Budget whichever cost you pick.

## Comparison Matrix (ratings 1-5 for production multi-agent systems)

| Framework | Abstraction | Graph control | State/durability | HITL | Observability | Ecosystem | Production track |
|---|---|---|---|---|---|---|---|
| LangGraph | Low | 5 | 5 | 5 | 4 | 5 | 5 |
| CrewAI | High | 2 | 3 | 3 | 3 | 4 | 3 |
| AutoGen (0.4+) / AG2 | Mid | 3 | 3 | 2 | 3 | 4 | 3 |
| OpenAI Agents SDK | Low-mid | 2 | 3 | 3 | 4 | 3 | 3 |
| Pydantic AI | Low | 3 | 3 | 3 | 4 | 4 | 3 |
| smolagents | Low | 1 | 1 | 1 | 1 | 3 | 2 |
| LlamaIndex Workflows | Low-mid | 4 | 3 | 2 | 3 | 4 | 3 |
| Google ADK | Mid | 4 | 4 | 3 | 4 | 3 | 3 |
| Claude Agent SDK | Mid | 2 | 3 | 4 | 3 | 3 | 3 |

How to read it: a rating of 1 does not mean "bad framework" — it answers "how much production infrastructure do you have to build yourself?" LangGraph's row of 5s means the framework handles the hard parts; smolagents' row of 1s means you build them, which is fine if that is what you signed up for.

## Per-Framework Verdicts

| Framework | Choose it for | The ceiling / cost |
|---|---|---|
| LangGraph | Durable stateful agents as explicit graphs: checkpoints per super-step, resume, time travel, interrupt() HITL, token/event streaming, thread + cross-thread memory | Learning curve (nodes, edges, reducers, checkpointers), boilerplate tax, LangChain cultural pull, fast iteration, commercial platform for the best deployment path |
| CrewAI | Fastest onboarding; role/goal/backstory metaphor; demos, internal tools, managed enterprise path | LLM-mediated manager routing is the most expensive and least reliable orchestration style; behavior hidden in prompt engineering; low abstraction ceiling |
| AutoGen / AG2 | Event-driven distributed actor runtime; research-scale fan-out; group chat for divergent brainstorming; .NET support | Concept-heavy (agents, runtimes, subscriptions, topics); async everywhere; split-brain ecosystem (0.2 vs 0.4 vs AG2); group chat inherits LLM-orchestration costs |
| OpenAI Agents SDK | Fastest path to a working agent with production defaults: handoffs, parallel guardrails, sessions, built-in tracing, sandbox agents | OpenAI gravity (Responses API, hosted tracing); no graph abstraction; no durable checkpoint/resume natively; young and churning |
| Pydantic AI | Type-safe end-to-end agents: typed deps, structured outputs with auto retry-on-validation-failure, model-agnostic, strong testing culture | Multi-agent orchestration and durable execution are newer and less battle-tested; no turnkey platform; fast-moving surface |
| smolagents | Radical simplicity (~1,000 lines); CodeAgent writes Python as its action language (loops and arithmetic collapse into one call); sandboxes via Modal/E2B/Docker | No durable checkpoints, no HITL machinery, no graph control, minimal observability; multi-agent basic; model-written code demands sandbox discipline from day one |
| LlamaIndex Workflows | Retrieval/RAG pipelines needing explicit async control flow: event-type-equals-edge, static validation, LlamaCloud deploy | Agent-native features (durability, HITL, tool loops) newer and shallower; retrieval-first assumptions; smaller agent mindshare |
| Google ADK | GCP shops needing polyglot agents (Python/TS/Go/Java/Kotlin), strong context management, A2A interop, one-command Cloud Run/GKE deploys | Younger graph/durability vs LangGraph; Google-ecosystem gravity |
| Claude Agent SDK | Coding agents specifically: the Claude Code loop as a library — file tools, shell, subagents, hooks, permissions, sessions, MCP | Purpose-built for coding tasks, not general multi-agent products; Anthropic API-centric |

## The Token Tax of LLM-Mediated Orchestration

CrewAI-style manager pattern, quantified: a manager re-reading the task and each worker output at every delegation turn costs roughly 2,000-4,000 tokens per delegation; a task one well-designed graph completes in ~15,000 tokens commonly runs 40,000-80,000 through a manager pattern (3-5x), plus serial-delegation latency and misroute surface. Keep the crew metaphor in the product layer; implement actual routing as explicit graph edges.

AutoGen group chat, quantified: with 5 agents in a round-robin over a 5,000-token shared context, a 10-turn brainstorm generates 50,000+ tokens of re-reads plus 10 speaker-selection calls — and no participant has authority to terminate. Group chat is a good pattern for divergent idea generation and an anti-pattern for convergent task execution. Know which one you are doing before choosing the topology.

## The Day-60 Test: What Breaks First

Every framework's strengths are visible in the quickstart; its weaknesses appear on day 60, when the product has grown past the tutorial.

| Framework | The day-60 failure you will meet |
|---|---|
| LangGraph | State-schema and reducer confusion across a growing team; checkpoint bloat from unpruned history; "which LangChain object do I use here" |
| CrewAI | The manager agent creatively re-routing your deterministic flow; prompt bloat hidden in role/backstory; major-version upgrades mid-product |
| AutoGen | Async/runtime concepts leaking everywhere; two API dialects in the team's heads; group-chat termination and cost control |
| OpenAI Agents SDK | Lock-in gravity on sessions/tracing; handoff chains becoming spaghetti (who owns state at depth 3?); no durable resume when the process dies |
| Pydantic AI | Capabilities/harness surface churning under a young project; reinventing durable multi-agent patterns others ship |
| smolagents | You are the production scaffolding team (persistence, HITL, observability); sandbox management is your job |
| LlamaIndex Workflows | Durability/HITL younger and shallower than LangGraph's; retrieval-heavy assumptions when your agent needs general tooling |
| Google ADK | Google-ecosystem gravity (deploy paths, model defaults); younger graph features |
| Claude Agent SDK | "Coding-agent-shaped" assumptions when the task is not coding; Anthropic-centric paths |

The pattern: high-abstraction frameworks break on control; low-abstraction frameworks break on you (the missing features are your code); vendor frameworks break on gravity. Choose the failure you would rather own — a team that wants deterministic audits should not pick manager-agent patterns.

Before committing, spend an hour in each candidate's GitHub issues filtered to your use case's keywords ("checkpoint," "interrupt," "stream," "multi-agent") — issue trackers are the most honest framework documentation that exists.

## The 8-Question Selection Questionnaire

1. What is the unit of work? A chat turn (OpenAI SDK, Pydantic AI, CrewAI), a durable multi-step process (LangGraph, ADK), or a retrieval pipeline (LlamaIndex)? The unit of work eliminates frameworks not designed for that shape.
2. Does a run need to survive restarts, deploys, and hours-long pauses? If yes, durable checkpoints and resume are non-negotiable — LangGraph first, ADK and Pydantic AI as challengers.
3. Who builds it? Systems-minded Python engineers: LangGraph, Pydantic AI. Speed-first product engineers: CrewAI, OpenAI SDK. .NET shop: AutoGen .NET. GCP shop: ADK. Research team: AutoGen core or smolagents. The framework must fit the team, not the other way around.
4. How much explicit control flow do you need? First-class, auditable branching/retry/parallel → LangGraph, ADK 2.0, LlamaIndex Workflows, Pydantic AI graphs. "The model decides" is acceptable → CrewAI, AutoGen group chat, OpenAI handoffs.
5. Where do evals and observability live? Already have Langfuse/OTEL → any framework. Want it bundled → LangSmith, Logfire, OpenAI dashboard, GCP.
6. How attached are you to a model vendor? OpenAI-only → Agents SDK maximizes advantage. Multi-vendor → Pydantic AI, LangGraph, AutoGen, smolagents.
7. What is your risk appetite for framework churn? Most mature: LangGraph. Youngest surfaces: OpenAI SDK additions, Pydantic AI capabilities/harness, ADK 2.0.
8. Do you need a managed platform? LangSmith Deployment, CrewAI Enterprise, LlamaCloud, Azure/GCP runtimes — or self-host with Pydantic AI, smolagents, OSS LangGraph.

## The Scorecard Method

Assign each dimension a weight by product class, score candidates 1-5, compare weighted sums. Weight profiles from the book:

| Dimension | Chat assistant | Durable multi-step product | Internal research tool |
|---|---|---|---|
| Graph control | 2 | 5 | 2 |
| State/durability | 2 | 5 | 3 |
| HITL | 1 | 4 | 1 |
| Observability | 3 | 4 | 2 |
| Ecosystem | 3 | 3 | 2 |
| Abstraction speed | 4 | 1 | 4 |
| Track record | 2 | 4 | 1 |

Worked example (durable multi-step product): LangGraph scores 5,5,5,4,5,2,5 — dominating exactly the heavily-weighted columns; OpenAI Agents SDK loses on durability and graph control, the weighted core; CrewAI wins only on speed, weighted 1. The exercise of choosing the weights is the real decision, because it forces you to state what the product actually is. Reality check the scorecard cannot encode: team skill — a framework the team fights scores 0 on every dimension in practice.

The definitive test: build the same small-but-hard slice — one durable multi-step flow with one interrupt and one failure-recovery path — in your top two candidates, two days each. The winner is rarely the one with the better tutorial; it is the one whose abstraction still fits when the flow gets ugly. Every framework looks identical on "hello, agent"; they diverge on "retry step 4 with edited state, 3 hours later, after a deploy."

## Selection Anti-Patterns

| Anti-pattern | Why it fails | Correction |
|---|---|---|
| Demo-time bias | Demos optimize for exactly what production punishes: happy path, no state, no failure, one user | Hard-slice bake-off |
| Resume-driven decision ("we should learn X") | The framework serves the product, never the reverse | Choose by product need |
| Port-from-tutorial trap | The tutorial validates the tutorial's app, not yours | Only the hard slice is a valid test |
| Sunk-cost extension | Staying because "we've built so much" ignores forward cost | Ask what the next year costs on each option |
| Committee hedge (two orchestrators "to be safe") | Two learning curves, two upgrade trains, a permanent integration seam | One orchestrator; diversify only at tool/model layers via protocols |

## The Framework-Agnostic Core (the master rule)

Frameworks are glue; your tools and business logic are the product. Keep the product framework-free: tools as typed Python functions with JSON-schema-compatible signatures (the framework wraps them at the edge), domain rules as pure functions with unit tests, user data in your own storage as the system of record, prompts versioned in your registry. Zero business logic in framework wrappers. Benefits: testability (domain logic unit-tested without an LLM), portability (migration mechanical), safety (the framework owns almost nothing of value).

Portability self-audit (each "no" is future migration cost): plain-function tools independent of framework imports; business logic outside framework objects; prompts as versioned artifacts you own; eval suites as plain data plus code; user data in your own schema; traces tagged with your version IDs; tools protocol-exposed (MCP) or schema-first; a developer can name the framework-touching files; a written list of what a migration would lose; questionnaire re-run in the last 12 months. Score 8-10: you own your stack. 4-7: the framework is creeping. 0-3: it owns your product.

Refactor test, run monthly: for each file ask "would this survive a framework swap unchanged?" Files that pass sit in domain/ and tools/; files that fail belong at the orchestration edge — because framework contamination is not written, it creeps.

## Migration Notes

What moves and what does not: tools move in days if they are plain functions (framework tool wrappers mean a rewrite); prompts are mostly portable text but prompt management (versioning, evals attached) is not; orchestration logic does not translate mechanically — plan a re-implementation, not a port; checkpoints are framework-specific formats (usually discard for active runs; keep user-visible history in your own storage); traces are the real lock-in — a year of failure clusters and evals cannot import into another vendor's format, so export raw inputs/outputs and budget for re-collection.

Migration runbook: (1) inventory artifacts by cost to move; (2) run dual-track with both systems logging to your own metrics; (3) strangler-fig flow by flow, each independently rollbackable; (4) cut over when the new system matches the old on SLOs for a full traffic cycle, then freeze and delete the old — because the worst outcome is two frameworks maintained indefinitely. Budget expectation: tools and prompts move in days; orchestration rewrites take weeks; observability re-collection takes months.

Hedging, mechanically: expose tools via MCP (protocols outlive frameworks); adopt A2A only at genuinely external seams; own your prompts in git; own your evals as plain code plus data; re-run the questionnaire annually — usually the answer is "stay," and that should be a decision with evidence, not inertia. You are almost never choosing a framework globally; you are choosing per layer (orchestration in one, agent loops in another, DSPy-compiled prompts, MCP tools) — the all-or-nothing framework decision is a marketing artifact; the layered decision is engineering.
