# Eval Taxonomy

**Load this when:** choosing which eval layers to build for an agent, mapping eval work to SDLC stages, selecting tool-specific eval patterns, or assessing eval maturity.

## Why agent evals are a different species

Unit testing asserts `agent.run("refund my order")` returns a known string. An agent eval must judge a stochastic trajectory with side effects. Four hardnesses follow, and each one shapes the suite:

1. **Non-determinism.** Same input, different trajectories (temperature, tool ordering, provider scheduling). A test passing 3 of 5 runs is not flaky — it is sampling a distribution. Because of this, the design question is "what fraction of runs must succeed," not "did this run succeed."
2. **Trajectories, not just answers.** A correct answer via a hallucinated tool call is a latent liability, not a success. Because the steps carry the diagnostic signal that tells you WHERE the system went wrong.
3. **Side effects.** An eval that really charges cards, sends emails, or deletes records is not testing. Every eval environment needs sandboxed/stubbed tools — and testing the stubs themselves becomes part of the eval system, because unfaithful mocks make the evals measure the wrong thing.
4. **Judging quality.** Somewhere in every pipeline sits a judge (human or LLM) that is itself imperfect. Its imperfection is a first-class quantity you measure and calibrate, not a footnote.

Consequence: agent evals are a statistical instrument — run 100 inputs, compare distributions with confidence intervals. "The eval passed" about a single run is weather reporting from one window.

## The layered taxonomy

| Level | What it tests | Cost per check | Catches | Misses |
|---|---|---|---|---|
| Unit / node test | one node/tool with fixed inputs | cents, milliseconds | tool contract bugs, parser bugs, schema violations | integration failure, emergent behavior |
| Component / subgraph test | a sub-agent with stubbed dependencies | low | routing logic, sub-agent output contracts | cross-agent interaction |
| Trajectory eval | the full run, judged on steps | moderate | wrong tool choice, loops, missing steps, unsafe sequences | subjective quality of prose |
| End-to-end task eval | the full run, judged on outcome | moderate-high | overall success rate on real tasks | why it failed (no trajectory signal) |
| Online / production eval | sampled live traffic, judged | high | real distribution, drift | controlled comparisons, safety risk |

**Engineering rule: push failures down the pyramid.** A tool bug should be caught by a unit test (ten cents, fifty milliseconds), not an end-to-end eval (fifty cents, three minutes, a judge call). Teams that only run end-to-end evals spend a hundred times the money for a tenth of the diagnostic resolution; teams that only run unit tests ship agents that fail beautifully in the field. Each level exists because it catches what the level below structurally cannot.

## Unit/node testing: two patterns to institutionalize

| Pattern | How | Because |
|---|---|---|
| Mock the model, not just the tools | stub the LLM to return a fixed tool call, then assert the graph called the right node with the right state | the only way to test graph structure deterministically |
| Golden-record tools | record real tool responses once; replay them in tests | tools are the flaky layer; recordings make tests deterministic while preserving the real response shapes that break parsers |

```python
def test_router_goes_to_refund_branch():
    # stub the model: next tool call is always "refund"
    fake_model = StubLLM(returns=[tool_call("refund", {"amount": 10})])
    graph = build_graph(model=fake_model, tools=sandboxed_tools())
    result = graph.invoke({"messages": [user("refund my order")]})
    assert result["branch"] == "refund"
    assert not real_payment_gateway.was_called()      # sandbox assertion
```

## Tool-specific eval patterns (highest-signal layer)

| Pattern | What it measures | Note |
|---|---|---|
| Tool selection accuracy | when the expected plan says "use search_internal," did the agent call it (and not web_search)? | compute per-intent; selection difficulty varies wildly by intent; catches routing regressions and prompt-induced tool confusion |
| Argument extraction accuracy | per call: (a) schema validity, (b) value correctness vs golden expected args | the #1 real-world failure is right tool, wrong args; "date should be 2026-06-03, model said 2026-03-06" is a VALUE error — format checks pass on confident nonsense |
| Hallucinated tool calls | calls to tools not in the toolset; args referencing entities absent from context (an order ID the user never mentioned) | distinct metric from selection: a model can pick the right tool and fabricate its input; detect by diffing call names against the registered toolset and args against context entities |
| Tool failure recovery | feed a failing tool stub (returning the error contract) and score recovery: retry with correction, switch approach, or stop appropriately | directly evaluates the reliability layer's error contracts — a closed loop |
| Docstring eval | arg-accuracy per tool surfaces misleading tool descriptions ("date as string" when the API wants ISO) | a docstring that misleads the model is a bug you will never catch by testing the tool alone; fixing one is a 5-minute fix for a 5-point metric improvement |

## Trajectory evaluation: the binary check catalog

Implement trajectory evals as binary checks — properties that must hold on every run of a class, judged pass/fail. Because "did the refund happen before the approval?" is mechanical (near-perfect judge reliability), while "how helpful was this answer?" is a judgment call judges get wrong 15-40% of the time. Maximize binary checks; reserve scored judgments for what cannot be checked mechanically.

| Check | Property | Catches |
|---|---|---|
| No-sensitive-action-without-approval | refund/delete/send appears only after an approval step | unsafe-action regressions, injection success |
| Identity-verified-before-data | account-data tool calls preceded by identity verification | data-leak regressions |
| Searched-before-answered | retrieval happened before a knowledge answer | agents answering from nothing |
| Retried-with-correction | a tool error followed by a different call, not an identical one | error-contract regressions |
| Cited-every-claim | every factual claim has a source citation | groundedness collapse |
| Stayed-in-toolset | no calls to non-existent tools, no fabricated entity IDs | hallucinated tool calls |
| Within-iteration-budget | run finished in <= N loop iterations | loop bugs, cost explosions |
| No-reasoning-in-answer | final answer contains no chain-of-thought leakage | UX regressions, hidden-thought leaks |

Runnable implementations live in references/templates.md (trajectory checker).

## Where evals bite in the agent SDLC

| Stage | Eval work | What it prevents |
|---|---|---|
| Design | write the eval before the code — define success as a testable property | building an agent nobody can judge |
| Development | unit/node tests with mock models + golden tools; local dataset subset | slow iteration on broken code |
| Code review | CI smoke evals on the PR; reviewer reads the eval deltas | reviewing prose instead of behavior |
| Merge | full dataset run; regression report with CIs | shipping regressions to users |
| Pre-release | backtest vs historical cases; red-team pass; safety suite | the "it worked in dev" incident |
| Release | canary + online judged evals + kill switch | full-population regressions |
| Post-release | label production failures into the dataset; recalibrate the judge quarterly | eval rot |
| Retirement | keep a frozen eval snapshot for the old version | losing the baseline |

Evals are not a stage — they are the connective tissue between stages. The same dataset runs in dev, in CI, and on the canary; the same judge scores offline and online; the same red-team cases gate the release and grow the dataset. Teams that treat evals as a QA step at the end get a gate they bypass; teams that thread evals through the lifecycle get a steering mechanism.

Design-review question that prevents the most eval pain: **"how would we know if this agent got worse?"** If the room cannot answer with a measurable signal, the design is not evaluable — and an unevaluable agent is an unchangeable agent, because every change is a gamble. Retrofitting evaluability onto a shipped agent is always harder and usually deferred forever.

## The eval maturity model

| Level | What exists | What it catches | Next step |
|---|---|---|---|
| 0: none | manual testing | almost nothing, expensively | unit tests for tools (1 day) |
| 1: unit + smoke | deterministic node/tool tests, hand-run e2e checks | contract breaks | curated dataset v1 (1 week) |
| 2: dataset + simple judge | fixed dataset, basic rubric judge | obvious regressions on known cases | judge calibration + CI gating (1 week) |
| 3: calibrated judge + CI gate | calibrated judge, trajectory checks, CI blocking, online sampling | silent quality drops, loop regressions | full portfolio: cost/safety evals, canaries |
| 4: eval-driven loop | telemetry-to-eval-to-fix running weekly, dataset compounds, online evals trended | distribution drift, judge decay, contamination | continuous red-team ops |

Two observations: (a) levels 1-2 deliver disproportionate value — the first 30% of the effort catches 80% of the regressions, so start small and climb by pain; (b) the jump from 2 to 3 is the hard one because it is cultural, not technical — gates that block merges, judges that get audited, watched online sampling require organizational discipline, and teams stall at the gate, not at the dataset.

## The five eval questions

Ask in every design review and every quarter after; stale answers mean instruments calibrated for a different aircraft.

1. **What is success, measurably?** Judged criteria and thresholds, written down. No answer: no SLO, no gates, no release decisions.
2. **What is the dataset, and where did it come from?** Provenance, versioning, refresh cadence. No answer: the evals measure a fiction.
3. **Who or what is the judge, and how calibrated is it?** Agreement numbers vs human labels, per criterion. No answer: a random number generator wearing a rubric.
4. **What happens in CI when the evals fail?** Block, warn, or nothing. No answer: a report nobody reads.
5. **How do we know the evals still measure the real system?** Online-vs-offline agreement, judge recalibration, dataset coverage. No answer: a cargo cult with a UI.

## Eval scorecard (self-assessment, 0-60)

Score each line 0-3 with EVIDENCE (the CI config, the calibration record, the meeting invite) — the score drops by a third on the first honest pass.

| Dimension | Lines |
|---|---|
| Pyramid | unit tests for every tool + mock-model tests for graph structure; trajectory checks on dangerous/expensive steps; end-to-end task evals with judged outcomes |
| Dataset | curated from production failures, versioned, with provenance; refresh cadence + coverage metric |
| Judge | calibrated against human labels, agreement recorded per criterion; few-shots include boundary/disagreement cases; recalibration on a schedule |
| Metrics | success rate reported with CIs; tool metrics (selection, argument format AND value, hallucination); cost + safety suites in the portfolio |
| CI and release | unit + smoke gate on PRs, full dataset on merge; backtest before release, canary with kill switch after; online judged evals on sampled live traffic |
| Program | annotation queue with a standing review meeting; red-team pass per release with findings becoming tests; the five eval questions answered and current |

Scoring: below 20 = evals are theater; 20-35 = regressions caught, but late; 35-50 = regressions caught in CI and the loop is closing; 50+ = the eval-driven loop is the development process.
