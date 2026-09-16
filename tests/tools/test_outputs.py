"""Contract tests for build_map + build_report (tool-registry.md, 6 cases).

Map and report fixtures are built via a local `_pub` helper from the real
Event/Verdict/PublishedEvent state models — no stubbing of the tools.
"""

import os
from typing import Literal

from roadfix.state import Event, PublishedEvent, Verdict
from roadfix.tools.map_builder import MapArgs, _render_map, build_map
from roadfix.tools.report import ReportArgs, build_report

Kind = Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]
Severity = Literal["low", "medium", "high"]


def _pub(
    event_id: str = "run-POTHOLE-f00000-1",
    kind: Kind = "POTHOLE",
    t_seconds: float = 1.0,
    lat: float | None = None,
    lon: float | None = None,
    snapshot_path: str = "",
    severity: Severity = "medium",
    confidence: float = 0.9,
    verifier: str = "stub",
) -> PublishedEvent:
    """One published fixture: Event + the Verdict that let it through."""
    return PublishedEvent(
        event=Event(
            event_id=event_id,
            kind=kind,
            frame_id="f00000",
            t_seconds=t_seconds,
            lat=lat,
            lon=lon,
            snapshot_path=snapshot_path,
            bbox=None,
            track_id=None,
            evidence={"consecutive_count": 2},
        ),
        verdict=Verdict(
            event_id=event_id,
            kind=kind,
            attempt=0,
            confirmed=True,
            severity=severity,
            confidence=confidence,
            reason="ok",
            verifier=verifier,
        ),
    )


def _table_rows(markdown: str) -> list[list[str]]:
    """Parse report event-table data rows (header + separator excluded)."""
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == "kind" or set(cells[0]) <= set("- "):
            continue
        rows.append(cells)
    return rows


# --- build_map ---------------------------------------------------------------


def test_map_with_gps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import folium
    from folium.plugins import HeatMap

    events = [
        _pub(
            event_id="run-POTHOLE-f00000-1",
            t_seconds=1.0,
            lat=17.40,
            lon=78.50,
            snapshot_path=str(tmp_path / "a.jpg"),
            severity="high",
            confidence=0.95,
        ),
        _pub(
            event_id="run-POTHOLE-f00001-2",
            t_seconds=2.0,
            lat=17.42,
            lon=78.52,
            snapshot_path=str(tmp_path / "b.jpg"),
            severity="medium",
            confidence=0.85,
        ),
    ]
    out_path = str(tmp_path / "run" / "map.html")
    result = build_map(MapArgs(events=events, out_path=out_path))

    assert result.ok is True
    assert result.path == out_path
    assert result.event_count == 2
    assert result.used_fallback_center is False
    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        html = f.read()
    assert "folium" in html

    m = _render_map(events, (17.41, 78.51), False)  # introspect the folium object
    children = list(m._children.values())
    assert sum(isinstance(c, folium.Marker) for c in children) == len(events)
    assert any(isinstance(c, HeatMap) for c in children)


def test_map_without_gps_uses_fallback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import folium
    from folium.plugins import HeatMap

    events = [
        _pub(event_id="run-POTHOLE-f00000-1", snapshot_path=str(tmp_path / "a.jpg")),
        _pub(event_id="run-POTHOLE-f00001-2", snapshot_path=str(tmp_path / "b.jpg")),
    ]
    out_path = str(tmp_path / "run" / "map.html")
    result = build_map(MapArgs(events=events, out_path=out_path, fallback_center=(17.0, 78.0)))

    assert result.ok is True
    assert result.used_fallback_center is True
    assert os.path.exists(out_path)

    m = _render_map(events, (17.0, 78.0), True)
    children = list(m._children.values())
    assert not any(isinstance(c, HeatMap) for c in children)
    # only the fallback reference marker — the 2 unlocatable events are never placed
    assert sum(isinstance(c, folium.Marker) for c in children) == 1


def test_map_empty_events(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out_path = str(tmp_path / "run" / "map.html")
    result = build_map(MapArgs(events=[], out_path=out_path))

    assert result.ok is True
    assert result.event_count == 0
    assert os.path.exists(out_path)


# --- build_report ------------------------------------------------------------


def test_report_full(tmp_path) -> None:  # type: ignore[no-untyped-def]
    snap = str(tmp_path / "snaps" / "e1_full.jpg")
    published = [
        _pub(
            event_id="run-POTHOLE-f00000-1",
            kind="POTHOLE",
            t_seconds=10.0,
            lat=17.4,
            lon=78.5,
            snapshot_path=snap,
            severity="high",
            confidence=0.95,
        ),
        _pub(
            event_id="run-KIDS_CROSSING-f00010-1",
            kind="KIDS_CROSSING",
            t_seconds=20.0,
            snapshot_path="",
            severity="medium",
            confidence=0.8,
        ),
        _pub(
            event_id="run-TRAFFIC_JAM-f00020-1",
            kind="TRAFFIC_JAM",
            t_seconds=30.0,
            lat=17.41,
            lon=78.51,
            snapshot_path=snap,
            severity="low",
            confidence=0.75,
        ),
    ]
    out_path = str(tmp_path / "run" / "report.md")
    result = build_report(
        ReportArgs(
            run_id="run-42",
            published=published,
            dropped_count=2,
            track_summary={"unique_vehicles": 7, "per_class": {"car": 12, "bus": 2}},
            out_path=out_path,
        )
    )

    assert result.ok is True
    assert result.path == out_path
    assert result.error is None
    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        markdown = f.read()

    assert "# RoadFix Run Report — run-42" in markdown
    assert "Total published events: 3" in markdown
    assert "POTHOLE=1" in markdown
    assert "KIDS_CROSSING=1" in markdown
    assert "TRAFFIC_JAM=1" in markdown
    assert "high=1" in markdown and "medium=1" in markdown and "low=1" in markdown
    assert "Dropped events: 2" in markdown
    assert "Unique vehicles tracked: 7" in markdown
    assert "car=12" in markdown and "bus=2" in markdown
    assert "17.4, 78.5" in markdown  # gps cell
    assert "[photo](" in markdown  # relative markdown photo link
    assert "stub" in markdown  # verifier names in caveats
    rows = _table_rows(markdown)
    assert len(rows) == 3  # one row per published event
    assert [r[1] for r in rows] == ["high", "medium", "low"]  # severity ordering


def test_report_empty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out_path = str(tmp_path / "run" / "report.md")
    result = build_report(
        ReportArgs(
            run_id="run-empty",
            published=[],
            dropped_count=4,
            track_summary={},
            out_path=out_path,
        )
    )

    assert result.ok is True
    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        markdown = f.read()
    assert "Total published events: 0" in markdown
    assert "— no events cleared verification —" in markdown
    assert "Dropped events: 4" in markdown


def test_report_severity_ordering(tmp_path) -> None:  # type: ignore[no-untyped-def]
    published = [
        _pub(event_id="e-med", severity="medium", confidence=0.80, t_seconds=2.0),
        _pub(event_id="e-high-1", severity="high", confidence=0.90, t_seconds=1.0),
        _pub(event_id="e-low", severity="low", confidence=0.70, t_seconds=3.0),
        _pub(event_id="e-high-2", severity="high", confidence=0.80, t_seconds=4.0),
    ]
    out_path = str(tmp_path / "run" / "report.md")
    result = build_report(
        ReportArgs(
            run_id="run-ord",
            published=published,
            dropped_count=0,
            track_summary={},
            out_path=out_path,
        )
    )

    assert result.ok is True
    with open(out_path, encoding="utf-8") as f:
        markdown = f.read()
    rows = _table_rows(markdown)
    assert [r[1] for r in rows] == ["high", "high", "medium", "low"]
    assert rows[0][2] == "0.90"  # confidence DESC within severity
    assert rows[1][2] == "0.80"
