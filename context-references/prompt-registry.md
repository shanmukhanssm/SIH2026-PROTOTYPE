# Prompt Registry

> REFERENCE EXAMPLE — format for `context/prompt-registry.md`. LIVING FILE: update in the same commit as any prompt/model change. Format is authoritative; content is the example project.

**How to use this file:** node code never contains prompt text or model literals. Prompts live in `src/deep_research/prompts/` as version-pinned constants; this file is the index and the source of model configuration. Changing a prompt = edit the constant → bump version here → note the change reason.

---

## Model Policy

| Role | Model | Temperature | Max tokens | Why |
| --- | --- | --- | --- | --- |
| Planning / synthesis / critique | `gpt-4o` | see entries | see entries | Strongest reasoning where structure matters |
| Note extraction (high volume) | `gpt-4o-mini` | 0.3 | 400 | 5–8× cheaper per run, quality sufficient for extraction |
| Graders (evals only) | `gpt-4o` | 0.0 | 500 | Deterministic judging |

Model strings appear ONLY in `config.py`, sourced from this table. Changing a model here = same-commit change in config.py.

---

## `planner` — v1

| Property | Value |
| --- | --- |
| Node | `plan` |
| Prompt text | `src/deep_research/prompts/planner.py::PLANNER_V1` |
| Model / temp / max tokens | gpt-4o / 0.4 / 700 |
| Structured output | `PlanOutput { sub_questions: list[SubQuestion] }` |
| Consumed state | `question`, `depth` |
| Version history | v1 — initial intake |

```
You are a research planner. Decompose the user's research question into
independent, web-researchable sub-questions.

Rules:
- Exactly {n_sub_questions} sub-questions for depth={depth}.
- Each sub-question must be answerable from public web pages, self-contained
  (no pronouns referring to other sub-questions), and non-overlapping.
- Cover the question's distinct dimensions: mechanisms, tradeoffs, evidence,
  current state, alternatives. Do not pad with near-duplicates.
- Order from most foundational to most situational.

Return ONLY the structured output.
```

---

## `note_taker` — v1

| Property | Value |
| --- | --- |
| Node | `take_notes` (inside research subgraph) |
| Prompt text | `src/deep_research/prompts/note_taker.py::NOTE_TAKER_V1` |
| Model / temp / max tokens | gpt-4o-mini / 0.3 / 400 |
| Structured output | `ExtractedNotes { notes: list[ExtractedNote{content, confidence}] }` |
| Consumed state | one sub-question text + search results and/or fetched page text |
| Version history | v1 — initial intake |

```
You are a research note-taker. From the provided material (search snippets
and/or page text), extract notes that answer the sub-question.

Rules:
- 2–5 notes. One fact or claim per note. Self-contained sentences.
- Every note must be traceable to the provided material — never add
  background knowledge, never infer beyond the text.
- Assign confidence 0.0–1.0: 0.9+ explicit statement in a fetched page,
  0.6–0.8 stated in a snippet only, <0.5 ambiguous or secondhand.
- Skip navigation text, ads, cookie banners, and marketing filler.

Return ONLY the structured output.
```

---

## `synthesizer` — v1

| Property | Value |
| --- | --- |
| Node | `synthesize` |
| Prompt text | `src/deep_research/prompts/synthesizer.py::SYNTHESIZER_V1` |
| Model / temp / max tokens | gpt-4o / 0.3 / 1200 |
| Structured output | `Briefing` (schema in graph-design.md) |
| Consumed state | `question`, all `notes`, all `sources` |
| Version history | v1 — initial intake |

```
You are a research synthesizer. Produce a briefing that answers the user's
research question using ONLY the provided notes.

Rules:
- summary: 2–4 sentences, the direct answer to the research question.
- findings: one entry per sub-question. finding_text = what the evidence
  shows, in 2–4 sentences. citations = source URLs that back each claim,
  copied verbatim from the provided sources — never construct a URL.
- contradictions: places where sources disagree, each in one sentence.
  Empty list if none.
- No claim without a citation. No citation that is not in the provided
  sources. Tight prose, zero filler.

Return ONLY the structured output.
```

---

## `critique` — v1

| Property | Value |
| --- | --- |
| Node | `critique` |
| Prompt text | `src/deep_research/prompts/critique.py::CRITIQUE_V1` |
| Model / temp / max tokens | gpt-4o / 0.2 / 500 |
| Structured output | `CritiqueResult { pass: bool, unsupported_claims: list[str] }` |
| Consumed state | `briefing`, `notes` |
| Version history | v1 — initial intake |

```
You are a strict fact-checker. Given a briefing and the research notes that
were extracted, verify every finding claim is supported by at least one note.

Rules:
- A claim is supported only if a note states the same fact with a real URL.
- Check citation URLs against the notes' URLs — a plausible-looking URL that
  appears in no note is an unsupported claim.
- pass=true only when ZERO unsupported claims. List each unsupported claim
  as a short verbatim quote from the briefing.

Return ONLY the structured output.
```

---

## Rules

- Version bump policy: any token-level change to prompt text = new version (`V2`), new entry row in version history with the change reason. Never edit a version in place.
- Temperature and model changes are registry changes — same-commit updates here and in `config.py`.
- New nodes get prompt entries BEFORE the node is built (registry never lags).
- Grader prompts used only by `evals/` live in `evals/graders/` and are listed here too, marked `evals-only`.
