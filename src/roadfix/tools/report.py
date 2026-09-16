"""build_report tool — auto-generate the run's markdown report.

Contract: context/tool-registry.md `build_report`. Summary counts, event table
sorted severity high->medium->low then confidence DESC, honest dropped section
and caveats. Never raises.
"""

from __future__ import annotations

import logging
import os
from collections import Counter

from pydantic import BaseModel

from roadfix.state import PublishedEvent

logger = logging.getLogger(__name__)

_SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


class ReportArgs(BaseModel):
    """Args for build_report."""

    run_id: str
    published: list[PublishedEvent]
    dropped_count: int
    track_summary: dict[str, object]  # mirrors state.py — passed through honestly
    out_path: str  # data/runs/<run_id>/report.md


class ReportResult(BaseModel):
    """Path written + ok/error."""

    path: str
    ok: bool
    error: str | None = None


def _summary_lines(args: ReportArgs) -> list[str]:
    """Counts: totals, per-kind, per-severity, dropped, track summary passthrough."""
    kinds = Counter(pe.event.kind for pe in args.published)
    severities = Counter(pe.verdict.severity for pe in args.published)
    lines = [f"- Total published events: {len(args.published)}"]
    if kinds:
        lines.append("- By kind: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
        by_sev = sorted(severities.items(), key=lambda kv: _SEVERITY_RANK[kv[0]])
        lines.append("- By severity: " + ", ".join(f"{s}={n}" for s, n in by_sev))
    lines.append(f"- Dropped events: {args.dropped_count}")
    if (vehicles := args.track_summary.get("unique_vehicles")) is not None:
        lines.append(f"- Unique vehicles tracked: {vehicles}")
    per_class = args.track_summary.get("per_class")
    if isinstance(per_class, dict):
        items = sorted(per_class.items(), key=lambda kv: str(kv[0]))
        lines.append("- Per-class detections: " + ", ".join(f"{k}={v}" for k, v in items))
    return lines


def _event_table(args: ReportArgs) -> str:
    """Markdown table sorted severity high->medium->low, then confidence DESC."""
    ordered = sorted(
        args.published,
        key=lambda pe: (_SEVERITY_RANK[pe.verdict.severity], -pe.verdict.confidence),
    )
    header = "| kind | severity | confidence | t_seconds | gps | photo |"
    sep = "| --- | --- | --- | --- | --- | --- |"
    if not ordered:
        return "\n".join([header, sep, "| — no events cleared verification — |  |  |  |  |  |"])
    base_dir = os.path.dirname(args.out_path)
    rows = []
    for pe in ordered:
        e, v = pe.event, pe.verdict
        gps = f"{e.lat}, {e.lon}" if e.lat is not None and e.lon is not None else "—"
        photo = f"[photo]({os.path.relpath(e.snapshot_path, base_dir)})" if e.snapshot_path else "—"
        rows.append(
            f"| {e.kind} | {v.severity} | {v.confidence:.2f} | {e.t_seconds} | {gps} | {photo} |"
        )
    return "\n".join([header, sep, *rows])


def _caveat_lines(args: ReportArgs) -> list[str]:
    """Honest gaps: GPS-less runs, missing snapshots, verifier names."""
    lines: list[str] = []
    has_gps = any(
        pe.event.lat is not None and pe.event.lon is not None for pe in args.published
    )
    if not has_gps:
        lines.append("- No GPS track — map uses fallback center.")
    missing = sum(1 for pe in args.published if not pe.event.snapshot_path)
    if missing:
        lines.append(f"- {missing} event(s) without snapshot on disk.")
    verifiers = sorted({pe.verdict.verifier for pe in args.published})
    if verifiers:
        lines.append("- Verifier(s) seen in verdicts: " + ", ".join(verifiers))
    return lines


def _render_markdown(args: ReportArgs) -> str:
    """Full report body: title, summary, event table, dropped, caveats."""
    head = f"# RoadFix Run Report — {args.run_id}"
    summary = "\n".join(["## Summary", *_summary_lines(args)])
    events = "\n".join(["## Events", _event_table(args)])
    dropped = "\n".join(
        [
            "## Dropped",
            f"{args.dropped_count} event(s) dropped — these are unverified or low-confidence "
            "detections that failed the gate. Dropped never means published.",
        ]
    )
    caveats = "\n".join(["## Caveats", *_caveat_lines(args)])
    return "\n\n".join([head, summary, events, dropped, caveats]) + "\n"


def build_report(args: ReportArgs) -> ReportResult:
    """SHOW — auto-generate the run's markdown report. Never raises."""
    try:
        markdown = _render_markdown(args)
        os.makedirs(os.path.dirname(args.out_path) or ".", exist_ok=True)
        with open(args.out_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        return ReportResult(path=args.out_path, ok=True)
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning("[build_report] failed for run %s: %s", args.run_id, exc)
        return ReportResult(path="", ok=False, error=f"{type(exc).__name__}: {exc}")
