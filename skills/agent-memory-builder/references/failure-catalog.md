# Failure Catalog, Debugging, Evaluation, and Build Order

**Load this when:** memory misbehaves in production, before launching a memory system to verify the design against known failures, or when planning what to build next.

## The Failure Catalog: Detection-to-Fix in One Table

Every entry names the symptom, the detection method, and the fix. Memory failures are a closed set of mechanisms, each with a known detection and fix — teams with this table spend an afternoon where teams without it spend weeks.

| Failure | Symptom | Detection | Fix |
|---------|---------|-----------|-----|
| Hallucinated memory | Agent states a "remembered" fact the user never said | Provenance spot-check (trace to citation) | Citation requirement + confidence threshold at the source |
| Stale fact wins | Agent asserts a months-old preference as current | Contradiction test (plant A, then B, assert B wins) | Recency metadata + supersede-on-conflict |
| Contradiction pileup | Both old and new facts retrieved; model picks randomly | Retrieval trace shows both items | Conflict resolution policy — mark old fact superseded |
| Retrieval noise | Memory injected but irrelevant; answers degrade | Precision@k benchmark | Relevance threshold + rerank + smaller top-k |
| Injected memory | Instruction-shaped "fact" stored, steers future sessions | Injection check on stored facts | Write-boundary detector + provenance on every item |
| Cross-tenant bleed | User A's answer references user B's data | Isolation eval (red-team, in CI) | Namespace discipline + query-time tenant filter |
| Silent truncation | Old memories never retrieved | Pagination audit (default limit is 10, no overflow signal) | Offset pagination + limit set above expected maximum |
| Storage bloat | Searches slow, costs climb | Growth-rate metric trended over time | TTL + per-user caps + consolidation jobs |
| Extractor regression | New memories stop being created (or explode in volume) | Write-path cost/volume trend | Extraction eval + prompt versioning |
| Contribution goes negative | Memory hurts more than it helps | Contribution delta (with vs without memory) | Off-switch drill + root-cause the curation |

The four naive-memory failures map onto these: context overflow (visible, least dangerous), stale/contradictory facts (rows 2-3), retrieval noise / distractor effect (row 4), and the context-stuffing trap (row 10 — memory's real job is selecting the right small set, not hoarding).

## The Memory Debugging Toolkit

When the agent quotes a wrong "memory," each tool maps to a specific diagnosis step:

1. **The retrieval trace** — which items were injected, with what scores. The first question: did the wrong answer come from a wrong memory (retrieval failure), a misused memory (reasoning failure), or no memory at all (the model invented it)? Three causes, three completely different fixes: retrieval tuning, prompt framing ("memories are possibly relevant, not facts"), or the extractor (the stored memory itself is wrong).
2. **The provenance chain** — every fact's source and message_index. Trace the disputed memory back to the message that created it. The finding is usually one of: hallucinated extraction (fix the extractor), a joke taken literally (fix the exclusion list), or a fact that was true once and is now stale (fix the lifecycle — TTL or conflict).
3. **The state history diff** — for memory writes gone wrong, checkpoint history shows what the memory node wrote and when; replay the write with a fixed extractor in a sandbox to confirm the fix.
4. **The cross-tenant probe** — for suspected leakage, the isolation eval reproduces retrieval as user A and checks for user B's items. A one-line query that converts suspicion into evidence.
5. **The injection check** — for a suspicious stored fact, look for instruction-shaped content in its source message and the conversation around it. The memory is the attack's persistence layer; the source message is the entry point.

The toolkit's lesson: every memory failure is diagnosable only if the metadata was stored — provenance, citation, retrieval traces.

## Case Study: The Assistant That Remembered Too Much

**The bug.** A personal assistant shipped with a background extractor and no write policy. Six months in, users report the agent "acts like a creepy stalker" — it references a divorce mentioned once in March, asks about a diet from a joke in February, and confidently states "you're traveling to Berlin next week" from the hypothetical "what if I went to Berlin?"

**The diagnosis:** (1) no citation requirement — the extractor stored the hypothetical as fact; (2) no confidence threshold — every extraction stored at equal weight; (3) no dedup or conflict logic — "actually, I'm staying home" never superseded the Berlin trip, so both were retrieved; (4) no retrieval traceability — the bug was invisible for weeks; (5) no user visibility — users could not see or correct what was stored, so creepiness compounded.

**The fix, in the order applied:** visible memory UI with edit/delete restored trust in days; citation + confidence requirements dropped the hallucination rate from roughly 8% to roughly 2% on the spot-check; supersede-on-conflict stopped contradictions surfacing; retrieval traceability made the next weird answer diagnosable in minutes; a TTL for transient-type facts let "angry today" decay naturally.

**The lesson:** every failure traced to a missing lifecycle mechanism, not to the embedding model or store backend. The team built the storage half of memory and none of the curation half.

## The Memory Eval Suite

| Eval | What It Measures | Method |
|------|------------------|--------|
| Retrieval precision@k | Do the top-k memories actually help the current task? | Judged: does the answer use the retrieved items; baseline with empty memory |
| Memory contribution | Does memory make answers better? | A/B judged: with-memory vs without-memory on the same dataset |
| Hallucination rate | Fraction of stored facts not traceable to evidence | Trace-to-citation spot check (monthly sample) |
| Contradiction handling | Does a new contradicting fact supersede the old? | Synthetic: plant fact A, then fact B, assert B wins and A is marked superseded |
| Cross-tenant isolation | Does user A's retrieval ever contain user B's items? | Red-team eval, run in CI |
| Injection resistance | Does a poisoned input produce a stored "fact"? | Red-team: instruction-shaped inputs, assert write-boundary rejection |
| Lifecycle correctness | TTL, consolidation, cap behavior | Time-travel tests with mocked clocks |
| Latency/cost budget | Memory adds under X ms, under Y% of run cost | Per-run measurement |

The two highest-value and most commonly skipped: **memory contribution** (teams assume memory helps; measured, it sometimes hurts — retrieval noise is real) and **cross-tenant isolation** (protects the thing memory most endangers).

## The Benchmark Suite: Seven Numbers on Your Own Data

1. **Precision@k on real queries:** 200 historical conversations, hand-label "which memories would have helped this next turn?", measure the labeled fraction in the top-k. Target: precision@5 of 0.7+ is decent; below 0.5 the retrieval injects noise and the system is net-negative.
2. **Recall on the exact-key class:** identity and financial facts — how often the exact lookup path finds what the similarity path would miss. Target: 100%. It is a get call; any miss is a design bug.
3. **Hallucination rate:** monthly sample of 50 stored facts, each traced to its cited message. The single most honest quality number. Target: under 3%.
4. **Contradiction resolution accuracy:** plant conflicting fact pairs; how often the newer, higher-confidence fact wins and the old one is marked superseded. Target: above 90%. Failures mean stale facts silently win in production.
5. **Write-path cost:** extraction cost per session, embedding cost per item, storage growth rate — trended. The surprise: at scale, extraction is usually the dominant memory cost, not storage.
6. **Latency contribution:** memory read's share of the run budget, p95 across runs. If memory adds more than 15% of run p95, the retrieval path needs performance treatment.
7. **The contribution delta:** judged answer quality with-memory minus without-memory on a fixed dataset. The headline metric — whether the whole system earns its cost. Run it per feature change; a negative delta triggers the off switch until curation is fixed.

Memory systems ship on faith ("obviously remembering helps") and rot on silence. These seven numbers convert faith into a dashboard that keeps the system honest through twelve months of traffic growth.

## Cost/Benefit Balance Sheet and the Off Switch

- **Benefits (measured, not assumed):** reduced re-explanation turns; personalization quality delta (the contribution eval); faster task completion (fewer clarification round-trips); user trust ("it remembered me").
- **Costs (all measurable):** write path (extraction calls, embeddings, storage); read path (retrieval latency + injected-token cost); curation machinery (worker jobs, conflict logic, evals); risk (poisoning surface, privacy obligations, PII exposure); maintenance (quarterly lifecycle jobs, retrieval re-tuning).
- **Net test (the off-switch drill):** run the contribution eval with memory ON vs OFF on the same dataset. If the delta is within noise, the memory system is a liability wearing a feature's costume — turn it off until the curation is fixed.

Ship memory with an off switch and exercise it quarterly: MEMORY_ENABLED=false must produce a fully working agent (dumber, but working). This proves the rest of the system does not secretly depend on memory — an agent that breaks when memory is off has let "memory" become load-bearing context, the context-stuffing trap wearing a costume. Worked cost reality: one background LLM call per session (~$0.01-0.03), two searches per conversation (~$0.001), a few hundred tokens of injected context (~$0.001) — roughly 1 to 3% of run cost in exchange for continuity that would otherwise take 20 turns of re-explaining. Memory is cheap when it is curated; it is only expensive when it is stuffing.

## The Build Order Roadmap

Each step adds a retrieval or write mechanism only after the previous one's failure is user-visible. Memory sophistication is earned by measured failures, not purchased in advance.

| Step | Trigger to Advance | What You Build |
|------|--------------------|----------------|
| 1. Conversation buffer only (week 0) | Users repeat themselves across sessions | State + checkpointer; good enough for single sessions |
| 2. Exact-key profile | Users complain about repetition | A ("user", uid, "profile") item with a dozen fields, fetched by key. No embeddings, no search — solves 60% of the pain for 10% of the complexity |
| 3. Collection facts + semantic search | The profile gets unwieldy | Extraction, dedup, conflict machinery — curation becomes a component with its own tests |
| 4. Episodic + few-shot | The agent needs to learn | Episodes feed few-shot injection; evaluation becomes mandatory because injected episodes hurt as easily as help |
| 5. Managed tool adoption | Curation cost exceeds tool cost | Swap hand-built curation for Mem0 or Zep where needs match — usually much later than teams expect |
| 6. Procedural memory (last, most carefully) | Everything above is solid | Reflection agents with reviewed instruction updates — highest-risk memory form; one bad reflection affects everyone |

Teams that jump straight to step 6 with a $400/month memory platform and a self-editing system prompt are building the most complex possible version of a problem they have not yet experienced.

## The Requirements Interview: Twenty Questions Before You Build

Ask before writing a single line of code — the answers change the design fundamentally:

1. What must the agent remember across sessions? (Candidate fact classes: preferences, identity, context, history.)
2. What must it NOT remember? (The deny-list — health, relationships, sensitive classes.)
3. What is the worst possible wrong memory? (A wrong account number? A leaked preference? Determines the exact-key vs similarity split.)
4. Does memory need to work across users (org-shared) or only per user? (Namespace design.)
5. How long must memories live? (TTL and consolidation design.)
6. Who can see, correct, or delete memories? (Visibility; deletion.)
7. What is the latency budget for retrieval? (Determines rerank and cache choices.)
8. What is the cost budget for the write path? (Background extraction frequency.)
9. What does "contradiction" mean in this domain, and who wins? (Conflict resolution policy.)
10. Which memories must be exact — identity, money, compliance? (The exact-key rule.)
11. Does the agent need to learn how (procedural) or only what (semantic)? (Procedural memory.)
12. What should happen when memory is turned off? (The off-switch drill.)
13. What does the user see when the agent uses a memory? (Transparency.)
14. Which regulations apply to the stored facts? (Privacy obligations.)
15. Who owns the memory data at the end of the customer relationship? (Deletion cascade.)
16. Is temporal reasoning needed ("what did we tell them before the refund?")? (The Zep-shaped question.)
17. How will we measure whether memory helps? (The contribution eval.)
18. What is the creepiness line, written as explicit rules? (The write policy's deny-list.)
19. How does memory interact with multi-agent components? (Cross-agent memory injection.)
20. What is the simplest memory system that solves today's actual complaints? (The roadmap discipline — the meta-answer.)

## Pre-Launch Checklist

- Memory taxonomy mapped to your state/checkpointer/Store layout.
- Namespace convention documented (user/org/global/agent).
- Write policy: hot-path vs background, with trigger schedule.
- Fact schema: provenance, confidence, recency on every item.
- Contradiction handling: supersede, do not silently keep both.
- TTL / cap / consolidation jobs scheduled.
- Semantic search index configured (or deliberately skipped for exact-key data).
- Retrieval traceability: which memories fed which answer.
- Tenant isolation tested (cross-user retrieval eval in CI).
- Deletion cascade wired: store, threads, traces, datasets.
- Injection defense at the memory-write boundary (never store instruction-shaped "facts").
- Memory owner assigned; monthly memory review scheduled.
- Contribution delta measured with-memory vs without.
- Deny-list of fact types in the write policy before launch.
