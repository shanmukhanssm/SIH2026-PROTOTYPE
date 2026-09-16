# Runnable Templates — Supervisor, Handoff, Hierarchical, Network, Fan-Out, Event-Driven

**Load this when:** writing the actual graph code — runnable Python templates with `{{PLACEHOLDER}}` markers for required customization and `[optional]` comments for optional parts.

## Conventions Used by Every Template

```python
# Standard imports used throughout these templates:
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, Send
```

- `{{LIKE_THIS}}` marks a required customization point (a string to replace or a body to implement).
- `[optional]` marks a part you can delete.
- `next` and `active_agent` are **single-writer** channels: only the routing function or the transferring agent writes them — same-step double writes silently lose one value under the default overwrite reducer.

---

## Template 1 — Supervisor Graph with Workers

The default production topology. Loop control: FINISH contract + done-tracker + max rounds.

```python
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


# ---- 1. State -----------------------------------------------------------
class TeamState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]   # shared transcript
    next: str                                              # single-writer: supervisor only
    done: Annotated[list[str], lambda a, b: a + b]         # append-only done-tracker


# ---- 2. Workers (plain nodes; results flow back through the supervisor) --
def researcher(state: TeamState) -> dict:
    facts = "{{RESEARCH_FINDINGS}}"                        # replace with the real worker body
    return {"messages": [AIMessage(content=facts, name="researcher")],
            "done": ["researched: facts gathered"]}        # write the done-tracker


def writer(state: TeamState) -> dict:
    draft = "{{DRAFT}}"                                    # replace with the real worker body
    return {"messages": [AIMessage(content=draft, name="writer")],
            "done": ["drafted: v1"]}


def reviewer(state: TeamState) -> dict:
    verdict = "{{REVIEW_VERDICT}}"                         # must produce a checkable signal
    return {"messages": [AIMessage(content=verdict, name="reviewer")],
            "done": ["reviewed: approved"]}                # FINISH precondition reads this


# ---- 3. Supervisor: cheap model + registry prompt + evidence block ------
SUPERVISOR_PROMPT = """You are the coordinator of a report-production team. You do not
do the work; you decide who does it next and when the work is finished.

Workers (route to EXACTLY one name, or FINISH):
  - researcher: gathers sources and facts. Use when facts are missing or
    unverified. NOT for writing prose.
  - writer: drafts and revises the report from verified facts. Use when the
    report needs drafting or revision.
  - reviewer: checks the draft against the requirements list. Use after
    every draft. NOT before the first draft exists.

Reply format: exactly one token from {researcher, writer, reviewer, FINISH}.
FINISH only when: the draft exists AND reviewer returned "approved".

Evidence you can see each round (assembled by the system, do not invent it):
{evidence}
"""

ROUTES = ("researcher", "writer", "reviewer", "FINISH")


def assemble_evidence(state: TeamState) -> str:
    """System-assembled progress evidence. The prompt forbids the model from
    inventing this — a supervisor that hallucinates it re-dispatches forever."""
    last = state["messages"][-1] if state["messages"] else None
    last_line = f"{last.name} -> {last.content[:80]}" if last else "none"
    return f"goal: {{ORIGINAL_TASK}}\ndone: {state['done']}\nlast: {last_line}"


class RouterStub:
    """[optional] Deterministic stand-in so the graph runs without an API key.
    Swap for a real Haiku-class chat model client in production."""

    def invoke(self, prompt: str) -> str:
        done = "reviewed: approved"
        return "FINISH" if done in prompt else "researcher"


router_llm = RouterStub()


def supervisor(state: TeamState) -> dict:
    prompt = SUPERVISOR_PROMPT.format(evidence=assemble_evidence(state))
    reply = router_llm.invoke(prompt)                      # cheap model, one token back
    route = reply if isinstance(reply, str) else reply.content
    route = route.strip()
    if route not in ROUTES:                                # parse + fallback route
        route = "FINISH"                                   # [optional] pick your safest default
    return {"next": route}


def route_after_supervisor(state: TeamState) -> Literal["researcher", "writer", "reviewer", "synthesize"]:
    if state["next"] == "FINISH":
        return "synthesize"                                # supervisor synthesizes on FINISH
    return state["next"]                                   # worker names are node names


# ---- 4. Synthesis: the step everyone forgets ----------------------------
def synthesize(state: TeamState) -> dict:
    return {"messages": [AIMessage(content="{{FINAL_DELIVERABLE}}", name="supervisor")]}


def build():
    g = StateGraph(TeamState)
    g.add_node("supervisor", supervisor)
    g.add_node("researcher", researcher)
    g.add_node("writer", writer)
    g.add_node("reviewer", reviewer)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "supervisor")
    # conditional edge ONLY — no static edges from the supervisor, because
    # they would run in addition to the chosen route on every round
    g.add_conditional_edges("supervisor", route_after_supervisor,
                            ["researcher", "writer", "reviewer", "synthesize"])
    for worker in ("researcher", "writer", "reviewer"):
        g.add_edge(worker, "supervisor")                   # results flow back through the supervisor
    g.add_edge("synthesize", END)
    return g.compile()


graph = build()
result = graph.invoke(
    {"messages": [HumanMessage(content="{{ORIGINAL_TASK}}")], "next": "", "done": []}
)
print(result["messages"][-1].content)
```

[optional] Add a max-rounds cap: track `rounds` in state; on breach, route straight to `synthesize` (best-effort answer from whatever exists).

---

## Template 2 — Handoff Conversation (Deterministic Routing on active_agent)

Multi-turn support with domain specialists. The transferring agent writes the required tool-call pair; the graph routes on `active_agent`. Hop caps + no-immediate-return stop ping-pong.

```python
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

MAX_HOPS = 4   # after N handoffs nobody addresses the user anymore — force a reply


class ConvState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    active_agent: str    # single-writer: the transferring agent only
    hop_count: int       # the ping-pong brake


# Briefing rules (the ToolMessage content is the receiver's ONLY context):
#   task: what the receiver must answer/produce (the open question)
#   facts: what was already learned (artifacts by reference: IDs, not content)
#   constraints: tone, deadline, policy limits the receiver must respect
# Never pass the sender's whole transcript — the receiver drowns in irrelevant
# reasoning and pays tokens for it (T28: an agenda beats a summary).


def support_agent(state: ConvState) -> dict:
    # ai = support_llm.invoke(state["messages"])  # tools: order_lookup, transfer_to_billing
    ai = AIMessage(                                # demo path: forces a transfer
        content="",
        tool_calls=[{"name": "transfer_to_billing",
                     "args": {"briefing": "{{BRIEFING}}"},
                     "id": "{{TOOL_CALL_ID}}"}],
    )
    if ai.tool_calls and ai.tool_calls[0]["name"].startswith("transfer_to_"):
        call = ai.tool_calls[0]
        target = call["name"].replace("transfer_to_", "") + "_agent"
        # Handoff payload contract: the AIMessage carrying the transfer tool call
        # MUST be paired with a synthetic ToolMessage of the SAME tool_call_id.
        # Malformed conversation history otherwise — verified docs requirement.
        return {
            "messages": [
                ai,
                ToolMessage(content=f"Handed to {target}. {call['args']['briefing']}",
                            tool_call_id=call["id"]),
            ],
            "active_agent": target,                # the routing signal
            "hop_count": state["hop_count"] + 1,
        }
    return {"messages": [ai], "active_agent": ""}  # plain reply ends the conversation


def billing_agent(state: ConvState) -> dict:
    # Receiver sees: user message + transfer pair + briefing — no dangling calls.
    ai = AIMessage(content="{{REFUND_ANSWER}}")    # real body: refund_policy_check(...)
    return {"messages": [ai], "active_agent": ""}  # last agent speaks to the user, one hop


def wrapup(state: ConvState) -> dict:
    """Hop cap breached: force a user-facing best-effort reply instead of
    another transfer."""
    return {"messages": [AIMessage(content="{{BEST_EFFORT_REPLY}}")], "active_agent": ""}


def route_on_active_agent(state: ConvState):
    target = state["active_agent"]
    if not target:
        return END
    if state["hop_count"] >= MAX_HOPS:             # hop budget exhausted
        return "wrapup"
    return target


def build():
    g = StateGraph(ConvState)
    g.add_node("support_agent", support_agent)
    g.add_node("billing_agent", billing_agent)
    g.add_node("wrapup", wrapup)
    g.add_edge(START, "support_agent")
    g.add_conditional_edges("support_agent", route_on_active_agent,
                            ["billing_agent", "wrapup"])
    g.add_conditional_edges("billing_agent", route_on_active_agent,
                            ["support_agent", "wrapup"])
    # No static edges between agents: agents never talk to each other directly.
    g.add_edge("wrapup", END)
    return g.compile()


graph = build()
result = graph.invoke({"messages": [HumanMessage(content="I want a refund for order 88213")],
                       "active_agent": "", "hop_count": 0})
```

[optional] Production hardening: a `no_immediate_return` check in `route_on_active_agent` (track `last_transfer_target` in state; A transferring to B must not be immediately transferred back to A). For model-decided transfers, define the transfer as a tool whose description encodes the policy ("transfer only when the user asks about billing").

---

## Template 3 — Handoff-as-Tool with Command.PARENT (Book's Exact Payload Contract)

Use when agents live inside a team subgraph and control must move to a sibling node in the **parent** graph.

```python
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command


def transfer_to_sales(last_ai_message: AIMessage, tool_call_id: str) -> Command:
    """Transfer tool called from inside a subgraph agent.

    The update writes the required tool-call pair plus the routing signal.
    Do NOT pass the sender's entire internal history: the receiver gets
    confused by irrelevant reasoning and pays tokens for it. If the receiver
    needs context, put a summary in the ToolMessage content.
    """
    return Command(
        update={
            "messages": [
                last_ai_message,   # the AIMessage with this tool call
                ToolMessage(content="Handed to sales. Customer wants a 2-year plan quote.",
                            tool_call_id=tool_call_id),
            ],
            "active_agent": "sales_agent",   # the routing signal
        },
        goto="sales_agent",
        graph=Command.PARENT,   # navigate the PARENT graph to the sibling node
    )
```

Two verified sharp edges:
- Dynamic edges from Command coexist with static edges (both run) — do not mix them on one node.
- When a subgraph node updates a key shared with the parent, the parent state key needs a reducer defined.

---

## Template 4 — Hierarchical Team (Coordinator + Team Leads + Specialists)

Depth 2 is the practical ceiling for interactive systems. Shows both parent-child state mapping patterns.

```python
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class TeamState(TypedDict):
    """Team-private state: conclusions go up, process stays here."""
    messages: Annotated[list[BaseMessage], add_messages]
    next: str
    done: Annotated[list[str], lambda a, b: a + b]


# ---- Research team (built like Template 1, compressed) ------------------
def web_researcher(state: TeamState) -> dict:
    return {"messages": [AIMessage(content="{{WEB_FINDINGS}}", name="web")],
            "done": ["web: done"]}


def docs_researcher(state: TeamState) -> dict:
    return {"messages": [AIMessage(content="{{DOCS_FINDINGS}}", name="docs")],
            "done": ["docs: done"]}


def research_lead(state: TeamState) -> dict:
    # real body: cheap-model routing prompt (Template 1)
    nxt = "docs_researcher" if "docs: done" not in state["done"] else "FINISH"
    return {"next": nxt}


def research_route(state: TeamState) -> Literal["web_researcher", "docs_researcher", "__end__"]:
    return END if state["next"] == "FINISH" else state["next"]


t = StateGraph(TeamState)
t.add_node("lead", research_lead)
t.add_node("web_researcher", web_researcher)
t.add_node("docs_researcher", docs_researcher)
t.add_edge(START, "lead")
t.add_conditional_edges("lead", research_route, ["web_researcher", "docs_researcher"])
t.add_edge("web_researcher", "lead")
t.add_edge("docs_researcher", "lead")
research_graph = t.compile()


# ---- Parent graph --------------------------------------------------------
class ParentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str
    done: Annotated[list[str], lambda a, b: a + b]
    topic: str
    findings_ref: str          # conclusions upward, not process
    team_status: str


def call_research_team(state: ParentState) -> dict:
    """Schema-transform wrapper: control exactly what crosses the boundary.
    (Pattern A alternative: add_node("research_team", research_graph) directly —
    simpler, but every team key becomes a parent key: state sprawl.)"""
    team_input = {"messages": [AIMessage(content=state["topic"])], "next": "", "done": []}
    team_output = research_graph.invoke(team_input)
    summary = team_output["messages"][-1]        # the team lead's conclusion
    return {"findings_ref": summary.content,     # [optional] store a ref, not the content
            "team_status": "complete",
            "done": ["research: complete"]}


def coordinator(state: ParentState) -> dict:
    if "research: complete" in state["done"]:
        return {"next": "FINISH"}
    return {"next": "research_team"}


def coordinator_route(state: ParentState) -> Literal["research_team", "__end__"]:
    return END if state["next"] == "FINISH" else state["next"]


g = StateGraph(ParentState)
g.add_node("coordinator", coordinator)
g.add_node("research_team", call_research_team)
g.add_edge(START, "coordinator")
g.add_conditional_edges("coordinator", coordinator_route, ["research_team"])
g.add_edge("research_team", "coordinator")
parent_graph = g.compile()
```

Keep team-private state private: if every team writes its working notes into the parent's messages, the coordinator's context becomes every team's rough drafts and top-level routing quality collapses within a few rounds.

---

## Template 5 — Network / Shared Scratchpad

2-4 agents, short tasks, total visibility. Coordination is protocol, not authority: route on an explicit `next` field, never on last-message-author.

```python
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class NetworkState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str   # single-writer: only the routing function or the token holder


def researcher(state: NetworkState) -> dict:
    return {"messages": [AIMessage(content="{{FINDINGS}}")], "next": "writer"}


def writer(state: NetworkState) -> dict:
    return {"messages": [AIMessage(content="{{DRAFT}}")], "next": "critic"}


def critic(state: NetworkState) -> dict:
    if "{{ACCEPTS_DRAFT}}" == "yes":                  # real acceptance check here
        return {"next": "FINISH"}
    return {"messages": [AIMessage(content="{{FEEDBACK}}")], "next": "writer"}  # loop back


def route_on_next(state: NetworkState):
    return END if state["next"] == "FINISH" else state["next"]


g = StateGraph(NetworkState)
g.add_node("researcher", researcher)
g.add_node("writer", writer)
g.add_node("critic", critic)
g.add_edge(START, "researcher")
g.add_conditional_edges("researcher", route_on_next, ["writer"])
g.add_conditional_edges("writer", route_on_next, ["critic"])
g.add_conditional_edges("critic", route_on_next, ["writer"])
network_graph = g.compile()
```

[optional] Scratchpad variant for longer runs: replace raw `messages` with a structured scratchpad — `{"facts": [...], "hypotheses": [...], "open_questions": [...], "next": ...}` with append-only, merge-by-ID reducers — and reserve the transcript for user-facing text. This is the difference between a network that scales to 10 rounds and one that dies at 5.

---

## Template 6 — Parallel Fan-Out with Send (Map-Reduce)

T30: parallel subagents writing into one shared state is how results get silently dropped. Each subagent writes its own results into one channel guarded by a merge reducer — or run them sequentially.

```python
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send


def merge_by_id(existing: list | None, new: list) -> list:
    """Append-only merge: keep every result even when writers finish out of
    order (task-completion order is non-deterministic)."""
    return (existing or []) + new


class FanOutState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tasks: list[str]
    findings: Annotated[list[dict], merge_by_id]   # per-subagent results merge here


def fan_out(state: FanOutState) -> list[Send]:
    # one Send per task: each worker gets its own state slice, all run in the
    # same super-step; results merge back through the reducer
    return [Send("research_worker", {"task": t}) for t in state["tasks"]]


def research_worker(payload: dict) -> dict:
    result = {"task": payload["task"], "finding": "{{FINDING}}"}
    return {"findings": [result]}


def merge(state: FanOutState) -> dict:
    # Partial-failure rule: handle missing results explicitly — fail the
    # subtask, retry with a cap, or proceed with a flag. Never silently drop.
    found = {f["task"] for f in state["findings"]}
    missing = [t for t in state["tasks"] if t not in found]
    summary = f"merged {len(state['findings'])} findings; missing: {missing}"
    return {"messages": [AIMessage(content=summary)]}


g = StateGraph(FanOutState)
g.add_node("research_worker", research_worker)
g.add_node("merge", merge)
g.add_conditional_edges(START, fan_out, ["research_worker"])
g.add_edge("research_worker", "merge")   # fires once after all fan-out tasks complete
fanout_graph = g.compile()
```

Checkpoint pending-writes apply per Send task, so a mid-superstep crash re-runs only the unfinished workers — completed siblings' results survive resume.

---

## Template 7 — Event-Driven Trigger and Emit

Correlation IDs thread every event and every graph run. Events are side effects; state stays the system of record.

```python
from langchain_core.messages import HumanMessage


# ---- Consumer: a fresh run per event, checkpointed per correlation ID ----
def on_event(event: dict, graph) -> None:
    # idempotency FIRST: events can arrive twice (at-least-once delivery)
    if seen(event["idempotency_key"]):                 # [optional] any durable KV store
        return
    graph.invoke(
        {"messages": [HumanMessage(content=event["payload"]["task"])]},
        config={"configurable": {"thread_id": event["correlation_id"]}},
    )
    mark_done(event["idempotency_key"], ttl_hours=72)  # dedupe window


# ---- Producer: publish inside a node as a side effect --------------------
def publish_doc_ready(state: dict, bus) -> dict:
    bus.publish({
        "type": "evt.doc.ready",
        "correlation_id": state["correlation_id"],     # thread it through everything
        "payload": {"doc_ref": state["doc_ref"]},      # a reference, not the content
        "idempotency_key": f"doc-ready:{state['doc_id']}",   # deterministic key
    })
    return {}   # no state change: the message is a notification of intent
```

Consumers must be idempotent (an event can arrive twice), commands need deadlines, and every drop path needs a counter with an alert — see references/comms-protocols.md.

---

## Verifying the Wiring (before any LLM is involved)

1. `python3 -m py_compile your_graph.py` — syntax gate.
2. Invoke once with placeholder workers/stubs (as in Template 1) — the run must reach `synthesize`/END without touching a real model.
3. Contract test the handoff: assert every AIMessage carrying tool calls is followed by a ToolMessage with the same `tool_call_id`.
4. Contract test the reducers: fire two same-step writes at every multi-writer channel and assert the merge (not the overwrite) result.
5. Failure injection: force ping-pong (remove the done-tracker), force lost context (empty briefing), kill one fan-out worker — assert the recovery path from references/failure-modes.md.
