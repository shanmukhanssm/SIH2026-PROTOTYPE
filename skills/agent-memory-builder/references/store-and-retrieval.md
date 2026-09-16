# Store, Retrieval, and Multi-Tenancy

**Load this when:** implementing the LangGraph Store (namespaces, semantic index, search), deciding between the Store and an external vector DB, tuning embeddings/hybrid search/reranking, building RAG-in-the-loop, or scaling memory across tenants.

## The Store API Core

The Store is LangGraph's long-term memory primitive: cross-thread, namespaced, optionally semantic-searchable. The core API maps (namespace, key) to a value dict:

```python
store.put(("user_123", "facts"), "pref_language", {"value": "python"})
item = store.get(("user_123", "facts"), "pref_language")   # None if missing
store.delete(("user_123", "facts"), "pref_language")
items = store.search(("user_123",), limit=10, offset=0)     # prefix match
namespaces = store.list_namespaces(prefix=("user_123",), max_depth=2)
```

Inside graph nodes, nodes receive `runtime: Runtime` (injected by type annotation) and use `runtime.store`, with `runtime.context` carrying per-run context such as `user_id`. The store is also available outside the graph via a plain store instance.

### Behaviors That Have Bitten Production Systems

| Behavior | Detail | Because |
|----------|--------|---------|
| Namespace prefix matching | `search(("alice",))` returns items under ("alice",), ("alice", "facts"), ("alice", "episodes") — prefix match, not exact | To get exactly one level, pass the full namespace tuple or filter client-side |
| Silent truncation | `limit` defaults to 10 with no overflow signal or warning | The classic "memory works in dev, breaks in prod" bug: paginate with offset and set limit above your expected maximum |
| Backend-dependent ordering | PostgresStore returns `updated_at` descending; InMemoryStore returns insertion order | If order matters (it usually does for memory), sort client-side on `item.updated_at` and never rely on backend order |
| Item metadata | Items carry `value`, `key`, `namespace`, `created_at`, `updated_at` | Lifecycle logic (TTL, consolidation, conflict resolution) consumes `updated_at` constantly |
| Opt-in semantic search | Compile the store with `index={"embed": ..., "dims": ..., "fields": [...]}`; embed specific fields or `"$"` for the whole value | Without the index config, passing `query=` to search is meaningless; `search(..., query=...)` ranks by similarity and attaches `score` |
| Per-item indexing control | `put(..., index=["field"])` embeds only that field; `index=False` stores without embedding | Reference data stays retrieval-visible but not searchable, cheaply |

### Backends

- Development: `InMemoryStore` — development only, never production.
- Production: `PostgresStore`, `RedisStore`, `MongoDBStore`, `UpstashStore` — all implementing `BaseStore`.
- Custom store: subclass `BaseStore` and implement the async set (`aput`, `aget`, `adelete`, `asearch`, `alist_namespaces`), sync counterparts optional.
- Natural SQL schema: `(namespace TEXT[], key TEXT, value JSONB, created_at, updated_at)` with a GIN index on namespace.

**Warning:** memory values are plaintext JSON in your database. "Memory" is not encrypted just because it is called memory. Encrypt columns or the whole database if memories contain sensitive user facts, and wire PII deletion onto the memory deletion path.

## Namespace Design: The Access-Control Boundary

```text
("user", user_id, "facts")        per-user semantic memory       <- the default
("user", user_id, "episodes")     per-user episodic memory
("org", org_id, "policies")       org-shared knowledge
("global", "reference")           global static reference data
("agent", "instructions")         procedural memory (self-refined prompts)
```

- Name namespaces before writing the first memory. A flat ("memories",) namespace means every search scans every user's data — a privacy incident and a latency tax at once. The tuple path is your permission model; the first element is almost always a tenant ID.
- **Tenant isolation is namespace discipline.** Every store access must derive its namespace from the authenticated identity (`runtime.context.user_id` from the auth layer), never from model output or user-supplied strings. An attacker-influenced namespace is both an injection channel and a cross-tenant-read vulnerability.

```python
# The safe pattern: namespace from auth, not from the model
async def save_memory(state, runtime):
    user_id = runtime.context.user_id          # from auth, not from the model
    facts = extract_facts(state["messages"])   # LLM extracts candidate facts
    for fact in facts:
        await runtime.store.aput(
            (user_id, "facts"), str(uuid4()), {"fact": fact})

async def recall_memory(state, runtime):
    user_id = runtime.context.user_id
    items = await runtime.store.asearch(
        (user_id, "facts"), query=state["messages"][-1].content, limit=3)
    return {"memories": [i.value["fact"] for i in items]}   # injected into the prompt
```

## Store vs External Vector DB: The Decision Walkthrough

Step 1 — classify the data. **Memory-shaped**: small JSON docs, per-tenant, "facts about this user/org," searched by meaning within a namespace. **Corpus-shaped**: large chunks, global, "the knowledge base," searched with filters + reranking + BM25 hybrids. The distinction is size, tenancy, and lifecycle.

Step 2 — the comparison table:

| Criterion | LangGraph Store | External vector DB |
|-----------|-----------------|--------------------|
| Item size | Small JSON docs (KBs) | Chunks to documents |
| Write pattern | High-frequency agent writes | Batch indexing pipelines |
| Namespacing | Built-in tuples (tenant-first design) | Your schema (you build tenancy) |
| Search | Semantic + prefix + filter (basic) | Hybrid BM25+vector, reranking, facets |
| Injection | `runtime.store` in nodes — zero plumbing | Your client code |
| Deployment | Ships with LangGraph | Separate infra to operate |
| Scale | Fine for agent memory (10^4-10^6 items) | Built for 10^6-10^9+ vectors |

Step 3 — worked decision. A support system with 5k tenants, each with ~100 memory items (500k total), written continuously, searched per-turn within one tenant: the Store wins — tenancy is free, the write path is free, and 500k items is nothing. The same system's product-documentation knowledge base (2M chunks, global, filters + reranking, strict latency SLAs): a vector DB. Hybrid resolution: Store for user/agent memory, vector DB for the document corpus. Retrieval for grounding and memory for continuity are different problems with different SLAs.

Step 4 — validate empirically where it matters: run recall@k on labeled queries against both candidates plus write-path latency at expected write rate. If both pass the quality bar, pick the one with less operational surface — inside LangGraph, usually the Store.

## What Belongs Where: The Four-Way Split

Before putting anything in the Store, run the four-way test: within-run transient data → tool parameters; conversation state → state + checkpointer; ground-truth records (users, orders, documents) → your database, with references in state; cross-thread knowledge (preferences, facts, procedures) → Store. Keeping order history in the Store "just in case" produces search returning 400 items of noise that degrades retrieval quality; keeping the full CRM record in state balloons checkpoints and complicates GDPR deletion.

## Vector Stores and Embeddings: The Right Tool, Sometimes

- **Chunking: the unit of retrieval must match the unit of answer.** For memories, the natural chunk is one fact or one episode per item. A store of two-sentence facts beats a store of 500-token blobs on every retrieval metric, because the query matches the item's core meaning directly rather than hitting a buried sentence in a blob.
- **Quality levers:** embedding model choice (test on your data, not benchmarks); top-k tuning (start at 5-10 and measure); hybrid search merging BM25 keyword search with vector search (the single most reliable quality lift where product codes, names, and jargon are keyword-shaped and embeddings miss them); reranking with a cross-encoder over the top 20-50 candidates (roughly 50-200 ms extra latency, consistently lifts precision). Hybrid plus rerank is the best quality-per-effort available.
- **When embeddings are the WRONG tool:** (1) structured data with exact keys — "user's subscription tier" belongs in a `get(namespace, "profile")` call or a DB row; (2) small memory sets of a few dozen facts — inject them all or filter exactly; (3) exact-match or negation logic ("customers who did NOT renew") — embeddings are similarity machines with no concept of negation; (4) high-cardinality identifiers (order IDs, emails) — keyword or database lookup, always.

**Pro tip from production:** a team stored "user's account number" as a vector memory and chased a bug where the agent occasionally quoted the wrong account number — retrieval returned the nearest account, not the correct one. The rule that saves teams: if a fact could ever be used in a money- or identity-moving action, it must be fetched by exact key, never by similarity. Similarity is for inspiration; exactness is for execution.

## RAG-in-the-Loop: Retrieval as an Agent Skill

One-shot RAG ("embed, retrieve, stuff, answer") generalizes into RAG-in-the-loop, where retrieval becomes one of the agent's tools, called repeatedly, model-steered:

| Component | What It Does |
|-----------|--------------|
| Query rewriting | Model rewrites the user's question into a retrieval-shaped query ("How do I get my money back" → "refund policy terms and process") |
| Multi-query expansion | Three query variants, merged results — trades latency for recall when recall matters |
| Hybrid + rerank in the loop | The search tool runs hybrid retrieval plus reranking per call |
| Cite-then-answer | Answer from retrieved passages, citing which support each claim — converts retrieval quality from invisible to measurable |
| Self-RAG (reflection) | After retrieving, the model checks whether passages answer the question; if not, re-queries with a revised query instead of answering from noise |
| Tool-shaped retrieval | Search exposed as a tool with structured args (query, filters, limit) so the model can combine it with other tools ("search the refund policy AND check the account status") |

```python
def retrieve_node(state):
    q = rewrite_query(state["question"])            # model rewrites for retrieval
    candidates = hybrid_search(q, top_k=20)         # BM25 + vector, merged
    ranked = rerank(q, candidates, top_k=5)         # cross-encoder re-scores
    return {"retrieved": ranked}

def reflect_node(state):
    verdict = llm.with_structured_output(Verdict).invoke(
        question=state["question"], passages=state["retrieved"])
    if not verdict.sufficient:
        return {"retry_query": verdict.suggested_query}   # loop back to retrieve
    return {"ready": True}
```

Cost profile: two to four extra LLM calls per query plus retrieval latencies. Use it when retrieval quality materially changes outcomes (high-value questions); skip it for a FAQ with three stock answers. RAG-in-the-loop is the premium path, not a default.

## Cross-Thread Memory Patterns (Four Cover Most Needs)

| Pattern | Shape | Notes |
|---------|-------|-------|
| Profile | One JSON doc per user, key "main"; fetch at thread start, inject into system prompt | Update by regeneration — send previous profile plus new facts to a model, get the merged profile back; error-prone as the profile grows |
| Collection | Many items, one per fact; search by query at need-time | Higher downstream recall than profiles, at the cost of update hygiene |
| Episodic | Solved-task traces stored as few-shot examples; retrieve similar ones to steer the current run | Episodes are semantic-searchable: "how did we resolve similar login issues?" |
| Procedural | Per-tenant refined instructions; a reflection step rewrites them from feedback | See memory-taxonomy.md for the guardrails |

## Multi-Tenant Scale: The Performance View

- **Index size and search cost:** per-user namespaces keep searches small — a user has 50 to 500 items, not 50 million. This is the performance argument for namespace-per-user on top of the privacy argument. A shared global index makes every search expensive and every tenant filter a correctness risk.
- **Write amplification:** background extraction runs one LLM call per session. At 100,000 sessions/day that is 100,000 extraction calls/day — roughly $30,000/month at $0.01 per call. Levers: extract only for high-value sessions (long conversations, explicit asks), batch extraction hourly instead of per-session, use a tiny model — extraction is a small-model task.
- **Embedding cost:** every write embeds its item, every search embeds its query. Batch re-embeddings during consolidation and cache query embeddings for repeated questions.
- **Consolidation at scale:** TTL and cap jobs become background workers with their own queues and dead-letter queues. A consolidation job that silently stops is how a 50,000-user store degrades into retrieval noise over a quarter — monitor job health like any other production job.
- **Retrieval latency budget:** memory search adds 30 to 200 ms (embed, search, optional rerank). Keep it inside the run budget or cache it: a small per-user cache of the top-10 most-used facts makes the common path a cache hit.
