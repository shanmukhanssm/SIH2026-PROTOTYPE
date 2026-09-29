"""SPOT — run both YOLO eyes over every frame; merge boxes with `source` per eye.

Consumes tools `yolo_street` + `yolo_pothole` (tool-registry.md); writes ONLY the
`detections` key (writer audit, graph-design.md). Eyes degrade independently:
a missing/unloadable model contributes nothing for that eye while the other still
runs; both absent -> `detections=[]`. Tool results are extended verbatim —
Detection.frame_id is basename(frame.path) (detectors.py), which equals
FrameRef.frame_id, so downstream joins need no path surgery. Never raises past
the node.
"""

import logging

from roadfix.config import STREET_CONF, pothole_conf
from roadfix.state import Detection, RoadfixState
from roadfix.tools.detectors import DetectArgs, yolo_pothole, yolo_street

logger = logging.getLogger(__name__)


def detect(state: RoadfixState) -> dict[str, object]:
    """SPOT — both YOLO eyes per frame, per-eye degradation composes. Returns state UPDATE only."""
    detections: list[Detection] = []
    if not state.frames:
        return {"detections": detections}
    try:
        pothole_conf_val = pothole_conf()
        for frame in state.frames:
            street = yolo_street(DetectArgs(frame_path=frame.path, conf=STREET_CONF))
            pothole = yolo_pothole(DetectArgs(frame_path=frame.path, conf=pothole_conf_val))
            if not street.model_loaded:
                logger.warning(
                    "[detect] street eye degraded on %s: %s", frame.frame_id, street.error
                )
            if not pothole.model_loaded:
                logger.warning(
                    "[detect] pothole eye degraded on %s: %s", frame.frame_id, pothole.error
                )
            detections.extend(street.detections)
            detections.extend(pothole.detections)
    except Exception as exc:  # boundary — return whatever accumulated, never propagate
        logger.warning("[detect] %s: %s", type(exc).__name__, exc)
    return {"detections": detections}
