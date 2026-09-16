# Library Docs

> Generated from Intake for SIH2026-PROTOTYPE. Project-specific usage patterns per library. Read the relevant section before implementing any feature touching that library.

**Authority order for any library question:**

```
MCP server docs (if configured) → skills/ → THIS FILE → official docs → training knowledge
```

Never rely on training knowledge alone — LangGraph APIs especially have shifted repeatedly. Check the pinned version's actual signatures before writing against an API.

---

## LangGraph

**Version policy:** pinned in `pyproject.toml` (currently `langgraph>=0.4`, `langgraph-checkpoint-sqlite`). On any graph-API error, read the installed version's source in site-packages before improvising.

### Graph assembly

```python
# src/roadfix/graph.py — the ONLY root wiring file
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from roadfix.nodes.ingest import ingest
from roadfix.nodes.detect import detect
from roadfix.nodes.track_flag import track_flag
from roadfix.nodes.verify_event import verify_event
from roadfix.nodes.gate import gate
from roadfix.nodes.publish import publish
from roadfix.state import RoadfixState

def build_graph(checkpointer) -> StateGraph:
    g = StateGraph(RoadfixState)
    g.add_node("ingest", ingest)
    g.add_node("detect", detect)
    g.add_node("track_flag", track_flag)
    g.add_node("verify_event", verify_event)
    g.add_node("gate", gate)
    g.add_node("publish", publish)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "detect")
    g.add_edge("detect", "track_flag")
    # NO static/conditional edge out of track_flag besides the router below —
    # route_to_verify is the ONE routing mechanism from track_flag.
    g.add_conditional_edges("track_flag", route_to_verify)   # returns [Send...] or "publish"
    g.add_edge("verify_event", "gate")                       # fan-in ordering guarantee
    g.add_conditional_edges("gate", route_after_gate)        # returns [Send...] or "publish"
    g.add_edge("publish", END)
    return g.compile(checkpointer=checkpointer)
```

Rules: wiring in `build_graph` only; routers are pure functions taking state, returning a list of `Send` or the string `"publish"`; node names match the topology table in graph-design.md exactly; exactly ONE routing mechanism per node (no static edge + router from the same source — both fire).

### Send fan-out / fan-in

```python
from langgraph.types import Send
from roadfix.state import VerifyPayload

def route_to_verify(state: RoadfixState) -> list[Send] | str:
    if not state.events:
        return "publish"
    return [Send("verify_event", VerifyPayload(event=e, attempt=0).model_dump())
            for e in state.events]
```

- Send payload = the worker's input state. `VerifyPayload` is worker-scoped — workers never see graph state.
- Fan-in via the static edge `verify_event → gate`: gate is scheduled in the super-step AFTER all workers complete — the structural guarantee that all verdicts of the pass are merged before gate reads them.
- Every multi-writer key (`verdicts`) carries `operator.add`. Assert `len(verdicts) == len(events)` (invariant, never order) in tests.
- Gate recheck Sends must include `attempt+1`; `route_after_gate` computes pending rechecks from the LATEST verdict per event_id. The cycle is bounded by MAX_VERIFY_ATTEMPTS.

### Checkpointer

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": f"video:{run_id}"},
              "recursion_limit": config.RECURSION_LIMIT}
    graph.invoke(initial_state, config=config)
```

- `thread_id` minted in `scripts/run_pipeline.py` — never inside nodes.
- Resume a killed run: re-invoke the same thread_id with `None` input from the same CLI (`--resume`).
- SqliteSaver is single-writer/single-instance: one run at a time. Enforced by a lockfile in run_pipeline, not by hope.

### Verify-with-Send test invariants

- Race test: N events → after verify super-step `len(state["verdicts"]) == N` in any order.
- Resume test: kill after detect, resume → ingest/detect NOT re-run (frame-file side-effect counter), final published set equals uninterrupted run.
- State-diff test: gate pass writes ONLY `gate_decisions`; publish writes ONLY its five keys.

---

## ultralytics YOLO

```python
# src/roadfix/tools/detectors.py — lazy import, cached per-process
from ultralytics import YOLO

model = YOLO(weights_path)            # cached in module-level dict
results = model.predict(frame_path, conf=args.conf, verbose=False)[0]
for box in results.boxes:
    cls_name = results.names[int(box.cls)]
    xyxy = [float(v) for v in box.xyxy[0].tolist()]
    conf = float(box.conf)
```

- `predict` on a single image path — never `model.track()` (we own tracking via ByteTrack).
- Street weights `yolov8n.pt` auto-download on first use; offline machines must pre-seed the file or the eye degrades (handled).
- COCO class filter applied AFTER predict: keep {0 person, 1 bicycle, 2 car, 3 motorcycle, 5 bus, 7 truck}.
- RDD2022 pothole weights: classes kept verbatim from the weights file — never renamed.

---

## supervision ByteTrack

```python
import supervision as sv
import numpy as np

tracker = sv.ByteTrack()              # fresh tracker per RUN, fed frames in order
sv_dets = sv.Detections(
    xyxy=np.array([...], dtype=float),
    confidence=np.array([...], dtype=float),
    class_id=np.array([...], dtype=int),
)
tracked = tracker.update_with_detections(sv_dets)
# tracked.tracker_id — None where unmatched
```

- One tracker instance across the whole clip's frames, in `t_seconds` order — IDs are only meaningful in sequence.
- Pothole boxes never go through ByteTrack (static objects) — matched across frames by IoU in the rule engine.
- Empty detections frame → feed an empty `sv.Detections.empty()` to keep the tracker timeline aligned.

---

## OpenAI-compatible VLM (Qwen2.5-VL)

```python
# src/roadfix/tools/vlm.py
from openai import OpenAI
import base64

client = OpenAI(base_url=config.VLM_BASE_URL, api_key=config.VLM_API_KEY, timeout=config.VLM_TIMEOUT_S)
b64 = base64.b64encode(open(snapshot_path, "rb").read()).decode()
resp = client.chat.completions.create(
    model=config.VLM_MODEL,
    temperature=0.0,
    max_tokens=200,
    response_format={"type": "json_object"},
    messages=[{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": INSPECTOR_V1.format(kind=..., evidence=...)},
        ],
    }],
)
payload = json.loads(resp.choices[0].message.content)
```

- `response_format={"type": "json_object"}`: vLLM/ollama/OpenRouter all support it; if an endpoint 400s on it, drop the field and rely on the Pydantic validation + corrective retry (one code path, config-gated: `VLM_JSON_MODE=auto|off`).
- Validation loop: Pydantic `InspectorVerdict.model_validate_json` → on failure, ONE corrective re-ask with field-specific errors → still invalid → error verdict. Cap 2 total attempts.
- Timeout 10 s, one retry on transient — then `verifier="error:<code>"` verdict; the gate drops it. Never raise.

---

## opencv-python-headless

```python
import cv2
cap = cv2.VideoCapture(path)
fps = cap.get(cv2.CAP_PROP_FPS)
grab_every = max(1, round(fps / target_fps))
idx = 0
while True:
    ok, frame = cap.read()
    if not ok: break
    if idx % grab_every == 0:
        t = idx / fps
        cv2.imwrite(out, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    idx += 1
cap.release()
```

- Frame extraction by stride, not by setting CAP_PROP_POS — seek-then-read is slow and drifts on VFR dashcam files.
- GPS sync uses the frame's wall-clock `t_seconds` (index/fps), matching the GPS CSV timestamps.
- `cv2.imwrite` returns False silently on bad paths — check the return, mark `ok=False`.

---

## folium

```python
import folium
from folium.plugins import HeatMap

m = folium.Map(location=center, zoom_start=13)
HeatMap([[e.lat, e.lon] for e in events if e.lat], radius=15).add_to(m)
for e in events:
    folium.Marker([e.lat, e.lon], popup=folium.Popup(html, max_width=300)).add_to(m)
m.save(out_path)
```

- HeatMap needs ≥ 1 lat/lon point — guard empty/GPS-less runs (fallback center + warning).
- Popup HTML embeds the snapshot via relative path — dashboard serves run dir as static root.

---

## Streamlit

```python
# ui/dashboard.py
import streamlit as st
st.set_page_config(page_title="RoadFix", layout="wide")
# st_folium or st.components.v1.html for the map; sqlite read via store_query_events
```

- Dashboard is read-only: queries `events.db` + reads run dirs. It never imports roadfix.nodes or triggers graphs.
- Map rendered via `st.components.v1.html(open(map_html).read())` — zero extra deps.

---

## pytest

```python
@pytest.fixture()
def stub_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ROADFIX_STUB_MODELS", "1")
    monkeypatch.setenv("ROADFIX_DATA_DIR", str(tmp_path))
```

- Stub fixtures write real tiny mp4s via cv2 `VideoWriter` (mp4v codec) — extract_frames tests run against genuine video bytes.
- `asyncio_mode` not needed (sync graph path). Marker `e2e` registered in pyproject; e2e excluded from default unit run via `--addopts`-free explicit selection: `pytest tests/tools tests/nodes` vs `pytest -m e2e`.
