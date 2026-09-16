# Skills

This folder holds the skill packages that govern HOW work is done in this workflow. Per AGENTS.md §1–2, the `skills/` folder is the single source of truth for what skills exist.

## Convention

- Each skill lives in its own folder: `skills/<skill-name>/` containing `SKILL.md` (and, for stage skills, a `references/` folder loaded on demand).
- The pipeline lists this folder at the start of every session (Orient step) and loads the governing skill **before** the work it governs — never after.
- Each `SKILL.md` frontmatter carries trigger phrases and sibling exclusions, so skills activate on real user language and never double-load for one job.
- New skills must follow `FORMAT_SPEC.md` (repo root). When a skill is added, the mapping table in AGENTS.md §1 and this README are updated in the same commit.
- If a governing skill file is missing, the pipeline stops and asks for it. Skills are never approximated from memory.

## Installed skills (12)

### Governing — load every session, before the work they govern

| Skill | Governs | Loaded when |
| --- | --- | --- |
| `planning-and-task-breakdown/` | Planning — writing or extending `build-plan.md` | BEFORE writing a single line of the build plan |
| `ponytail/` | Writing ANY piece of code — tools, nodes, subgraphs, tests, config | BEFORE the first line of code, applied to every piece afterward |

### Stage — one PRIMARY skill per feature, by lifecycle stage

| Skill | Stage | Purpose |
| --- | --- | --- |
| `agent-architecture-advisor/` | DESIGN | Whether to build an agent; pattern selection; single vs multi; topology; framework; cost budget; decision record |
| `langgraph-builder/` | BUILD | Single-graph LangGraph code: state + reducers, edges, Send fan-out, Command routing, checkpointers, streaming |
| `agent-tool-designer/` | BUILD | Tool interfaces: descriptions, schemas, error contracts, idempotency keys; agent-facing prompts and completion contracts |
| `multi-agent-builder/` | BUILD | Supervisor / handoff / hierarchical / network topologies; supervisor prompt; A2A/MCP interop; failure detection |
| `hitl-builder/` | BUILD | `interrupt()` approval gates, resume sequences, human state edits, audit trails, async approvals |
| `agent-memory-builder/` | BUILD | Memory taxonomy, Store API, extraction prompts, write/consolidate/forget policies, memory evaluation |
| `agent-reliability-hardener/` | HARDEN | Failure taxonomy, retries + idempotency, fallback ladders, circuit breakers, durable execution, SLOs |
| `agent-guardrails-builder/` | HARDEN | Threat models, prompt-injection defense, tool security + sandboxing, PII, moderation, red-team cadence |
| `agent-eval-builder/` | HARDEN | Eval layers, golden datasets, trajectory checks, LLM-as-judge + calibration, CI gates, red-team suites |
| `agent-debugger/` | OPERATE | Observability wiring, 10-minute triage, symptom router, 16 incident runbooks, latency audits |

Lifecycle order: **DESIGN → PLAN → BUILD → HARDEN → OPERATE** (see AGENTS.md §4 for the full Stage → Skill Map).
