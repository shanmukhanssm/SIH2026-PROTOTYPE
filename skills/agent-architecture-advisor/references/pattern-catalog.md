**Load this when:** running Step 2 (pattern selection) or Step 7 (design review) — the full 9-pattern catalog with use-when/use-when-NOT conditions, the balance sheet, combination rules and canonical stack order, anti-patterns, the failure matrix, and the design-review checklist.

# Pattern Catalog

## The 7-Question Selection Framework (operational version)

Work the questions in order; each answer narrows the pattern space:

1. Is the task decomposable at all? No → single augmented call (tool use only).
2. Is the decomposition fixed or input-dependent? Fixed → chaining (sequential) or parallelization (independent). Input-dependent → orchestrator-workers or planning.
3. Does the number of steps depend on the environment (not just the input)? Yes → you need an agent loop (tool use + ReAct) at the core.
4. Are there distinct input classes with different handling? Yes → routing on top, with an unknown-intent bucket.
5. Is quality improvable by explicit critique? Criteria exist → evaluator-optimizer. No criteria but past feedback transfers → reflection.
6. Does the task need facts beyond the prompt? Yes → RAG (static if one-shot works; agentic if rewriting, loops, or verification are needed).
7. Does any role's context or toolset exceed one loop? Yes → multi-agent — not before.

Meta-rules: add patterns for a measured reason and remove them when the reason dies, because every added pattern multiplies latency, cost, and failure modes. The simplest pattern that hits the quality bar wins; stop adding patterns when the bar is met.

## The 9 Patterns

### 1. Prompt Chaining
Fixed sequence of calls, each consuming the previous step's output, with gates between steps. Without gates it is just a slow single-call pipeline; gates catch drift mid-stream before damage propagates.

| Use when | Do NOT use when |
|---|---|
| Task decomposes into fixed subtasks with independently checkable intermediate outputs (copy→translate, outline→draft, extract→verify) | Steps must share one reasoning session (splitting loses context) |
| Steps are the same every time regardless of input | Latency dominates (a 5-step chain is ≥5x minimum latency) |

Failure modes: error amplification (a mediocre step-A output becomes step-B's entire input); mid-chain format drift (fix: structured output per step); over-decomposition (10 steps where 2 would do). Hardening: gate verdicts written to state, retry counters with hard caps, checkpointing between steps, per-step model routing (cheap gates, expensive final prose). Gates should be deterministic where possible — an LLM gate on every step doubles cost and adds its own error rate.

### 2. Routing
A classifier picks one of several specialized downstream handlers. Specialization beats generalization, and routing enables model tiering: easy turns to a cheap model, hard ones to a frontier model — the single biggest blended-cost lever.

| Use when | Do NOT use when |
|---|---|
| Inputs cluster into distinct categories with materially different handling | Categories overlap heavily (a half-refund half-status request gets shoehorned) |
| The classifier can achieve high accuracy on those categories | Too many categories (classification degrades as option space grows) |
| Model routing is wanted (70% FAQ traffic to a cheap model ≈ 10x cost cut) | A single good prompt genuinely handles the variance |

Failure modes: classifier error is fan-out error — total for the misclassified request (a 95% router on 4 classes gives 5% of users the entirely wrong experience); missing unknown-intent handling; ambiguity between adjacent buckets. Hardening: confidence output plus explicit unknown bucket, fallback routing, log every decision with the eventual outcome. Build the router's eval set from production logs, not imagination — because users phrase things designers never predicted. One point of router accuracy (90→95%) roughly halves daily error cost; in routed stacks every downstream pattern inherits the router's error rate.

### 3. Parallelization
Multiple independent calls in one super-step, merged after. Three shapes: sectioning (one task split into independent sub-tasks — speed), voting (same task, multiple attempts, adjudicate — quality through independence), guardrail pairing (generate ∥ screen — safety).

| Use when | Do NOT use when |
|---|---|
| Independence holds: no subtask needs another's output | Subtasks have dependencies (dependency race — nondeterministic output) |
| Work is I/O-bound (LLM calls overlap on network wait) | Work is CPU-bound (queues on the GIL — parallel is a lie) |
| The merge is cheap and correct | Merging is harder than the subtasks |

Failure modes: partial-result merging errors (reducer silently drops a branch — explicit reducers on every merged key); dependency races; cost bloat (voting multiplies cost by vote count — vote only on high-stakes subsets); 8 parallel calls each re-reading the same 10k-token context (pre-split the context once instead). Hardening: bound fan-out width; per-branch retry without re-running siblings (checkpointing makes this possible).

### 4. Orchestrator-Workers
A central model dynamically decomposes the task, dispatches to workers, synthesizes results. The distinction from parallelization is critical: subtasks are input-determined ("I need to figure out what to research"), not predefined.

| Use when | Do NOT use when |
|---|---|
| Genuinely open decomposition: multi-file code changes, open research | Decomposition is known (you'd pay a planner to rediscover a fixed pipeline — use routing or parallelization) |
| Number and nature of subtasks cannot be enumerated in advance | Subtasks spawn subtasks (runaway depth — one level of dispatch plus a global budget cap) |

Failure modes: orphaned or duplicated subtasks (fix: dedup keys per subtask); synthesis loss — everything was found, nothing was reported (fix: cite workers by id, validate synthesis references every id); orchestrator hallucinating worker capabilities (fix: give it the worker registry as structured context). Hardening: worker registry with schemas, plan approval gate via interrupt() before expensive work (highest-ROI HITL placement in the catalog), result keys per subtask, strong orchestrator + mid-tier workers. The orchestrator's output format is the pattern: a strict structured list `[{subtask_id, goal, input_refs}]`, never prose.

### 5. Evaluator-Optimizer
Generation and critique in a loop until the evaluator approves or budget expires. Fit signals (both needed): human feedback demonstrably improves output, and an LLM can provide comparable feedback.

| Use when | Do NOT use when |
|---|---|
| Criteria are explicit and judgeable (tests, rubric, style guide, citation checks) | Criteria are vague ("high quality") — evaluator can't judge better than the generator → confident random walk |
| First drafts measurably benefit from iteration | Generator is near-ceiling (burns tokens for no delta) |
| Grounded external criteria exist | Evaluator shares the generator's blind spots (same model passes its own mistakes) |

Failure modes: convergence failure / oscillation (fix: stop after N passes, keep best-so-far not latest); infinite improvement ("make it better" never terminates — fix: thresholded metric, not direction); evaluator quality ceiling; cost (5 iterations ≈ 10x single-call cost — a greeting email does not need 5 drafts). Hardening: max_iterations in state, emit best-so-far on budget exhaustion, different/stronger evaluator model, cheap pre-checks (tests, linters) before the LLM judge, log per-iteration scores. Score trajectory diagnostics: healthy convergence vs oscillation (contradictory rubric) vs declining scores (vague feedback) vs flat line (ceiling). Spend the iteration budget on the rubric, then let the loop be dumb.

### 6. Tool Use
The model emits structured tool calls; the runtime executes and feeds results back. The two-node loop (agent ↔ tools, exit on no tool calls) is the backbone of most agents. Reasons it exists: grounding, action, capability extension.

Craft rules that determine agent quality more than any prompt tweak: verb-first unambiguous naming distinct from siblings; descriptions stating what/when/when-not with parameter examples ("intern test"); enums over free strings, `additionalProperties: false`; error contracts — errors are results (`{"ok": false, "error": "...", "hint": "..."}`) because a raised exception recovers nothing; keep the per-turn tool set under 20 (fewer for weaker models).

Failure modes: hallucinated calls and wrong-tool selection (FM1); tool result overload (cap and paginate at the tool boundary); side-effect asymmetry (separate read and write tiers; approval for the write tier; never expose writes an agent doesn't need). Hardening: validate arguments before execution, per-tool retries with backoff for transient failures only, parallel execution of independent calls, interrupt() inside side-effecting tools for approval. The best tool is the one the agent never has to call — push data into context before the loop when you can, because every tool you remove removes a failure class.

### 7. Planning
A planner produces a step list; execution works through it; a replan trigger regenerates the plan when reality diverges. Without the replan trigger, planning is just elaborate chaining.

| Use when | Do NOT use when |
|---|---|
| Long-horizon, decomposable tasks (research, migrations, reports) exceeding one prompt's capacity | Short tasks (planning a 2-step lookup adds overhead and rigidity) |
| The plan is worth inspecting/approving by a human | Environment is highly dynamic (plans go stale faster than they can be re-made — prefer a tight ReAct loop) |

Failure modes: plan staleness (fix: explicit replan triggers — step failure, evidence contradiction, budget signal, executor objection — measurable conditions, never "replan if things seem off"); rigid execution (give executors the right to say "this step is invalid"); over-planning (justify each step or cap plan length); plan-vs-output divergence. ReWOO optimization: planner emits steps with symbolic placeholders (#E1, #E2), a cheap executor resolves them, only the compact resolved plan reaches the synthesizer — because the expensive model is called once, not per step. This is the shape most serious research agents converge on.

### 8. Reflection / Reflexion
The agent critiques its own attempts and carries verbal feedback forward across attempts. Use only when no external criteria exist — where external criteria exist, evaluator-optimizer is strictly more reliable.

| Use when | Do NOT use when |
|---|---|
| Verifiable-ish tasks where past feedback transfers (code fixes against failing tests) | External criteria exist (use evaluator-optimizer instead) |
| Repeated decision tasks with observable outcomes | The missing fact is in a database — reflecting on "what went wrong" is expensive self-deception; just look it up |

Structural limits: self-preference bias (model rates its own outputs higher — use a different model for critique); critique quality caps convergence (some error classes are invisible from inside); drift without ground truth ("improvement" wanders confidently wrong). Empirical rule: reflection improves weak-to-strong gaps and plateaus fast at strong-to-excellent. Hardening: different model for attempt and critique; reflection memory as state with a reducer; inject the external signal (test output) verbatim into the critique; cap tries, keep best-so-far; keep only the last 2-3 critiques in context (full history in state/store) because replaying the whole log makes the agent re-litigate old failures.

### 9. Agentic RAG
Retrieval inside the agent loop instead of before it. Adds three loops to static RAG: query rewriting, retrieval loops (retrieve → assess sufficiency → reformulate), and self-RAG (decide per step whether retrieval is needed; self-assess each passage).

| Use when | Do NOT use when |
|---|---|
| Corpus is large or noisy (one-shot top-k isn't enough) | A static pipeline already meets the quality bar (a 200-doc corpus is probably fine statically) |
| Queries are ambiguous (rewriting pays) | Latency/cost per question is tightly capped (retrieval loops multiply both) |
| Answers must be verifiable (citation requirements) | |

Failure modes: retrieval-loop spirals (cap iterations; force an answer-with-citations on exhaustion); citation drift (verify mechanically — quote-in-chunk substring check); corpus-token flooding (distill or summarize passages); silent no-answer (force "I could not find support in the corpus" when retrieval is empty on a factual task). Hardening: retriever tiering (BM25 for identifiers, dense for semantics, hybrid for recall), per-step retrieval budget, cached retrievals per query hash.

## Pattern Balance Sheet

Every pattern is a trade between five currencies: latency, cost, quality ceiling, failure surface, engineering effort.

| Pattern | Buys | Costs (beyond baseline) | Characteristic failure | One-line verdict |
|---|---|---|---|---|
| Prompt chaining | Accuracy via smaller steps; inspectable stages | +latency per step | Error amplification down the chain | Fixed, gateable decompositions |
| Routing | Specialization; model tiering | Classifier error fan-out | Miscategorized input handled wrongly | Distinct classes + measurable router |
| Parallelization | Latency (sectioning); robustness (voting) | Nx cost; merge complexity | Merge/dependency bugs | Independent + I/O-bound + cheap merge |
| Orchestrator-workers | Input-dependent decomposition | Planner cost; synthesis risk | Synthesis loss; duplicate work | Decomposition cannot be enumerated |
| Evaluator-optimizer | Quality at generation time | 2-10x calls | Oscillation; evaluator ceiling | Criteria are explicit |
| Tool use | Grounding + action | Schema overhead; wrong-call rate | Hallucinated calls; result overload | The baseline pattern; craft the tools |
| Planning | Long-horizon coherence; inspectable plans | Planner call; staleness | Dead plans; rigid execution | Long decomposable tasks + replan triggers |
| Reflection | Cross-attempt learning | Attempt multiplier; drift | Self-deception; memory bloat | Only with external signals when they exist |
| Agentic RAG | Recall on noisy corpora; verifiability | Retrieval loop latency/cost | Query spirals; citation drift | Static RAG is not enough |
| Multi-agent | Role isolation; context partition | Coordination failure modes | Handoff loss; double work | Earn it (see topology-selector.md) |

Design-review question: "which row of the balance sheet are we deliberately accepting, and what is the metric that tells us we were wrong?" Teams that can answer ship agents; teams that cannot ship surprises.

## Combining Patterns

Canonical production research-agent stack (order matters):

```
routing (what kind of request?)
  -> planning (structure the work)            [reasoning model]
     -> orchestrated workers (fan-out via Send, one per sub-question)
        -> each worker: tool-use loop + agentic RAG + evaluator-optimizer gate
     -> synthesis with verified citations
  -> reflection pass (cheap) or human review (expensive) before emit
```

Layered support-system stack (each layer uses only the agency it needs):

```
layer 0 (code):    auth, PII scrubbing, budget/step caps, logging       [deterministic]
layer 1 (small):   router: intent in {faq, order, refund, tech, unknown} [small model]
layer 2 (workflow): faq -> static RAG with citations -> emit             [fixed path]
layer 3 (agent):   order/refund -> bounded tool loop (6 steps max)       [balanced model]
layer 4 (gate):    refund > $50 -> interrupt() for human approval        [HITL]
layer 5 (eval):    offline: per-layer evals, one metric per layer        [continuous]
```

Combination rules that keep stacks healthy — compose at different granularities (routing per request, planning per task, evaluation per artifact), because patterns occupying the same granularity fight; one owner per decision (one router, one planner, one verifier), because two patterns owning one decision equals oscillation; budget at the outermost layer with local budgets inside loops; model tiers follow the stack (reasoning for planning/verification, balanced for workers, small for gates/routers).

| Combination rot | Tell | Repair |
|---|---|---|
| Double ownership | Two components decide the same question | One owner; the other reads only |
| Stolen budget | Inner loop drains the outer cap | Inner loops get their own caps; outer cap is a hard ceiling |
| Granularity collision | Router and orchestrator both classify the request | Collapse to one; the other becomes a downstream step |
| Tier leakage | A reasoning model summarizes what a small model should | Audit per-node model usage; route by task shape |

Legitimacy rule — a combination is justified only when all three hold: separate granularity, separate ownership, measured contribution (a metric showing the pattern earns its cost). A pattern with no metric is decoration; delete it.

## Named Hybrids

- **ReAct + evaluator gate ("verifier-in-the-loop")** — the single highest-ROI hybrid: the loop supplies grounding, the gate supplies the quality bar the loop alone cannot.
- **Router + bounded agent ("triage and delegate")** — the shape most support/ops agents ship with; router quality silently caps the system.
- **Plan + fan-out + merge ("research skeleton")** — planner decomposes, Send fans out, merge synthesizes with id-referencing, evaluator checks completeness; fails loudly in one known way (synthesis loss) rather than mysteriously.
- **Loop + reflection memory ("learning assistant")** — lessons go through a store, not the message list; only the last few relevant ones enter context.
- **Chained agents ("relay")** — two full agents in sequence, structured handoff schema; use only when the task has two genuinely different phases each needing its own toolset.
- The everything-stack (router + planner + orchestrator + workers + reflection + RAG + evaluator + multi-agent "for completeness") does not deserve a name — delete to the smallest hybrid that covers the task.

## Anti-Patterns

| Anti-pattern | Symptom | Fix |
|---|---|---|
| Pattern soup | 30-node graph, 8 pass-throughs, minute latency, untraceable failures | Run the 7-question framework, then delete; graph draws on one screen |
| Over-engineering the trivial path | "Reset my password" passes an orchestrator, planner, reflection loop | Route early and hard; trivial path is a two-node graph |
| Single mega-prompt agent | One prompt "handles everything", no tools, no gates | Decompose or honestly ship a chatbot |
| Tool graveyard | Forty tools, most never called, several confusing the model | Prune from usage logs quarterly |
| Pattern that never terminates | Loops whose only exit is recursion limit or hope | Every cycle needs a visible, budgeted exit — draw it or it does not exist |
| Unverifiable stack | All the patterns, none of the evals | One metric per pattern |
| Demo-shaped system | Fully wired against the 10 demo inputs | Build the eval set from production-shaped inputs before building the stack |

Patterns are not dangerous; unbounded, unmeasured loops are. Budget every cycle; metric every pattern; the soup dissolves into a system.

## Failure Matrix (compressed)

| Failure class | Chaining | Routing | Parallelism | Orch-workers | Eval-opt | Reflection | Agentic RAG |
|---|---|---|---|---|---|---|---|
| Wrong-output amplification | gate each step | confidence + unknown bucket | score sections before merge | per-id verification | different model, concrete rubric | inject external signal | mechanical citation check |
| Silent data loss | structured output per step | — | explicit reducers | id-referencing synthesis | keep best-so-far on timeout | — | trim rules for tool pairs |
| Loop non-termination | retry cap | — | — | one level + budget | threshold + max_iterations | max tries | cap + forced answer |
| Cost bloat | deterministic gates | — | vote only high-stakes | ReWOO placeholders | route by task value | distill memory | cap + distill chunks |
| Context rot | — | lean router prompt | pre-split context | distill results into state | — | — | force no-support answer |

When designing: pick the patterns, read down their columns, write the mitigation for each populated cell into the design doc. Each cell doubles as an eval case ("give the gate an always-failing input; does the cap hold?") — exactly the adversarial cases happy-path eval sets miss.

## Worked Selections (from the book)

**Monthly investor update** — "Generate the monthly investor update from our CRM data and last month's report": 1. Decomposable? Yes (gather metrics, compare, draft, review). 2. Fixed — same four stages every month → chaining. 3. Environment-dependent steps? No → no agent loop. 4. Distinct input classes? No → no router. 5. Critique improvable? Yes (tone guide, numbers-must-match-claims) → evaluator gate. 6. Facts beyond prompt? Yes, but via deterministic CRM queries → tools, not RAG. 7. One loop suffices? Yes → single graph. Result: chain [pull metrics → compare → draft → evaluate vs tone guide → emit], no agents. The naive team would have designed an autonomous agent with 8 tools and a reflection loop; for a fixed four-stage task the framework answers "adapt to what?" with nothing.

**Competitive analysis** — "Generate a competitive analysis of 5 products from public sources, with citations": decomposable; mostly fixed per product → sectioning; gathering depth depends on what sources exist → a small tool loop per product; citation check is a crisp criterion → evaluator gate; retrieval per product (agentic, because sources vary); 5 products is fan-out, not multi-agent → Send. Result: Send fan-out over 5 products → each: retrieve-loop + write → merge → evaluator gate (citations) → emit.

**Onboarding email personalization** — fixed four stages → chaining; two input classes (self-serve vs sales-assisted) → small router; tone guide exists → evaluator gate on the final email; no external facts → no RAG; one loop → no multi-agent. Result: router (2 classes) → chain [profile → personalize → evaluate vs tone guide] → emit. Latency ~12s; cost ~$0.03; near-deterministic with the gate.

Three tasks, three completely different stacks, none an "everything" stack — the art is placement: put the loop where the environment is unpredictable, the gate where criteria exist, the routing where classes exist, and nothing anywhere else.

## Design Review Checklist

STRUCTURE: every cycle has a visible, budgeted exit; one routing mechanism per node; every multi-writer state key has an explicit reducer; the graph draws on one screen.
PATTERNS: each pattern answers a framework question; one owner per decision; each has a metric proving it earns its cost; the trivial path is trivial.
MODELS: per-role model tiering with a stated reason; snapshots pinned, upgrades gated on evals; toolset per turn under 20 with error contracts.
DATA: eval set built from production-shaped inputs; every gate's verdict logged to state; per-step cost and termination reasons instrumented.
FAILURE IMAGINATION: for each pattern, name its top failure mode and the detecting metric; the postmortem template exists ("which assumption was false?").

The checklist measures whether the stack is observable, bounded, and owned — the three properties that determine whether an agent system survives production.
