#!/usr/bin/env python3
"""Direct runner for the RoadFix LangGraph graph (Windows-friendly).

Runs the roadfix graph end-to-end against a dashcam clip. Three modes:
  --stub          : stub detectors + stub VLM (no YOLO weights, no VLM endpoint).
  --real-detect   : real YOLO weights (needs `pip install ultralytics`) but stub VLM.
                    Useful to verify YOLO actually finds objects in your video.
  default (no flag): real YOLO + real VLM endpoint (needs both ultralytics + VLM_* env).

VLM endpoint is configured via --vlm-base-url / --vlm-api-key / --vlm-model CLI args
OR via VLM_BASE_URL / VLM_API_KEY / VLM_MODEL env vars. CLI args win.

Tested against xkiro.com (https://api.xkiro.com/v1) with model
"qwen/qwen3-vl-plus:free" - a free vision-capable Qwen model.

Usage:
    python run_video.py --video C:\\path\\to\\clip.mp4 --stub
    python run_video.py --video C:\\path\\to\\clip.mp4 --real-detect
    python run_video.py --video C:\\path\\to\\clip.mp4 \\
        --vlm-base-url https://api.xkiro.com/v1 \\
        --vlm-api-key sk-xt-ab... \\
        --vlm-model qwen/qwen3-vl-plus:free
"""
from __future__ import annotations
import argparse, os, sys, tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _print_summary(state: dict) -> None:
    print("\n========== ROADFIX RUN SUMMARY ==========")
    print(f"status           : {state.get('status')}")
    print(f"run_id           : {state.get('run_id')}")
    print(f"frames           : {len(state.get('frames', []))}")
    detections = state.get("detections", [])
    print(f"detections       : {len(detections)}")
    if detections:
        comp = Counter(
            (d.get("source"), d.get("class_name")) if isinstance(d, dict)
            else (d.source, d.class_name) for d in detections
        )
        print(f"  composition    : {dict(comp)}")
    events = state.get("events", [])
    print(f"events           : {len(events)}")
    if events:
        print(f"  by kind        : {dict(Counter(e.get('kind') if isinstance(e, dict) else e.kind for e in events))}")
    verdicts = state.get("verdicts", [])
    print(f"verdicts         : {len(verdicts)}")
    if verdicts:
        verifiers = Counter(
            (v.get("verifier") if isinstance(v, dict) else v.verifier) for v in verdicts
        )
        print(f"  by verifier   : {dict(verifiers)}")
    gate = state.get("gate_decisions", [])
    print(f"gate_decisions   : {len(gate)}")
    if gate:
        acts = Counter(
            (d.get("action") if isinstance(d, dict) else d.action) for d in gate
        )
        print(f"  actions       : {dict(acts)}")
    print(f"published         : {len(state.get('published', []))}")
    print(f"dropped_count    : {state.get('dropped_count', 0)}")
    print(f"map_path         : {state.get('map_path')}")
    print(f"report_path      : {state.get('report_path')}")
    print(f"track_summary    : {state.get('track_summary', {})}")
    print("=========================================\n")


def main() -> int:
    p = argparse.ArgumentParser(description="Direct RoadFix graph runner.")
    p.add_argument("--video", required=True, help="Path to a dashcam .mp4 file")
    p.add_argument("--run-id", default="video-demo")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--recursion-limit", type=int, default=None,
                   help="Override RECURSION_LIMIT (default 50).")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--stub", action="store_true",
                      help="Stub BOTH detectors and VLM (no GPU/network).")
    mode.add_argument("--real-detect", action="store_true",
                      help="Real YOLO weights but stub VLM (verify YOLO works).")

    p.add_argument("--vlm-base-url", default=None,
                   help="OpenAI-compatible VLM endpoint. Default: env VLM_BASE_URL")
    p.add_argument("--vlm-api-key", default=None,
                   help="VLM API key. Default: env VLM_API_KEY")
    p.add_argument("--vlm-model", default=None,
                   help="VLM model id. Default: env VLM_MODEL or 'qwen2.5-vl-7b-instruct'")
    p.add_argument("--vlm-timeout", type=float, default=None,
                   help="Per-request timeout (s). Default: 30")
    p.add_argument("--vlm-json-mode", choices=["auto", "off"], default=None,
                   help="Send response_format:json_object. Default: auto")

    # Detection settings — needed to actually find potholes
    p.add_argument("--pothole-weights", default=None,
                   help="Path to a pothole-detection YOLO .pt file. REQUIRED for pothole "
                        "detection — without it the pothole eye is disabled. "
                        "Recommended: keremberke/yolov8n-pothole-segmentation/best.pt "
                        "(single class 'pothole', 6.8 MB) — download from "
                        "https://huggingface.co/keremberke/yolov8n-pothole-segmentation/resolve/main/best.pt")
    p.add_argument("--fps", type=float, default=None,
                   help="Frame-sampling rate (frames per second). Default: 2.5. "
                        "Bump to 5 for urban traffic, 10-15 for highway speeds — "
                        "denser sampling helps the POTHOLE_MIN_CONSECUTIVE rule fire.")
    p.add_argument("--pothole-conf", type=float, default=None,
                   help="Pothole YOLO confidence threshold. Default: 0.30. "
                        "Lower to 0.20-0.25 to catch weaker detections (VLM filters "
                        "false positives downstream).")
    args = p.parse_args()

    if args.stub:
        os.environ["ROADFIX_STUB_MODELS"] = "1"
        print("[run_video] MODE: stub (no YOLO weights, no VLM endpoint)")
    elif args.real_detect:
        os.environ.pop("ROADFIX_STUB_MODELS", None)
        print("[run_video] MODE: real-detect - uses real YOLO weights + real VLM endpoint")
        print("[run_video] NOTE: real-detect requires both ultralytics AND VLM_* settings.")
        print("[run_video]       If you only want to verify YOLO works (no VLM), the")
        print("[run_video]       gate will drop all events with error:no_endpoint - that's expected.")
    else:
        os.environ.pop("ROADFIX_STUB_MODELS", None)
        print("[run_video] MODE: real - real YOLO weights + real VLM endpoint")

    if args.vlm_base_url:
        os.environ["VLM_BASE_URL"] = args.vlm_base_url
    if args.vlm_api_key:
        os.environ["VLM_API_KEY"] = args.vlm_api_key
    if args.vlm_model:
        os.environ["VLM_MODEL"] = args.vlm_model
    if args.vlm_timeout is not None:
        os.environ["VLM_TIMEOUT_S"] = str(args.vlm_timeout)
    if args.vlm_json_mode:
        os.environ["VLM_JSON_MODE"] = args.vlm_json_mode

    if args.pothole_weights:
        os.environ["POTHOLE_MODEL_PATH"] = args.pothole_weights
    if args.fps is not None:
        os.environ["FRAMES_PER_SECOND"] = str(args.fps)
    if args.pothole_conf is not None:
        os.environ["POTHOLE_CONF"] = str(args.pothole_conf)

    if args.data_dir:
        os.environ["ROADFIX_DATA_DIR"] = args.data_dir
    elif "ROADFIX_DATA_DIR" not in os.environ:
        os.environ["ROADFIX_DATA_DIR"] = tempfile.mkdtemp(prefix="roadfix-run-")

    from langgraph.checkpoint.sqlite import SqliteSaver
    from roadfix.config import RECURSION_LIMIT, runs_dir
    from roadfix.graph import build_graph

    video_path = str(Path(args.video).resolve())
    if not Path(video_path).is_file():
        print(f"[run_video] ERROR: video not found at {video_path}")
        return 1

    print(f"[run_video] video         = {video_path}")
    print(f"[run_video] data_dir      = {os.environ['ROADFIX_DATA_DIR']}")
    print(f"[run_video] run_id        = {args.run_id}")
    print(f"[run_video] stub_models   = {os.environ.get('ROADFIX_STUB_MODELS', '(unset -> real mode)')}")
    print(f"[run_video] vlm_base_url  = {os.environ.get('VLM_BASE_URL', '(unset)')}")
    print(f"[run_video] vlm_model     = {os.environ.get('VLM_MODEL', '(default qwen2.5-vl-7b-instruct)')}")
    print(f"[run_video] vlm_timeout_s = {os.environ.get('VLM_TIMEOUT_S', '30 (default)')}")
    print(f"[run_video] vlm_json_mode = {os.environ.get('VLM_JSON_MODE', 'auto (default)')}")
    print(f"[run_video] fps           = {os.environ.get('FRAMES_PER_SECOND', '2.5 (default)')}")
    print(f"[run_video] pothole_conf  = {os.environ.get('POTHOLE_CONF', '0.30 (default)')}")
    print(f"[run_video] pothole_weights={os.environ.get('POTHOLE_MODEL_PATH', '(UNSET — pothole eye DISABLED)')}")

    ckpt = runs_dir() / "checkpoints.sqlite"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    print(f"[run_video] checkpoint db = {ckpt}")

    initial = {"video_path": video_path, "gps_track_path": None, "run_id": args.run_id}

    rlimit = args.recursion_limit or RECURSION_LIMIT
    with SqliteSaver.from_conn_string(str(ckpt)) as saver:
        graph = build_graph(saver)
        cfg = {"configurable": {"thread_id": f"video:{args.run_id}"},
               "recursion_limit": rlimit}
        print("[run_video] invoking graph...")
        final = dict(graph.invoke(initial, cfg))

    _print_summary(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
