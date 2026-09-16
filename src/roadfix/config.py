"""Single source of every constant and env-derived setting (context/code-standards.md).

Thresholds are module constants; anything from the environment is read at call
time via the small functions below so tests can monkeypatch env vars freely.
Never redeclare any of these in nodes or tools.
"""

import os

# --- Thresholds (code-standards.md; graph-design.md run limits) ---

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

# --- Model policy (prompt-registry.md — model strings live ONLY here) ---

DEFAULT_VLM_MODEL: str = "qwen2.5-vl-7b-instruct"
DEFAULT_STREET_WEIGHTS: str = "yolov8n.pt"
VLM_TEMPERATURE: float = 0.0
VLM_MAX_TOKENS: int = 200


# --- Env-derived settings (architecture.md env table), read at call time ---


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


def pothole_model_path() -> str | None:
    """User-supplied RDD2022-trained .pt; absent -> pothole eye disabled."""
    return os.environ.get("POTHOLE_MODEL_PATH")


def street_weights() -> str:
    return os.environ.get("YOLO_STREET_WEIGHTS", DEFAULT_STREET_WEIGHTS)
