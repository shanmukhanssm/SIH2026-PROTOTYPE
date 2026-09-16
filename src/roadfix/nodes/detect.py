"""SPOT (stub) — deterministic street+pothole boxes per frame; real YOLO eyes land in Phase 1."""

from roadfix.state import Detection, RoadfixState


def _boxes_for_index(frame_id: str, i: int) -> list[Detection]:
    """Deterministic boxes for frame i — same input, same output, always."""
    # Full stub run: 6 frames → 6 cars + 3 persons + 1 bus + 2 potholes = 12 boxes.
    boxes = [
        Detection(
            frame_id=frame_id,
            source="street",
            class_name="car",
            conf=0.81,
            bbox=[40.0, 220.0, 180.0, 340.0],
        )
    ]
    if i % 2 == 1:
        boxes.append(
            Detection(
                frame_id=frame_id,
                source="street",
                class_name="person",
                conf=0.66,
                bbox=[600.0, 300.0, 660.0, 430.0],
            )
        )
    if i == 2:
        boxes.append(
            Detection(
                frame_id=frame_id,
                source="street",
                class_name="bus",
                conf=0.74,
                bbox=[900.0, 140.0, 1150.0, 380.0],
            )
        )
    if i in (0, 1):
        boxes.append(
            Detection(
                frame_id=frame_id,
                source="pothole",
                class_name="pothole",
                conf=0.55,
                bbox=[300.0, 500.0, 420.0, 560.0],
            )
        )
    return boxes


def detect(state: RoadfixState) -> dict[str, object]:
    """SPOT (stub) — deterministic boxes per frame. Returns state UPDATE only."""
    detections: list[Detection] = []
    for i, frame in enumerate(state.frames):
        detections.extend(_boxes_for_index(frame.frame_id, i))
    return {"detections": detections}
