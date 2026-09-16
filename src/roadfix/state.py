"""RoadfixState + all Pydantic models + reducers. Contract: context/graph-design.md.

State Schema section is copied verbatim (with `dict` narrowed to
`dict[str, object]` so the module passes `mypy --strict`, which forbids
implicitly-`Any` generic parameters). Reducer rules live in graph-design.md:
`verdicts` appends via operator.add (parallel verify workers, same super-step);
every other key is last-writer-wins with exactly one writer node.
"""

from operator import add
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FrameRef(BaseModel):
    """One extracted snapshot: file on disk + timestamp (+ GPS when synced)."""

    frame_id: str  # f{index:05d}.jpg — basename of `path` (joins are simple equality)
    path: str  # absolute path to jpg on disk
    t_seconds: float
    lat: float | None = None
    lon: float | None = None


class Detection(BaseModel):
    """One detector box on one frame, from one eye."""

    frame_id: str  # basename of the frame's path — see FrameRef
    source: Literal["street", "pothole"]
    class_name: str  # car, bus, truck, motorcycle, bicycle, person, pothole, crack...
    conf: float = Field(ge=0.0, le=1.0)
    bbox: list[float]  # xyxy pixels
    track_id: int | None = None  # filled by tracker for street classes


class Event(BaseModel):
    """One flagged moment from the rule engine, with its evidence."""

    event_id: str  # {run_id}-{kind}-{frame_id}-{n}
    kind: Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]
    frame_id: str  # anchor frame — basename of the frame's path, see FrameRef
    t_seconds: float
    lat: float | None = None
    lon: float | None = None
    snapshot_path: str  # full-frame jpg; crop path derived
    bbox: list[float] | None = None
    track_id: int | None = None
    evidence: dict[str, object]  # consecutive_count / dwell_s+looks_small / unique_vehicles


class Verdict(BaseModel):
    """One VLM inspection verdict for one event."""

    event_id: str
    kind: str
    attempt: int  # 0 = first look, 1 = recheck — bounds the verify/gate cycle
    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    verifier: str  # model name or "stub" or "error:<code>"


class GateDecision(BaseModel):
    """One deterministic gate outcome for one event."""

    event_id: str
    action: Literal["publish", "recheck", "drop"]
    rule: str  # threshold rule that fired


class PublishedEvent(BaseModel):
    """An accepted event paired with the verdict that let it through."""

    event: Event
    verdict: Verdict


class RoadfixState(BaseModel):
    """The one graph state. Bulk data (frames, snapshots) stays on disk — paths only."""

    # inputs
    video_path: str
    gps_track_path: str | None = None
    run_id: str
    # WATCH — single writer: ingest (overwrite)
    frames: list[FrameRef] = []
    # SPOT — single writer: detect (overwrite)
    detections: list[Detection] = []
    # TRACK & FLAG — single writer: track_flag (overwrite)
    events: list[Event] = []
    track_summary: dict[str, object] = {}
    # DOUBLE-CHECK — many parallel workers; REDUCER REQUIRED
    verdicts: Annotated[list[Verdict], add] = []
    # gate — single writer: gate (overwrite, recomputed per pass)
    gate_decisions: list[GateDecision] = []
    # SHOW — single writer: publish (overwrite)
    published: list[PublishedEvent] = []
    dropped_count: int = 0
    map_path: str | None = None
    report_path: str | None = None
    status: Literal["running", "done", "failed"] = "running"


class VerifyPayload(BaseModel):
    """Worker-scoped Send payload — workers never see the full graph state."""

    event: Event
    attempt: int
