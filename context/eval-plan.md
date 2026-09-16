# Eval Plan

> Generated from Intake for SIH2026-PROTOTYPE. Format per context-references/eval-plan.md.

---

## Philosophy

Evals are layered to match the build ladder: every layer has its own datasets, graders, and gate. A phase gate = the corresponding layer passing at threshold. Evals run via `python -m evals.run_evals --layer tools|nodes|graph` and are recorded in `progress-tracker.md` on every run.

This is a prototype, so the eval bar is honest-but-small: deterministic graders over stub-mode runs, plus a documented operator procedure for real-model checks (real YOLO weights, real VLM endpoint). The zero-training promise means accuracy claims come from the hand-labeled precision/recall table, not from a benchmark we cannot run on a laptop.

Golden datasets are small and hand-picked — synthetic clips with known planted events, not bulk. Quality over volume; every case must have a knowable right answer.

---

## Layer 1 — Tool Evals (gate for Phase 1)

### `extract_frames`
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/tool_frames.jsonl` — 6 fixtures: 1 s / 2 s / 5 s / 10 s synthetic clips (cv2-written), 1 corrupt file, 1 missing file |
| Assertions | N-second clip at 2.5 fps yields `round(N*2.5)` frames ± 1; timestamps monotonic; corrupt/missing → `ok=False`, zero raises |
| Threshold | 6/6 contract compliance |

### `gps_sync`
| Aspect | Value |
| --- | --- |
| Dataset | unit — 5 cases (no csv, clean csv, malformed csv, gap beyond tolerance, exact match) |
| Assertions | nearest-timestamp match within tolerance; unmatched → `lat/lon=None` with honest reason string |
| Threshold | 5/5 |

### `yolo_street` / `yolo_pothole` (stub mode)
| Aspect | Value |
| --- | --- |
| Dataset | unit — stub frames with planted expectations; class filter, conf threshold, degraded eye (`model_loaded=False` → empty, not raise) |
| Assertions | 6/6 across both eyes |
| Real-model check | operator run: `python -m evals.run_evals --layer tools --real-models` on `data/inbox` sample; recorded, not gated |

### `bytetrack_tracks`
| Aspect | Value |
| --- | --- |
| Dataset | unit — 3-frame synthetic sequence (car crossing frames 1-3, second car appears frame 2, person appears frame 3) |
| Assertions | stable ID for the crossing car; distinct ID for the late car; person tracked; tracker-failure path returns untracked boxes with `tracked_ok=False` |
| Threshold | 4/4 |

### `vlm_inspect` (stub mode)
| Aspect | Value |
| --- | --- |
| Dataset | unit — stub endpoint success, malformed-JSON endpoint, timeout endpoint, stub-mode verdicts |
| Assertions | valid JSON → validated form; malformed → corrective retry → error verdict on second failure; timeout → error verdict; all never raise |
| Threshold | 4/4 |

### `store_upsert_events` / `build_map` / `build_report` / `save_event_snapshot`
| Aspect | Value |
| --- | --- |
| Dataset | unit — per-tool contract cases listed in tool-registry.md |
| Assertions | upsert idempotency (same event_id twice → 1 row); map fallback center on no-GPS; report counts match input; snapshot failure returns ok=False |
| Threshold | 10/10 across the four tools |

---

## Layer 2 — Node Evals (gate for Phase 2)

### `track_flag` rules
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/node_rules.jsonl` — 6 synthetic detection sequences: 2-consecutive pothole (fires), 1-frame pothole (silent), gap-IoU pothole (fires once, deduped), 3 s small person (fires), 1 s person (silent), 15 unique vehicles in 30 s window (fires once) |
| Assertions | each expected event fires exactly once with correct kind, anchor frame, and evidence dict; silent cases emit zero events |
| Threshold | 6/6 |

### verify → gate cycle
| Aspect | Value |
| --- | --- |
| Dataset | `evals/datasets/node_gate.jsonl` — verdict sets covering: publish-first-pass, recheck-then-publish, recheck-then-drop, drop-first-pass, verifier-error, unconfirmed-high-confidence |
| Assertions | actions match the gate rules table in graph-design.md; recheck Sends carry attempt+1; cycle terminates ≤ MAX_VERIFY_ATTEMPTS; verdicts accumulate without duplication in decisions |
| Threshold | 6/6 |

---

## Layer 3 — Graph Evals (gate for Phase 3)

### Golden cases (stub-model mode — deterministic, run in CI)

| ID | Input | Key property that must hold |
| --- | --- | --- |
| G1 | 10 s synthetic clip, planted: 2-frame pothole + 12 unique vehicles | ≥ 1 POTHOLE published, ≥ 1 TRAFFIC_JAM published, every published event has snapshot on disk + timestamp |
| G2 | 10 s synthetic clip, planted: 3 s small person on road | ≥ 1 KIDS_CROSSING published; report table row matches store row exactly |
| G3 | 4 s synthetic clip, nothing planted | zero events, zero published; report renders empty-state honestly; run ends `status="done"` |

### Graders

| Grader | Method | Threshold |
| --- | --- | --- |
| Structure validity | Pydantic validation of all published events + snapshot paths exist on disk | 100% on all 3 |
| Store/report consistency | script: report table row count == store rows for run_id, counts match state | 100% on all 3 |
| Gate honesty | script: zero published events with `confirmed=False` or `confidence < GATE_PUBLISH` (first-pass) in final state | 100% |
| Resume integrity | run G1, kill process after `detect` checkpoint, restart, resume same thread_id | published set identical to uninterrupted run; completed nodes not re-run (side-effect counter) |

### Real-model evals (operator runs, not CI gates)

| Aspect | Value |
| --- | --- |
| Procedure | drop 20–30 hand-labeled real clips in `data/inbox/`, run `python -m evals.run_evals --layer graph --real-models --label-file labels.csv` |
| Output | precision/recall table per problem type (POTHOLE / KIDS_CROSSING / TRAFFIC_JAM), appended to progress-tracker.md |
| Threshold | none enforced at prototype stage — the table itself is the deliverable (honest numbers) |

### Gate mapping

| Ladder phase | Required layer | Threshold |
| --- | --- | --- |
| Phase 0 — skeleton | none (local stub run) | — |
| Phase 1 — tools | Layer 1 | per-tool thresholds above |
| Phase 2 — nodes | Layer 2 | per-node thresholds above |
| Phase 3 — main graph | Layer 3: structure + consistency + gate honesty + resume integrity | per grader |
| Phase 4 — evals | real-model precision/recall pass scaffolded; labeling is operator work | scaffold-only |

---

## Regression Policy

- Any change to a tool → rerun its Layer 1 set + Layer 3 golden cases.
- Any prompt version bump → rerun Layer 2 gate cases + Layer 3 (verdicts affect gate honesty).
- Any state schema or routing change → rerun Layer 2 + Layer 3 full.
- A golden case that regresses blocks push until fixed or the case is explicitly re-scoped with user sign-off (recorded in progress-tracker.md).

---

## Flakiness Rules

- Stub-mode graders are deterministic — a failure is a real regression, never retried away.
- Real-model operator runs are recorded with model version + endpoint + date; borderline rows rerun once, both results recorded.
