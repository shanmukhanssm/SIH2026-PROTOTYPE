# Judge Design

**Load this when:** writing or revising an LLM-as-judge prompt, choosing rubric criteria and anchors, adding few-shots, running the calibration playbook, or diagnosing judge drift.

An LLM judge takes an input, a trajectory, and an output, and returns a score or verdict. It is the only scalable way to score thousands of agent runs, and it is wrong some fraction of the time. The discipline is not pretending the judge is perfect — it is measuring and minimizing that fraction. Give the judge the rubric, not vibes: judges with calibrated examples beat judges with longer instructions, consistently.

## The judge prompt, line by line

```
JUDGE_SYSTEM = """
You evaluate a customer-support agent's output.                     # role
You are NOT the support agent. Judge only; never answer the user.   # role separation

Evaluate three criteria independently:
1. correctness (0-5): the answer matches the reference facts.
2. groundedness (0-5): every factual claim cites a source that supports it.
3. helpfulness (0-5): the answer addresses the user's question; 0 if it dodges.

Anchors: 5=no errors, 3=partially right, 1=mostly wrong, 0=absent/garbage.   # anchors

Do NOT reward length, tone, or formatting.                          # kills verbosity bias
If the reference is marked UNKNOWN, score correctness 5 unless the answer
asserts facts it cannot support - then score 1.                     # UNKNOWN rule

You must output JSON: {"correctness": int, "groundedness": int,
                       "helpfulness": int, "reason": str}           # structured output
"""

JUDGE_FEWSHOT = [ ... ]   # 8-12 scored examples, at least 2 per criterion boundary,
                          # hand-checked against human labels
```

Five load-bearing design decisions:

1. **Role separation.** Judges told they are the agent will sometimes answer the user's question instead of scoring it — a real failure when both roles share a template.
2. **Anchors.** A bare 0-5 scale drifts between runs, making trend lines meaningless.
3. **The UNKNOWN rule.** Judges fabricate reference facts when none are provided, silently wrecking correctness scores. Give them permission to say "cannot judge" and route those rows to humans.
4. **Structured output.** Free-text verdicts need parsing, and parsing failures silently drop scores; JSON eliminates the failure class.
5. **Few-shots from disagreements.** The highest-leverage examples are the rows where judge and human disagreed, because they encode the boundary where the judge is wrong.

Rubric template (adapt per agent): one criterion per score dimension, each with a definition, anchors, and what does NOT count (length, tone, formatting). Score criteria independently — a single blended "quality 0-10" hides trade-offs like better answers with worse sourcing.

## The four judge biases

| Bias | What happens | Fix |
|---|---|---|
| Position | pairwise comparisons favor the first candidate | randomize order; judge both ways |
| Verbosity | judges reward longer answers | "do not reward length" in the prompt, or compare length-matched pairs |
| Self-preference | judges rate outputs from their own model family higher | treat judge-same-family scores with suspicion; prefer a different judge family |
| Acquiescence | judges agree with a suggested answer when one is shown | never show the reference inside the candidate text being judged |

## Where judges are reliable, where they lie

| Reliable | They lie |
|---|---|
| correctness against a known reference | taste (tone, voice) |
| groundedness against retrieved sources | "would a user be satisfied" |
| format compliance | anything requiring facts not in the judge's context |
| tool-call correctness | the judge confidently scores a factually wrong answer as correct if it shares the same ignorance |

For the right-hand column: use human labels or reference-based checks only. Pairwise comparison belongs here too — when "is this better?" is easier to judge than "how good is this?" (prose, tone, helpfulness), run head-to-head: same input, both outputs, judge picks a winner or tie. Pairwise is more stable for relative decisions ("did my prompt change help?") and worse for absolute thresholds ("is success above 90%?"). Use both: absolute scores for SLOs, pairwise for iteration.

## The calibration playbook

1. **Build the calibration set** — 150-300 rows sampled from production, hand-labeled by domain owners (NOT feature authors), all criteria per row, with anchors. Cost: an afternoon to a day. This is the entire budget of the exercise.
2. **Run the judge over the same rows.** Compute per-criterion agreement: simple agreement on 0-5 scales bucketed to {pass, partial, fail}, or weighted kappa for rigor. Record the numbers (example outcome: correctness 0.83, groundedness 0.79, helpfulness 0.61).
3. **Decision per criterion:** agreement 0.85+ gates; 0.70-0.85 usable for trend detection, not gating; below 0.70 observation-only or redesign the criterion. Do not ship a gate on a 0.61 criterion.
4. **Mine the disagreements** (the highest-value ~15% of rows): judge too harsh or lenient becomes few-shot boundary examples; ambiguous rubric gets sharpened wording; missing domain knowledge goes into the judge prompt. Re-run and re-measure; expect 0.05-0.15 improvement from this step alone.
5. **Record the calibration**: judge model + version, prompt hash, agreement per criterion, calibration set version, date. The quarterly recalibration compares against this record.

Also quantify the **bias direction**: a judge that over-rates everything by half a point still tracks relative improvement but invalidates absolute SLOs. Calibrate per criterion — groundedness judges are usually more reliable than helpfulness judges.

**Highest-ROI eval investment:** 100-300 human-labeled rows for calibration. One afternoon of labeling 200 rows, computing agreement per criterion, and adding the 30 disagreement cases as few-shots moved one team's judge from 0.62 to 0.85 agreement — more than any prompt rewrite would have. Teams that skip it have a plausible-sounding number generator.

## Judge decay and judge tests

- **Judge decay:** agreement with humans drifts as generation models, judge models, or answer styles change. Symptom: judge scores trend upward while user feedback does not. Detection: a rolling calibration set of 50 human-labeled rows, re-scored monthly; alert on agreement drops. Fix: recalibrate and add few-shots from the new disagreements. Because swapping the judge model inherits a new judge's biases and calls them your agent's regressions.
- **Test the judge like production code:** a judge eval suite of 50 labeled rows with known scores, run after every judge prompt change. Because a "better" judge prompt can silently change scoring semantics (helpfulness now means politeness) and three weeks of trend lines lie to you. The judge is software; software gets tests.
- **Goodhart's law onset:** the team optimizes the metric until it stops meaning what it meant ("groundedness 4.8 by citing everything, including irrelevant sources"). Symptom: single-metric gains with flat or declining user outcomes. Fix: never gate on one metric alone; keep a holistic held-out anchor (user satisfaction, escalation rate) and treat criterion scores as diagnostics.
- **Judge-vs-judge arguments:** two judges disagree and the team debates which is "right" without labels. Fix: human labels are the tiebreaker, and they cost an afternoon.

Runnable code: the judge function and the calibration script are in references/templates.md.
