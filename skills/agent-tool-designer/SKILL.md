---
name: agent-tool-designer
description: >-
  Design and implement an agent's tool interface and agent-facing prompts: tool signatures and
  naming, four-part descriptions that prevent wrong-tool selection, parameter schemas with
  examples, idempotency keys for mutating tools, LLM-readable error contracts, structured output
  schemas, system prompts with completion contracts, and context budgets.
  Trigger: "design agent tools", "write tool descriptions", "agent keeps calling the wrong tool",
  "tool error contract", "write the agent system prompt", "completion contract",
  "structured output schema", "idempotency key for tool writes", "agent loops
  after a tool error", "poka-yoke tool schema".
  Do NOT use for: choosing architecture, topology, or framework (agent-architecture-advisor);
  writing LangGraph state/edge/node wiring (langgraph-builder); building supervisor or handoff
  topologies (multi-agent-builder); retry engines, circuit breakers, SLOs
  (agent-reliability-hardener); building eval datasets and CI gates (agent-eval-builder).
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# agent-tool-designer

## Overview

The model's entire view of your system is the system prompt plus the tool schemas — the Agent-Computer Interface (ACI). Most agent misbehavior (wrong tool selected, loops after a failed call, hallucinated arguments, agents that never stop) is an interface-design bug, not a model bug. This skill designs that interface: each tool treated as a capability, a documentation page, an error contract, and a cost center — plus the agent-facing prompt treated as versioned code. Stage: **BUILD** (agent interface layer: tools + prompts). It assumes architecture/topology is already chosen; it produces code-ready tool definitions, error contracts, idempotency strategy, structured output schemas, system prompts, and a tool-selection verification set. Code idioms are langchain-core / LangGraph (Python 3.10+); the design rules apply to any framework.

## When to Load Which Reference File

| File | Load when... |
|------|--------------|
| references/tool-description-spec.md | Designing or reviewing any tool signature/description; wrong-tool selection; overlapping tools; naming; argument schemas; result contracts; tool eval patterns |
| references/error-contracts.md | Defining what a tool returns on failure; retryable vs fatal classification; remedy text; idempotency keys for writes |
| references/prompt-patterns.md | Writing or revising the system prompt: instruction hierarchy, completion contract, few-shot tool examples, context engineering checklist, provider quirks |
| references/templates.md | Writing the actual Python: tool with error contract, pydantic args schema, mutating tool with idempotency key, structured-output node, tools node, system prompt template, tool-selection eval cases |

## Execution Checklist

- [ ] 1. Inventory what the agent must do; split judgment (tools) from guarantee (deterministic code) at an explicit seam.
- [ ] 2. For each tool: verb-first name, four-part description (what / when / when-NOT / returns+costs), poka-yoke parameters with examples.
- [ ] 3. Define the error contract: three failure classes, four-field error object, remedy text per class.
- [ ] 4. Give every world-mutating tool an idempotency key generated once per run in graph state.
- [ ] 5. Write the system prompt: six blocks, instruction hierarchy, completion contract LAST, 2-4 tool examples.
- [ ] 6. Define structured output schemas for machine-consumed outputs; keep human-facing prose free.
- [ ] 7. Verify: run tool-selection test cases (happy path, error recovery, ambiguous stop, injection) before shipping.

## Step-by-Step Workflow

### Step 1 — Inventory capabilities; find the judgment/guarantee seam [FREEFORM]

List everything the agent must be able to DO. Classify each capability:

| Use a TOOL (LLM judgment) | Use DETERMINISTIC CODE (guarantee) |
|---|---|
| Step count/order genuinely unpredictable | Fixed pipeline, fixed order |
| Model must judge among near-equal options | Arithmetic, validation, format conversion |
| Inputs are messy natural language | A guarantee is required (no "mostly deterministic") |

Rules:
- Deterministic code where you need a guarantee, agentic code where you need judgment, and make the seam explicit in the graph — because a node that is "mostly deterministic but sometimes asks the model" is a liability nobody can reason about.
- Never ask the model for data the system can fetch — because "the model knows" is not a data-access strategy, and every prompt line that requests a fact the context lacks is a scheduled hallucination.
- Keep the toolset small and orthogonal; merge tools that are called together more than 80% of the time into one tool with a parameter — because two similar tools split the decision into a coin flip and double the description maintenance.

**Verify:** a capability table where every row has exactly one owner (tool or code); no capability owned by "the model, probably".

### Step 2 — Design each tool signature and description [EXACT]

For every tool, write the four-part description plus one worked example (see references/tool-description-spec.md for the full spec):

1. **WHAT** it does — one line.
2. **WHEN** to use it — a concrete trigger, not a vibe.
3. **WHEN NOT** to use it — the negative is the underrated half; it kills the number-one misuse class.
4. **RETURNS + COSTS** — result shape and size limits, side effects, latency, money.

Signature rules (poka-yoke — make the mistake un-expressible, because a discouraging prompt is weaker than an impossible mistake):
- Enums over free strings; required fields over optional-with-guess; absolute paths over relative.
- Units in the field name (`amount_usd`, not `amount`); verb-first names (`lookup_order` beats `order_tool`); never reuse one word for two meanings (`ticket_status` vs `payment_status`).
- One worked usage example in the description — because models pattern-match on examples, and one example fixes more argument-format errors than any prose.
- Bound the result at the tool boundary and state the truncation policy in the description ("returns the first 50 rows only") — because a model that believes a 100KB scrape was 8 lines will confidently describe the missing 99.9KB.
- Add a cost note to expensive/side-effecting tools ("takes ~10s, moves real money") — because models with a cost signal call expensive tools more carefully.

**Verify: the intern test.** Given only the tool definitions, could a competent intern perform the task without asking a question? Every question the intern would ask, the model will answer by calling the wrong tool.

### Step 3 — Define the error contract [EXACT]

A tool failure returns text the model reads and acts on — tool errors are prompts. For each tool, define the contract BEFORE the happy path (see references/error-contracts.md):

- Three failure classes: **transient** (retry), **permanent_input** (fix the arguments and re-call), **permanent_capability** (stop and tell the user).
- Four-field model-facing error object: `error`, `category`, `detail`, `retry_hint`.
- Fatal-class remedy text must contain "Do NOT retry" plus what to do instead — because a model told "403" without it retries five times to learn what three words could have taught in one.
- Never let an exception or traceback propagate as the tool result — because tracebacks are full of tokens that trigger another bad call; log them outside the context instead.
- Never return an error string as a successful result ("error: timeout" as data) — because the model will happily build its answer on top of that string.

**Verify:** for each tool, write the three class-specific error strings first, and run the bad-vs-good transcript check: does the corrected next call actually differ from the failed one?

### Step 4 — Add idempotency keys to mutating tools [EXACT]

Crash recovery re-runs nodes, retries re-run calls, resumes re-run pre-interrupt code — so every external side effect needs a strategy. Classify each tool:

| Operation | Retryable? | Mechanism |
|---|---|---|
| Pure read (search, GET) | Yes, freely | Plain retry |
| Write with natural key (upsert by ID) | Yes | Idempotent by design |
| Write needing a key (charge, create, send) | Yes, with care | `idempotency_key` param; server dedupes |
| Non-idempotent write, no key support | Only with pre-check | Verify state before retry |
| Human-audience send (email, SMS, Slack) | Never | Deliver via outbox |

Pattern: generate the key ONCE per run in graph state and pass it to every mutating tool — the backend stores key-with-result and returns the stored result on replay. The key must be stable across retries of the same logical action — because a fresh UUID per attempt makes the server see two different "first" calls.

**Verify:** for every mutating tool answer "what happens if this executes twice?" — if the answer is not "nothing", the key is missing.

### Step 5 — Write the system prompt [GUIDED]

Compose the six stable blocks, in this order (see references/prompt-patterns.md):

1. ROLE + TASK — one sentence, no preamble.
2. TOOL POLICY — when to use which tool, when NOT to, the fallback when a tool fails.
3. PROCEDURE — step order to follow.
4. OUTPUT CONTRACT — final answer format, refusal format, never-do rules.
5. FEW-SHOT EXAMPLES — 2-4 real (input → tool calls) pairs, including one error-recovery exemplar.
6. BOUNDARIES — what the agent cannot do; escalation rules.

Non-negotiables:
- The completion contract goes LAST ("You are done when ...") — because termination instructions written early get forgotten late; "lost in the middle" applies to your own prompt. Write it as a checklist the model can verify against its own context ("the refund has an approval AND the ticket is updated"), not a vibe — because "help the customer" vs that checklist differs by an entire incident class: an agent that stops vs an agent that loops.
- Rules the user must not override live in the system/developer message; user content is delimited data — because the hierarchy (developer > user > assistant) is both the instruction order and the injection defense.
- Constraints beat exhortations: "never call more than two tools per turn", not "be efficient" — because if you cannot write a test that detects a violation, the model cannot follow it reliably either.
- Give an escape hatch: "If the information is not available, say exactly: UNABLE_TO_COMPLETE: <reason>" — because agents without a sanctioned way to fail hallucinate, and the hatch gives you a machine-detectable failure signal.
- Keep the system prompt static and small (300-1,500 tokens); task state lives in state fields and messages, never in the system prompt — because it re-bills every call, over-weights stale instructions, and puts injection targets in the highest-authority region.
- Few-shot examples: 2-4, taken from the failure log, using your EXACT tool-result format (including the error prefix) — because models that have seen one error-recovery example retry correctly far more often than models that have not. Caveat: heavy examples can hurt strong reasoning models — test both.

**Verify:** every rule is checkable; prompt is under budget; the cached prefix is byte-identical across requests (no timestamps or user names inside it).

### Step 6 — Define structured output schemas [GUIDED]

Where downstream code consumes the output (classification, extraction, verdicts), bind generation to a schema. Name the consumer first, then pick the mechanism:

| Consumer / need | Mechanism |
|---|---|
| External API payload, DB write | Schema-constrained decoding (Structured Outputs) |
| One-of-N decision (intent, verdict) | Enum field in schema (JSON mode is enough) |
| Small extraction, internal routing | JSON mode + your own validation |
| Long prose for humans | Plain text — never a JSON envelope |

Schema-as-prompt checklist (the model reads every field description):
- Field descriptions with examples or counter-examples for tricky semantics; enums only where the value space is truly closed.
- `additionalProperties: false`; optional fields as nullable unions; nesting depth 2-3.
- A confidence field anywhere a wrong answer has real cost — because scoring is cheap and re-asking is not.
- Semantic validators for valid-but-wrong fields (amount > 0, non-empty names) — because the schema guarantees shape, never content.
- Wrap the call in a validation loop: on failure, feed back WHICH field failed and why; cap at 2-3 retries — because vague "output was invalid" retries almost never fix anything, and beyond 3 attempts marginal success collapses while cost climbs.

**Verify:** schema survives schema-gaming (required field filled with "") and valid-but-wrong cases; the failure path when all retries fail is designed, not an accident.

### Step 7 — Verify with tool-selection test cases [EXACT]

Run the model against the tools on many inputs and fix the TOOL, not the prompt — because a tool fix helps every caller forever, while a prompt patch is per-context and fragile. Minimum suite (frozen, mocked tool responses — never live services, because flaky evals get ignored):

| Case | Input shape | Expect |
|---|---|---|
| Happy path | Clear single-tool request | Correct tool called, then stops |
| Error recovery | Tool returns each error class | Corrected retry (different args), then success or clean stop |
| Ambiguous stop | Underspecified request | Asks / refuses instead of guessing a tool |
| Injection / policy | Tool result or user text with instructions inside | Obeys policy, treats content as data |

Rules:
- Assert on behavior (which tool, stop signal, schema shape), never on exact text — because text assertions break on every model upgrade.
- Log failure TYPES, not just counts: unknown-tool = schema drift; bad-argument = schema needs enums; wrong-tool = description boundary work; timeout = latency/granularity problem — because a flat "tool error rate" hides which disease is killing you.
- For any tool mis-called more than ~5% of the time, tighten the JSON schema BEFORE touching the prompt — because models respect schemas far more reliably than prose.
- Measure tool-selection accuracy before and after every toolset change — because attaching new tools (e.g., a large MCP pack) degrades selection on the tools you already had.

**Verify:** suite green; turns-per-task and mis-call rates charted per release; every prompt/tool change ships with a before/after eval diff.

## Examples

**Example 1 — simple (read-only lookup agent).**
Input: "Agent answers order-status questions; backend has GET /orders/{id} and GET /orders/{id}/items."
Output: two read tools — `lookup_order(order_id: str)` with a four-part description ("RETURNS the header only; returns ORDER_NOT_FOUND if the ID does not exist"), and `get_order_items(order_id: str)` with "NOT order status — use lookup_order". Formatting for the user stays in deterministic code, not an LLM tool. No overlapping search tool: with a known-ID lookup only, there is nothing to merge.

**Example 2 — typical (mutating tool + error contract + prompt).**
Input: "Support agent may issue refunds up to $200; larger needs approval; must not invent order numbers."
Output: `issue_refund(order_id, amount_usd, reason, idempotency_key)` where the key is generated once per run in state and injected by the tools node. Error contract: `ACCOUNT_CLOSED` → "permanent_capability — Do NOT retry; offer account-recovery options instead." Description says "NOT for amounts over $200 — escalate instead." System prompt TOOL POLICY: "Call lookup_order before any refund"; completion contract LAST: "You are done when the refund has an approval AND the ticket status is resolved"; escape hatch: "UNABLE_TO_COMPLETE: <reason>".

**Example 3 — edge case (wrong-tool boundary).**
Input: "Model keeps calling search_orders when it needs line items; both descriptions currently say 'search orders'."
Output: rewrite descriptions with explicit boundaries — `search_orders`: "order headers ONLY. NOT for line items — use search_order_items"; add a `level` enum if merging is justified; check the co-call rate first (called together >80% of the time → merge into one tool with a parameter). Add one few-shot exemplar showing the disambiguation and an eval case asserting `not_tool: search_orders` for the line-items question.

## Known Gotchas

1. **Agent retries the same failing call 5-12 times; the run costs $4.** → **Cause:** error text is uninformative or actively misleading — remedy says "check the ID and retry" for a fatal error (e.g., deleted account). → **Response:** fix the error contract at the tool layer (correct class + "Do NOT retry" + what to do instead); one tool-level change fixes every caller; add loop detection (3x identical call → forced answer).
2. **Model copies a Python traceback into its answer, or invents a fix for a KeyError.** → **Cause:** the tool exception propagated as the tool result. → **Response:** catch at the boundary; return the four-field error object; log the traceback outside the message — your logs get the stack, the model gets "temporary failure, try X".
3. **One 100KB tool result destroys quality for the rest of the run.** → **Cause:** unbounded returns; context floods every subsequent call. → **Response:** cap at the tool boundary (2k-30k chars by tool); digest or out-of-band ref with a fetch-slices tool; state the truncation policy in the description so the model does not hallucinate the missing content.
4. **Customer charged (or emailed) twice after a retry or a resume.** → **Cause:** non-idempotent write retried — often a blanket HTTP-client retry added "temporarily". → **Response:** idempotency key from graph state on every mutating tool; outbox for human-audience sends; scope client retries to reads only.
5. **Model calls the sibling tool instead of the right one.** → **Cause:** overlapping descriptions and no negative guidance — selection is a coin flip. → **Response:** explicit "NOT for ..." boundary lines; merge if co-called >80%; tighten the schema with enums before editing the prompt.
6. **Model invents order numbers or answers from memory despite empty retrieval.** → **Cause:** no completion contract or escape hatch; prompt lines that request facts the context lacks. → **Response:** completion contract + UNABLE_TO_COMPLETE hatch + grounding tool; audit every prompt line that implicitly asks the model to know something.
7. **Structured output is valid but worthless: required fields filled with "unknown", sentiment "pos" on sarcasm.** → **Cause:** schema guarantees shape, not content. → **Response:** semantic validators as a second gate; field descriptions with counter-examples; confidence field; cap the validation loop at 2-3 with field-specific corrective messages.
8. **A prompt "cleanup" silently breaks a case type in production.** → **Cause:** prompt changed without a regression gate. → **Response:** frozen 50-100 case eval with a one-command runner; before/after diff on every prompt change; prompt version logged in every trace.
9. **Prompt cache never hits; input cost stays high for no visible reason.** → **Cause:** per-request variation in the system prompt or tool order (user name, timestamp). → **Response:** keep cached prefixes byte-identical; move per-user data into messages or state fields.
