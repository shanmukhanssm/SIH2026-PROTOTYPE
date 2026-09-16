"""Central constants and path helpers — single source of truth."""

import os
from pathlib import Path

# Detection and rule thresholds (code-standards.md — do not redeclare elsewhere).
FRAMES_PER_SECOND: float = 2.5
STREET_CONF: float = 0.35
POTHOLE_CONF: float = 0.30
POTHOLE_IOU_MATCH: float = 0.4
POTHOLE_MIN_CONSECUTIVE: int = 2
KIDS_DWELL_S: float = 2.0
KIDS_SMALL_FRACTION: float = 0.25
KIDS_ROAD_BAND: tuple[float, float] = (0.55, 1.0)
JAM_WINDOW_S: float = 30.0
JAM_MIN_VEHICLES: int = 12
GATE_PUBLISH: float = 0.7
GATE_RECHECK: float = 0.4
MAX_VERIFY_ATTEMPTS: int = 2
VLM_TIMEOUT_S: float = 10.0
RECURSION_LIMIT: int = 50
DEFAULT_MAP_CENTER: tuple[float, float] = (17.3850, 78.4867)  # Hyderabad

# Environment-derived settings, read once at module import (process-lifetime config).
VLM_BASE_URL: str | None = os.getenv("VLM_BASE_URL")
VLM_API_KEY: str | None = os.getenv("VLM_API_KEY")
VLM_MODEL: str = os.getenv("VLM_MODEL", "qwen2.5-vl-7b-instruct")
POTHOLE_MODEL_PATH: str | None = os.getenv("POTHOLE_MODEL_PATH")
YOLO_STREET_WEIGHTS: str = os.getenv("YOLO_STREET_WEIGHTS", "yolov8n.pt")
STUB_MODELS: bool = os.getenv("ROADFIX_STUB_MODELS", "") == "1"


# WHY functions, not constants: tests monkeypatch ROADFIX_DATA_DIR after import,
# so the env var must be read at call time, not at module import.
def data_dir() -> Path:
    return Path(os.getenv("ROADFIX_DATA_DIR", "data"))


def runs_dir() -> Path:
    return data_dir() / "runs"


def checkpoint_db() -> Path:
    return runs_dir() / "checkpoints.sqlite"


def events_db() -> Path:
    return runs_dir() / "events.db"
