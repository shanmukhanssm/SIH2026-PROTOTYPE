**Load this when:** building the output moderation pipeline (Step 4), PII handling and data governance (Step 5), rate limiting (Step 6), or audit logging and compliance mapping (Step 7).

# PII, Output Moderation, Rate Limits, Audit Logging, Compliance

## PII handling: detect, redact, store, delete

PII and other regulated data flow through every layer: prompts, tool calls, traces, logs, memory, and training data.

- **Detect** at the ingestion boundary and on outputs. Regex for structured types — emails, SSNs, card numbers — catches the easy 80%; add an NER classifier for names and addresses.
- **Redact** at the logging and tracing boundary — BEFORE PII enters trace vendors, log stores, or datasets. Redact in prompts sent to third-party providers where your data processing agreement requires it.
- **Store** PII in your own encrypted datastores with access control — not in LangSmith traces, not in vector stores without access control, not in a shared provider's prompt cache. Vector stores deserve extra suspicion: retrieval happily returns other tenants' chunks when tenant filtering is missing (LLM08).
- **Delete** on request, cascading across every store: threads and checkpoints (delete the thread), store memories (delete the namespace), traces (purge by user ID), logs (retention plus purge), datasets that ingested the data. Build the deletion path before launch, because retrofitting deletion across six storage systems is how GDPR deadlines get missed.

**The second-order leak is the one that happens:** PII captured in a tool result, echoed by the model, logged in a trace, imported into an eval dataset, embedded in a few-shot example, and shipped in a prompt six months later — each hop looked innocent. Treat PII like radiation with a half-life: redact at the first boundary and assume anything unredacted will propagate everywhere.

## Data-flow inventory and retention matrix

The data-flow inventory is one table — data class (PII, financial, health, internal, public) x location (prompts in flight, provider logs, traces, checkpoints, Store, your logs, datasets, backups) x jurisdiction. It is the compliance audit's first question and most teams' first scramble. Build it once; update it when a storage system changes.

Retention matrix (adjust to your regime):

| Data class | Retention | Deletion mechanism |
|-----------|-----------|--------------------|
| Chat threads / checkpoints | 90 days or user request | Thread deletion + checkpoint retention job |
| Store memories | Until user deletion or TTL | store.delete per namespace |
| Traces | 30-90 days | Trace purge by user ID / project retention |
| Audit logs | Years (legal minimum) | NEVER "save money" by deleting early |
| Datasets | Until re-curation | Dataset version archival, not silent edit |
| Model-provider data | Provider retention policy | Zero-retention setting where offered |

## Compliance mapping

- **Data residency:** traces and logs must live in the right region, and this constrains the tracing backend and model provider. An EU customer's prompt sent to a US-hosted model provider is a data transfer that needs a DPA — often the deciding argument for self-hosted tracing or OTel.
- **DPAs and sub-processors:** the model provider AND the tracing vendor are sub-processors for data-protection purposes; both need DPAs signed before customer data flows, and both must appear in the data-flow inventory.
- **Zero-retention:** enable no-training and no-retention settings for any traffic containing customer data, and know the difference — they are different commitments, and default settings usually retain.
- **Region routing:** verify actual processing regions; an "EU region" model that still routes through US infrastructure for safety filtering has happened in practice.
- **Provider as dependency:** the provider's security posture (breach notification terms, SOC2 reports, incident SLAs) is part of yours; vendors who cannot show audit reports for regulated workloads fail vendor review. A dual-provider setup doubles as a compliance hedge — if one provider fails review in a jurisdiction, the other may pass.
- **Model updates change the threat surface:** a provider model update shifts instruction-following behavior, so injection resistance and refusal rates move. Pin model versions and re-run the safety suite on every model version change — model updates are releases, from your security program's point of view.
- **Make compliance queryable:** "show me every store of user 42's data and the deletion status of each" should be a real endpoint, run quarterly as a drill and on every real deletion request. Compliance that is a document is forgotten; compliance that is a query is maintained.

## Output moderation: three pillars, three stages

Kill the dangerous assumption "it came from our model, so it is safe" — the model is only as safe as its worst input (injection) and its worst hallucination. Every consumer of agent output (payment processors, email senders, stored documents, other agents) must validate it like any other external input. This is the single most common architectural sin in agent deployments.

Pillars: (1) filters — toxicity/off-policy classification for user-facing text, schema constraints for structured outputs; (2) fact-checking — citations from retrieved sources plus claim-source agreement (groundedness), "no citation, no publication" for regulated content; (3) consumer-side validation — structured outputs are checked where they are consumed, not just where they are produced.

The pipeline in code shape (runnable version: Template 3 in references/templates.md):

```python
def moderate_output(output, context):
    # stage 1: off-policy classification (fast model or heuristics)
    if policy_classifier(output.text) == "block":
        return {"blocked": True, "reason": "off_policy"}
    # stage 2: schema/contract enforcement (mechanical)
    if output.structured and not validate_schema(output.structured):
        return {"blocked": True, "reason": "schema_violation"}
    # stage 3: grounding (claims must be supported by retrieved sources)
    unsupported = [c for c in output.claims if not c.supported_by(output.sources)]
    if output.requires_citations and unsupported:
        output.text = strip_claims(output.text, unsupported)   # strip, don't refuse
        output.claims = [c for c in output.claims if c not in unsupported]
    return {"blocked": False, "output": output}
```

Failure modes to design for:

- The classifier has latency (100-300ms for an LLM-based check) — cache per output hash.
- The classifier has its own false-positive rate — log every block with the reason and the blocked text, because you will tune the threshold weekly at first.
- The groundedness check only works when sources exist — an output without sources in a domain that requires them should FAIL the contract, not skip the check.

Design principle: block what must be blocked, strip what can be stripped, and log everything — refusal is a user-visible cost, so spend it only where stripping is impossible.

## Rate limiting and abuse prevention

The agent is a cost object with a public endpoint — attackers will find a way to make you spend money (LLM10).

- **Per-user rate limits** are the first line: token budgets per user per day, tool-call caps per user, conversation-length caps. Cap the LLM bill at the user level, not just the API level — an API-level cap stops one endpoint being hammered but not one user driving 10,000 iterations that each cost $0.05.
- **Abuse detection** adds intelligence on top of flat caps: velocity checks (a user tripling their normal rate), loop detection (iteration counts spiking), content-level abuse signals (scraping, mass-extraction prompts). Slow-lane or challenge suspicious users — the agent is too expensive to serve attackers at full speed.
- **Quota exhaustion** is the hard kill switch: a total-spend kill switch per tenant, so when the monthly budget hits 90% you drop to a degraded mode (canned responses) rather than an overage bill. That is the difference between a $500 surprise and a $50,000 one.

Runnable: Template 5 in references/templates.md.

## Audit logging

Audit logging is the "who did what, and can I prove it" layer — the foundation of incident response, compliance, and legal defense.

- **Properties:** immutable, tamper-evident storage — append-only, or at minimum write-once semantics plus hash chaining. A compromised agent must not be able to erase its own trail.
- **What to log:** every tool call with arguments and result hash; every human approval (who approved what); every state-changing checkpoint; every authentication and authorization decision; every guardrail trigger.
- **Replayability:** from the audit log alone, reconstruct any run's actions — what the agent did, in what order, on whose inputs.
- **Retention:** long enough for the compliance regime (finance typically requires years; chat might get by with weeks). Audit logs are the one log class where "delete to save money" is a legal problem.

Runnable: Template 1 in references/templates.md.
