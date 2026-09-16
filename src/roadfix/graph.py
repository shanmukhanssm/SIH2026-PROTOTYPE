"""Root graph assembly — the only file that wires the roadfix graph (per library-docs.md)."""

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from roadfix.config import GATE_PUBLISH, GATE_RECHECK, MAX_VERIFY_ATTEMPTS
from roadfix.nodes.detect import detect
from roadfix.nodes.gate import _latest_by_event, gate
from roadfix.nodes.ingest import ingest
from roadfix.nodes.publish import publish
from roadfix.nodes.track_flag import track_flag
from roadfix.nodes.verify_event import verify_event
from roadfix.state import Event, RoadfixState, VerifyPayload


def route_to_verify(state: RoadfixState) -> list[Send] | str:
    """Fan out one Send per event, or go straight to publish when no events exist."""
    if not state.events:
        return "publish"
    return [
        Send("verify_event", VerifyPayload(event=event, attempt=0).model_dump())
        for event in state.events
    ]


def route_after_gate(state: RoadfixState) -> list[Send] | str:
    """Re-Send events whose latest verdict sits in the recheck band; else publish."""
    events_by_id: dict[str, Event] = {event.event_id: event for event in state.events}
    rechecks: list[Send] = []
    for verdict in _latest_by_event(state.verdicts).values():
        event = events_by_id.get(verdict.event_id)
        if event is None:
            continue  # verdict without a live event — nothing to recheck, skip
        # WHY: recheck band per graph-design gate rules — confirmed and half-sure gets
        # one more look; unconfirmed never rechecks. attempt+1 < MAX_VERIFY_ATTEMPTS
        # bounds the verify/gate cycle (default limit 2 => only first-look verdicts
        # recheck; final passes always end at publish). The recursion_limit backstop
        # is applied per-invoke by the CLI, not here.
        if (
            verdict.confirmed
            and verdict.attempt + 1 < MAX_VERIFY_ATTEMPTS
            and GATE_RECHECK <= verdict.confidence < GATE_PUBLISH
        ):
            rechecks.append(
                Send(
                    "verify_event",
                    VerifyPayload(event=event, attempt=verdict.attempt + 1).model_dump(),
                )
            )
    if not rechecks:
        return "publish"
    return rechecks


def build_graph(
    checkpointer: BaseCheckpointSaver[str] | None = None,  # str: SqliteSaver's version type
) -> CompiledStateGraph[RoadfixState, None, RoadfixState, RoadfixState]:
    """Wire the six-node roadfix graph; compile with the optional checkpointer."""
    # WHY annotated subscript: mypy's inference of StateGraph's Input/Output TypeVar
    # defaults inside a function body mis-solves add_node overloads — pin StateT.
    g: StateGraph[RoadfixState] = StateGraph(RoadfixState)
    g.add_node("ingest", ingest)
    g.add_node("detect", detect)
    g.add_node("track_flag", track_flag)
    # WHY input_schema: verify_event is a Send worker — its input is VerifyPayload,
    # not the graph state (topology table). WHY RunnableLambda: add_node's protocol
    # typing rejects bare callables whose parameter is not named `state`, so the
    # template-exact worker is adapted at the wiring site. langgraph still hands the
    # worker a raw dict at runtime — verify_event validates at its own boundary.
    g.add_node("verify_event", RunnableLambda(verify_event), input_schema=VerifyPayload)
    g.add_node("gate", gate)
    g.add_node("publish", publish)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "detect")
    g.add_edge("detect", "track_flag")
    # WHY: no static edge out of track_flag — route_to_verify is its one routing
    # mechanism (static + conditional from the same node would both fire).
    g.add_conditional_edges("track_flag", route_to_verify)
    # WHY: static fan-in — gate is scheduled only after ALL verify workers finish.
    g.add_edge("verify_event", "gate")
    # WHY: no static edge out of gate — route_after_gate owns recheck-vs-publish.
    g.add_conditional_edges("gate", route_after_gate)
    g.add_edge("publish", END)
    return g.compile(checkpointer=checkpointer)


# WHY: checkpointer=None default keeps module import side-effect free; run_pipeline/e2e
# build their own checkpointered graph. langgraph.json points at roadfix.graph:graph.
graph = build_graph()
