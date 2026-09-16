**Load this when:** writing the actual graph file — copy the closest template, rename nodes and keys, fill `{{PLACEHOLDER}}` values, delete what you don't need.

# Runnable graph templates

Conventions:

- All four templates are syntactically valid Python 3.10+ AS-IS: every customization point is a quoted `"{{PLACEHOLDER}}"` string or an `[optional]` comment, so the files parse and lint before you edit them.
- Only LangGraph APIs from the source chapters are used: `StateGraph`, `START`/`END`, `add_node`, `add_edge`, `add_conditional_edges`, `compile`, `Send`, `Command`, `Annotated` reducers, `add_messages`, `get_remaining_steps`, checkpointer classes, `get_state`/`get_state_history`/`update_state`.
- Import paths follow standard LangGraph packaging; adjust to your installed version.
- After scaffolding, run the verification from SKILL.md Step 8 (compile clean, test run, state assertions).

## Template 1 — Minimal ReAct-style tool graph

Loop shape: agent decides between `tools` and finishing; budget guard prevents infinite loops; reducers on the multi-writer `messages` key.

```python
"""Minimal ReAct-style tool loop: agent -> tools -> agent -> respond -> END.

Wire a real chat model and tool registry at the marked points.
Deps: pip install langgraph langchain-core
"""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from langgraph.config import get_remaining_steps
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # append + update-by-ID
    final_answer: str


TOOLS = {}  # {{TOOL_REGISTRY}}: {tool_name: callable(args_dict) -> str}


def build_graph(llm):
    """llm: any chat model whose responses carry .tool_calls (bind your tools
    to the model before passing it in, per your provider's tool-calling API)."""

    def agent_node(state: AgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {"messages": [response]}  # partial update only

    def tools_node(state: AgentState) -> dict:
        last = state["messages"][-1]
        new_messages = []
        for call in last.tool_calls:
            observation = TOOLS[call["name"]](call["args"])
            new_messages.append(
                ToolMessage(content=observation, tool_call_id=call["id"])
            )
        return {"messages": new_messages}

    def respond_node(state: AgentState) -> dict:
        return {"final_answer": state["messages"][-1].content}

    def route_after_agent(state: AgentState) -> str:
        # Budget guard FIRST: never rely on recursion_limit alone, because the
        # default (1000) lets an infinite loop burn hundreds of LLM calls.
        if get_remaining_steps() <= 2:
            return "respond"
        if getattr(state["messages"][-1], "tool_calls", None):
            return "tools"
        return "respond"

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_node("respond", respond_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent", route_after_agent, {"tools": "tools", "respond": "respond"}
    )
    builder.add_edge("tools", "agent")  # the cycle: tools results go back to agent
    builder.add_edge("respond", END)

    return builder.compile(
        checkpointer=None,  # [optional] attach a checkpointer here (Template 4)
    )


# --- usage ---
# graph = build_graph(llm={{YOUR_CHAT_MODEL}})
# config = {"configurable": {"thread_id": "user:42:session:{{SESSION_ID}}"}}
# result = graph.invoke(
#     {"messages": [HumanMessage(content="{{USER_REQUEST}}")]}, config
# )
# print(result["final_answer"])
```

## Template 2 — Map-reduce fan-out graph with Send

Runtime-N parallelism: a conditional edge returns `Send` objects; workers write into a reduced key; a static edge worker-to-aggregator gives the fan-in ordering guarantee.

```python
"""Map-reduce: fan out N Send workers, merge via reducer, aggregate.

N is known only at runtime (state["items"]), so static parallel edges cannot
express it. Runnable as-is with the toy analyze_one; swap in real work.
Deps: pip install langgraph
"""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class MapReduceState(TypedDict):
    items: list[str]
    # REDUCER REQUIRED: every worker writes `results` in the SAME super-step.
    # Without operator.add, the default overwrite silently drops all but the
    # last write (classic vanished-result bug).
    results: Annotated[list[str], operator.add]
    summary: str


class WorkerState(TypedDict):
    """Worker-scoped input: each Send payload becomes the worker's state."""

    item: str


def plan(state: MapReduceState) -> dict:
    # {{PLANNER}}: produce the work list (LLM plan, DB query, file list, ...).
    return {"items": ["alpha", "beta", "gamma"]}


def route_to_workers(state: MapReduceState) -> list[Send]:
    # One Send per independent unit of work; all fire in one super-step.
    return [Send("analyze", {"item": item}) for item in state["items"]]


def analyze(state: WorkerState) -> dict:
    # state here is ONLY the Send payload, not the graph state.
    return {"results": [analyze_one(state["item"])]}


def analyze_one(item: str) -> str:
    # Replace with real per-item work (LLM call, retrieval, ...).
    return f"analysis of {item!r}: {len(item)} chars"


def aggregate(state: MapReduceState) -> dict:
    # Scheduled in the super-step AFTER all workers complete — the structural
    # guarantee that merge sees every worker's write.
    return {"summary": "\n".join(state["results"])}


builder = StateGraph(MapReduceState)
builder.add_node("plan", plan)
builder.add_node("analyze", analyze)
builder.add_node("aggregate", aggregate)

builder.add_edge(START, "plan")
builder.add_conditional_edges("plan", route_to_workers)
builder.add_edge("analyze", "aggregate")  # fan-in: static edge from worker
builder.add_edge("aggregate", END)

graph = builder.compile()

# --- verification (run this; assert the invariant, never the order) ---
# state = graph.invoke({"items": ["alpha", "beta", "gamma"]})
# assert len(state["results"]) == 3
```

Rate limits: N Sends means N simultaneous provider slots — cap concurrency at what the weakest dependency has been load-tested to survive.

## Template 3 — Conditional-routing graph with Command

Route-and-update atomically: `triage` returns `Command(goto=..., update=...)`; NO static or conditional edge is added from `triage` because Command replaces them (adding one makes both fire).

```python
"""Intent triage that routes AND records the decision in one atomic step.

Runnable as-is with the toy classifier; swap in a structured-output call.
Deps: pip install langgraph langchain-core
"""
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

Intent = Literal["billing", "technical", "general"]


class TriageState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: Intent  # single writer (triage): overwrite semantics are fine


def classify_intent(text: str) -> Intent:
    # Replace with: schema = {{INTENT_SCHEMA}}; llm.with_structured_output(schema)
    # Structured output for routing: shorter outputs, fewer iterations, no
    # parse-retry loops. It makes output SHAPED, not correct.
    if "refund" in text:
        return "billing"
    if "error" in text:
        return "technical"
    return "general"


def triage(state: TriageState) -> Command:
    decision = classify_intent(state["messages"][-1].content)
    return Command(goto=decision, update={"intent": decision})


def billing(state: TriageState) -> dict:
    return {"messages": []}  # {{BILLING_FLOW}}


def technical(state: TriageState) -> dict:
    return {"messages": []}  # {{TECHNICAL_FLOW}}


def general(state: TriageState) -> dict:
    return {"messages": []}  # {{GENERAL_FLOW}}


builder = StateGraph(TriageState)
builder.add_node("triage", triage)
builder.add_node("billing", billing)
builder.add_node("technical", technical)
builder.add_node("general", general)

builder.add_edge(START, "triage")
# NO add_edge / add_conditional_edges from "triage": Command(goto=) owns routing.
for intent in ("billing", "technical", "general"):
    builder.add_edge(intent, END)

graph = builder.compile()

# Inside a SUBGRAPH, escape to the parent graph (agent-to-agent handoff):
#   return Command(graph=Command.PARENT, goto="parent_node", update={...})
# Resume context (interrupt pause/resume is hitl-builder's territory):
#   graph.invoke(Command(resume={"approved": True}), config)  # same thread_id
```

## Template 4 — Checkpointer-wired graph with time travel

Production persistence plus the four time-travel operations in one runnable file.

```python
"""Durable graph: PostgresSaver checkpointer, thread_id config, and the four
time-travel operations (inspect, replay, fork, update-live).

Deps: pip install langgraph langgraph-checkpoint-postgres
For local dev swap in SqliteSaver; NEVER deploy InMemorySaver.
"""
import uuid
from typing import Annotated, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph


class ReportState(TypedDict):
    topic: str
    draft: str


def generate_topic(state: ReportState) -> dict:
    # {{TOPIC_GENERATION}}
    return {"topic": state["topic"]}


def write_joke(state: ReportState) -> dict:
    # {{WRITER_FLOW}}
    return {"draft": f"a joke about {state['topic']}"}


builder = StateGraph(ReportState)
builder.add_node("generate_topic", generate_topic)
builder.add_node("write_joke", write_joke)
builder.add_edge(START, "generate_topic")
builder.add_edge("generate_topic", "write_joke")
builder.add_edge("write_joke", END)

checkpointer = PostgresSaver.from_conn_string("{{DATABASE_URL}}")
# checkpointer.setup()  # run once on a fresh database

graph = builder.compile(
    checkpointer=checkpointer,
    # store={{STORE}},                # [optional] cross-thread memory
    # interrupt_before=["write_joke"],  # [optional] DEBUG pause points only
)

config = {"configurable": {"thread_id": f"task:{uuid.uuid4()}"}}
graph.invoke({"topic": "{{FIRST_TOPIC}}"}, config)

# --- 1. INSPECT: latest snapshot, or the whole commit chain (newest first) ---
latest = graph.get_state(config)                 # values / next / metadata
history = list(graph.get_state_history(config))

# --- 2. REPLAY: re-execute from a past checkpoint (re-buys tokens, re-fires
#     interrupts; never replay against tools that send money or emails) ---
past = next(s for s in history if s.next == ("write_joke",))
graph.invoke(None, past.config)

# --- 3. FORK: branch from the past with corrected state; original untouched ---
fork_cfg = graph.update_state(past.config, {"topic": "{{CORRECTED_TOPIC}}"})
graph.invoke(None, fork_cfg)

# --- 4. UPDATE-LIVE: amend the present; as_node decides what runs next ---
graph.update_state(latest.config, {"draft": "{{FIXED_DRAFT}}"}, as_node="write_joke")

# Forensics: metadata.source == "update" flags human/script edits; metadata.writes
# attributes each transition to a node. Read history BEFORE changing code.
```

SqliteSaver alternative for local dev: `from langgraph.checkpoint.sqlite import SqliteSaver` and `SqliteSaver.from_conn_string("{{SQLITE_PATH}}")` — single-writer, single-instance only.
