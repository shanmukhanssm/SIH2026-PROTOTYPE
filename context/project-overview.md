# Project Overview

> Generated from Intake (AGENTS.md §18) for SIH2026-PROTOTYPE. Architecture pass: `adr-001-architecture.md`.

---

## About the Agent

SIH2026-PROTOTYPE (working name **RoadFix**) is a workflow built on LangGraph for Smart India Hackathon 2026, problem SIH26124: one dashcam on a public bus + ready-made AI models — we never train anything ourselves. The run takes one bus-dashcam video (+ optional GPS log), chops it into snapshots at 2–3 per second, runs two pre-trained YOLO "eyes" over every snapshot (a COCO street model and an RDD2022 pothole model), turns detection boxes into flagged events with tiny if-else rules (POTHOLE, KIDS CROSSING, TRAFFIC JAM), and fans each event out to a Qwen2.5-VL inspector that verifies it in strict JSON. A deterministic confidence gate publishes, re-checks, or drops each verdict, and every accepted event lands in an SQLite store that feeds a Folium heatmap, a Streamlit dashboard, and an auto-generated markdown report. Runs end when the report is written — the system never acts on the road, it only produces the sorted road-work list.

The entire run is checkpointed in SQLite (SqliteSaver), so a crashed or killed run resumes from its last checkpoint instead of starting over.

---

## The Problem It Solves

Manual road-survey needs engineers to drive routes and note defects by hand: slow, expensive, and impossible to scale across a city's bus network. Existing AI submissions either train custom models (we have no dataset budget) or dump raw detector output on a map (hundreds of false alarms per clip — unusable by a municipal team).

RoadFix removes the grunt work but keeps precision honest: cheap detectors watch every frame, and the smarter VLM only sees the 3–8 flagged moments, confirming each against a fixed form before it publishes. The city gets video in and a sorted, verified road-work list out — with a precision/recall table per problem type that is measurable, not a guess.

---

## Trigger Points

```
CLI run              → python scripts/run_pipeline.py --video data/inbox/clip.mp4 [--gps data/inbox/route.csv]
LangGraph Studio     → manual run (graph: roadfix.graph:graph)
Streamlit dashboard  → read-only views over data/runs (map, feed, report) — never triggers runs
```

A run is one video file. Batch runs are just repeated CLI invocations.

---

## One Perfect Run

1. Operator drops `clip.mp4` (+ `route.csv` GPS log) in `data/inbox/` and runs the CLI.
2. `ingest` (WATCH) extracts ~25 snapshots at 2–3 fps, syncs each frame's GPS from the CSV by timestamp, writes them under `data/runs/<run_id>/frames/`.
3. `detect` (SPOT) runs both YOLO eyes per frame: street model boxes cars/buses/bikes/people (COCO), pothole model boxes potholes/cracks (RDD2022).
4. `track_flag` (TRACK & FLAG) runs ByteTrack across frames so the same car counts once, then fires the three rules: same pothole box in 2 consecutive snapshots → POTHOLE; a small person on the road > 2 s → KIDS_CROSSING; too many unique vehicles in 30 s → TRAFFIC_JAM. Each event gets its snapshot + GPS + time.
5. `gate` fans events out — one `verify_event` Send per event (DOUBLE-CHECK). Each worker asks Qwen2.5-VL to read the snapshot and fill the fixed form: `{"confirmed": true, "severity": "medium", "confidence": 0.87}`.
6. `gate` applies the confidence knob: very sure → publish · half sure → one more look (re-Send, once) · not sure → drop.
7. `publish` (SHOW) upserts accepted events into `events.db`, renders `map.html` (Folium heatmap), and writes `report.md` (event table, severity counts, run meta).
8. The municipal team opens the Streamlit dashboard: one page — map, photos, event feed, report link.

---

## Inputs and Outputs

| Direction | Field | Type | Notes |
| --- | --- | --- | --- |
| Input | `video_path` | string (path) | Dashcam video. Required. |
| Input | `gps_track_path` | string (path) | CSV `t_seconds,lat,lon`. Optional — events get `lat/lon=None` without it. |
| Output | `published` | list[PublishedEvent] | Accepted events, also upserted into `events.db` |
| Output | `map_path` | string (path) | Folium heatmap HTML for the run |
| Output | `report_path` | string (path) | Markdown report for the run |
| Output | `run_meta` | object | frame counts, event counts, dropped count, verifier name, thread_id |

---

## Scope: In

- Frame extraction at 2–3 fps with GPS timestamp sync
- Street detection via pre-trained YOLO COCO weights (downloaded, never trained)
- Pothole detection via user-supplied RDD2022-trained YOLO weights (path-configured; degrades to street-only if absent)
- ByteTrack identity tracking over vehicle/pedestrian boxes
- Three deterministic flag rules → events with snapshot + GPS + time
- Send fan-out VLM verification (Qwen2.5-VL via any OpenAI-compatible endpoint) with strict-JSON form
- Deterministic confidence gate with one bounded re-check per event
- SQLite event store (system of record), Folium heatmap HTML, markdown report per run
- Streamlit read-only dashboard: map + feed + report
- SqliteSaver checkpointing — crashed runs resume
- Golden-case eval suite (3 stub-mode cases) with structure graders

## Scope: Out

- Training or fine-tuning any model (the zero-training promise)
- Real-time / streaming processing — batch per clip only
- Multiple concurrent runs (SqliteSaver is single-writer)
- Pothole depth/severity estimation beyond the VLM's severity field
- User accounts, auth, multi-tenancy on the dashboard
- Automatic municipal-system integration (CSV export is a future feature)
- HARDEN stage (reliability hardening, guardrails, red-team) — prototype: no harden stage
- Languages other than English in reports

---

## Human-in-the-Loop

None in v1. The confidence gate is deterministic arithmetic; dropping vs publishing an event is cheap and reversible, so no interrupt point is designed. Recorded as an ADR decision — revisit if publishing ever becomes irreversible (e.g. direct municipal work orders).

---

## Success Criteria

- A 10-second clip runs END→END in under 2 minutes on CPU (stub models: under 15 s)
- Every published event carries a snapshot on disk + timestamp, and GPS when the CSV is provided
- A kill at any node resumes from the last checkpoint with zero re-run of completed nodes
- Zero unverified events in the store — verification failure drops, never publishes
- Gate thresholds live in config; changing them changes publishing behavior without touching models
- The report names counts per event kind and severity — matches the store exactly
- Dashboard loads a run's map, feed, and report with no manual file wrangling
- Hand-label 20–30 clips → precision/recall table per problem type (scaffolding in evals/, labeling is operator work)

---

## Target User

Municipal road-maintenance teams and SIH 2026 judges: a public-works officer opens one page and sees where the worst road spots are, with photo evidence — not a research demo. Secondary user: the student team operating the pipeline from a laptop before a demo.
