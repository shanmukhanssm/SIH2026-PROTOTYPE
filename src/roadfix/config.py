"""Central constants and path helpers — single source of truth.

Two access styles, one read-logic per env var:
- call-time readers (below) — canonical; tools and tests use these so env vars can
  be monkeypatched freely after import;
- import-time snapshots (STUB_MODELS, VLM_* ...) — Phase 0 module-level names kept
  for nodes/tests that read config as constants; derived FROM the readers at import.
"""

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
VLM_TIMEOUT_S: float = 30.0
RECURSION_LIMIT: int = 50
DEFAULT_MAP_CENTER: tuple[float, float] = (17.3850, 78.4867)  # Hyderabad

# Model policy (prompt-registry.md — model strings live ONLY here).
DEFAULT_VLM_MODEL: str = "qwen2.5-vl-7b-instruct"
DEFAULT_STREET_WEIGHTS: str = "yolov8n.pt"
VLM_TEMPERATURE: float = 0.0
VLM_MAX_TOKENS: int = 200


# --- Env-derived settings, read at CALL time ---
# WHY functions, not constants: tests monkeypatch env vars after import, so the env
# must be read when the tool runs, not when the module is imported.


def stub_models_enabled() -> bool:
    """`ROADFIX_STUB_MODELS=1` -> deterministic detections/verdicts (tests, no-GPU demos)."""
    return os.environ.get("ROADFIX_STUB_MODELS", "") == "1"


def vlm_base_url() -> str | None:
    """OpenAI-compatible endpoint (vLLM/ollama/OpenRouter/DashScope-compatible)."""
    return os.environ.get("VLM_BASE_URL")


def vlm_api_key() -> str | None:
    return os.environ.get("VLM_API_KEY")


def vlm_model() -> str:
    return os.environ.get("VLM_MODEL", DEFAULT_VLM_MODEL)


def vlm_json_mode() -> str:
    """`auto` | `off` — endpoints that 400 on response_format drop it (library-docs.md)."""
    return os.environ.get("VLM_JSON_MODE", "auto")


def vlm_timeout_s() -> float:
    """Per-request HTTP timeout (seconds).

    Default 30.0 — generous enough for free-tier gateways (e.g. xkiro) that
    run 3-8s per vision request + retries + JSON-mode fallback. Override
    via VLM_TIMEOUT_S env var if you need tighter bounds for production.
    """
    try:
        return float(os.environ.get("VLM_TIMEOUT_S", "30.0"))
    except ValueError:
        return 30.0


def pothole_model_path() -> str | None:
    """User-supplied RDD2022-trained .pt; absent -> pothole eye disabled."""
    return os.environ.get("POTHOLE_MODEL_PATH")


def street_weights() -> str:
    return os.environ.get("YOLO_STREET_WEIGHTS", DEFAULT_STREET_WEIGHTS)


# --- Run artifact paths, read at call time (ROADFIX_DATA_DIR overridable) ---


def data_dir() -> Path:
    return Path(os.getenv("ROADFIX_DATA_DIR", "data"))


def runs_dir() -> Path:
    return data_dir() / "runs"


def checkpoint_db() -> Path:
    return runs_dir() / "checkpoints.sqlite"


def events_db() -> Path:
    return runs_dir() / "events.db"


# --- Import-time env snapshots (Phase 0 module-level names) ---
# WHY still here: nodes/tests may import these as constants; they are derived from the
# call-time readers above so each env var has exactly one read-logic. Call-time readers
# remain the canonical access for tools — snapshots do NOT see post-import env changes.

STUB_MODELS: bool = stub_models_enabled()
VLM_BASE_URL: str | None = vlm_base_url()
VLM_API_KEY: str | None = vlm_api_key()
VLM_MODEL: str = vlm_model()
POTHOLE_MODEL_PATH: str | None = pothole_model_path()
YOLO_STREET_WEIGHTS: str = street_weights()
