**Load this when:** running Steps 3-4 (single vs multi-agent, topology) or the deployment/capacity part of Step 6 — topology comparison, when-NOT tables, mono vs micro-graphs, capacity planning math, and deployment topologies.

# Topology Selector

## Multi-Agent Is Earned Complexity

Multi-agent systems trade single-loop coherence for role specialization — separate agents with separate prompts, tools, and contexts, communicating through structured interfaces. They add coordination failure modes (misrouted handoffs, dropped context, double work) on top of every single-agent failure mode. Most production systems start single-agent and only split when a role's context or toolset provably cannot fit one loop, because a single agent with good tools and a lean context outperforms a mediocre multi-agent system. Multi-agent latency multiplies, it does not add: two chained agents of 3 sequential calls each is 6 calls plus handoff serialization — before adding an agent, ask whether a better tool would do.

The topologies map onto single-agent patterns: supervisor/worker is orchestrator-workers with agents as workers; handoffs are routing with state-carrying transitions; peer networks are parallelization with richer protocols; hierarchical teams nest all of the above.

## Topology Comparison Matrix

| Topology | Shape | Who owns routing | Wins when | Cost / risk |
|---|---|---|---|---|
| Send fan-out (parallel workers) | Planner or splitter fans out N subtasks, merge node collects | Code (fixed fan-out plan) | Fixed or plan-per-input sections; per-worker loop is one loop | Merge/reducer bugs; cost scales with N — is fan-out, NOT multi-agent |
| Supervisor / worker | Central supervisor agent routes to specialized worker agents | Supervisor model (+ code edges around it) | Distinct task classes with materially different toolsets; bounded delegation | Supervisor re-reads context per delegation (2-4k tokens/turn); misroute surface |
| Handoff router | Agent passes control to a specialist with a state-carrying transition | Model (handoff decision) | Conversation ownership transfers cleanly (triage → specialist) | Handoff loss; router context starvation on follow-ups |
| Peer network / group chat | Agents message each other, no central owner | Emergent (speaker selection per turn) | Divergent idea generation, brainstorming | Token explosion (10 turns x 5 agents x 5k context = 50k+ re-read tokens); no termination authority — anti-pattern for convergent execution |
| Hierarchical teams | Supervisor of supervisors; nested teams | Nested supervisors | Org-scale decomposition with per-team autonomy | Highest coordination cost; deepest debugging; inherits every layer's error rate |

## When-NOT Table (per topology)

| Topology | Do NOT use when | Because |
|---|---|---|
| Send fan-out | Subtasks depend on each other's outputs, or merge is harder than the subtasks | Dependency race and merge bugs; run sequentially instead (T8: name the channel, the reducer, and the join-failure behavior — if you cannot answer all three, do it sequentially) |
| Supervisor/worker | One agent with a lean toolset fits the whole task | Supervisor tax (3-5x tokens vs a well-designed graph) plus non-deterministic routing you must audit |
| Handoff router | Categories overlap heavily or the router's context is only the first message | Follow-ups misroute ("actually, about my invoice..."); route on the full thread and allow "unclear — ask" |
| Peer/group chat | The task is convergent (produce one artifact, execute one plan) | No participant has termination authority; cost climbs while the conversation stays alive |
| Hierarchical | Fewer than ~3 genuinely distinct specialist domains | Nesting multiplies latency and error rates without a matching payoff |
| Any multi-agent | The failure you are solving is prompt quality or tool quality | Multi-agent amplifies weak components; fix the component first |

Field rules for any multi-agent design: give every subagent a single, verifiable deliverable ("return the last 3 invoices and flag duplicate charges", not "look into the account") — undefined deliverables are how subagent trees become runaway cost. Handoff with an agenda, not a summary: send the task, the user's goal, and what was already ruled out — bare summaries force re-derivation, which is where multi-agent systems lose coherence. Parallel subagents writing into one shared state is how results get silently dropped: each subagent gets its own channel with a merge reducer, or runs sequentially — there is no third option that survives production.

## Single Agent vs Multi-Agent Decision Path

1. Can one loop hold the needed context and toolset (per-turn tools < 20)? Yes → single agent (or plain workflow), because a single agent with good tools outperforms a mediocre team.
2. Is the split driven by a distinct class of inputs with different handling? Prefer routing to a second prompt/graph over a second "agent" — topologies are for context and toolset isolation, not for prompt organization.
3. Does exactly one role need isolation (different tools, credentials, or release cadence)? Split that role as a subgraph/worker and keep everything else single.
4. Only when multiple roles each provably exceed one loop → supervisor or hierarchical, chosen from the matrix above.

## Mono-Graph vs Micro-Graphs

| Dimension | Mono-graph | Micro-graphs (subgraph composition) |
|---|---|---|
| Visibility | One trace, one state — debugging easy | Parent trace links child traces; state hops boundaries |
| Team ownership | Whole team touches one file; merge conflicts | Each team owns a graph, versioned/deployed separately |
| Coupling | Shared state schema changes ripple everywhere | Explicit input/output contracts per subgraph |
| Performance | Fewer hops, one checkpoint store | Serialization at each boundary (minor) |
| When | One team, one product, <20 nodes | Multiple teams, shared platform, different release cadences |

The production pattern that has won: mono-graph per team, micro-graphs across teams, RemoteGraph (remote graph invocation) across trust boundaries — a remote call with its own authnZ is mandatory when payments meets content. Field rules: the moment two teams want to edit the same graph, split it; the moment one graph passes 30 nodes, split it anyway — because the cost of a subgraph boundary is milliseconds of serialization while the cost of a 400-node monolith is that nobody can hold the state machine in their head.

Node-per-call vs loop-in-node: (A) node-per-call exposes every model call as a node — each is a checkpoint, a streaming event, an interrupt point; (B) loop-in-node hides the loop inside one node — simpler graph, but no mid-loop checkpoints, interrupts, or trace granularity. Decision rule: (B) for specialists whose loop you never pause mid-flight (a KB researcher whose findings are all you care about), (A) for any loop containing a step you might interrupt, budget, or inspect separately (a refund specialist with a gate between calls). If unsure, build (A): collapsing a verbose graph into a loop-in-node later is easy, but extracting a hidden loop back into explicit nodes means re-architecting shipped state — visibility is easier to give away than to earn back.

## Capacity Planning Math (6 steps)

1. **Measure the per-run profile.** From one week of traffic: tokens_in_per_call (e.g. 3,500), tokens_out_per_call (400), llm_calls_per_run (4), tool_calls_per_run (2); run_latency = 4 × 1.2s + 2 × 0.8s ≈ 6.4s; cost_per_run = 4 × (3500 × $2/1M + 400 × $8/1M) ≈ $0.041.
2. **Translate to load.** runs_per_day 50,000 → avg ≈ 0.58 rps; peak = 5x ≈ 2.9 rps; concurrency = peak × run_latency ≈ 19 concurrent runs (Little's Law); daily tokens ≈ 780M; daily cost ≈ $2,050.
3. **Rate-limit math against the provider tier.** peak RPM = peak_rps × calls × 60 (here ≈ 696 RPM); peak input TPM = RPM × tokens_in (≈ 2.4M). Compare with your tier — a 30k TPM tier is 80x short. This mismatch is the number-one capacity surprise; retries cannot fix a tier cap (only a higher tier, multi-key sharding, or a cheap model on high-volume calls helps).
4. **Shaping policies** (enforced in the model gateway, never in the prompt): per-tenant token budgets with hard stops; per-run LLM-call caps; model routing by task (routing 60% of calls to a 10x-cheaper model cuts cost ~60%); prompt caching for stable prefixes (10-50x discount on cached input).
5. **Unit economics sanity line.** When cost_per_run exceeds revenue_per_run, no engineering saves you — cheaper models, fewer round-trips, or a pricing change. Compute monthly.
6. **Latency budget.** budget = user_tolerance − ~2s transport overhead; llm_round_trips_allowed ≈ (tolerance − 2s) / 1.2s per call. If the budget allows 6 calls and the agent needs 9, you do not have a latency problem — you have an architecture problem (parallelize independent tools, merge draft-and-verify, let a confident router answer directly).

Round-trip sensitivity (the chart that explains most performance complaints):

| Design | Sequential LLM calls | Est. latency | Est. cost/run |
|---|---|---|---|
| Single-shot answer | 1 | 2-4s | ~$0.010 |
| ReAct with 2 tools | 3 | 6-10s | ~$0.031 |
| Plan → 3 parallel tools → answer | 3 | 6-10s | ~$0.031 |
| Plan → 3 sequential tools → answer | 5 | 10-16s | ~$0.051 |
| Supervisor → 2 workers → synthesizer | 6+ | 12-20s | ~$0.061+ |
| Unbounded loop (no stop condition) | 25 (recursion limit) | 50s+ | ~$0.25+ and climbing |

Every sequential round-trip costs 1-3 seconds regardless of model speed; the difference between a disciplined agent (3 calls) and a naive one (5 calls) is ~2x on latency and cost. Concurrency is what sizes the orchestrator: longer runs (30 calls, 90s) at the same traffic turn 19 concurrent runs into ~260 — now you need a queue, autoscaling workers, and multi-pod per-thread locks.

Worked capacity plan (support co-pilot, 2,000 conversations/day, median 4 calls, 3.2k in/350 out per call): latency ~7.5s, cost ~$0.037/run → ~$2.2k/month; peak ≈ 0.12 rps → ~1 concurrent run → one small worker, zero queue; provider ≈ 29 RPM at peak — trivial. Verdict: the real constraints are the ticket DB the tools hit every run and the human review queue (~8% of tickets = 160 reviews/day). Most "scaling agent platform" problems are actually "scaling tool dependencies and review capacity" problems. At 200k conversations/day the math bites: ~2,784 RPM peak, $222k/month — now cutting one round-trip is worth ~$55k/month.

The deliverable is one page with five lines: peak rps, peak concurrency, peak TPM/RPM versus tier, expected cost per day at three growth scenarios, and the name of the first bottleneck per scenario. If you cannot name the first bottleneck, planning is not finished.

## Deployment Topologies

| | A) Managed cloud | B) Self-hosted server | C) Hybrid |
|---|---|---|---|
| What you run | Graphs only (platform runs server, queue, persistence, console) | LangGraph Server image in your K8s + Postgres + Redis | Graphs self-hosted in VPC; observability/console managed |
| Wins | Fastest to production; background runs, cron, HITL queue pre-wired | Data never leaves the VPC; full control | Data plane home, control plane managed — the most common real-world config |
| Costs / tradeoffs | Data residency may force self-hosting; per-node pricing needs the capacity math (budget on nodes executed, not conversations) | You own queue/autoscaling, Postgres failover, SSE-terminating ingress, upgrades; ~one platform engineer at 50% once traffic is non-trivial | Trace payloads still leave the VPC — PII redaction must happen before trace export |

Four non-negotiable deployment practices regardless of topology: blue-green with version pinning (in-flight runs resume against the graph version they started on, or a deploy strands them with a checkpoint-schema mismatch); drain before terminate (on SIGTERM stop accepting runs, let in-flight runs checkpoint, then exit); SSE-aware ingress (buffering off, heartbeats on, long timeouts on stream routes only); cold-start budget (warm sandbox pools and model connections, because the first run after a deploy pays the tax and shows up in p95 as a deploy-shaped spike).

Field rule: start on the managed option even if the end state is self-hosted — the platform's console, queue, and HITL wiring teach you what production agent systems actually need, then you re-implement exactly those pieces. Self-hosting first is how teams spend two months building a queue and zero months improving their agent.

## Platform Maturity Ladder (where your decision lands you)

| Level | Name | Runs are... | You can... |
|---|---|---|---|
| L1 | Prototype | Stateless, in-memory | Demo |
| L2 | Durable | Checkpointed | Retry and resume by hand |
| L3 | Operated | Traced, metered, alerted | Roll back, kill, replay |
| L4 | Gated | Eval-gated per release | Block bad deploys automatically |
| L5 | Self-correcting | Governed by budgets and policy | Let the feedback loop improve the system |

Each level is roughly an order of magnitude less on-call pain, and each is a platform investment rather than an agent-model improvement. Most teams plateau at L2 for a year because they keep improving the agent while the platform stays invisible — name the target level in the architecture decision record so the gap is a plan, not a surprise.
