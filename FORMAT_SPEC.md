# FORMAT SPEC — Mandatory Rules for Every Skill Package (v1.0)

Every skill package MUST comply with this spec exactly. Non-compliance fails validation.

## 1. Directory layout

```
skills/<skill-name>/
├── SKILL.md                  # ≤ 400 lines. The TOC + workflow. Progressive disclosure.
└── references/               # ONE hop from SKILL.md only. Descriptive filenames.
    ├── <topic>.md            # e.g. fallback-ladders.md, supervisor-prompt.md
    └── templates.md          # runnable code templates (if skill produces code)
```

No nested references (references/a/b.md is FORBIDDEN — two hops get missed by the loading agent).

## 2. SKILL.md structure (exact order)

```markdown
---
name: <kebab-case, identical to directory name>
description: >-
  <What it does. Then "Trigger:" + 5-10 phrases a real user would say.
  Then "Do NOT use for:" + 3-5 near-miss exclusions.>
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# <skill-name>

## Overview
One paragraph: what problem it solves, when to load it, what it assumes.
State which agent-building stage it belongs to (design / build / harden / operate).

## When to Load Which Reference File
| File | Load when... |
|------|--------------|

## Execution Checklist
- [ ] 1. ... (5-12 one-line items, the whole workflow scannable in 5 seconds)

## Step-by-Step Workflow
### Step 1 — <name> [EXACT|GUIDED|FREEFORM]
...action + why + verification...
(4-8 steps. Label every step: EXACT = verbatim/required, GUIDED = principles + adapt,
 FREEFORM = judgment call. Every rule carries a "because ..." reason.)

## Examples
2-3 concrete input → output pairs (simple, typical, edge-case).

## Known Gotchas
Numbered. Each: **Symptom.** → **Cause.** → **Response.** (5-10 items, ordered by cost)
```

## 3. Frontmatter rules

- `description` total ≤ 1024 chars, NO angle brackets `<` `>` anywhere in frontmatter.
- Triggers mirror real user language ("build a LangGraph graph", "add a reducer", "supervisor agent"...).
- Exclusions exclude SIBLING skills' jobs (see §6 roster).
- `allowed-tools`: comma list, minimal set justified by the workflow.

## 4. Content rules

- Self-contained: never "as mentioned above/earlier/before". Assume zero session history.
- No definitions of industry-standard concepts (HTTP, REST, git). Only what is unique to this skill.
- Bullets and tables over paragraphs. If a section reads like a textbook, cut it.
- Every directive: rule + "because <concrete consequence>".
- Code templates: syntactically valid as-is; use {{PLACEHOLDER}} for required customization
  points and `[optional]` comments for optional parts. Python 3.10+ / LangGraph python idioms
  (langchain-core, langgraph). ONLY use APIs that appear in the source chapters — never invent.
- Reference files: each starts with a one-line "**Load this when:** ..." note.
- All content in English.

## 5. Quality bar (from source book)

- Decision points use TABLES (symptom→cause→fix, use-when, comparison matrices).
- Include the counter-intuitive warnings from the source (sharp edges, anti-patterns).
- Every workflow ends with a verification step.

## 6. Roster — sibling exclusions (copy the relevant line into your description)

1. agent-architecture-advisor — decides WHAT to build (patterns, topology, framework). Does NOT write graph code (langgraph-builder), does NOT implement tools (agent-tool-designer).
2. langgraph-builder — writes single-graph LangGraph code (state/edges/Send/Command/checkpointers). Does NOT choose architecture (agent-architecture-advisor), does NOT build multi-agent topologies (multi-agent-builder), does NOT design tool interfaces (agent-tool-designer).
3. agent-tool-designer — designs tool schemas/descriptions/error contracts and agent-facing prompts. Does NOT wire graphs (langgraph-builder), does NOT choose patterns (agent-architecture-advisor).
4. multi-agent-builder — implements supervisor/handoff/hierarchical/network topologies and inter-agent messaging (A2A/MCP). Does NOT build single graphs (langgraph-builder), does NOT design the individual agents' tools (agent-tool-designer).
5. hitl-builder — interrupt(), approval payloads, resume, audit trails. Does NOT build the surrounding graph (langgraph-builder), does NOT build evals (agent-eval-builder).
6. agent-memory-builder — memory taxonomy, Store API, extraction, write/forget policies. Does NOT design run-scoped state schemas (langgraph-builder), does NOT build evals (agent-eval-builder).
7. agent-reliability-hardener — retries, idempotency, fallback ladders, circuit breakers, durable execution, SLOs. Does NOT debug live incidents (agent-debugger), does NOT add safety guardrails (agent-guardrails-builder).
8. agent-eval-builder — golden datasets, LLM-as-judge, trajectory evals, CI gates, red-team evals. Does NOT instrument tracing (agent-debugger), does NOT fix regressions' root cause (agent-debugger).
9. agent-guardrails-builder — threat models, prompt-injection defense, tool security, PII, moderation, sandboxing. Does NOT handle random runtime failures (agent-reliability-hardener), does NOT run eval suites (agent-eval-builder).
10. agent-debugger — tracing/observability wiring, triage, 16 incident runbooks, latency audits. Does NOT redesign for reliability upfront (agent-reliability-hardener), does NOT author eval suites (agent-eval-builder).
