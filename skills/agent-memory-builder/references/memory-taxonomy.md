# Memory Taxonomy

**Load this when:** classifying what the agent should remember, choosing among semantic/episodic/procedural memory, diagnosing why a naive append-everything memory design fails, or applying the recurring design patterns of memory-literate agents.

## The Four Types and Their LangGraph Homes

The psychology-derived taxonomy (adopted by the CoALA framework and LangGraph's documentation) maps cleanly to framework primitives:

| Memory Type | What It Stores | Human Analog | Agent Example | LangGraph Home |
|------|------|------|------|------|
| Conversation buffer (working memory) | Current thread's messages and state | What you hold in mind mid-conversation | The message list in state | State + checkpointer (short-term, thread-scoped) |
| Episodic | Past experiences and trajectories | "Remember the time we..." | Past runs, what worked and failed | Store, e.g. ("user", "episodes") |
| Semantic | Facts and knowledge | "Paris is the capital of France" | User preferences, extracted facts | Store, e.g. ("user", "facts"), vector index |
| Procedural | How to do things | Riding a bike | System prompts, skills, learned instructions | Code + prompts; self-refined prompts in the Store |

Two primitives cover everything: the checkpointer gives thread-scoped short-term memory automatically (state persists per thread_id); the Store gives long-term, cross-thread memory in namespaced key-value items, optionally semantic-searchable. There is no third storage primitive — two well-understood primitives beat five half-understood ones.

## Rules That Prevent Design Bugs

- **Facts go to semantic memory; experiences go to episodic memory.** "The user prefers Python" is a fact; "last Tuesday the refund flow failed twice" is an episode. Conflating them produces stores that are neither searchable by fact nor replayable as history.
- **Semantic memory vs semantic search.** Semantic memory is the psychology term for stored facts; semantic search is embedding-based retrieval. You can have semantic memories retrieved by exact key, and you can run semantic search over any kind of item. Teams conflate these constantly in design discussions.
- **Memory vs context.** Memory is stored data; context is assembled data for one particular LLM call. The whole discipline is the write path (what to store) and the read path (what to retrieve) connecting them.
- **Episodic memory's most practical use is few-shot learning.** Past (situation, action, outcome) triples become examples for future decisions — episodic memories can double as few-shot example pools.

## Use-When Table

| Type | Use when | Do NOT use when |
|------|------|------|
| Conversation buffer | Always — it is the current session | You expect cross-session continuity (add Store types) |
| Semantic (facts) | Preferences, constraints, identity context that should survive sessions; similarity search helps discovery | The fact is exact/identity/money-shaped (use exact-key get, not vector search) |
| Episodic (experiences) | Past runs would steer future ones as few-shot examples; postmortems of what worked | Episodes are irrelevant to future tasks — irrelevant episodes are worse than none |
| Procedural (instructions) | Instructions are hard to specify upfront and feedback is available (reflection pattern) | A single bad reflection would rewrite behavior for everyone without review guardrails |

## Why Naive Memory Fails: Four Failure Modes

The naive approach — "keep appending everything to the prompt" — fails in four distinct ways:

| # | Failure | What Happens | Why It Matters |
|---|---------|--------------|----------------|
| 1 | Context overflow | Conversation exceeds the context window; API rejects the call | Visible and annoying, but the least dangerous — at least you know something broke |
| 2 | Stale and contradictory facts | Agent remembers "user is vegan" from March, recommends a steakhouse in August | Memory without recency or authority structure decays into misinformation; the model cannot know which fact is current |
| 3 | Retrieval noise (the "distractor effect") | Retriever returns eight half-relevant items that drown the one relevant one | Measured: injected irrelevant context drops answer accuracy — retrieval without quality control is worse than no memory at all |
| 4 | Context-stuffing trap | "More memory in context = better answers" — false | Long contexts raise cost/latency and models attend worse to the middle ("lost in the middle"). A system that always injects 5,000 tokens is failing at its one job |

**Design conclusion:** memory is a retrieval problem with a write problem attached. You must decide what is worth storing (write policy), how to find the right subset fast (retrieval quality), and how to keep stored facts fresh (the lifecycle). Skip any of the three and you reproduce one of the four failures.

## Design Patterns of the Memory-Literate Agent

| Pattern | Shape | Why It Wins |
|---------|-------|-------------|
| Profile-vs-collection split | One exact-key profile document for the small stable set (identity, plan, hard preferences) plus a collection of small items for the long tail | The two stores have different update semantics (overwrite vs append/supersede) and different read paths (get vs search); one store doing both jobs does neither well |
| The memory sandwich | Retrieve before the first LLM call (cheap, narrow: profile + top-k facts) and again mid-task when the need sharpens | The initial query is too vague to retrieve well; the agent's first step generates a better query — RAG-in-the-loop applied to memory |
| Write-through vs write-back | Write-through (saved during the conversation) for user-stated facts; write-back (background worker) for inferred facts | The explicit statement is cheap to store immediately; inference needs full-conversation context the background worker has |
| The memory budget | Explicit per-user token budget for injected memory (e.g. 800 tokens) with an allocation policy (profile first, facts next, episodes with the remainder) | Forces retrieval to choose — the whole job; without it, injected context drifts with index state and bloats unpredictably |
| The trust gradient | Profile facts presented as settled context; retrieved facts as possibilities ("you may prefer email"); episodes as suggestions | Encodes provenance into prompt framing; measurably improves handling of stale or wrong memories — the model can ignore a "possible" without ignoring a "settled" |
| The memory shadow | Every memory carries its shadow: provenance record, retrieval history, effect record (which answers it influenced) | Makes debugging and poisoning detection possible; without shadows a memory system remembers everything and explains nothing |

## Procedural Memory: Two Practice-Grade Patterns

1. **Reflection agents.** After each session (or after failures), a meta-prompt reviews the transcript and proposes instruction improvements ("the agent repeatedly failed to ask for the order number before searching — add this to the workflow"). Refined instructions go to the Store namespace ("agent", "instructions"), versioned, and are injected as the system prompt next session. Works where instructions are hard to specify upfront and feedback is available. Guardrails: reflection writes to a versioned, reviewed instruction document — never let the agent hot-edit its own live system prompt without a visible diff — and eval the new instructions on the dataset before they become the default, or one bad session rewrites the agent for everyone.
2. **Episodic few-shot injection.** Retrieve two or three past (situation, action, outcome) triples similar to the current task and inject them as few-shot examples. Cheapest procedural-memory win. Retrieval-quality caveats apply in full: irrelevant episodes are worse than none, because the model may follow a bad example.

**Warning:** an agent that rewrites its own system prompt is an agent whose "learning" can also be poisoning. A single injected session that gets reflected into the instruction store becomes permanent behavior for every future user. The reflection write path needs the same guardrails as any other memory write plus human review.

## The Creepiness Line Is a Product Decision

Ask before building: what should the agent remember, and what would a user consider creepy? The line between "helpful memory" and "stalker" belongs in the write policy as explicit rules ("never store health, relationship, or identity-precise facts without explicit user confirmation") — not discovered after the first complaint. Every mature memory system has a deny-list of fact types no extraction may store. None of them started with one. The best memory feature is often the visible one: "I remember you prefer email — want me to keep using that?" Visible memory builds trust and generates free labels (every user correction trains the extraction and conflict logic); invisible memory is a liability discovered by surprise.
