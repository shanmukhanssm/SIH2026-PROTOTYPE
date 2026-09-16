**Load this when:** securing tools (Step 3) — scoping credentials, choosing allowlists, classifying dangerous operations, picking a sandbox level, gating irreversible actions, managing secrets, and pre-writing the containment runbook.

# Tool Security

## Tools are the security policy

Tools are the agent's hands; their design IS the security policy. The agent cannot do anything its tools cannot do, so every tool you expose is a capability you are granting to anyone who can influence the agent's behavior — including the attacker who injects a document. An injected instruction can only do what the tools can do.

## Least privilege

- Every tool gets a scoped credential, not the platform admin token: a search tool gets read-only search access; a refund tool gets exactly one endpoint with a hard amount cap.
- The LLM inherits every permission you give its tools, plus the ability to be talked into using them — an admin credential given "because scoping was harder" is admin access for every attacker who can reach the agent.
- Sign tool results where integrity matters: HMAC-sign payloads the agent will write into durable state, so tampering (memory poisoning) is detectable. Rarely needed for search results; very needed for anything that persists.

## Per-tenant credentials (multi-tenant systems)

- A multi-tenant agent must not hold one all-tenant credential, because Tenant B's injection into a shared tool result becomes Tenant A's data breach.
- Use per-tenant tokens, and derive the tenant ID from the authenticated session, never from model output — the model can be talked into asserting it is tenant A.
- The model's cooperation is irrelevant; the credential decides.

## Allowlists over blocklists

- Enumerate the URLs, files, and tools the agent MAY access; refuse everything else. Blocklists ("block admin domains") are beaten by the first attacker who discovers an unlisted admin domain.
- Add an egress allowlist at the network layer for the agent's outbound calls — the same principle applied to exfiltration routes.

## Validate tool arguments at the boundary

The model's arguments are attacker-influenced data; treat them like any other untrusted input. In the tool itself: schema validation plus range plus type checks, and a semantic check for anything dangerous (negative amounts, email fields containing command characters).

## Policy lives in the tool, not in the prompt

"Never refund more than 500 dollars" written in the system prompt is a suggestion the model might follow; written in the tool's assert statement, it is a fact the model cannot override. Every security-critical rule gets enforced where the model cannot argue with it:

```python
def refund_tool(order_id, amount, *, idempotency_key, actor):
    # boundary validation: the model's arguments are untrusted data
    assert 0 < amount <= REFUND_CAP, "amount outside policy"
    assert actor.tenant_id == order_owner(order_id), "cross-tenant attempt"
    # approval gate for anything above the auto-approve line
    if amount > AUTO_APPROVE_LIMIT:
        approval = interrupt({
            "action": "refund", "order_id": order_id, "amount": amount})
        if not approval.confirmed:
            return {"status": "rejected_by_human"}
    return payment_gateway.refund(order_id, amount, key=idempotency_key)
```

## Dangerous-op gating (human approval)

Classify every tool: read-only / gated / irreversible. Then:

- Funnel every irreversible or high-value action through a single gate node that calls interrupt() — the human is the last filter the model cannot be injected past, assuming the approval UI shows what will actually happen.
- The approval UI is the weakest link nobody reviews. "Approve refund?" without amount, order, and customer is theater — the human clicks yes on reflex and you have built a rubber stamp with extra steps. Show the resolved arguments (concrete, final values, not a template), show what changed since last time, and make reject one click.
- Give every approval a deadline and a default (auto-deny for money actions, escalate to a manager otherwise), and let the human edit the proposed value, not just approve/reject — otherwise a 50-dollar proposal for a 35-dollar refund becomes a full restart.
- Red-team the approval flow: aim injections at producing a confusing approval prompt, because a confused approver clicks approve.

Runnable gate node: Template 6 in references/templates.md.

## Sandboxing: the isolation ladder

When the agent runs code, the isolation level is a security decision with a cost attached — pick deliberately:

| Level | Mechanism | Cost | Stops | Does NOT stop |
|-------|-----------|------|-------|---------------|
| 0: Same process | exec()/eval() in the app | Zero | Nothing (RCE by design) | — |
| 1: Restricted interpreter | Subprocess, limited stdlib, no os/socket imports | Low | Naive payloads | Escapes via dunder tricks, ctypes |
| 2: Container | Docker/gVisor: no network, read-only FS, CPU/mem caps, wall-clock kill | Moderate | Most escapes, network exfil | Kernel exploits (rare), resource abuse without caps |
| 3: microVM | Firecracker-style per-task VM | Higher | Container escapes, kernel bugs | Hardware-level attacks (out of scope) |
| 4: External service | Hosted code-execution sandbox (third party) | Ops-free, trust-based | Your infrastructure risk | Data leaving your boundary |

Decision rules:

- Anything that executes attacker-influenced code must be level 2 minimum — a container with no network, read-only filesystem, memory and time caps, and a hard kill. Level 1 is a speed bump for bored attackers and a liability claim for auditors. Levels 3-4 are for high-value targets where the cost is justified.
- Resource limits are part of the sandbox, not an extra: an unsandboxed `while True: alloc()` at level 2 still burns your CPU budget.
- "No network" is the single most important rule: the difference between "the agent ran bad code" and "the agent exfiltrated data and phoned home" is network egress.

## Secrets management

- No secrets in prompts: keys, tokens, and internal URLs in the system prompt leak via prompt-exfiltration injection and prompt logging. A prompt with a secret is one injection away from being a prompt without one.
- Tools fetch secrets from a secrets manager at call time, scoped to the tool.
- No secrets in state: graph state is serialized into checkpoints — a secret in state is a secret in the database, in backups, and in every state dump. Same for long-term memory stores (plaintext JSON in your DB).
- No secrets in logs or traces: redaction middleware at every logging boundary, plus a secret scanner in CI that fails builds on key-shaped strings.
- Rotate, don't reuse: per-environment keys, provider keys in a KMS, rotation schedules.

## Build vs buy

| Layer | Build | Buy | Recommendation |
|-------|-------|-----|----------------|
| Ingress detection | Regex + heuristics | Guardrails AI, LLM Guard, Protect AI | Buy managed scanners if volume is high; open-source ones are decent and self-hostable |
| Prompt/context safety | Your layered prompts | Prompt-security libraries (rebuff-class) | Build — it is five paragraphs of prompt plus code, and no library knows your hierarchy |
| Tool security | Scoped credentials, sandbox policy | Cloud sandbox services (E2B-class) | Build the policy; buy the sandbox infra |
| Output moderation | Your classifiers + judges | Provider moderation APIs | Build the policy; buy classifiers where coverage matches your domain |
| Audit logging | Append-only store + your conventions | Compliance platforms | Build — audit logging is storage discipline, not magic |
| Secrets | KMS/secrets manager | Same (managed KMS) | Always use the platform KMS; never roll your own crypto |
| Red-teaming | Internal drills | External services, Giskard-class tools | Hybrid: tools for coverage, humans for creativity |

Meta-rule: buy the infrastructure (sandboxes, KMS, scanners), build the policy (hierarchy, tool scoping, approval gates, audit schema). The policy is your threat model expressed as rules, and no vendor knows your threat model. Teams that buy everything get vendor-shaped security that misses their specific excessive-agency holes; teams that build everything spend months on sandbox escapes vendors solved years ago.

## Containment runbook (pre-write it, drill it)

Security incidents in agents are slow-burning and evidence-rich. The playbook:

1. **Contain the capability, not just the traffic.** A classic breach says "rotate keys". An agent breach says: revoke the tool credential AND kill the in-flight runs that hold it AND freeze the memory writes AND suspend the approval queue. A poisoned memory written 10 minutes ago will still be retrieved next week if the write path is not frozen.
2. **Freeze the evidence fast.** Order: audit logs first (immutable if built correctly), traces second, checkpoints third. Every minute spent deciding is a minute evidence may be overwritten.
3. **Reconstruct, don't guess.** The timeline comes from the trace tree and the audit log. The classic failure is responding before reconstructing: revoking everything, losing the evidence, and never learning the mechanism.
4. **Scope the blast radius along the memory graph.** Which other users' contexts could the poisoned state reach — shared workspace, org-level memory, few-shot pools? The namespace map is the blast-radius map.
5. **Postmortem into tests.** Every finding becomes red-team cases, guardrail rules, and a signature-library entry. Security postmortems without test output are how the same attack succeeds twice.

Pre-write the containment commands: the exact API calls to revoke credentials, kill runs, and freeze writes take 20 minutes to find in the docs and 20 seconds to execute — and the difference is how far the poisoned state travels. One page, copy-paste ready, rehearsed in a drill. (Live incident execution belongs to agent-debugger; this runbook is the prepared artifact.)
