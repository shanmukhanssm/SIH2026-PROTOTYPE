# Golden Datasets

**Load this when:** creating, curating, labeling, versioning, or refreshing a golden dataset; deciding how to use synthetic rows; standing up the human labeling program.

A golden dataset is a versioned, labeled set of inputs with expected outputs — the fuel of every eval above the unit level. Without a good dataset the evals measure nothing meaningful. T57: an eval without a golden set is a vibe check; curate 100-300 real, messy production cases with known-good outcomes before trusting any score, and the cases you personally found embarrassing are the most valuable ones in the set.

## Three sources, in order of value

| Source | Value | Caveat |
|---|---|---|
| Production traffic | the distribution is real because it IS your real distribution; curate failures (highest-value rows) plus a stratified sample of common intents | must be triaged for PII/licensing before storing |
| Hand-written edge cases | failure modes you know about before traffic does: injection attempts, multi-intent messages, twenty-turn conversations, adversarial tool results | limited by your imagination; essential for the attack surface |
| Synthetic generation | cheap breadth for coverage you cannot sample yet | biased toward easy cases; a synthetic-only eval inflates your success rate |

Practical pull from a trace store: filter by feedback type (thumbs-down), error status, or iteration count, and add to a versioned dataset. Last month's 143 thumbs-down runs are a treasure trove — the distribution of real failure that no synthetic generator reproduces.

## The curation workflow (recurring, not one-off)

1. **PULL** — query the trace store for the week's runs, grouped: all thumbs-down runs (failure gold); all runs with iterations over 2x baseline (loop suspects); a stratified sample, 15-20 rows per top intent (coverage layer).
2. **TRIAGE** — drop duplicates, unjudgeable rows (truncated, gibberish), and data you are not licensed to store (PII rules).
3. **LABEL** — per row: golden answer + rubric scores + trajectory expectations (the expected tool sequence). Labelers are domain owners, rotated; the author of a change never labels rows judging that change. Because a golden answer "reviewed by the author of the code being tested" is a conflict of interest, not a label.
4. **BOUNDARY-CHECK** — the dataset must cover near-boundary cases (right answer, wrong trajectory; partial-credit rows). Because the judge struggles exactly at boundaries, so that is where coverage pays.
5. **VERSION** — append to a new dataset version; never mutate a pinned one; record date range and sampling rules in the version metadata. Because experiments compared across silently-edited rows are meaningless.
6. **VERIFY** — run the judge over the new rows and spot-check judge-vs-human agreement on a sample; the calibration check rides along.

Cadence: weekly for failure rows (cheap to curate, high-value), monthly for the full stratified refresh (the coverage layer decays slower). The failure pattern: dataset "done" after week one, traffic shifts, offline evals keep passing while production quality drops, and the team learns about it from angry customers. Dataset curation is a standing operational task — budget the hours every week or the whole eval edifice silently decouples from reality.

## Row schema

Use one JSON object per line (JSONL); a loader with validation is in references/templates.md.

```json
{
  "id": "support-0001",
  "input": "my order 5581 arrived broken, refund it",
  "reference": "Refund approved after identity check; 30-day window applies.",
  "expected_trajectory": [
    {"expect_tool": "verify_identity", "before": "get_account_data"},
    {"expect_check": "no-sensitive-action-without-approval"}
  ],
  "criteria": {"correctness": 5, "groundedness": 5, "helpfulness": 4},
  "tags": ["refund", "hard", "tier-1"],
  "provenance": "production",
  "source_trace_id": "tr-2026-0512-8841"
}
```

Fields: `id` stable; `input` verbatim; `reference` golden answer or the literal string UNKNOWN when unknowable; `expected_trajectory` tool sequence and binary checks the run must satisfy; `criteria` human rubric scores (present whenever the row feeds judge calibration); `tags` intent/difficulty/tier for slicing; `provenance` production | handwritten | synthetic; `source_trace_id` for provenance audits.

## Dataset hygiene: three rules

1. **Version the dataset.** A dataset is an artifact, not a folder. Version it, run evals against pinned versions, never silently edit rows an experiment used.
2. **Beware contamination** — three sneaky forms: (a) golden answers written by the same model family you are testing ("the model agrees with the model" scores high); (b) the fix was tuned until the dataset passed, making the dataset a mirror, not a measure; (c) eval awareness — frontier models have increasingly seen benchmark content in training, and agent eval datasets are vulnerable the same way; Anthropic's 2026 postmortem on Opus 4.6's BrowseComp results is the canonical discussion of contamination masquerading as capability.
3. **Label honestly.** Rotate reviewers so the dataset owner is never the feature owner. Because self-judged changes convert the pipeline's cost into its meaning.

Rotate and refresh: treat a static dataset as a depreciating asset.

## Synthetic data: the careful version

| Mode | Rule | Because |
|---|---|---|
| Synthetic inputs, human labels | LLM generates 200 refund-inquiry variants; humans label them | inputs are cheap to generate and hard to label wrongly; the labels stay honest even though the distribution bias remains |
| Synthetic labels, verified on a sample | human checks 10-15% of generated labels; if the error rate exceeds threshold, discard the whole batch | a synthetic label set with 15% error poisons judge calibration beyond repair |
| Adversarial synthetic | generate rows designed to break the agent: multi-intent, injection-laden, near-boundary | fills the hard end of the distribution where production sampling is sparse — the reverse of the usual too-easy bias, if aimed correctly |
| Coverage vs truth split | synthetic for breadth (rare intents, edge combinations); production for truth (real distribution and difficulty) | a dataset 70% synthetic is a coverage tool; a dataset 100% synthetic is a fantasy measurement |

Meta-rule: synthetic data is a supplement with a known bias, not a replacement. Track the synthetic fraction, report eval results separately for synthetic and production subsets, and when they disagree (synthetic high, production low — the classic pattern), trust production and fix the generator's realism. The disagreement itself measures the gap between your synthetic distribution and reality.

## Dataset drift and rot

| Failure mode | Symptom | Detection | Fix |
|---|---|---|---|
| Dataset drift | online (live) evals diverge from offline (dataset) evals for weeks | coverage metric: what fraction of live inputs has a dataset row within embedding distance X; alert on decline | scheduled re-curation from recent traffic |
| Stale dataset | month-18 traffic evaluated against a month-18 dataset — numbers are real and meaningless | refresh cadence missing | the curation workflow above, on a calendar |

Write down the expiration date on the day you ship an eval: datasets rot in months, judges in quarters. The failure mode is silent — the pipeline still runs, prints numbers, gates PRs, and has stopped measuring the real system. The quarterly "eval the evals" ritual (recalibrate the judge, refresh the dataset, check online-vs-offline agreement) is the maintenance task that keeps the whole edifice from becoming a cargo cult.

## The human eval program

- **Annotation queues, not marathons.** A standing queue where sampled runs land for labeling; about 50 rows per week beats quarterly bursts. Because steady labels feed continuous judge calibration and dataset growth instead of sporadic dumps.
- **Who labels matters.** Domain experts handle correctness; rotated reviewers handle tone and helpfulness; never the feature's author for their own feature. The labeler sees input, trajectory, output, and the rubric with anchors — but NOT the judge's score, to avoid anchoring the human to the judge.
- **Disagreement is signal.** Human-judge disagreements are the highest-value rows in the system: either the judge is wrong (fix it with few-shots) or the rubric is ambiguous (sharpen it). Route every disagreement into the calibration set; treat the disagreement rate as a health metric.
- **Close the loop with a weekly review.** 30 minutes with the 10 worst labeled runs of the week, with the people who can fix them. Because labels without review are data without decisions — and the queue dies the week nobody reviews it, then stays dead. Scheduling, not motivation, keeps the program alive: the failure review meeting is standing, with the queue at the top of the agenda.

## Eval program budget (honest numbers)

| Line item | Cost | Notes |
|---|---|---|
| Dataset curation | 2-4 hours/week standing | the failure-review meeting doubles as curation time |
| Judge token spend | ~$0.005-0.03 per judged run | 500 runs/week = $10-60/month — the cheapest line |
| Calibration labels | 1 afternoon/quarter | 200 rows x 1-2 min each |
| Annotation queue | 30 min/week + review meeting | the standing program |
| CI compute | dataset runs x run cost | an hour of eval runs nightly = agent cost x dataset size |
| Online evals | judge x sample rate | 5-10% of traffic judged |

Total: roughly one engineer-day per month plus a token bill that rounds to noise — 3-5% of project cost for the layer that decides every release. One uncaught regression costs a week of debugging, a trust hit, and often a rollback; the program is priced at about one-tenth of one bad release per month and prevents several. When the program gets "temporarily paused," the pause is a bet that no regression will happen — and the eval-driven loop is the argument that this bet loses.
