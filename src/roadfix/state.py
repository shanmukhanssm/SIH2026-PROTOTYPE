"""RoadfixState and state models — the contract per graph-design.md; reducers per Reducer Rules."""

from operator import add
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FrameRef(BaseModel):
    """One extracted snapshot — path on disk, timestamp, optional GPS."""

    frame_id: str  # f{index:05d}
    path: str  # absolute path to jpg on disk
    t_seconds: float
    lat: float | None = None
    lon: float | None = None


class Detection(BaseModel):
    """One box from one eye on one frame."""

    frame_id: str
    source: Literal["street", "pothole"]
    class_name: str  # car, bus, truck, motorcycle, bicycle, person, pothole, crack...
    conf: float = Field(ge=0.0, le=1.0)
    bbox: list[float]  # xyxy pixels
    track_id: int | None = None  # filled by tracker for street classes


class Event(BaseModel):
    """One flagged road issue, anchored to a frame."""

    event_id: str  # {run_id}-{kind}-{frame_id}-{n}
    kind: Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]
    frame_id: str  # anchor frame
    t_seconds: float
    lat: float | None = None
    lon: float | None = None
    snapshot_path: str  # full-frame jpg; crop path derived
    bbox: list[float] | None = None
    track_id: int | None = None
    # WHY dict[str, object] not dict: mypy --strict forbids bare generic params; object is the
    # honest wide type (code-standards: never Any).
    # rule-specific: consecutive_count / dwell_s+looks_small / unique_vehicles
    evidence: dict[str, object]


class Verdict(BaseModel):
    """One inspector decision for one event — VLM or stub."""

    event_id: str
    kind: str
    attempt: int  # 0 = first look, 1 = recheck — bounds the verify/gate cycle
    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    verifier: str  # "qwen2.5-vl-..." or "stub"


class GateDecision(BaseModel):
    """Gate ruling for one event — publish, recheck, or drop."""

    event_id: str
    action: Literal["publish", "recheck", "drop"]
    rule: str  # threshold rule that fired


class PublishedEvent(BaseModel):
    """Event plus its accepted verdict, as handed to publish."""

    event: Event
    verdict: Verdict


class RoadfixState(BaseModel):
    """Full graph state — every key has exactly one writer except verdicts (reduced with add)."""

    # inputs
    video_path: str
    gps_track_path: str | None = None
    run_id: str
    # WATCH
    frames: list[FrameRef] = []  # single writer: ingest — overwrite
    # SPOT
    detections: list[Detection] = []  # single writer: detect — overwrite
    # TRACK & FLAG
    events: list[Event] = []  # single writer: track_flag — overwrite
    # WHY dict[str, object] not dict: mypy --strict forbids bare generic params; object is the
    # honest wide type (code-standards: never Any).
    track_summary: dict[str, object] = {}  # single writer: track_flag — overwrite
    # DOUBLE-CHECK (fan-out)
    # REDUCER REQUIRED — parallel workers, same super-step
    verdicts: Annotated[list[Verdict], add] = []
    # single writer: gate — overwrite (recomputed per pass)
    gate_decisions: list[GateDecision] = []
    # SHOW
    published: list[PublishedEvent] = []  # single writer: publish — overwrite
    dropped_count: int = 0
    map_path: str | None = None
    report_path: str | None = None
    status: Literal["running", "done", "failed"] = "running"


class VerifyPayload(BaseModel):
    """Worker-scoped Send payload — workers never see the full graph state."""

    event: Event
    attempt: int
