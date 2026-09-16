# Runbook Library — 16 Incident Runbooks

**Load this when:** the symptom router (references/triage-protocol.md) gave you a runbook number and you need the ranked causes, investigation steps, fix, and prevention.

Every production agent failure belongs to one of a small number of failure classes. Each runbook below has a ranked cause list with probabilities, a cheapest-first investigation order, a concrete fix, and a permanent prevention. A runbook is a decision tree you write once, in daylight, so that 3am-you doesn't have to be smart — only disciplined.

## Severity at a Glance

| # | Runbook | Typical severity | Money exposed? | Typical detect-to-fix |
|---|---------|------------------|----------------|------------------------|
| 1 | Loops forever | HIGH (cost+availability) | Usually no | Hours without cost alerts |
| 2 | Wrong tool chosen | MEDIUM (quality) | Sometimes | Days (eval slices catch it) |
| 3 | Empty/truncated responses | MEDIUM (UX) | No | Hours |
| 4 | Mysterious timeout | MEDIUM | No | Hours |
| 5 | Cost spike overnight | HIGH | Yes | Days without daily cost deltas |
| 6 | State corruption / lost messages | HIGH | Yes (double-apply!) | Days |
| 7 | Streaming breaks the UI | MEDIUM | No | Hours |
| 8 | Parallel results lost | MEDIUM-HIGH (silent) | Sometimes | Weeks (silent!) |
| 9 | Interrupt won't resume | HIGH (blocked money) | Yes | Days |
| 10 | Fallback not triggering | HIGH (during provider outage) | No | Only during an outage |
| 11 | Memory pollution | MEDIUM-HIGH | Sometimes | Weeks |
| 12 | Eval score regression | MEDIUM (unshipped) | No | Days |
| 13 | Hallucinated tool calls | MEDIUM | Yes (if registry lax) | Hours |
| 14 | Model got worse without deploy | MEDIUM | No | Days without pins |
| 15 | Injection attempt detected | HIGH (if boundary fails) | Yes | Minutes (if alerted) |
| 16 | HITL queue overflow | MEDIUM-HIGH (blocked money) | Yes | Days (sneaks up) |

Runbooks 8 and 11 are the silent ones — they fail without failing, and weeks can pass before anyone notices. That is why their preventions are eval suites rather than alerts: you cannot alert on what produces no error.

---

## Runbook 1 — Agent Loops Forever

**Symptom:** A run never terminates; the trace shows the same node visited 15, 40, 100 times. Latency p95 goes to max run time, cost ticks up steadily, users report "it's still thinking." Sometimes dies with `GraphRecursionError: Recursion limit of 25 reached` — that error is the GOOD outcome, because the framework caught the loop. The runs that don't error and just keep going are the ones that hurt.

**Likely causes (ranked):**
1. The model doesn't know what "done" looks like (~40%) — no explicit completion condition; it keeps clarifying or re-running tools. It's doing exactly what you asked: "keep going until you have an answer."
2. A tool result never changes between calls (~20%) — e.g. "check refund status" returns "pending" forever. The loop is correct behavior against a data dependency that hasn't changed yet.
3. A retry policy re-invokes a failing node indefinitely (~15%) — usually `retry=True` hardcoded with no max attempts or terminal failure path.
4. The finish/END transition condition has a bug (~15%) — a routing node that never matches a branch condition and defaults back to itself.
5. Summarization middleware disabled or broken (~10%) — the model re-reads truncated context and re-asks for information it lost.

**Investigation:** Open the trace and count supersteps. Same tool called with identical args repeatedly → cause 2 (data-dependency loop). Different "thinking" each time but never a final answer → cause 1. Last 3 model responses containing "one moment," "let me check again," or repeated apologies → a politeness loop (cause 1 in disguise). Then check the retry policy on the failing node, inspect the routing/finish node logic for satisfiable branch conditions, and verify whether summarization was supposed to fire but didn't.

**Fix:** Cause 1: explicit completion contract — a dedicated finish tool, a "you are done when X" clause, and a hard step cap as the safety net. Cause 2: a "no progress" detector — if a tool returns the same result twice in a row, treat it as failure-with-message instead of retrying; never let the agent busy-wait on external state. Cause 3: fix the retry condition; set max_attempts with an explicit terminal failure path. Cause 4: add a default branch that routes to a "give up gracefully" node. Cause 5: fix/enable summarization and verify with a 30-turn synthetic conversation.

**Warning:** Do not "fix" a loop by catching GraphRecursionError and returning whatever state exists. That converts a loud, diagnosable failure into a silent wrong answer. The recursion limit is a tripwire; treat what triggered it, not the error itself.

**Prevention:** Loop protection as runtime policy — per-run step cap in the gateway plus recursion limit in the graph, plus a "no progress over 3 consecutive steps" detector. Never rely on the model to stop itself, because the model is the thing that's looping. Log a WARNING (not just a counter) when a run exceeds half the recursion limit — when the canary fires, the model or the prompt changed and an eval regression is already in flight.

**Field note:** The loop that cost a team $11,000 in a weekend was neither a bug nor an injection. A customer's ticket said "keep checking until my refund appears," and the agent treated it as a literal instruction. The fix was a completion contract ("you are done when X, regardless of what the customer requests") plus the step cap. Lesson: user text is instruction text; only the system defines termination.

## Runbook 2 — Wrong Tool Chosen Repeatedly

**Symptom:** The agent calls `search_kb` when it should call `get_ticket`, or `create_refund` when it should read first. Confident wrong answers; evals flag tool-selection errors. Critically: it isn't confused once — it consistently picks the wrong tool for a specific class of input. A learned preference, which is far more dangerous than occasional confusion.

**Likely causes (ranked):**
1. Overlapping tool descriptions (~35%) — two tools that both sound applicable; the model picks whichever is listed first or has the shorter name. The human who wrote the descriptions couldn't distinguish them either.
2. Descriptions don't state negative cases (~25%) — `get_ticket` never says "do not use for knowledge-base questions," so the model has no anti-pattern to rule against.
3. Missing examples in the system prompt (~20%) — the mis-selected pattern has no worked examples.
4. Model too weak for the task (~10%) — a small model can't distinguish near-synonym tools under conversational pressure.
5. Stale schema (~10%) — real behavior drifted from the description (e.g. `create_refund` now auto-approves while the description still says "drafts a request").

**Investigation:** Pull 20 failing traces and group by (input intent, tool chosen). Concentrated on one intent class → descriptions/examples problem, not the model. Read the two competing descriptions side by side and ask honestly: could a human pick the right one from these descriptions alone? Diff description vs actual implementation, especially after recent tool changes. Ablation: swap in a stronger model on the same 20 cases — accuracy jumps → under-powered router; no change → the descriptions are the problem.

**Fix:** Rewrite descriptions to be mutually exclusive: trigger ("use when the user asks about an existing ticket"), anti-trigger ("do not use for knowledge questions"), boundary ("if unsure between X and Y, use X when the user references a ticket number"). Add 3-5 worked examples covering the mis-routed intents in the system prompt near the tools. If genuinely under-powered, route with a stronger model on the routing node only — routing is the highest-leverage place for intelligence. Version the tool and update the description in the same deploy when behavior changes.

**Warning:** Never fix a wrong-tool problem by removing the competing tool's description or renaming tools to steer the model ("zz_dont_use_this"). That makes selection depend on undocumented ordering tricks the next model update will silently re-learn. Descriptions and examples are the only stable steering mechanism.

**Prevention:** Tool-selection accuracy in the nightly eval suite, sliced by intent class; alert when any class drops below threshold.

**Field note:** A support agent kept answering billing questions from the knowledge base because `search_kb` was listed before `get_invoice` and its description contained the word "charges." Two tools, one word of overlap, six weeks of wrong-but-plausible answers. The fix took ten minutes (negative cases in both descriptions); the discovery took an eval slice nobody had built yet.

## Runbook 3 — Empty or Truncated Responses

**Symptom:** An empty string, or a response that cuts off mid-sentence. Sometimes the trace ends cleanly with a suspiciously short final message; sometimes an error is buried in logs. One of the most user-visible failures and the most frustrating: the server-side trace often looks perfectly healthy.

**Likely causes (ranked):**
1. max_tokens / output-token limit too low (~30%) — the model wanted 800 tokens, got capped at 200. Most common, easiest to check.
2. Context overflow (~25%) — history plus tool results exceeded the window; the provider raised `context_length_exceeded` and an over-broad exception handler swallowed it into an empty string.
3. Structured-output parse failure (~20%) — output didn't match the schema, the parse threw, the fallback silently returned None, which serialized as empty.
4. The finishing node returns the wrong state field (~15%) — final answer written to `messages`, API reads `final_reply` (or vice versa). Run "succeeds" with a healthy trace and an empty payload.
5. Streaming bug (~10%) — server streamed correctly but the client's assembler dropped chunks (proxy buffering or a chunk-boundary bug).

**Investigation:** Open the trace: was the model's raw response itself truncated? Yes → cause 1 or 2. Response complete but the user saw nothing → cause 4 or 5. Check error logs for swallowed `context_length_exceeded` or JSON parse errors. Input tokens near the context limit → cause 2 (also visible as a rising trend over long threads). Compare the trace's final state against what the API returns — differ → cause 4. Reproduce with curl against the stream endpoint and inspect chunk boundaries for cause 5.

**Fix:** Raise max_tokens to a sane budget (e.g. 1500-4000 for drafting nodes) with a graceful truncation guard (ellipsis marker, never a bare mid-word cut). Turn on context management (summarization/trimming) for long threads. Make the over-broad exception handler raise instead of returning "" — a visible 500 beats a silent empty string. For structured output: retry once with the parse error fed back to the model; on second failure return a typed "unparseable" result, not None. Fix the field-name mismatch and add a contract test asserting the field the API reads is present. For streaming: disable proxy buffering for SSE routes, send heartbeats, make the client assembler defensive.

**Prevention:** An eval assertion that "every successful run has a non-empty final message over N tokens" catches all five causes at the harness level. Add it tonight.

**Field note:** A team spent three days chasing a "model truncation bug" — intermittent, production only. The model was fine: the finishing node read `final_reply` while the API serialized `messages`, renamed in a "cleanup" deploy two weeks earlier. The contract test was the permanent fix.

## Runbook 4 — Mysterious Timeout

**Symptom:** Runs intermittently time out — 30s, 60s, sometimes at the platform level ("gateway timeout"). No error in the trace beyond a run that just stopped. Correlates with nothing obvious. Your p99 latency is a cliff, not a tail.

**Likely causes (ranked):**
1. A tool hangs on an external dependency that's slow, not down (~35%) — the ticket DB under load answers in 45 seconds; the tool has no timeout, so the node blocks indefinitely.
2. LLM provider tail latency (~25%) — prefill spikes on a shared tier, or a 429-retry-backoff sequence burned 40 seconds before the call even started.
3. Parallel-branch deadlock (~20%) — a fan-out node waits on a branch that errored without signaling the join; the join never fires.
4. Checkpointer slowness (~10%) — unbounded checkpoint blobs make each superstep's persist take seconds; creeps up as conversations get longer.
5. Streaming connection held open but nothing emitted (~10%) — the run finished, but the client waits on a dead SSE stream; neither side sent a close signal.

**Investigation:** Find the timed-out run's trace and identify the last recorded event — the node that never logged a completion is your prime suspect. Correlate with the tool's external dependency metrics at that timestamp. Check gateway logs for 429/backoff sequences in that window. For parallel runs, count branches: did every Send produce a completion event? A branch that ends with an exception but no join event is the deadlock signature. Measure checkpoint write duration per superstep — growing with thread length → cause 4. Trace shows a clean finish → check the client-side stream (cause 5).

**Fix:** Every tool gets a wall-clock timeout (e.g. 15 seconds) and a retryable-failure policy — a slow dependency becomes a fast failure with a clean user message. Per-call timeouts, retries, and a circuit breaker on the LLM gateway; log backoff sequences so tail latency is visible instead of mysterious. For fan-out, every branch must end in a guaranteed state update (wrap branch logic in try/except and write an explicit error marker), and the join must not block forever on a missing branch. Cap checkpoint size — trim/summarize state, move large artifacts to object storage with a reference in state. Add SSE heartbeats and client-side run-status polling as a fallback ("if no token for 20 seconds, query the run status").

**Prevention:** Timeouts at three levels — tool, node, run — and alert when p99 run latency approaches any of them. Timeouts should be designed, not discovered.

**Field note:** The "mysterious" 60-second timeouts were the load balancer's read timeout — 60 seconds flat, no jitter, no errors; the trace just ended. The run itself finished fine at 75 seconds server-side, and re-running always worked because the second attempt resumed from checkpoint 12 of 14. The fix was SSE heartbeats and a 120-second stream timeout. The deeper lesson: client and server disagreed about whether the run had failed, and only the checkpoint made that disagreement harmless.

## Runbook 5 — Cost Spike Overnight

**Symptom:** Daily LLM spend tripled overnight with flat traffic. No errors, no latency change, no deploy — just a bill. Or there was a deploy, and nobody connected it to the bill until accounting noticed three weeks later. This is the runbook that gets you a meeting with the CFO.

**Likely causes (ranked):**
1. A prompt/context change silently increased input tokens per call (~30%) — a new tool result format, a longer system prompt, a summarizer disabled "temporarily."
2. A loop class emerged (~25%) — some runs now hit the recursion limit every time (a subtle routing regression); each run burns its full budget.
3. A background job re-runs failed runs on a schedule (~20%) — a cron retry bug re-billing the same doomed tool call hourly, forever.
4. Traffic moved to a more expensive model (~15%) — a routing config change, or a fallback policy now sending 80% of traffic to the expensive provider.
5. Provider pricing changed or cache misses jumped (~10%) — prompt-cache hit rate dropped from 80% to 20% because someone moved the tool schemas to the end of the prompt.

**Investigation:** Slice cost by run type, then by node, then by model — the spike localizes to one cell. One tenant → look for a loop or a scraping script. One intent → read five traces; usually a prompt-cache breakage or a tool contract regression. Across the board → check prompt-cache hit rate and model mix. Compare tokens-per-call distribution before/after the spike for the same traffic profile. Check the deploy log for the spike window and cross-reference with prompt registry history. Pull the highest per-run cost runs and look for the loop signature (Runbook 1) or giant tool results.

**Fix:** Depends on the cell you found: revert the prompt change; fix the routing regression and add the no-progress detector; fix the cron retry bug with idempotency-key deduplication; correct the routing/fallback config; restore prompt-cache placement. Each is a one-line to one-hour fix — finding which one requires the sliced metrics.

**Prevention:** Per-node, per-model cost metrics with a daily delta alert, plus a per-run cost cap. A cost dashboard that only updates at month-end converts a one-hour fix into a one-month bill.

**Field note:** The "overnight" spike was actually nine days old by the time accounting noticed. A background job had started retrying every failed run hourly, each retry re-billing the same doomed tool call. Daily cost alerts would have caught it day one; idempotency-key dedupe would have made the retries free. Both were added after the fact. The $3,400 bill was not refunded by the provider.

## Runbook 6 — State Corruption or Lost Messages

**Symptom:** The agent forgets mid-conversation what it said two turns ago. A resumed run starts from the wrong point. Intermittent "checkpoint not found" errors. A resumed thread replays a side effect — double refund. Crashes with `PicklingError` / `Object of type X is not JSON serializable` on a specific node. The most dangerous failure class because the system appears to work — it just works wrongly.

**Likely causes (ranked):**
1. Wrong reducer (~30%) — a channel written by multiple nodes uses the default overwrite reducer; the last writer silently erases earlier writes. In fan-out, three branches write to `findings`, only the last survives.
2. Non-serializable object in state (~25%) — a datetime, enum, Decimal, or dataclass that worked with the in-memory dev checkpointer but crashes the production JSON checkpointer.
3. Checkpoint version skew (~20%) — graph code changed (schema renamed/fields added) and an old checkpoint no longer maps onto the new schema at resume.
4. Two processes running the same thread (~15%) — a cron job and a user action both invoke the same thread_id; interleaved checkpoint writes clobber each other.
5. Hand-edited or partially-migrated checkpointer data (~10%) — a "cleanup" script left checkpoint tables inconsistent.

**Investigation:** Reproduce locally with the same checkpointer and sequence — cause 1 is deterministic in fan-out paths. Find the crash node and inspect what it writes: anything not a dict/list/str/int/float/bool/None is suspect. Compare the state schema at checkpoint creation time versus now using git history. Search for duplicate thread usage — runs on the same thread with overlapping start times. Audit recent database scripts touching checkpoint tables.

**Fix:** Define explicit reducers (merge/append/overwrite-with-reason) for every shared channel; assert in an eval that parallel results all survive. Serialize at the boundary — convert non-JSON types to primitives inside nodes — and add a test that checkpoints and resumes every node in the graph against a real production checkpointer. Version the state schema and map old checkpoints on resume, or drain in-flight runs before deploying. Enforce single-runner-per-thread with a per-thread lock (advisory lock in Postgres). Restore from backup rather than patching checkpoint data directly; block direct writes to checkpoint tables.

**Prevention:** A CI test that (1) runs every node through checkpoint+resume against a real Postgres checkpointer, (2) asserts reducer behavior under simulated concurrency, and (3) fails on any non-serializable state. This one test prevents half of this runbook.

**Field note:** The corruption that took down a support agent's memory for a week was a single datetime in one node's return value. Invisible for months because dev used the in-memory checkpointer; prod crashed only on that one path, in JSON serialization, at 2am. The fix list: one line of code (`.isoformat()`) and one line of test (checkpoint+resume every node in CI). The test has caught three more serialization bugs since.

## Runbook 7 — Streaming Breaks the UI

**Symptom:** Tokens don't appear until the end (buffered), appear garbled or interleaved, or the UI crashes mid-stream on a partial JSON tool call. Server-side traces show a perfectly healthy run. The classic report: "it works in localhost but not behind the load balancer."

**Likely causes (ranked):**
1. A proxy/CDN/load balancer buffers SSE responses (~40%) — nginx `proxy_buffering on` is the default; the stream arrives as one lump at the end. The agent works perfectly; the infrastructure between agent and user doesn't.
2. The client renders partial content as final (~25%) — a tool-call event arrives with a partial JSON args string and the UI calls JSON.parse on it.
3. Mixed streaming modes (~15%) — server emits both token events and message events; the client double-renders because each mode also carries the full message.
4. Stream dropped by idle timeout (~10%) — a proxy kills the connection after 60 seconds of no events during a long tool execution, while the run continues server-side.
5. Chunk-boundary bug in the client's assembler (~10%) — SSE events split across TCP chunks are concatenated without newline handling, mangling multi-byte characters or dropping delimiters.

**Investigation:** `curl -N` the stream endpoint directly, bypassing the proxy. Streams smoothly → the proxy is the problem (cause 1 or 4). Inspect raw bytes of the first events — look for missing buffering headers (`X-Accel-Buffering: no`) and absent heartbeat comments. Reproduce the client crash with the exact event that triggered it — partial event → cause 2. Count duplicate renderings of the same content → cause 3. Check proxy idle timeouts against maximum tool-execution time → cause 4.

**Fix:** Disable buffering for the stream route (`X-Accel-Buffering: no` for nginx), flush at the app layer, verify with `curl -N`. Client renders defensively — buffer partial tool-call events until the args JSON is complete; never parse half a string. Pick one streaming mode per endpoint; namespace custom events so the client can safely ignore what it doesn't understand. SSE heartbeats (`: ping` comment lines) every 10-15 seconds during long tool runs. Client-side re-attach path: on stream drop, poll run status and re-subscribe from the last seen event index.

**Prevention:** A "streaming contract" test in CI that spins up the real server behind the real proxy config, streams a synthetic run, and asserts events arrive with inter-event latency under 500ms. Streaming bugs are environment bugs; only an environment test catches them.

**Field note:** A team burned two days because streaming worked in localhost, staging, and production — except behind the one CDN edge the mobile app used, which buffered SSE by default. The fix was a one-line header; the discovery took a `curl -N` from a phone network. The permanent fix was the environment test; the temporary fix was a support macro: "Works on WiFi, not on 4G? It's not the agent."

## Runbook 8 — Parallel Results Lost or Interleaved

**Symptom:** A fan-out node — three subagents researching in parallel — produces results for two branches but the third is missing. Or results concatenate in random order from run to run. No error anywhere. The run "succeeds" with incomplete data and the final answer confidently omits one branch's findings. One of the two silent failure classes; weeks can pass before anyone notices.

**Likely causes (ranked):**
1. All branches write to the same state channel with an overwrite reducer (~50%) — last writer wins, earlier results silently destroyed. The single most common cause; a one-line fix once you know to look.
2. Branches write to separate channels but the join node reads before all writes land (~25%) — the graph proceeds on "first result in" instead of "all results in."
3. Non-deterministic ordering (~15%) — results collected without a stable order; the model treats ordering as meaning and produces inconsistent answers.
4. One branch fails and its error is swallowed (~10%) — a broad exception handler catches silently, so the join sees two of three results and never knows a branch died.

**Investigation:** Find a trace with missing data and check the branch's events. Branch executed and produced a result → cause 1 or 3 (overwritten or ordered away). Branch never completed → cause 2 or 4. Read the reducer on the shared channel — default overwrite is the smoking gun. Compare Send count to received count at the join. Run the same input 10 times; content changes run to run → cause 3.

**Fix:** Shared channels: explicit merge reducer — append results keyed by branch ID ({"branch": "billing", "result": ...}), and have the join iterate all keys. Separate channels: the join reads all channels explicitly and never proceeds until every expected channel is present. Fix ordering by sorting or keying results deterministically (by branch ID), never by completion time. Make branch failures loud: each branch ends in an explicit {"status": "ok|error"} record, and the join checks statuses before assembling.

**Pro tip:** In fan-out code, the bug is never where you're looking. It's almost always in the reducer or the join — the two places that feel like plumbing. Read those first.

**Prevention:** A dedicated eval: a fan-out graph on synthetic inputs must contain 100% of branch results across 50 repeated runs. Plus the CI serialization test from Runbook 6.

**Field note:** The symptom that finally exposed a lost-result bug was a report drafted from two of three subagent findings — correct and confident, just incomplete, like a three-legged chair that never falls over. The branch had errored, the exception handler had swallowed it, and the join had counted two of three as "all done." The branch-completeness eval now runs on every deploy and fails loudly — which is the entire point.

## Runbook 9 — Interrupt Won't Resume

**Symptom:** An interrupted run — typically a HITL approval — can't be found, resumes from the wrong point, re-triggers the same interrupt in a loop, or crashes with "thread not found." The approval queue shows pending items that can't be acted on. Customers are waiting, reviewers are clicking, and nothing happens. This is where money sits frozen on the table while you debug.

**Likely causes (ranked):**
1. thread_id mismatch (~35%) — the resume call uses a different thread_id than the run was created with (a UI bug, or a new thread created on resume).
2. Checkpointer mismatch (~25%) — run created with one checkpointer instance/database, resume reads from another (environment misconfiguration or a multi-region split).
3. Resume value wrong (~20%) — passes {"approved": true} when the interrupt expected {"decision": "approve"}.
4. Interrupt inside a loop (~10%) — the interrupt re-fires on every superstep because it's placed in a node that re-runs; resuming lands on a fresh interrupt every time.
5. Checkpoint expired or GC'd (~10%) — retention deleted the checkpoint before resume. Approvals can take days or weeks; a shorter TTL means the approval is gone.

**Investigation:** Log the exact thread_id at run creation and at resume, then diff them — the number-one cause takes one minute to check. Verify both calls hit the same checkpointer (same DB endpoint, same table prefix). Inspect the interrupt payload recorded in the checkpoint versus what the resume command passes. Look at the trace: does the same interrupt fire again immediately after resume? Check retention/GC configuration on the checkpointer.

**Fix:** Pass thread_id through the entire UI stack without re-deriving it; add a server-side assertion that the resume thread_id matches a pending interrupt's thread. Make the checkpointer config a single source of truth; forbid per-environment overrides that diverge. Type the resume value — define the interrupt payload schema alongside the interrupt and validate resume values against it with a clear error message. Move the interrupt so it fires once (outside any re-executed loop body) or guard it with an "already interrupted" state flag. Align retention policy with the longest expected HITL delay — approvals can take weeks, and the TTL must exceed that.

**Prevention:** An integration test that interrupts a run, kills the process, resumes with the correct payload, and asserts the run continues from the right node — against the production-grade checkpointer in CI. Never let thread_id or checkpointer config vary between environments.

**Field note:** A HITL approval queue showed 47 pending refunds that "couldn't be approved" for three days. Every resume hit "thread not found" because the queue UI had regenerated thread IDs when rendering the approval list. The approvals were one commit away the whole time; the customers' patience was not. The end-to-end thread_id assertion was added the same day; the 47 refunds went through in an hour.

## Runbook 10 — Model Fallback Not Triggering

**Symptom:** The primary LLM provider is down or rate-limited, and instead of falling back to the secondary, runs fail or queue. The fallback code exists, was tested, is in production — it just never fires when it should. Insidious: it looks correct in code review and passes unit tests; it simply doesn't match the actual failure signature of a real outage.

**Likely causes (ranked):**
1. Exception-type mismatch (~35%) — the fallback catches `OpenAIError`, but the failure surfaces as a generic Exception or a different wrapper type after the retry layer re-raises. The catch block never matches.
2. Retry policy exhausts before fallback (~25%) — the gateway retries the primary 3 times with long backoff (burning 60 seconds), and the run-level timeout kills the run before fallback is considered.
3. Fallback triggers on errors but not on *slow* (~15%) — the primary isn't erroring; it's returning a 500ms-per-token crawl. The fallback condition never evaluates latency.
4. Fallback target shares the outage (~15%) — the "fallback" is the same provider with a different API key or same region; the infrastructure is identical.
5. The fallback path itself fails (~10%) — the secondary model lacks a feature the code assumes (no tool calling, different message format); the fallback throws and masks the original error.

**Investigation:** Force a primary failure in staging (kill the API key, block the endpoint) and watch. Does the fallback fire? Usually no — and the log tells you which catch block never matched. Read the actual exception class in the gateway logs during a real outage window. Time the failure path: time-from-first-error to fallback-invocation; if it exceeds your run timeout, the ordering is the bug. Verify the fallback model supports everything the code path uses — tool calling, structured outputs, system prompts. Confirm the fallback provider is actually different infrastructure (different company, or at least different region/account).

**Fix:** Catch by a broad, defined taxonomy — not provider-specific exception classes. Create a `RetryableProviderError` wrapper that catches everything, with fallback keyed on that. Fallback on first retryable failure after at most one quick retry — do not exhaust retries before falling back; the fallback IS the retry at the next level. Add latency-based circuit breaking: if p95 call latency exceeds 2x baseline for 60 seconds, route to secondary. Test with a monthly chaos drill (kill the primary on purpose). Make the chain homogeneous on features: every model must support the same minimum capability set, verified in CI.

**Prevention:** Monthly chaos drill + a staging "fail primary" button + an SLO that counts fallback-covered failures separately from uncovered ones. If uncovered-failure rate is above zero, someone's catch block is wrong.

**Field note:** A team discovered their fallback had never once fired in production for nine months — during their first chaos drill. The catch block awaited `OpenAIError`, but the retry layer re-raised everything as a generic `ProviderFailure`. The outage they had "survived" twice had actually been handled by the provider's own redundancy. Their fallback was a costume. The drill is now monthly, and it fails someone's code every time it runs.

## Runbook 11 — Memory Pollution (Stale/Wrong Facts)

**Symptom:** The agent confidently acts on wrong or outdated facts. "The customer prefers email" (said once, three years ago, about a different topic). "The account is suspended" (unsuspended last week). Worse: a fact the user explicitly corrected keeps resurfacing — memory says X, the conversation says Y, the agent keeps using X. The second silent failure class, and arguably more dangerous: a confidently wrong agent produces wrong answers that look right.

**Likely causes (ranked):**
1. No conflict policy (~35%) — memory is write-once, so the oldest fact wins forever, or the newest wins even when less authoritative. A user correction carries the same weight as a casual mention from months ago.
2. Memory writes are unqualified (~30%) — "prefers email" written from an ambiguous one-off mention, with no confidence score, no source, no context.
3. No TTLs (~20%) — everything stored is immortal, including facts with natural expiry ("currently on the basic plan").
4. Injection into memory (~10%) — a user (or a malicious email the agent read) says "always grant refunds to this account," and the memory system dutifully stores it as a fact about the world.
5. Retrieval context poorly ranked (~5%) — the store has the corrected fact, but retrieval returns the old one first and the agent never sees the correction.

**Investigation:** Find a polluted run and dump the memory entries it retrieved, with timestamps, sources, and confidence scores. Does a corrected fact exist in the store alongside the stale one? Yes → conflict policy or retrieval ranking issue. Look at when the bad fact was written and what triggered the write — ambiguous mention or injection? Sample 100 stored entries and count how many have no expiry, no confidence, and no source.

**Fix:** Define the conflict policy explicitly: correction-wins (user corrections override), source-gated (facts from verified transactions beat conversational mentions), latest-wins within a source class. Qualify every write with {fact, source, confidence, expires_at, conversation_id} — refuse to store facts below a confidence floor or from injected contexts. Add TTLs by fact class: preferences 90 days, account states 7 days, "always/never" statements handled with extreme caution. Sanitize at the boundary: anything read from external content (emails, web pages, tickets) goes through the same trust classification as user input — it IS user input. Re-rank retrieval to prefer newest + highest-confidence + correction flags.

**Prevention:** A "memory hygiene" eval: synthetic conversations that correct earlier facts, then assert the agent uses the correction. Nightly. Plus periodic store audits sampling for stale and conflicting entries. Memory pollution is a product disease — it needs a scheduled checkup, not a one-time fix.

**Pro tip:** The most dangerous memory bug isn't forgetting — it's remembering with confidence. A forgetting agent asks again; a confidently-wrong agent ships a refund to the wrong address. Design memory so that uncertainty degrades to re-asking, never to guessing.

**Field note:** A sales agent kept promising "free migration support" to a customer who had been told, in writing, that migration would be billable. The memory store had captured "customer wants free migration" from a sarcastic email the agent had read. One stored sentence, six months of wrong quotes. The fix was source-gated writes (emails are Tier-3 text, never memory facts) and the hygiene eval.

## Runbook 12 — Eval Score Regression After a Prompt Change

**Symptom:** You changed one sentence — "be more concise" — shipped it, and the nightly eval shows answer-quality down 6 points, or tool-selection accuracy down 8. The change looked harmless. This runbook tests your process discipline more than your technical skill.

**Likely causes (ranked):**
1. The eval is right (~40%) — the change altered behavior on a slice you didn't consider. "Conciseness" stripped the reasoning steps the model needed to pick the right tool.
2. Judge drift (~25%) — LLM-as-judge scores are noisy (especially a temperature-1 judge); the "regression" is within the judge's noise band. You didn't regress; your measurement tool flickered.
3. The eval set is stale (~20%) — the golden set contains cases the old prompt overfit to; the new prompt generalizes differently — down on the set, possibly up in production.
4. Interaction effect (~15%) — the prompt change combined with a model update or middleware change; you changed two things and attributed the delta to one.

**Investigation:** Reproduce: re-run the eval on both prompt versions, three times each. Stable 6-point delta is real; wandering 2-8 is noise. Slice the regression by category — which eval cases lost points, and what do they have in common? Read the judge's rubrics on the lost cases: "too verbose" (the intended change) or "wrong tool" (unintended)? Check the deploy log for what else changed in the window — model version pin, middleware config. Finally, A/B in a canary with 5% of live traffic, comparing production outcomes (satisfaction, escalation rate, refund-error rate), not just judge scores.

**Fix:** Unintended regression on a slice → fix the prompt by keeping the intended change while preserving the reasoning scaffold. Judge noise → tighten the judge (lower temperature, more constrained rubric) before trusting deltas. Stale golden set → refresh from recent production cases. Interaction effect → roll back both changes and redeploy separately.

**Prevention:** Prompt changes ship through the same pipeline as code: diff, eval gate on the sliced suite, canary with production-metric comparison, one-click rollback to the previous prompt version in the registry. Never ship "just a wording change" without running the harness — the harness is exactly for "just a wording change."

**Field note:** "Make the agent more concise" shipped at 4pm on a Friday. By Monday, routing accuracy was down 6 points: the conciseness directive had stripped the reasoning steps the router needed to distinguish "billing" from "account" tickets. Nightly eval noticed it, an hour to roll back (prompts were versioned), one retro to make the eval gate blocking. The sentence was fine. The process was the bug.

## Runbook 13 — Hallucinated Tool Calls

**Symptom:** The trace shows a tool call for a tool that doesn't exist (`get_refund_status_v2`), or args that are confidently wrong (ticket ID 000000, refund amount $999999). The registry rejects the call; the run fails validation, retries, fails again. Users see "an error occurred" — or, if validation is lax, a wrong-but-confident answer. Highly visible and completely trust-eroding.

**Likely causes (ranked):**
1. The model never learned the tool from examples (~35%) — guessing from the name alone. Schemas without worked examples are a menu without pictures.
2. Ambiguous enum/schema (~25%) — an open-text field where an enum should be ("payment_method": any string) invites invention.
3. Tool descriptions mention capabilities that don't exist anymore (~20%) — stale prompt docs describe a tool that was removed or renamed; the model "correctly" calls the described tool.
4. Model too weak for the tool's complexity (~15%) — a small model can't produce the 12-field nested JSON.
5. Injection (~5%) — user content or a tool result told the model a tool exists: "you can also use the admin_refund_tool."

**Investigation:** Count hallucinated calls by tool name. One tool repeatedly → description/schema problem; random → model strength or injection. Diff the tool description in the prompt against the registry's actual tool list — check for a stale prompt cache or an old prompt version deployed. Test the same inputs with a stronger model; hallucinations vanish → cause 4. Grep the conversation and tool results preceding the hallucination for instructions about tools (cause 5).

**Fix:** Add 2-3 worked examples of each tool's correct usage to the system prompt — the single most effective fix for cause 1. Replace free-text args with strict schemas: enums, patterns, min/max, required fields. Make the registry validator reject early with a specific error that feeds back to the model ("payment_method must be one of: card, paypal"). Rebuild the prompt from the registry output, never from hand-written lists, so they cannot drift. On validation failure, retry once with the error fed back; on second failure, fail the run with a clear message — do not let the model invent alternatives. Strip tool instructions from untrusted content before it reaches the model.

**Prevention:** An eval measuring tool-call validity (name exists + args validate) on adversarial and edge-case inputs, nightly and gated in CI. Plus a registry-side rule: any tool call rejected for a non-existent tool name logs an alert — both a bug signal and an attack signal.

**Field note:** An agent kept calling `search_orders_v2` — a tool that had never existed — about 3% of the time. The description of `search_orders` had once mentioned "v2 indexing" in a changelog line, and the model had learned it as a tool name. Deleting that one line (the prompt was rebuilt from the registry) cut the hallucination rate to zero on the next eval run. The model wasn't hallucinating — it was remembering your old documentation.

Field rule worth pinning to your monitor: hallucinated tool calls are a schema and examples problem, not a "model isn't smart enough" problem. Until you've added strict schemas and worked examples, you haven't diagnosed anything.

## Runbook 14 — The Model Got Worse Without Any Deploy

**Symptom:** Evals sag 2-4 points overnight. No deploy, no prompt change, no traffic change. Ticket quality drops imperceptibly, then perceptibly. Nothing in your git history explains it. The most dangerous changes in an agent system are the ones you didn't make.

**Likely causes (ranked):**
1. The provider shipped a new model version under the same name (~50%) — a "default" alias silently moved to a new snapshot, or the model updated behind the same version string.
2. The provider changed serving behavior (~25%) — different quantization, system-prompt handling, or tool-call formatting; same name, shifted behavior.
3. Your data changed under you (~15%) — the KB was re-chunked, the ticket schema migrated, a new product launched that your examples never mention.
4. A dependency change (~10%) — an SDK update altered retry/streaming/JSON handling; a Pydantic major bump changed validation strictness.

**Investigation:** Confirm by running the nightly eval 3 times — stable delta means something real changed. Diff the traces — same prompt, same inputs — and check whether the model's raw output is different (provider changed) or your processing is different (dependency changed). The trace's model call spans answer this directly. Check the provider changelog and status page and your SDK lockfile for the window. Slice the regression by intent: a model change hits all intents roughly uniformly; a data change hits one domain. Test the identical golden set against a pinned older model version — scores recover → the model is the variable.

**Fix:** Pin model versions explicitly — a dated model snapshot — for production graphs. Treat provider "latest" as an uncontrolled variable, because that's exactly what it is. If the new model is genuinely better on most slices, adapt: update prompts and examples for the regressed slice and re-gate. If worse, stay pinned and file a provider report. Data change → refresh the KB indexes and add the new product's examples. Dependency → bisect the SDK version, lock it, and add a "golden trace" test that replays recorded model responses through your processing code.

**Prevention:** Pin model versions. Nightly evals against production-model pins. Record model version in every trace so "what changed?" is answerable in the trace itself. Accept the uncomfortable truth: your provider is another engineering team whose releases you must gate, exactly like an internal dependency.

**Field note:** The regression was 3 points on groundedness, discovered Tuesday, traced to a Monday model update announced in a changelog nobody subscribed to. The provider's "same name, new snapshot" policy made the traces identical except for one field: the model version hash, which the team had been logging since the last incident of this kind. Pinning took an hour; adapting the prompt took a week and recovered the 3 points.

## Runbook 15 — Injection Attempt Detected

**Symptom:** The registry alert fires: a tool call rejected for a non-existent tool name, or a permission denial hit a tool the agent never legitimately calls (`admin_refund_tool`, `dump_all_customers`). Or a sandbox logged an egress denial. Or a user reports the agent "told me its system prompt." Any one in isolation is routine noise. A pattern is an attack. The security incident runbook you hope never to need.

**Likely causes (ranked):**
1. A genuine prompt-injection attempt (~60%) — a pasted jailbreak, or a Tier-3 email carrying one. The expected vector.
2. An automated probe (~20%) — someone scanning your public agent for vulnerabilities; the agent equivalent of port scanning. Happens to anyone with a public endpoint.
3. Benign trigger (~15%) — a support ticket quotes an internal doc mentioning a real tool name; the model tries it innocently.
4. A bug (~5%) — the registry has a stale entry and the "hallucinated" tool actually existed last week.

**Investigation:** Pull the trace and read the content that preceded the rejected call — the injection text, if present, is right there. Quote it in the incident channel (redacted). Check the content's tier: ticket body, email, web page? Tier-3 content is the expected vector. Correlate: how many threads, tenants, what time pattern? One thread = probe or benign trigger; many threads = something automated. Confirm the defense held: did the registry reject? Did the sandbox deny? Did any WRITE or MONEY tool get called in those threads? The incident is only an incident if a boundary failed. If a boundary failed, treat it as a full security incident: what data could the injected model access, what did it return, and to whom?

**Fix:** Immediately: if a boundary failed, kill-switch the involved tool and tenant while you assess. Harden the boundary that failed — registry allowlists, Tier-3 sanitization, egress policy — concretely and specifically. Add the injected text (redacted) to the adversarial eval set so the regression suite remembers it. If it was a probe, add the pattern to your WAF or input filter — probes repeat. Notify the affected tenant if their data was involved; injection incidents are data-incident-shaped.

**Prevention:** The tier-based trust model, the registry as the security boundary, the adversarial eval suite, and the alert on every rejected-for-nonexistent-name call. The defense-in-depth story you can tell an auditor: content is classified, boundaries are enforced in code, attempts are alerted and logged. The one defense you cannot skip is the registry — everything else is input hygiene around it.

**Field note:** A support agent's public form was probed with a pasted jailbreak instructing it to call `admin_read_all_tickets` and return "everything about user 4412." The registry rejected the call (no such tool — the attacker guessed the name), the alert fired, and the incident was a log entry, not a breach. The retro conclusion: the system survived by having no such tool. The deeper question — what real tools could an injected agent reach? — produced the read-only credential scoping and the adversarial eval cases that had been on the backlog for two quarters. Attackers are free security consultants; listen to them.

## Runbook 16 — The HITL Queue Overflow

**Symptom:** The approval queue grows day over day. Interrupt-to-resume latency climbs from hours to days. Customers complain "my refund has been pending for a week." Everything in the graph is healthy — traces clean, zero errors, SLOs green — and the product is failing anyway, because the slowest component is a person. A capacity problem, not a code problem, and the hardest to see on a dashboard.

**Likely causes (ranked):**
1. Volume growth without review-capacity growth (~45%) — the 8% of tickets needing approval became 8% of 3x more tickets; the reviewer count didn't move.
2. Interrupt design inflation (~25%) — every minor action interrupts ("please approve this $3 goodwill refund"); reviewers drown in noise and starve the important cases.
3. Reviewer-side friction (~20%) — a slow, multi-step, unbatched approval UI caps a reviewer at roughly 20 approvals per hour regardless of willingness.
4. No deadline policy (~10%) — nothing auto-denies or escalates, so the queue only ever grows.

**Investigation:** Plot the queue: depth over time, inflow versus outflow per day — the gap between the lines is your capacity deficit in reviews per day. Slice the queue by interrupt type and amount — the long tail of small approvals quantifies cause 2. Time a reviewer: how many approvals per minute does the UI actually allow? That's where cause 3 hides. Check the age distribution: growing p90 age → the deadline/escalation policy is missing or not firing.

**Fix:** Re-tune the gate: raise the auto-approve threshold, or add a "policy-approved" tier for small, well-evidenced refunds — the shadow-review measurement tells you what's safe to automate. Batch the review UX: one queue, keyboard navigation, review sessions instead of individual pings. Add the deadline policy: auto-deny money items after 24 hours with escalation, and tell customers the SLA up front. If genuinely capacity-bound: hire, or route overflow to a second queue with a slower SLA. No amount of engineering fixes a missing person; the honest answer is capacity.

**Prevention:** Track interrupt-to-resume latency as a first-class metric with its own alert. Revisit the interrupt threshold quarterly against shadow-review data. Treat the human queue like any other capacity-limited resource in the capacity plan — the human queue, not the LLM, was the binding constraint.

**Field note:** A platform's approval queue grew for six weeks while every dashboard stayed green, because "interrupt pending" is not an error and "queue depth" was not a chart. The fix was one metric and one honest conversation. The product now shows "estimated review time: 2 business days" at interrupt time — which reduced tickets about pending approvals more than the capacity increase did. Users forgive waiting; they don't forgive not knowing.

---

## Cross-Runbook Prevention Checklist

Strip the sixteen runbooks to their preventions and one checklist emerges. Audit every new graph against it before it sees traffic — the checkboxes are cheap compared to the incidents they prevent.

```
[ ] Loop protection as runtime policy (step cap, no-progress detector, cost cap)         R1, R5
[ ] Tool descriptions with triggers + anti-triggers + examples, rebuilt from registry    R2, R13
[ ] Empty-output and schema-parse assertions in the eval suite                           R3
[ ] Timeouts at tool, node, and run level; alert on p99 approach                         R4
[ ] Daily cost deltas + per-tenant grouping + cache hit-rate monitoring                  R5
[ ] Serialization + checkpoint/resume tests for every node, in CI                        R6
[ ] Streaming contract test behind the real proxy                                        R7
[ ] Reducer tests + fan-out completeness eval (100% of branch results)                   R8
[ ] thread_id end-to-end assertions + typed resume payloads                              R9
[ ] Fallback taxonomy (not exception classes) + monthly provider drill                   R10
[ ] Memory conflict policy, TTLs, tier-gated writes, hygiene eval                        R11
[ ] Sliced eval suite + prompt versioning + canary + one-click rollback                  R12
[ ] Strict schemas + worked examples + registry alerts for unknown tools                 R13
[ ] Model version pins recorded in every trace                                           R14
[ ] Tier-based trust model + registry as security boundary + adversarial eval            R15
[ ] Queue-age metric + interrupt deadline policy + batched review UX                     R16
```
