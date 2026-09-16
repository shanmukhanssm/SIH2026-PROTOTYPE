# Prompt Registry

> Generated from Intake for SIH2026-PROTOTYPE. LIVING FILE: update in the same commit as any prompt/model change. Format per context-references/prompt-registry.md.

**How to use this file:** node code never contains prompt text or model literals. Prompts live in `src/roadfix/prompts/` as version-pinned constants; this file is the index and the source of model configuration. Changing a prompt = edit the constant → bump version here → note the change reason.

---

## Model Policy

| Role | Model | Temperature | Max tokens | Why |
| --- | --- | --- | --- | --- |
| Event inspector (verify_event) | `VLM_MODEL` env — default `qwen2.5-vl-7b-instruct` via OpenAI-compatible endpoint | 0.0 | 200 | Deterministic form-filling; small output; VLM chosen because the task is "read the photo", not detect (detectors already ran) |
| Street detector | `yolov8n.pt` (COCO, ultralytics) | — | — | Pre-trained, zero training from us; n-scale for CPU laptops |
| Pothole detector | user-supplied RDD2022-trained `.pt` (ultralytics) | — | — | Pre-trained on damaged-road imagery incl. Indian roads |

Model strings appear ONLY in `config.py`, sourced from this table. Changing a model here = same-commit change in config.py. The detectors are not prompts — they are listed here because this is the model-policy table.

---

## `inspector` — v1

| Property | Value |
| --- | --- |
| Node | `verify_event` (Send worker) |
| Prompt text | `src/roadfix/prompts/inspector.py::INSPECTOR_V1` |
| Model / temp / max tokens | VLM_MODEL env / 0.0 / 200 |
| Structured output | `InspectorVerdict {confirmed: bool, severity: "low"\|"medium"\|"high", confidence: float, reason: str}` — JSON object mode + Pydantic validation |
| Consumed state | Send payload: event kind, evidence, snapshot (base64 image), attempt |
| Version history | v1 — initial intake |

```
You are a road-event inspector for a city bus dashcam system.
Cheap detectors flagged this moment; your job is to decide if it is REAL
before it reaches a municipal road-repair list.

You get: one dashcam photo, the event kind, and the detector evidence.

Event kinds:
- POTHOLE: claim of a pothole/crack on the road surface.
- KIDS_CROSSING: claim of a small child standing/crossing on the road.
- TRAFFIC_JAM: claim of abnormally dense, slow traffic.

Rules:
- Judge ONLY what is visible in the photo. The evidence is a hint, not proof.
- confirmed=false if the photo does not clearly show the claimed event.
- severity: low = minor/situational, medium = should be scheduled,
  high = urgent hazard (deep pothole, child in live traffic, gridlock).
- confidence: 0.0-1.0, your honest certainty. Low light, blur, rain, or an
  ambiguous surface marks down the confidence.
- reason: ONE sentence, at most 20 words, saying what you see that decides it.

Answer with ONLY the JSON object:
{"confirmed": true/false, "severity": "low|medium|high", "confidence": 0.0-1.0, "reason": "..."}
```

### Correction message (invalid JSON retry)

```
Your previous answer was not valid JSON matching the schema.
Field problems: {{validation_errors}}
Answer again with ONLY the JSON object:
{"confirmed": true/false, "severity": "low|medium|high", "confidence": 0.0-1.0, "reason": "..."}
```

---

## Rules

- Version bump policy: any token-level change to prompt text = new version (`V2`), new entry row in version history with the change reason. Never edit a version in place.
- Temperature and model changes are registry changes — same-commit updates here and in `config.py`.
- New nodes get prompt entries BEFORE the node is built (registry never lags).
- Grader prompts used only by `evals/` live in `evals/graders/` and are listed here too, marked `evals-only`.
