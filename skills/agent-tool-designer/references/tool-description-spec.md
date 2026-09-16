**Load this when:** you are designing, reviewing, or fixing any tool's signature or description — wrong-tool selection, overlapping tools, naming, argument schemas, result contracts, or tool evals.

# Tool Description Spec

## The ACI principle

- The model's entire view of your system is the prompt plus the tool schemas. Tool descriptions are not documentation-of-code; they ARE the interface — invest in them the way you would invest in a public API's docs.
- A tool is four things at once: a capability, a documentation page, an error contract, and a cost center. Design all four.
- Field evidence: Anthropic's SWE-bench team spent more time optimizing tools than the overall prompt. Their documented fix: the coding agent mishandled relative paths after changing directories; they made the tool require absolute paths only and the error class disappeared. Poka-yoke (make the mistake impossible) beats begging.
- The Anthropic loop for misuses: run the model against the tool on many inputs, read its mistakes, fix the tool — not the prompt. A tool bug fixed once helps every caller forever; a prompt patch is per-context and fragile. Ask "how could the tool make this misuse impossible?" before "how can the prompt beg harder?"

## Description anatomy: four parts + example

Every tool description carries these blocks (a fill-in skeleton is in references/templates.md):

| Block | Content | Why it exists |
|---|---|---|
| WHAT | One line: what the tool does | First routing signal together with the name |
| WHEN | Concrete trigger for use ("when you have a concrete order ID...") | Prevents both under- and over-calling |
| WHEN NOT | The misuse cases ("NOT for line items — use get_order_items") | Kills the #1 misuse class; the underrated half |
| RETURNS | Shape, keys, size limits, truncation policy | The model writes parsing assumptions into its reasoning |
| COSTS | Side effects, latency, money ("~10s, moves real money") | Models with a cost signal call expensive tools more carefully |
| EXAMPLE | One worked call → exact result format | One example fixes more argument-format errors than prose |

The intern test: could a competent intern, given nothing but your tool definitions, perform the task? If the intern would ask a question, the model will too — by calling the wrong tool.

## Naming conventions

- Verb-first: `lookup_order`, `get_order_items`, `issue_refund`. The name is the model's first routing signal.
- Verbs disambiguate reads vs writes: `get_`/`lookup_`/`search_` are read-only; `update_`/`create_`/`issue_`/`send_` scream "side effect".
- Units and domains in the field name: `amount_usd`, not `amount`; `ticket_status`, not `status` if another tool has `payment_status`. Never use the same word for two meanings across tools — the model conflates them under load.
- No near-duplicate names (`search_orders` vs `search_order_items`) without explicit boundary lines in both descriptions.
- Versioning: when a tool's behavior changes, change its VERSION, not its schema in-place — old prompts, cached examples, and in-flight runs assume the old semantics; ship a new version with a migration window.
- Delegation tools (sub-agents exposed as tools): the description is a routing contract; the delegate must carry an explicit INSUFFICIENT_INFO signal so it asks rather than invents.

## Poka-yoke the arguments

| Mistake | Consequence | Fix |
|---|---|---|
| Optional-with-guess field | Model fills it with garbage rather than skip | Required fields |
| Free-text string where a category is meant | Invalid formats; wrong-tool symptom | Enums / Literal, explicit nullability |
| Relative file paths | Breaks after working-directory changes (SWE-bench class) | Absolute paths only |
| Bare `amount` | Unit confusion; wrong-magnitude writes | `amount_usd` |
| Loose stringly-typed dates | "next tuesday" reaches your API | Format + example in description; validate before execution |
| Tool mis-called > ~5% of the time | Chronic misuse | Tighten the JSON schema FIRST — models respect schemas more reliably than prose |

Validation-before-execution stays in the loop regardless: a hallucinated argument must never reach your API; return the schema with the error instead.

## Wrong-tool selection: five causes and fixes

| Cause | Symptom | Fix |
|---|---|---|
| Similar names/descriptions | Sibling tool picked; args leak between tools | Distinguishing descriptions with explicit boundaries |
| Schema ambiguity | Right tool, wrong arguments | Enums, formats, examples |
| Context rot | Late in long loops, selection degrades as schemas get buried | Lean toolset; context management; distill fat results |
| Hallucinated tools/args | Calls a function that does not exist | Validation before execution + honest error feedback |
| Prompt/model mismatch | Mis-calls appear after a model swap | Eval-gated migrations; run tool evals FIRST on any new model (format breaks before quality) |

Log failure TYPES, not just counts — unknown-tool, bad-argument, wrong-tool-for-intent, and timeout are different diseases with different cures. A flat "tool error rate" dashboard hides which one is killing you.

## The overlap test

If two tools are called together more than 80% of the time, they are probably one tool — merge and add a parameter (`depth`, `level`). Overlapping tools split the model's decision into a coin flip and double your description maintenance. Measure the co-call rate on logs, not intuition.

## The result contract

What a tool returns is a channel into the model's context. Four properties:

1. **Stable schema** — dict with consistent keys; the model hard-codes parsing assumptions.
2. **Size-bounded** — cap at the tool boundary (2k-30k chars by tool), never "at the prompt".

   | Policy | Mechanism | Best for | Risk |
   |---|---|---|---|
   | Hard cut at N chars | `content[:N] + "...[truncated, N of M shown]"` | Text dumps | Mid-sentence cuts read as facts |
   | Head/tail keep | First/last 10% | HTML, JSON blobs (signal at edges) | Middle-only answers are wrong |
   | Digest at the tool | Summary returned; full payload stored | Big tables, file trees | Digest quality = tool quality |
   | Out-of-band ref | Return "stored as ref:xyz, 12,400 rows; ask read_rows(ref, start, count)" | Huge result sets | Extra round-trip latency |

3. **Deterministic where possible** — non-deterministic results make the agent re-query "to be sure" and loop; if results can change, say so explicitly in the description. Return progress markers for long tasks ("5 of 12 items processed").
4. **Error-explicit** — every failure path returns the four-field error object (see references/error-contracts.md). A tool returning `None` on failure has no error contract.

Whatever truncation policy you pick, TELL THE MODEL in the description — a model that believes a 100KB scrape was 8 lines will make confident claims about the other 99.9KB.

## Tool eval patterns

- Per-tool eval suite (~50 cases) plus an end-to-end set (~100). Frozen mocked responses, never live services — flaky evals get ignored, and ignored evals are no evals at all.
- The four core cases per tool: happy path, tool-error recovery, ambiguous stop, injection/policy.
- Assert on behavior (tool called, stop signal, schema shape), never on exact text — text assertions make evals brittle to model upgrades.
- Fixed seeds/temperature where supported; otherwise run 3x and assert on the distribution.
- Before and after every toolset change, re-measure tool-selection accuracy on the existing eval set — attaching a big tool pack (e.g., an MCP server with hundreds of tools) degrades selection on the tools you already had; subset new tools to what this agent actually needs.
- Before any model swap, run the tool-selection and structured-output suites before the quality suite — the first thing to break is format and tool behavior, and a broken JSON contract masks everything else until fixed.
- Prefer a handful of your own well-crafted tools over a truckload of mediocre third-party MCP tools; treat every adopted MCP server like a dependency: pin versions, test tool accuracy on your evals, monitor its error classes.
