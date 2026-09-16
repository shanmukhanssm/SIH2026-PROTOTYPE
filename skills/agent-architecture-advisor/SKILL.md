---
name: agent-architecture-advisor
description: >-
  Decides WHAT agent system to build before any code exists: whether an agent is justified at
  all, which agentic pattern(s) fit the task, single-agent vs multi-agent, which multi-agent
  topology, which framework, and which deployment topology. Produces a defensible architecture
  decision record with cost, latency, and reliability budgets — not code. Stage: DESIGN, the
  first skill invoked in any agent project.
  Trigger: "should we build an agent for this", "do we need a multi-agent system",
  "which agentic design pattern fits", "which framework
  should we use", "LangGraph vs CrewAI vs AutoGen", "supervisor or handoffs", "single agent
  or multiple agents", "capacity and cost plan for an agent", "architecture decision record
  for our agent".
  Do NOT use for: writing LangGraph graph code (langgraph-builder), designing tool schemas
  (agent-tool-designer), implementing supervisor/handoff topology code (multi-agent-builder),
  building eval suites (agent-eval-builder), or debugging a live incident (agent-debugger).
allowed-tools: Read, Write, Edit, Glob, Grep
---

# agent-architecture-advisor

## Overview

This skill runs the DESIGN stage of an agent project: it turns a task description into a defensible architecture decision before a single line of graph code is written. It gates the build on feasibility (when NOT to build an agent), selects the smallest pattern stack that meets the quality bar, decides single vs multi-agent and the topology, picks a framework via questionnaire plus bake-off, and produces a cost/latency/reliability budget that the arithmetic has already argued with. The output is an Architecture Decision Record (ADR), not code — because writing graph code before this gate is the most expensive mistake in agent projects: agents built for fixed-step tasks pay latency, cost, and non-determinism for nothing, and unbounded designs are discovered on the invoice rather than in review. Assumes a task description and a quality bar exist; no code, no eval set, and no framework commitment yet.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/when-not-to-build.md | Step 1 and Step 6: the 7-question build/no-build checklist, agency levels L0-L3, the agency dial, economics math, seven failure modes, and the budget worksheet with its worked example. |
| references/pattern-catalog.md | Step 2 and Step 7: the 9 patterns with use-when / use-when-NOT tables, the balance sheet, combination rules, canonical stack order, anti-patterns, the failure matrix, and the design-review checklist. |
| references/topology-selector.md | Steps 3-4 and Step 6: single vs multi-agent decision path, topology comparison and when-NOT tables, mono vs micro-graphs, node-per-call vs loop-in-node, capacity planning math, deployment topologies. |
| references/framework-matrix.md | Step 5: framework comparison matrix, day-60 test, 8-question questionnaire, scorecard weights, selection anti-patterns, framework-agnostic core, migration rules. |

## Execution Checklist

- [ ] 1. Run the 7-question when-NOT-to-build checklist; 2+ "no" answers means build the simpler thing (workflow or single call).
- [ ] 2. Set the agency level L0-L3 per edge and sketch the deterministic version first.
- [ ] 3. Run the 7-question pattern-selection framework; commit to the smallest pattern stack.
- [ ] 4. Decide single vs multi-agent; split only when a role provably cannot fit one loop.
- [ ] 5. Select the topology and graph granularity (mono vs micro) using the comparison and when-NOT tables.
- [ ] 6. Answer the 8-question framework questionnaire; scorecard the top 2; run the 2-day hard-slice bake-off.
- [ ] 7. Fill the budget worksheet: latency, cost, reliability, safety; do the TPM/RPM math against the provider tier.
- [ ] 8. Write the one-page ADR (context, decision, alternatives, consequences, evidence) and verify with the design-review checklist.

## Step-by-Step Workflow

### Step 1 — Scope and Feasibility Gate [EXACT]

Ask the user for the task, the quality bar, the expected volume, and the latency tolerance. Then run the 7-question checklist (references/when-not-to-build.md section 4): unpredictable decomposition? more than one model call genuinely needed? observable feedback signal? variable latency tolerable? worst-case cost bounded? eval coverage exists? failure cost asymmetric in your favor? Each "yes" pushes toward an agent; two or more "no" answers means build the workflow or single call, because agency is a purchased good paid for in latency, money, determinism, and compounding risk. Also ask "what would the deterministic version look like?" — if nobody can describe it, the problem is not understood well enough to hand to a model, and if they can, the deterministic version is the v1.

Next, set the agency level L0-L3 per edge (not one answer for the system) and fill one row of the agency-dial audit table per edge: who decides, cost if wrong, dial setting. The dial moves with error cost, not task difficulty.

Verification: you can state which edges are model-driven, what a wrong decision costs at each, and the checklist verdict. If you cannot, do not proceed — an unpointable design is unshippable.

### Step 2 — Pattern Selection via the 7-Question Framework [EXACT]

Load references/pattern-catalog.md and answer, in order: (1) decomposable at all? (2) decomposition fixed or input-dependent? (3) do step counts depend on the environment, not just the input? (4) distinct input classes with different handling? (5) explicit critique criteria exist? (6) facts needed beyond the prompt? (7) does any role exceed one loop? Each answer narrows the pattern space to chaining / parallelization / routing / orchestrator-workers / planning / evaluator-optimizer / reflection / agentic RAG / tool use, or multi-agent.

Commit to the smallest stack that hits the quality bar, because every added pattern multiplies latency, cost, and failure modes. Apply the two meta-rules: add patterns for a measured reason and remove them when the reason dies; stop adding patterns when the bar is met. For every loop in the stack, name its visible budgeted exit (step cap, token cap, threshold, or timeout) — a cycle without one is a schedule of future incidents.

Verification: every pattern in the stack answers a framework question, and every cycle has a named budgeted exit. If a pattern answers nothing, delete it now, before it accrues eval debt.

### Step 3 — Single-Agent vs Multi-Agent Decision [GUIDED]

Default to single-agent. Load references/topology-selector.md and walk its decision path: (1) can one loop hold the needed context and toolset (under 20 tools per turn)? (2) is the split driven by a distinct input class — if so, prefer routing to a second prompt over a second agent; (3) does exactly one role need isolation (tools, credentials, release cadence) — split only that role as a worker/subgraph; (4) multiple roles each provably exceeding one loop — only then go multi-agent. Split only when a role's context or toolset demonstrably cannot fit one loop, because multi-agent adds coordination failure modes (misrouted handoffs, dropped context, double work) on top of every single-agent failure mode, and its latency multiplies rather than adds.

Verification: a one-sentence written justification per agent naming exactly what cannot fit one loop. If the justification is "cleaner separation" or "the diagram looks better", collapse back to one agent.

### Step 4 — Topology and Granularity Selection [FREEFORM]

This is a judgment call: the tables inform but do not mechanically decide, because topology fit depends on error costs, team seams, and context isolation needs that only the project owner can weigh. If (and only if) Step 3 produced multiple agents, pick the topology from the comparison matrix in references/topology-selector.md: Send fan-out (fixed or plan-driven parallel sections), supervisor/worker, handoff router, peer/group chat, or hierarchical. Read the when-NOT row for the chosen topology before committing — peer/group chat is for divergent ideation and is an anti-pattern for convergent execution; supervisor routing inherits the supervisor's token tax; fan-out requires naming the channel, the reducer, and the join-failure behavior. Give every subagent a single verifiable deliverable and hand off with an agenda (task, goal, what was ruled out), not a bare summary, because re-derivation is where multi-agent systems lose coherence.

Also decide graph granularity: mono-graph per team, micro-graphs across teams, remote graphs across trust boundaries (split at two teams or 30 nodes); and loop modeling: node-per-call whenever a step inside the loop might be interrupted, budgeted, or inspected (e.g. refund gates), loop-in-node only for specialists you never pause mid-flight. If unsure, choose node-per-call — visibility is easier to give away than to earn back.

Verification: for each fan-out, the channel, reducer, and one-branch-failure behavior are named; the topology's when-NOT row is explicitly answered in writing. If any answer is missing, do it sequentially or stay single-agent.

### Step 5 — Framework Selection [GUIDED]

Load references/framework-matrix.md. Answer the 8 questions in order: unit of work (chat turn, durable multi-step process, retrieval pipeline)? runs must survive restarts and deploys? who builds it (team skills)? explicit control flow needed? where evals and observability live? vendor attachment? churn appetite? managed platform needed? Then scorecard the shortlist with weights chosen for the product class, because choosing the weights forces the real decision — stating what the product is. Confirm the pick with the 2-day hard-slice bake-off: the same durable multi-step flow with one interrupt and one failure-recovery path in the top two candidates, because every framework looks identical on "hello, agent" and diverges on "retry step 4 with edited state, 3 hours later, after a deploy."

Whatever the pick, mandate the framework-agnostic core: tools and business logic as plain typed Python with the framework only at the orchestration edge, user data in your own storage, prompts versioned in your own registry — this caps any wrong pick at the orchestration layer. Do not adopt two orchestrators "to be safe": diversify at tool and model layers via protocols instead.

Verification: the weight table is filled, the bake-off was run (or the decision is explicitly deferred with a dated re-check), and the chosen framework's day-60 failure is one this team accepts owning.

### Step 6 — Capacity and Cost Budget Worksheet [EXACT]

Fill the worksheet in references/when-not-to-build.md (section 7): task and quality bar; agency level per edge; latency budget (max steps, calls per step, per-call and tool latency, user tolerance); cost budget (input tokens per call, context growth per step, output tokens, steps, run cost via the formula, 1.2-1.5x retry/eval multiplier, monthly volume and monthly cost); reliability budget (per-step p — measured or 0.95 if unknown — end-to-end (1-p)^n, and where failures are cheap); safety budget (irreversible actions, approval gates, sandbox coverage). Then run the 6-step capacity math from references/topology-selector.md: per-run profile, load and concurrency (Little's Law, 5x burst), TPM/RPM versus provider tier, shaping policies, unit-economics line, latency budget in round-trips.

The reliability row is where designs die and where they should die: if the end-to-end number embarrasses the design, redesign — add verification gates to raise p, shorten loops to reduce n, add HITL where errors are expensive — because the worksheet's entire purpose is to make the arithmetic argue with the design before production does. Skipping it converts the cost overrun and the refund error into discoveries rather than decisions.

Verification: every blank in the worksheet is filled with a number (not a hope), the monthly cost and end-to-end reliability are computed, and the first bottleneck per growth scenario is named.

### Step 7 — Output the Architecture Decision Record [EXACT]

Write the one-page ADR to the project repo (e.g. docs/adr/ADR-001-agent-architecture.md) using this structure, because decisions not written down at decision time are re-litigated forever:

```
ADR-001: <system name> — <one-line architecture decision>
CONTEXT:    <task, quality bar, volume, latency tolerance; the feasibility-gate verdict>
DECISION:   <pattern stack; single vs multi-agent + topology; framework + weight-table
            rationale; graph granularity; agency level per edge; deployment topology>
ALTERNATIVES CONSIDERED:
  - <rejected option> : rejected — <because ...>
  - <deferred option> : viable; deferred until <measurable trigger>
CONSEQUENCES:
  + <what this buys, per the balance sheet>
  - <what this costs and the failure mode accepted>
  <budgets: latency, monthly cost, end-to-end reliability, safety gates>
EVIDENCE (filled in after 30 days):
  <measured cost/run, termination-reason distribution, eval score, escalation rate>
```

The ADR must also commit the eval plan (eval set built from production-shaped inputs, one metric per pattern) and the 30-day build plan: week 1 single call + retrieval + eval gate, week 2 the two tools the failure log justifies, week 3 gates where logs show wrong outputs, week 4 budget pass — and only then consider plan/worker/multi-agent for task classes that still fail. This sequencing exists because you cannot design an agent you have not seen fail: week 1's real failures are the specification for everything after.

Verify the ADR with the design-review checklist in references/pattern-catalog.md (structure / patterns / models / data / failure imagination): every cycle budgeted, one owner per decision, one metric per pattern, eval set from production-shaped inputs, top failure mode per pattern named with its detecting metric. Final gate: a reviewer who reads only the ADR can answer "which edges are model-driven and what does a wrong decision cost at each?" — if not, the ADR is incomplete.

## Examples

**Simple — translate our FAQ page to 4 languages.** Checklist: decomposition predictable (chunk → translate → stitch); one call per chunk suffices; latency fine async; eval = back-translation spot check. Verdict: workflow (chaining), zero agency, L0 everywhere. ADR decision: fixed chain with length/terminology gates; no agent, no router — because calling this an agent would add cost and non-determinism for zero benefit.

**Typical — monthly investor update from CRM data and last month's report.** 7-question run: decomposable (gather metrics, compare, draft, review); fixed — same four stages every month; no environment-dependent steps; one task type (no router); criteria exist (tone guide, numbers must match claims) so an evaluator gate applies; facts come from deterministic CRM queries (tools, not RAG); one loop suffices. ADR decision: chain [pull metrics → compare → draft → evaluate vs tone guide → emit], no agents, no multi-agent. The naive design — autonomous agent, 8 tools, reflection loop — would have been pure cost for a fixed four-stage task.

**Edge-case — competitive analysis of 5 products with citations.** Decomposition mostly fixed per product, but gathering depth depends on what sources exist → small tool loop per product; citation check is a crisp criterion → evaluator gate; agentic RAG per product because sources vary; 5 products is fan-out, not multi-agent → Send with named channel/reducer/join behavior. ADR decision: Send fan-out over 5 products → each: retrieve-loop + write → merge → evaluator gate (citations, every worker id referenced) → emit. Chosen against the multi-agent reflex, because role context fits one loop; only the data fan-out is parallel.

## Known Gotchas

1. **The team "designs an agent" for a fixed-step task.** Symptom: a 10-node autonomous graph whose steps never vary. Cause: nobody ran the deterministic-version question or the checklist, so agency was chosen by fashion. Response: run Step 1 and ship the workflow; the agent version is v2 only if the failure log demands it.
2. **Pattern soup: every pattern wired in because the talks made them sound good.** Symptom: a 30-node graph, minute-long latency, nobody can trace a failure end to end. Cause: patterns added without ownership rules, metrics, or budgeted exits. Response: re-run the 7-question framework, delete every pattern without a metric, and give every remaining cycle a visible budgeted exit.
3. **The reliability row of the worksheet is skipped or filled with optimism.** Symptom: 0.83 end-to-end reliability discovered after refunds go wrong. Cause: "we'll measure after launch" — but the worksheet's job is to kill the bad design before you pay to build it. Response: fill it with honest estimates now (0.95 per step if unknown), and add gates, shorter loops, or HITL until the number is acceptable.
4. **Multi-agent chosen before any single agent shipped.** Symptom: supervisor plus four specialists, and the demo already misroutes. Cause: topology picked for the diagram, not for provable context or toolset overflow. Response: collapse to the smallest single-agent (or router + one bounded agent); split only per the Step 3 justification test.
5. **Framework chosen by demo or GitHub stars.** Symptom: quickstart glow, then day-60 pain — manager agents creatively re-routing, no durable resume, handoff spaghetti. Cause: selection by hype instead of the questionnaire, scorecard, and hard-slice bake-off. Response: re-run Step 5; the winner is the framework whose abstraction still fits when the flow gets ugly.
6. **The framework owns the product.** Symptom: business logic inside framework tool wrappers, eval suites calling framework APIs, a migration estimate measured in quarters. Cause: no framework-agnostic core from day one. Response: mandate the layered core in the ADR — plain-function tools, domain logic as pure functions, user data and prompts owned by you — and run the monthly refactor test.
7. **Eval set built from imagined inputs.** Symptom: 97% on the launch eval, 81% in production ("my package thingy is broken help"). Cause: the eval measures the designer, not the agent. Response: commit the ADR's eval plan to production-shaped inputs and rebuild from two weeks of logs before launch.
8. **Unbounded agency: a step limit without a cost limit (or vice versa).** Symptom: $500/hour at 3 a.m. or a 30-minute run that burns the bill. Cause: the loop is bounded on one axis only, so it violates the other two. Response: in the ADR, bound steps, tokens, and dollars together, enforced by the platform (gateway caps, recursion limits), never by asking the model nicely.
9. **Error-cost asymmetry ignored.** Symptom: a 90%-accurate agent deployed where 99.9% is required. Cause: economics evaluated on run cost only, forgetting the error term. Response: price the error per the escalation ladder; put irreversible actions behind deterministic gates with human approval, or do not build.
10. **TPM/RPM math never done.** Symptom: a customer-visible queue builds invisibly while polite retries burn the rate-limit budget. Cause: provider limits are per-account tier, and retries cannot fix a tier cap. Response: Step 6's tier math before launch; higher tier, multi-key sharding, or cheap-model routing on high-volume calls.
