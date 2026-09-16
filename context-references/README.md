# Context References — The Ten Context File Examples

This folder holds filled EXAMPLES of the ten context files that every agent project gets in its `context/` directory. The example project is **DeepResearch** — a LangGraph research assistant — so every format is demonstrated with real, coherent content.

**The rule:** the examples define the FORMAT. When generating context files for a new project, copy the structure exactly — sections, order, tables, code-block conventions — and fill it with the new project's truth from the intake interview. Never copy the example's content; never invent new sections; mark non-applicable sections `N/A — <reason>`.

| # | File | Living? |
| --- | --- | --- |
| 1 | `project-overview.md` | no |
| 2 | `architecture.md` | no |
| 3 | `graph-design.md` | no (updated only on topology change) |
| 4 | `tool-registry.md` | **YES** |
| 5 | `prompt-registry.md` | **YES** |
| 6 | `eval-plan.md` | no (updated on gate/threshold change) |
| 7 | `code-standards.md` | no |
| 8 | `library-docs.md` | no (updated on new library pattern) |
| 9 | `build-plan.md` | no (written under `planning-and-task-breakdown` skill) |
| 10 | `progress-tracker.md` | **YES** |
