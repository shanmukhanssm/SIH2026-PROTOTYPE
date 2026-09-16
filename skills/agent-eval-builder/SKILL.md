---
name: agent-eval-builder
description: >-
  Build the evaluation system for an agent: layered eval taxonomy (unit, tool, trajectory,
  end-to-end, cost, safety), golden dataset curation from production failures, deterministic
  trajectory checks, an LLM-as-judge with rubric and human-agreement calibration, CI regression
  gates with statistical power math and flakiness controls, red-team suites, and the
  eval-driven development loop with the eval written before the fix.
  Trigger: "build evals for my agent", "a golden dataset", "LLM-as-judge",
  "judge calibration", "trajectory evaluation", "CI eval gate", "agent regression testing",
  "red-team suite", "my evals are flaky", "eval-driven development",
  "is my agent getting worse".
  Do NOT use for: wiring tracing or observability (agent-debugger); debugging a live incident
  or finding a regression's root cause (agent-debugger); retries, idempotency, circuit
  breakers (agent-reliability-hardener); guardrail code and injection defense
  (agent-guardrails-builder); HITL interrupts and approval flows (hitl-builder).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# agent-eval-builder

## Overview

Agent evals are a statistical instrument, not a binary gate: the same input produces different trajectories run to run, so "did this run pass" is the wrong question — "what fraction of runs succeed, within what confidence interval" is the right one. This skill builds the measurement system for an existing agent: the layered eval suite, the golden dataset it runs on, the deterministic trajectory checks and calibrated LLM-as-judge that score it, the CI gate that blocks regressions, and the loop that converts every production failure into a permanent test. Stage: **HARDEN** (verification). It assumes the agent is already built and traced; it authors eval suites and gates — it does not instrument tracing or fix a regression's root cause.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/eval-taxonomy.md | Choosing which eval layers to build; mapping evals to SDLC stages; tool-specific eval patterns (selection, args, hallucination, recovery); the binary check catalog; maturity model; the five eval questions |
| references/golden-datasets.md | Curating, labeling, versioning, or refreshing a golden dataset; contamination rules; synthetic data the careful way; the human annotation program |
| references/judge-design.md | Writing the judge prompt line by line; rubric anchors and few-shots; the four judge biases; the calibration playbook; judge decay and judge tests |
| references/ci-gates.md | Wiring the CI pipeline and gate thresholds; sample-size and confidence-interval math; flakiness controls; backtest, canary, A-B; the eval anti-patterns catalog |
| references/templates.md | Writing the Python: golden dataset schema + loader, trajectory checker, tool-arg eval, LLM-as-judge function, calibration script, CI gate script |

## Execution Checklist

- [ ] 1. Pick eval layers from the taxonomy (unit/tool, trajectory, end-to-end, online) plus cost and safety suites; every skipped layer gets a written reason.
- [ ] 2. Build golden dataset v1 from production failures plus a stratified intent sample; version it; record provenance; no author labels their own change.
- [ ] 3. Write deterministic trajectory checks (binary, from the catalog) for every dangerous or expensive step BEFORE any scored judge.
- [ ] 4. Author the LLM-as-judge (role separation, anchors, UNKNOWN rule, JSON output, few-shots) and calibrate against 150-300 human labels per criterion.
- [ ] 5. Wire CI gates: unit + smoke block PRs; full dataset alerts on merge; backtest pre-release; repetitions and confidence intervals everywhere.
- [ ] 6. Add red-team (100-500 adversarial cases per release), cost (median +10% / p95 +25% vs baseline), and safety (refuse / no-leak / no-act / boundary) suites.
- [ ] 7. Run the eval-driven loop: telemetry failure, hypothesis, eval written BEFORE the fix, fix, canary confirm, failures flow back into the dataset.
- [ ] 8. Verify the eval system itself: judge-human agreement re-measured, gate dry-run (baseline passes, injected regression blocks), one historical regression replayed and caught.

## Step-by-Step Workflow

### Step 1 — Pick eval layers from the taxonomy [GUIDED]

Read references/eval-taxonomy.md and select layers per the table below. Default floor: unit/node tests + trajectory checks + a judged end-to-end set + cost/safety suites.

| Failure you fear | Layer that catches it |
|---|---|
| Tool contract bug, parser break, schema violation | Unit/node test (mock model, golden-record tools) |
| Routing logic, sub-agent output contract | Component/subgraph test with stubbed dependencies |
| Wrong tool, loops, missing steps, unsafe sequences | Trajectory eval (binary checks) |
| Overall success rate on real tasks | End-to-end task eval with judged outcomes |
| Silent drift, provider model update damage | Online eval on sampled live traffic |
| Bill explosion from a prompt change | Cost suite (distribution check, not per-run gate) |
| Injection, leakage, unauthorized action | Red-team + safety suite |

Why: push failures down the pyramid — a tool bug should be caught by a unit test (cents, milliseconds), not an end-to-end eval (fifty cents, three minutes, a judge call); teams that only run end-to-end evals spend ~100x more for a tenth of the diagnostic resolution, and teams that only run unit tests ship agents that fail in the field. Verification: answer "how would we know if this agent got worse?" with a named metric, test, or judge for each layer — an unevaluable agent is an unchangeable agent.

### Step 2 — Build golden dataset v1 [EXACT]

Follow the six-step curation workflow in references/golden-datasets.md: PULL (all thumbs-down runs, all runs over 2x iteration baseline, stratified 15-20 rows per top intent), TRIAGE (drop duplicates, unjudgeable rows, PII you cannot store), LABEL (golden answer + rubric scores + expected trajectory), BOUNDARY-CHECK (add near-boundary rows: right answer wrong trajectory, partial credit), VERSION (append to a new version, never mutate a pinned one; record date range and sampling rules), VERIFY (judge-vs-human spot check).

- Target 100-300 rows for v1. Because an eval without a golden set is a vibe check, and production failure rows are the only source of the real failure distribution — the 143 thumbs-down runs from last month are a treasure no synthetic generator can reproduce.
- Labelers: domain owners, rotated; the author of a change never labels rows judging that change. Because a golden answer reviewed by the author of the code being tested is a conflict of interest, not a label.
- Synthetic rows allowed for coverage only, with the care rules from references/golden-datasets.md (synthetic inputs + human labels, or sample-verified labels; keep synthetic fraction reported). Because synthetic-only datasets bias easy and inflate success rates.

Verification: dataset version is pinned with provenance metadata; every labeled row names a labeler who is not the feature author; synthetic vs production fractions are recorded.

### Step 3 — Write deterministic trajectory checks first [EXACT]

Open references/templates.md (trajectory checker) and implement binary checks from the catalog in references/eval-taxonomy.md for every dangerous or expensive step: no-sensitive-action-without-approval, identity-verified-before-data, searched-before-answered, retried-with-correction, cited-every-claim, stayed-in-toolset, within-iteration-budget, no-reasoning-in-answer. Each check returns pass/fail with a detail string.

- Write these BEFORE the LLM judge and BEFORE fixes. Because "did the refund happen before the approval?" is a mechanical question with near-perfect reliability, while "how helpful was this answer?" is a judgment call judges get wrong 15-40% of the time; and the eval-before-fix ordering converts every fix into a permanent regression test (teams that write the eval after discover the fix only fixed the one example they looked at).
- One check = one property = one failure class. Because a compound check that fails tells you nothing diagnostic.

Verification: each check has a red test — a hand-built trace that must fail it — and a green trace that must pass; run both in unit CI (seconds).

### Step 4 — Author the LLM-as-judge and calibrate it [EXACT]

Write the judge per references/judge-design.md: role separation ("judge only; never answer the user"), one criterion per score dimension with scale anchors, the UNKNOWN-reference rule, mandatory JSON output, and 8-12 few-shot scored examples including boundary cases. Then run the calibration playbook (references/judge-design.md Step-by-step):

1. Build a calibration set of 150-300 production rows, hand-labeled by domain owners. Cost: an afternoon — this is the entire budget, and skipping it converts the judge into a plausible-sounding number generator.
2. Run the judge over the same rows; compute per-criterion agreement (bucket 0-5 scores to pass/partial/fail).
3. Apply the decision rule: agreement 0.85 or above gates; 0.70-0.85 trends only, not gating; below 0.70 observation-only or redesign the criterion. Because a judge at 0.61 agreement gating a release is noise wearing a rubric.
4. Mine the disagreements: judge too harsh or lenient becomes few-shot boundary examples; ambiguous rubric gets sharpened; missing domain knowledge goes into the judge prompt. Expect 0.05-0.15 agreement improvement from this step.
5. Record the calibration: judge model + version, prompt hash, agreement per criterion, calibration set version, date. Because the quarterly recalibration compares against this record.

Judge biases to design against: position (randomize order, judge both ways), verbosity (explicitly forbid rewarding length), self-preference (never let the judge share a model family with the judged agent uncritically), acquiescence (never embed the reference inside the candidate text). Verification: calibration record exists and no criterion below 0.70 is used for gating; helpfulness-style taste criteria are demoted to observation.

### Step 5 — Wire CI gates with power math and flakiness controls [EXACT]

Build the pipeline from references/ci-gates.md:

| Stage | Runs | Decision |
|---|---|---|
| PR opened | unit tests + smoke subset of trajectory evals | BLOCK on failure |
| Merge to main | full dataset eval, 3-5 repetitions per row | report + ALERT on regression |
| Pre-release | backtest vs historical dataset + red-team + safety suite | BLOCK on safety; review others |
| Release | canary 5% with online judged evals | kill switch ready in 30 seconds |

- Gate on large regressions (5-10 points) in CI; leave fine-grained tracking to online evals. Because detecting a difference d needs roughly n = 16 p(1-p) / d-squared runs per group: at p=0.9 and d=0.03 that is about 1,600 runs per group, and 50-run evals carry a 95% CI of plus or minus 8.3 points, so a "5-point regression" there is pure noise.
- Every eval report publishes "91% (+/- 3)" style confidence intervals; repetitions average sampling noise but not dataset variance. Because numbers without intervals turn a 1-point wobble into a Friday-evening rollback decision.
- Never respond to flaky evals by lowering the bar — shrink variance with repetitions, more rows, and fixed temperature instead. Because the gate you quieten becomes decorative.

Verification: gate dry-run — the current baseline passes, and an injected 8-point synthetic regression blocks the build with exit code 1; an overlapping-interval result returns inconclusive and triggers a rerun, not a merge.

### Step 6 — Add red-team plus cost and safety suites [GUIDED]

Red-team (references/ci-gates.md and templates): define the adversary model, map every input channel (user text, documents, tool results, fetched pages — the surface is always wider than you think), build an attack catalog (prompt injection, jailbreaks, exfiltration probes, unauthorized-action probes, PII harvesting, resource exhaustion), run 100-500 cases per release against the full system — not the model alone, because injection success depends on tool wiring. Score refusal rate, leakage rate, unintended-action rate; failures become guardrail tests AND dataset rows.

Cost suite: assert distribution budgets on the dataset — median cost per run within baseline +10%, p95 within baseline +25% — on every prompt change. Because prompt changes are the number one source of silent cost regressions (a reworded prompt breaking the cache, a "be thorough" instruction doubling iterations), and cost is an observability signal, not an accounting detail.

Safety suite, four parts: refusal cases (must refuse), leakage cases (must not leak), policy cases (must not act), boundary cases (must behave — a legitimate refund just under the cap must succeed, because over-blocking is also a safety failure). This is the one eval gate that should never have a bypass flag. Verification: red-team metrics trend per release; cost and safety suites appear in the combined release report next to quality — a 4-point quality gain that costs 60% more is a budget decision, and only the combined report surfaces that.

### Step 7 — Run the eval-driven development loop [FREEFORM]

Operate the master loop weekly, not per-release: (1) telemetry shows WHERE it fails — filter failing traces and read them; (2) failure analysis turns traces into hypotheses ("fails because the planner picks the wrong tool"); (3) encode each hypothesis as an eval; (4) fix until the eval passes; (5) release behind a canary and confirm with online evals; (6) new failures flow back to step 1 and the dataset grows.

- Write the eval before the fix. Because it is the cheapest lie detector in the pipeline — it forces the fix to address the failure class, not the single instance.
- Keep the human program alive: a standing annotation queue (about 50 rows/week), labels feeding calibration, and a 30-minute weekly failure review of the 10 worst labeled runs with the people who can fix them. Because the annotation queue dies the week nobody reviews it, and it stays dead.
- Judge, dataset, and gate are living assets with expiration dates: datasets rot in months, judges in quarters. Run a quarterly "eval the evals" ritual (recalibrate judge, refresh dataset, check online-vs-offline agreement). Because the failure mode is silent — the pipeline still prints numbers and gates PRs while measuring a system that no longer exists.

Verification: the team can state how many failure classes exist, how many have evals, and how many have fixes — bugs have become inventory, and "we changed the agent" and "we ran the evals" are the same sentence.

### Step 8 — Verify the eval system itself [EXACT]

Run all three verifications and record the results:

1. Judge agreement: re-measure judge vs human on the rolling 50-row calibration set; alert on agreement drops. Because judge decay after a model or prompt update inherits new biases and calls them your agent's regressions.
2. Gate dry-run: baseline passes; an injected regression (edited results file) blocks with exit code 1; overlapping CIs return inconclusive. Because a gate that has never blocked anything is not a gate.
3. Regression replay: take one real historical incident row, revert its fix (or replay the old prompt), and confirm the suite catches it. Because a suite that passes everything including last month's incident is measuring nothing.

Also re-read the eval scorecard in references/eval-taxonomy.md with evidence (CI config, calibration record, meeting invite) — fill it honestly, expect the score to drop by a third on the first honest pass, and use the delta as the backlog.

## Examples

1. Simple — two-tool FAQ bot: unit tests for both tools with golden-record responses; two binary trajectory checks (searched-before-answered, stayed-in-toolset); a 60-row dataset from early failures plus hand-written edge cases; one 2-criterion judge (correctness, groundedness) calibrated on 150 labels; PR smoke gate blocking on trajectory checks. Output: a floor eval stack that catches most contract breaks in seconds of CI.
2. Typical — support agent, four-week build (the worked pipeline): Week 1 unit + mock-model routing tests (catches ~70% of future regressions); Week 2 dataset v1 = 143 thumbs-down runs + 61 over-iteration runs + 200 stratified intent rows, versioned and human-labeled; Week 3 judge with 3-criterion rubric, calibration shows 0.84 correctness / 0.79 groundedness / 0.62 helpfulness, so helpfulness is demoted to observation-only, plus two binary checks (refund requires approval, identity verified before data reveal); Week 4 gate: unit on PR (2 min), full 404-row dataset on merge with 3 repetitions (nightly), backtest vs 60 days of cases pre-release, online judged evals on 5% of traffic. First-month payoff: three regressions caught in CI before users saw them.
3. Edge — "is the 3-point improvement real?": 40 runs before (82% success), 40 after (85%). CI at n=40 is roughly plus or minus 12 points — the intervals overlap completely. Detecting a true 3-point difference needs about 1,600 runs per group. Response: rerun with more rows and repetitions before believing the delta; if the claim survives, it survives with intervals attached.

## Known Gotchas

1. **Gate bypassed "just this once."** Symptom: team merges with the eval gate red; six weeks later nobody remembers the gate ever blocking anything. Cause: the gate fired on un-modeled noise (no repetitions, no CIs), so engineers learned to ignore it. Response: fix variance control first, then never bypass — roll back or fix; every bypass is a step toward a decorative gate.
2. **Uncalibrated judge gating releases.** Symptom: scores move week to week but nobody can say what they mean; a "regression" appears after swapping the judge model. Cause: no human-labeled calibration; the judge's bias direction was never measured. Response: label 150-300 rows, record agreement per criterion, gate only on 0.85+, re-check after any judge-model swap.
3. **Single-run pass/fail on stochastic evals.** Symptom: "flaky" red builds; "ran again and it passed." Cause: the eval samples a distribution; a test passing 3 of 5 runs is not flaky, it is sampling. Response: repetitions (3-5 per row), more rows, CIs on every report; never lower the bar to quiet the noise.
4. **Outcome-only evals.** Symptom: evals green while a correct answer was reached via a hallucinated tool call; right answer, dangerous path. Cause: no trajectory layer, so the diagnostic signal of the steps is discarded. Response: binary trajectory checks on every dangerous or expensive step; keep the sequence signal.
5. **Synthetic-only dataset.** Symptom: offline eval 95% while users complain; synthetic rows score high, production rows score low. Cause: synthetic generation biases toward easy cases. Response: curate production failures as the truth layer, report synthetic and production subsets separately, trust production, fix the generator's realism.
6. **Dataset rot.** Symptom: offline evals keep passing while live quality drops; the divergence is discovered by customers. Cause: traffic shifted (new intents, seasonal topics) and the pinned dataset no longer represents it. Response: coverage metric (fraction of live inputs near a dataset row), scheduled re-curation, weekly failure rows.
7. **Judge decay.** Symptom: judge scores trend upward while user feedback stays flat. Cause: generation-model or answer-style drift changed the population the judge scores. Response: rolling 50-row human-labeled set re-scored monthly; recalibrate and add the new disagreements as few-shots.
8. **Self-judged change.** Symptom: golden answers written by the prompt's author pass suspiciously easily; "fix" validated only on the example it was written for. Cause: labeler rotation missing; eval written after the fix. Response: rotate reviewers, exclude the change's author, write the eval before the fix.
9. **Temperature=0 delusion.** Symptom: "our evals are deterministic," yet results still vary run to run. Cause: provider-side nondeterminism (scheduling, batched inference) survives temperature 0. Response: repetitions and confidence intervals regardless; treat temperature 0 as variance reduction, not elimination.
10. **Single-metric gate (Goodhart).** Symptom: groundedness hits 4.8 while users complain the model now cites irrelevant sources. Cause: the team optimized the one gated metric. Response: never gate on one metric alone; keep a holistic held-out anchor (user satisfaction, escalation rate) and treat criterion scores as diagnostics.
