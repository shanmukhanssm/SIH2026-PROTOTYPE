**Load this when:** running Step 1 (scope and feasibility gate) or Step 6 (budget worksheet) — the build/no-build decision checklist, agency levels L0-L3, the seven failure modes, agent economics, and the budget worksheet.

# When NOT to Build an Agent

## 1. The Spectrum of Agency

"Agent" is not binary — it is a property of control flow. Every LLM-powered system sits on a spectrum defined by two questions: who decides what happens next (code or model), and how much state survives between decisions.

| Level | What it is | Who decides next step | State | Canonical example |
|---|---|---|---|---|
| Static prompt | Template + single LLM call | Nobody (no loop) | None | "Summarize this text" endpoint |
| Chatbot | Multi-turn conversation | Code (always call the model again) | Conversation history | Customer Q&A bot without tools |
| Workflow | Fixed graph of LLM/code steps | Code (edges hardcoded) | Passed along edges | Marketing copy → translation → review pipeline |
| Agent | Model chooses tools and step count | Model (within a loop code owns) | Shared state + tool results | ReAct-style support agent with 5 tools |
| Multi-agent system | Several agents, specialized roles, inter-agent protocols | Models + topology (handoffs, supervisor) | Per-agent state + shared memory | Research team: planner, searchers, writer, critic |

Definitions to anchor reviews: workflows orchestrate LLMs/tools through predefined code paths; agents dynamically direct their own processes and tool usage. The same tool-calling loop is a workflow if a fixed number of steps is hardcoded, an agent if the model decides when to stop.

Vehicle ladder (useful in design reviews): static prompt = thermostat; chatbot = cruise control; workflow = train on rails; agent = car with a driver who can turn; multi-agent = airport with control towers, specialized crews, and strict protocols.

## 2. The Augmentation Path and the Agency Dial

The path successful teams follow: start with a single augmented LLM call (retrieval plus a couple of tools), collect failures, hardcode the paths those failures suggest, and only convert a hardcoded path into a model-driven loop when the failure log shows decisions that genuinely cannot be predicted. If you can describe the deterministic version, it is usually the v1 and the agent is the v2 — because agency is a purchased good, not a default.

Agency is a dial you turn per edge, and the dial moves with the error cost, not the task difficulty. Fill one row per edge of your design:

| Edge | Who decides | Cost if wrong | Dial setting |
|---|---|---|---|
| FAQ vs order routing | Code (keywords) | $0.00 (retries) | Model NOT needed |
| Order lookup | Model (tool choice) | One wasted call | Model, 4-step cap |
| Refund eligibility | Model + code gate | Up to $250 | Model + HITL interrupt |
| Final answer wording | Model | Small (recoverable) | Model, temperature 0.3 |

Production reality check: most shipped systems are a hybrid — roughly 90% fixed workflow, a small bounded agent inside, and human escalation. If you cannot point at exactly which edges are model-driven, you do not understand your own system well enough to ship it safely.

## 3. Levels of Agency L0-L3

The L1→L2→L3 climb is a risk climb, not a capability climb. Each level demands a matching control surface; adding autonomy without the matching control surface invents a bug, not a feature.

| Level | Agency | Who decides | Example | Typical failure if misused |
|---|---|---|---|---|
| L0 | None | Code | Static prompt, pure workflow | Not a failure — just limited |
| L1 | Tool selection within a fixed loop | Model picks tools, code owns loop and stop condition | Support agent with 5 read-only tools | Wrong tool chosen; loop still bounded |
| L2 | Step planning | Model decomposes task into steps, code executes with gates | Plan-and-execute research agent | Bad plan propagates; replan needed |
| L3 | Environment autonomy | Model takes actions with side effects (writes, sends, purchases) | Coding agent committing code; computer-use agent | Irreversible actions; needs sandbox + approval |

Worked examples in one domain (e-commerce support): L0 = FAQ page; L1 = ticket triage agent (fixed loop of at most 6 steps, read-only tools, worst case a wasted call); L2 = "resolve this complaint" agent (plans, executes, gate checks the refund policy was consulted, human reviews drafts); L3 = autonomous resolution agent (may issue refund, email, close ticket — requires sandboxed execution, interrupt() approval before any refund over $50, audit log of every action).

Control surface per level: validation at L1, plan gates at L2, sandbox and human approval at L3. Error costs: L1 error costs a wasted tool call; L2 error costs a wasted run; L3 error costs real-world state — money moves, emails send.

Deployment rule: treat L3 autonomy as a deployment configuration, not a code property. Ship the same agent in "dry-run mode" (side-effecting tools replaced by a mock), run it in production for a week, and diff the mock log against what humans did — because agents that "work great in testing" and melt down in production almost always failed at this step.

## 4. The Decision Checklist (run before any build)

Each "yes" pushes toward an agent; each "no" pushes toward a workflow or a single call. Two or more "no" answers → build the simpler thing.

1. Is the task decomposition unpredictable in advance? If you can enumerate the steps, it is a workflow — because most "agentic" projects turn out to have discoverable step sequences once someone sits down and thinks.
2. Does the task genuinely require more than one model call? If a single augmented call solves 80% of cases, start there.
3. Is there a feedback signal the agent can observe (tests, search results, API responses)? Agents without feedback are chatbots with extra steps — they loop but never learn.
4. Can the environment tolerate variable latency (seconds to minutes per run)? A synchronous UX with a staring user needs workflows or aggressive streaming.
5. Can you bound worst-case cost per run with a budget and a step limit? If not, you are building a denial-of-service vector against your own cloud bill.
6. Do you have evaluation coverage (a dataset and a scorer)? No evals, no agent — you will not know when it breaks, and it will break.
7. Is the failure cost per run asymmetric in your favor? A wrong answer a human catches costs little; a wrong action that refunds $500 or emails a customer is not an agent problem yet.

Field red flags — each alone has justified saying no: "the steps are actually fixed, we just haven't written them down"; "we can't describe what a good answer looks like"; "the model will figure it out"; "we'll add evals later"; "latency doesn't matter for this."

The cheapest agent you will ever build is the one you do not build. When in doubt: ship the single-call version behind the same interface, log real user inputs for two weeks, then read the log. If failures are "the answer needed 3 steps" → build the workflow. If failures are "the number of steps depends on the input in ways we cannot predict" → only then build the agent.

## 5. The Economics of Agency

### 5.1 The three costs (all exponential in steps)

Latency — every loop iteration is a serial dependency (10-step agent at 50 tok/s, 500 tok/step ≈ 30s; reasoning model ≈ 170s; 3-call workflow ≈ 9s; deterministic code ≈ 0.05s). Cost — `cost = steps × (context_per_step × input_price + output_per_step × output_price)`; a support agent at 10 iterations ≈ $0.09/run; reasoning models are 3-8x more; at 1M runs/month a $0.09 task is a $90k/month line item. Reliability — if each step has probability p of being wrong, an n-step task succeeds with probability (1-p)^n:

| Per-step reliability | 3 steps | 10 steps | 30 steps |
|---|---|---|---|
| 0.99 | 97.0% | 90.4% | 74.0% |
| 0.95 | 85.7% | 59.9% | 21.4% |
| 0.90 | 72.9% | 34.9% | 4.2% |

At p = 0.95, a 10-step agent fails 40% of the time. This arithmetic justifies verification gates (raise p), shorter horizons (reduce n), and checkpoints (recover cheaply). It explains why agents are adopted first where verification is cheap (code: tests) and last where it is expensive (judgment calls).

### 5.2 The three archetypes compared

| | Workflow (3 fixed calls) | ReAct agent (10 steps) | Plan+execute w/ reasoner |
|---|---|---|---|
| Model calls | 3 | 10 | 1 planner + 12 exec |
| Latency (typical) | ~9s | ~30s | ~90s |
| Input tokens | ~6k | ~15k | ~25k |
| Run cost (balanced) | ~$0.02 | ~$0.09 | ~$0.35 |
| End-to-end reliability | 0.99^3 ≈ 0.97 | 0.95^10 ≈ 0.60 | 0.95^13 × gates ≈ 0.75-0.85 |
| Best when | Task shape is fixed | Task needs a few lookups and adapts | Horizon long, plan worth inspecting |

Every step up the agency ladder buys flexibility and pays reliability. There is no free row — which is why the choice belongs in a design doc with numbers, not in a demo.

### 5.3 Run economics and the escalation ladder

Planning numbers: typical production agent run = 10-30 model calls, 20k-100k input tokens, 3k-15k output tokens ≈ $0.05-$0.30 at frontier non-reasoning prices, 3-8x with reasoning models. Human comparison: support ticket ≈ $5-$20 fully loaded; research memo ≈ $200-$2,000. Error-cost asymmetry is the term everyone forgets: an agent that is right 90% of the time at 1/50th the human cost is only a good deal if a wrong answer costs less than ~50x the human's marginal error cost — in payments, medical triage, or contract drafting a 10% error rate is disqualifying at any price.

Cost components to put in the design doc:

| Cost component | Rough share | Notes |
|---|---|---|
| Model calls (loop) | 40-80% | Scales with steps × context |
| Tool infrastructure (search, retrieval, DB) | 5-20% | Usually fixed per call |
| Evaluation / monitoring / tracing | 10-30% | Often forgotten entirely |
| Human review (approvals, escalations) | 0-40% | Rises with L3 autonomy |

The escalation ladder — route work to the cheapest rung that resolves it; every rung needs a measurable promotion rule, and promoted work carries its context up:

| Rung | Cost per resolution | Handles |
|---|---|---|
| Deterministic path | ~$0.001 (no model) | FAQ hits, fixed flows |
| Small-model call | ~$0.005 | Routing, rewriting, classification |
| Balanced-model loop | ~$0.05-$0.30 | Standard agent turns |
| Reasoning-model pass | ~$0.30-$2 | Hard planning/verification |
| Human review | ~$0.50-$5 (2 min labor) | Approvals, escalations |
| Human ticket | ~$5-$20 full | The long tail |

When presenting economics, show verified error rate (eval set) and unverified error rate (production logs) side by side — the gap is usually 3-10x because eval sets contain the inputs the agent was designed for and production sends everything else.

## 6. Seven Failure Modes (design-time awareness)

These recur in every production agent; budget mitigations for the ones your architecture makes likely.

| Failure mode | Mechanism / first check | First fix (usually) |
|---|---|---|
| FM1 Tool hallucination & schema drift | Unknown-tool / bad-argument error classes | Schema validation + structured error results; enums; <20 tools per turn |
| FM2 Runaway loops | Step count p95; identical consecutive tool calls | Step budget + RemainingSteps + loop detection + cost ceilings |
| FM3 Context rot | Input tokens per step rising; "asked again" rate | Compaction/summarization; task-scoped notes; distill tool dumps |
| FM4 Prompt drift across model swaps | Behavior diff after "just an upgrade" | Pin model snapshots; eval-gated swaps; treat swap as a migration |
| FM5 Silent wrong answers | Sampled human review; citation mismatch | Verification gates; self-checks; HITL on irreversible actions |
| FM6 Non-idempotent side effects on retry/resume | Duplicate rows/emails after crash or resume | Idempotency keys; upserts; side effects after interrupts |
| FM7 Cost/latency overruns in production shape | Per-thread and per-hour cost dashboards | Cost formula before launch; prompt caching; small-model routing; per-thread budgets |

FM2 and FM7 are the same bug viewed from two angles: an unbounded loop. Budget time (steps), money (tokens), and scope (verification gates) at once, because an agent bounded on only one axis happily violates the other two.

## 7. The Budget Worksheet (fill before any build; refill at every architecture review)

```
TASK:  <one sentence, with the quality bar stated>
AGENCY LEVEL (L0-L3):  <per edge, not one answer for the system>
LATENCY BUDGET
  max steps per run:            ____    (your step cap, not the recursion default)
  model calls per step:         ____    (parallel tool calls count as one round trip)
  per-call latency:             ____    (measure: model tier x expected tokens)
  tool latency per call:        ____
  expected total:               ____    (product + serial overhead)
  user tolerance:               ____    (p50/p95 from the UX spec)
COST BUDGET (per run)
  input tokens per call:        ____    (system + schemas + working context)
  context growth per step:      ____    (tokens/step; the leak detector)
  output tokens per call:       ____
  steps per run:                ____
  run cost:                     ____    (the cost formula, computed)
  retry/eval multiplier:        ____    (1.2-1.5x typical)
  monthly volume:               ____
  MONTHLY COST:                 ____
RELIABILITY BUDGET
  per-step reliability:         ____    (measured, not hoped; 0.95 if unknown)
  steps per run:                ____
  end-to-end reliability:       ____    ((1-p)^n — confront the number)
  where failures are cheap:     ____    (verification points, gates, HITL)
SAFETY BUDGET (if L2/L3)
  irreversible actions:         ____    (list them)
  approval gates:               ____    (which actions, which threshold)
  sandbox coverage:             ____
```

Worked example — support agent:

```
TASK: resolve order/refund tickets; bar: no incorrect refunds, 90% no-escalation.
AGENCY: L1 (tool loop, 6-step cap) for order/refund; L0 for FAQ; L3 nowhere.
LATENCY: max 6 steps; 1 call/step; 3s/call; tool ~0.5s; expected ~21s; tolerance 30s p95. PASSES (barely).
COST: 2.5k in/call, +300 growth/step; 400 out/call; 6 steps; run ~$0.055; x1.3 -> $0.072;
      50k runs/month -> ~$3.6k/month. ACCEPTABLE.
RELIABILITY: per-step 0.97 (measured on 200 logged runs); 6 steps -> 0.83 end-to-end.
      83% is NOT acceptable for refunds -> add verification gate on refund step (p -> 0.99)
      + human approval > $50. Recompute ~0.94. STILL NOT ENOUGH -> cap refund loop at 4
      steps, gate every refund. ~0.97. ACCEPT, human escalation absorbs the rest.
SAFETY: irreversible = issue_refund, send_email. Approval gate on issue_refund > $50
        and ALL send_email. Sandbox: refunds execute in a shadow env in CI.
```

The worksheet's entire purpose: make the arithmetic argue with the design before production does. The reliability row is where designs die, and where they should die — if the end-to-end number embarrasses the design, that is the worksheet working.
