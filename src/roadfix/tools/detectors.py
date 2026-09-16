"""YOLO detector tools (tool-registry.md `yolo_street` / `yolo_pothole`) — never raise."""

import hashlib
import importlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field

from roadfix.config import pothole_model_path, street_weights, stub_models_enabled
from roadfix.state import Detection

logger = logging.getLogger(__name__)

# COCO ids kept by the street eye — verbatim from tool-registry.md `yolo_street`
# (person 0, bicycle 1, car 2, motorcycle 3, bus 5, truck 7). Applied AFTER predict
# (library-docs.md ultralytics section), never inside predict.
STREET_CLASS_IDS: frozenset[int] = frozenset({0, 1, 2, 3, 5, 7})


class DetectArgs(BaseModel):
    """One frame path + the confidence floor for one eye."""

    frame_path: str
    conf: float = Field(0.35, ge=0.0, le=1.0)


class DetectResult(BaseModel):
    """One eye's detections for one frame; model_loaded=False marks a degraded eye."""

    detections: list[Detection]
    model_loaded: bool
    error: str | None = None


_Source = Literal["street", "pothole"]

# ultralytics is a lazy heavy dep that may be absent (tests run without it); these
# protocols describe the only surface this tool touches, so the module stays
# mypy --strict clean whether or not the package is installed. Real ultralytics
# objects enter via cast() in _load_model and are never structurally checked.


class _TensorRow(Protocol):
    """One xyxy row of the (N, 4) tensor with its .tolist() surface."""

    def tolist(self) -> list[float]: ...


class _BoxLike(Protocol):
    """Per-box attributes read by the post-predict loop (library-docs.md pattern)."""

    @property
    def cls(self) -> int: ...

    @property
    def conf(self) -> float: ...

    @property
    def xyxy(self) -> Sequence[_TensorRow]: ...


class _ResultsLike(Protocol):
    """predict()[0] surface: boxes plus the id->name map from the weights file."""

    @property
    def boxes(self) -> Sequence[_BoxLike]: ...

    @property
    def names(self) -> dict[int, str]: ...


class _YoloLike(Protocol):
    """The one ultralytics call this tool makes — predict, never track."""

    def predict(self, source: str, conf: float, verbose: bool) -> Sequence[_ResultsLike]: ...


_CACHE: dict[str, _YoloLike] = {}


def _load_model(weights: str) -> _YoloLike:
    """Cached per-process ultralytics YOLO for `weights`; raises on import/load failure."""
    key = str(Path(weights).resolve())  # cache key: resolved weights path
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # Lazy heavy import — identical sys.modules semantics to `import ultralytics`
    # (so sys.modules["ultralytics"]=None still forces ImportError), but mypy never
    # needs the package to exist on the analysis machine.
    mod = importlib.import_module("ultralytics")
    yolo_ctor = mod.YOLO  # untyped module attr — contained here, cast below
    model = cast(_YoloLike, yolo_ctor(weights))
    _CACHE[key] = model
    return model


def _pothole_weights() -> str | None:
    """POTHOLE_MODEL_PATH when configured AND the .pt exists; else None (eye disabled)."""
    weights = pothole_model_path()
    if weights is None or not Path(weights).is_file():
        return None
    return weights


# --- Stub mode (ROADFIX_STUB_MODELS=1) -----------------------------------------
# Fixed candidate pools with hash-derived pairing: every stub frame straddles conf
# thresholds and carries a non-street class ("cat", COCO id 15), so the shared
# _collect pipeline demonstrably applies class filter + conf cut in stub mode too.
# Hash drives the rotation/permutation and box coords; pool VALUES stay fixed so
# the mix exists for every filename (layer-1 tests stay filename-independent).

_STUB_CANVAS: float = 640.0
_STUB_STREET: tuple[tuple[int, str], ...] = (
    (2, "car"),
    (0, "person"),
    (5, "bus"),
    (1, "bicycle"),
    (3, "motorcycle"),
    (7, "truck"),
    (15, "cat"),
)
_STUB_STREET_CONFS: tuple[float, ...] = (0.93, 0.87, 0.55, 0.52, 0.48, 0.21, 0.80)
_STUB_POTHOLE: tuple[tuple[int, str], ...] = ((0, "pothole"), (1, "crack"), (0, "pothole"))
_STUB_POTHOLE_CONFS: tuple[float, ...] = (0.91, 0.62, 0.27)


class _StubRow:
    """One xyxy row with the tensor's .tolist() surface."""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _StubBox:
    """One candidate box shaped like the per-box attrs the predict loop reads."""

    def __init__(self, cls: int, conf: float, xyxy: list[float]) -> None:
        self.cls = cls
        self.conf = conf
        self.xyxy: list[_StubRow] = [_StubRow(xyxy)]


class _StubResults:
    """predict()[0] stand-in: boxes + names dict, fed through the real _collect."""

    def __init__(self, boxes: list[_StubBox], names: dict[int, str]) -> None:
        self.boxes = boxes
        self.names = names


def _stub_results(frame_path: str, source: _Source) -> _ResultsLike:
    """Deterministic candidate boxes from the frame FILENAME hash — zero file I/O."""
    digest = hashlib.sha256(Path(frame_path).name.encode("utf-8")).digest()
    members, confs = (
        (_STUB_STREET, _STUB_STREET_CONFS)
        if source == "street"
        else (_STUB_POTHOLE, _STUB_POTHOLE_CONFS)
    )
    names = {cls_id: cls_name for cls_id, cls_name in members}
    cls_rot = digest[0] % len(members)
    conf_rot = digest[1] % len(confs)
    rotated = members[cls_rot:] + members[:cls_rot]
    confs = confs[conf_rot:] + confs[:conf_rot]
    boxes: list[_StubBox] = []
    for i, ((cls_id, _cls_name), conf) in enumerate(zip(rotated, confs, strict=True)):
        b = digest[2 + 4 * i : 6 + 4 * i]  # 4 hash bytes -> 4 box coords
        x1 = float(b[0] % 480 + 20)
        y1 = float(b[1] % 480 + 20)
        x2 = min(x1 + float(b[2] % 120) + 20.0, _STUB_CANVAS)
        y2 = min(y1 + float(b[3] % 120) + 20.0, _STUB_CANVAS)
        boxes.append(_StubBox(cls_id, conf, [x1, y1, x2, y2]))
    return _StubResults(boxes, names)


def _collect(
    results: _ResultsLike, source: _Source, frame_id: str, min_conf: float
) -> list[Detection]:
    """Post-predict pipeline shared by real and stub paths: class filter + conf cut."""
    detections: list[Detection] = []
    for box in results.boxes:
        cls_id = int(box.cls)
        if source == "street" and cls_id not in STREET_CLASS_IDS:
            continue  # street eye keeps its COCO ids; pothole classes stay verbatim
        conf = float(box.conf)
        if conf < min_conf:
            continue
        detections.append(
            Detection(
                frame_id=frame_id,
                source=source,
                class_name=results.names[cls_id],
                conf=conf,
                bbox=[float(v) for v in box.xyxy[0].tolist()],
            )
        )
    return detections


def _detect(args: DetectArgs, *, source: _Source) -> DetectResult:
    """One eye over one frame: stub or real weights, degraded result instead of raise."""
    try:
        # basename, not stem — matches extract_frames' FrameRef.frame_id so nodes
        # can join detections to frames without path surgery.
        frame_id = Path(args.frame_path).name
        if stub_models_enabled():
            stub = _stub_results(args.frame_path, source)
            return DetectResult(
                detections=_collect(stub, source, frame_id, args.conf), model_loaded=True
            )
        weights = street_weights() if source == "street" else _pothole_weights()
        if weights is None:  # only reachable for the pothole eye
            logger.info("[yolo_pothole] eye disabled: POTHOLE_MODEL_PATH unset or file missing")
            return DetectResult(
                detections=[],
                model_loaded=False,
                error="pothole eye disabled: POTHOLE_MODEL_PATH unset or weights file missing",
            )
        model = _load_model(weights)
        results = model.predict(args.frame_path, conf=args.conf, verbose=False)[0]
        return DetectResult(
            detections=_collect(results, source, frame_id, args.conf), model_loaded=True
        )
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning("[yolo_%s] degraded: %s: %s", source, type(exc).__name__, exc)
        return DetectResult(
            detections=[], model_loaded=False, error=f"{type(exc).__name__}: {exc}"
        )


def yolo_street(args: DetectArgs) -> DetectResult:
    """SPOT eye #1 — pre-trained COCO YOLO over one frame; boxes vehicles/people. Never raises."""
    return _detect(args, source="street")


def yolo_pothole(args: DetectArgs) -> DetectResult:
    """SPOT eye #2 — RDD2022 YOLO over one frame; pothole/crack classes verbatim. Never raises."""
    return _detect(args, source="pothole")
