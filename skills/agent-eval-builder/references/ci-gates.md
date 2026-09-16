# CI Gates

**Load this when:** wiring evals into CI, choosing which gate blocks vs alerts, sizing eval runs with statistical power math, handling flaky gates, running backtests/canaries/A-B tests, or reading eval reports like a statistician.

Evals in CI convert "the new prompt is probably fine" into a gate — a hard, mechanical decision point that either lets code through or does not.

## The pipeline that works

```
PR opened
  -> unit tests (seconds)                     fast, deterministic
  -> smoke subset of trajectory evals (min)   gate: block on failure
merge to main
  -> full dataset eval (hour)                 report + alert on regression
  -> backtest vs historical dataset           pre-release check
release
  -> canary 5% with online judged evals       kill switch ready
```

| Stage | Runs | Decision | Why |
|---|---|---|---|
| PR opened | unit tests + smoke trajectory subset | BLOCK | seconds-to-minutes feedback catches contract breaks before review |
| Merge to main | full dataset, 3-5 repetitions per row | report + ALERT | you do not hold every merge for an hour, but you always know when the full picture looks bad |
| Pre-release | backtest vs historical cases + red-team + safety suite | BLOCK on safety | the backtest is the pre-flight check; the CI gate is the tripwire — both necessary, neither sufficient alone |
| Release | canary 5% + online judged evals | expand or kill | offline for iteration, online for confirmation |

**WARNING — the gate you ignore today is the gate that has no teeth tomorrow.** Every bypassed gate ("we'll fix the eval after the release") is a step toward decorative. If the gate fires, roll back or fix — do not merge-and-pray. A gate that occasionally blocks a real regression is working; a gate that never blocks anything is not a gate. The safety gate is the one gate that should never have a bypass flag, because a safety regression shipped on a Friday is the story the compliance audit retells forever.

## Statistical power arithmetic (know it cold)

**Binomial CI** (normal approximation, fine for n over 30): observed rate p over n runs gives a 95% CI of p +/- 1.96 x sqrt(p(1-p)/n).

| n at p=0.9 | 95% CI half-width |
|---|---|
| 50 | +/- 8.3 points |
| 300 | +/- 3.4 points |
| 1,000 | +/- 1.9 points |

**Detecting a difference d** between two proportions with ~80% power: n = 16 x p(1-p) / d-squared runs per group.

| True delta d | Runs per group (p=0.9) |
|---|---|
| 0.03 (3 pts) | ~1,600 |
| 0.05 (5 pts) | ~580 |
| 0.10 (10 pts) | ~145 |

Consequences: (a) small evals have wide error bars, and ignoring them is how a 1-point wobble becomes a Friday-6pm rollback; (b) CI gates can only see large regressions (5-10 points) unless you spend thousands of runs per comparison — gate on 5+ points in CI and track fine-grained deltas online; (c) every report publishes intervals — "91% (+/- 3)". The first time a stakeholder asks why the metric "dropped 2 points" and you point at overlapping intervals, the noise conversation ends forever.

**Repetitions and variance:** repetitions reduce sampling variance (temperature) but not dataset variance (easy rows stay easy). If repetition count matters a lot, the dataset has high per-row variance — stratify it by difficulty before increasing repetitions blindly.

**Multiple comparisons:** a report with 12 criterion-by-criterion comparisons shows a "significant" 2-point change somewhere by chance alone; Bonferroni says require p below 0.05/12. When someone says "our new prompt improved 8 of 12 metrics, ignore the other 4," that should set off alarms.

## Flakiness controls

| Lever | Setting | Effect |
|---|---|---|
| Repetitions | run each row 3-5 times, take the mean | averages sampling noise |
| More rows | widen the dataset | reduces variance of the estimate |
| Deterministic harness | temperature=0, seeds where possible | helps, does NOT fully eliminate provider-side variance |

Critical principle: do not respond to flaky evals by lowering the bar — shrink the variance, or the gate becomes decorative. Note that temperature=0 does not fully eliminate variance (provider-side scheduling and batched inference survive); the variance shrinks, it does not vanish.

## Regression detection

Compare new vs baseline **distributions on the same dataset version**; report per-criterion deltas with confidence intervals, never a single pass/fail. A 1-point groundedness drop with a tight CI is actionable; a 2-point drop with a wide CI is "rerun." Gate dry-run discipline: the baseline passes; an injected regression blocks; overlapping intervals return inconclusive and trigger a rerun with more power (exit-code contract in references/templates.md).

Alert on eval deltas, not just error rates (T61): an agent that got 3% worse at routing generates zero 500s and quietly misroutes 3% of customers. Run a nightly regression eval and alert on any score that drops below baseline. Log the exact model version, prompt version, and tool versions with every trace (T60) — "why did behavior change?" is unanswerable six weeks later unless the answer is in the trace.

## Offline vs online, canary, A-B

| Mechanism | Answers | Use |
|---|---|---|
| Offline eval (fixed dataset) | "is the new version better on known cases?" | cheap, controlled, distribution-bound — the dataset is not your traffic, and the gap grows |
| Online eval (judged live sample) | "is it better on live traffic?" | sample 5-10% of live traffic, judge (not the user), plot quality over time; detects silent drift like a provider update degrading tool-calling |
| Canary | "is the new version safe to expand?" | deploy to 5% of traffic, compare online eval scores + error rates vs the old 95%; the kill switch must be a config flag or routing weight, NOT a redeploy — a canary you cannot kill in thirty seconds is a full release with extra steps |
| A/B | "which wins on outcomes?" | agent quality differences need 2-4x the sample size of UI tests because variance is higher; A/B only measures outcome metrics (satisfaction, conversion, escalation) — pair it with online judged evals or you learn "B is better" without learning why |

## Eval anti-patterns catalog

| Anti-pattern | What it looks like | Fix |
|---|---|---|
| The cherry-picked report | "improved on 12 of 15 cases," selected post-hoc, failures unreported | fix dataset and sample BEFORE the run; report all rows |
| The self-judged change | the engineer who changed the prompt also labels the golden answers | labeler rotation and author exclusion |
| The single-metric gate | correctness gated alone while groundedness collapses | criterion portfolio with a holistic anchor metric |
| The temperature=0 delusion | "our evals are deterministic" | repetitions and CIs regardless; provider nondeterminism survives |
| The judge-without-calibration | an unvalidated judge gates the release | calibration record or no gate |
| The dataset-that-grew-stale | month-18 traffic vs a month-18 dataset | coverage metric plus refresh cadence |
| The eval-vs-eval argument | judges disagree; team debates without labels | human labels are the tiebreaker |
| The "we'll eval later" release | pipeline deferred past launch, forever | evaluability at design time |
| Synthetic-only dataset | inflated success rates | mix in curated production failures |

Common thread: every anti-pattern saves an afternoon now and spends weeks of wrong decisions later. Eval discipline is not running more evals — it is refusing the evals that lie.

## The eval review meeting (30 minutes, standing)

Read in this order:

1. **Confidence intervals first** — which deltas are real (intervals separated) vs noise (overlapping)? About one-third of the "changes" in a typical report are noise; filter them before discussing anything.
2. **Criteria separately** — +3 correctness with -2 groundedness is not "+1 overall"; it is a trade that needs a product decision.
3. **Failures, not the average** — the mean hides failure classes; the most valuable page is "worst 10 runs with reasons," and three of them are usually new failure classes.
4. **Against the baseline trend** — a number that looks fine but sits on a six-month slide is an emergency wearing a calm face.
5. **Decide, don't just review** — every meeting ends in one of four verbs: ship, rollback, re-run with more power, or fix the eval. A meeting that ends in "interesting" has failed.

The meeting is also the accountability mechanism: judge calibration drifts only when nobody reads the calibration numbers, the dataset rots only when nobody notices coverage decline, and the gate gets bypassed only when nobody defends it in a room.
