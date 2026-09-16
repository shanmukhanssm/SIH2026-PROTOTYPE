"""build_map tool — render the run's heatmap + per-event markers as one HTML file.

Contract: context/tool-registry.md `build_map`. folium.Map + HeatMap plugin
(only when >=1 event has lat AND lon) + one Marker per locatable event with a
photo popup; unlocatable events are never placed at the fallback center.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel

from roadfix import config
from roadfix.state import PublishedEvent

if TYPE_CHECKING:
    import folium

logger = logging.getLogger(__name__)


class MapArgs(BaseModel):
    """Args for build_map."""

    events: list[PublishedEvent]
    out_path: str  # data/runs/<run_id>/map.html
    fallback_center: tuple[float, float] = config.DEFAULT_MAP_CENTER  # when no GPS


class MapResult(BaseModel):
    """Path written + how many events the map covers."""

    path: str
    event_count: int
    used_fallback_center: bool
    ok: bool


def _popup_html(pe: PublishedEvent) -> str:
    """One-event popup: kind, severity, confidence, time, GPS, photo when present."""
    e, v = pe.event, pe.verdict
    gps = f"{e.lat}, {e.lon}" if e.lat is not None and e.lon is not None else "no gps"
    img = f'<br><img src="{e.snapshot_path}" width="240">' if e.snapshot_path else ""
    return (
        f"<b>{e.kind}</b><br>"
        f"severity: {v.severity}<br>"
        f"confidence: {v.confidence:.2f}<br>"
        f"t: {e.t_seconds}s<br>"
        f"gps: {gps}"
        f"{img}"
    )


def _rel_snapshot_events(events: list[PublishedEvent], base_dir: str) -> list[PublishedEvent]:
    """Rewrite snapshot_path relative to the map file dir so popup <img> resolves."""
    out: list[PublishedEvent] = []
    for pe in events:
        if not pe.event.snapshot_path:
            out.append(pe)
            continue
        rel = os.path.relpath(pe.event.snapshot_path, base_dir)
        event = pe.event.model_copy(update={"snapshot_path": rel})
        out.append(pe.model_copy(update={"event": event}))
    return out


def _render_map(
    events: list[PublishedEvent], center: tuple[float, float], used_fallback: bool
) -> folium.Map:
    """Pure map assembly: HeatMap over geolocated points + one Marker per locatable event."""
    import folium  # lazy heavy import (code-standards.md)
    from folium.plugins import HeatMap

    m = folium.Map(location=list(center), zoom_start=13)
    points: list[list[float]] = []
    for pe in events:
        lat, lon = pe.event.lat, pe.event.lon
        if lat is not None and lon is not None:
            points.append([lat, lon])
    if points:  # HeatMap needs >= 1 point (library-docs.md folium section)
        HeatMap(points, radius=15).add_to(m)  # type: ignore[no-untyped-call]  # folium plugins untyped
    for pe in events:
        lat, lon = pe.event.lat, pe.event.lon
        if lat is None or lon is None:
            continue  # never place unlocatable events at the fallback center
        folium.Marker(
            [lat, lon],
            popup=folium.Popup(_popup_html(pe), max_width=300),
        ).add_to(m)
    if used_fallback:  # registry: no-GPS runs keep one fallback reference marker
        folium.Marker(
            list(center),
            popup="No GPS track — map centered on fallback location.",
        ).add_to(m)
    return m


def build_map(args: MapArgs) -> MapResult:
    """SHOW — render run heatmap + event markers as one HTML file. Never raises."""
    try:
        geo_points: list[tuple[float, float]] = []
        for pe in args.events:
            lat, lon = pe.event.lat, pe.event.lon
            if lat is not None and lon is not None:
                geo_points.append((lat, lon))
        center: tuple[float, float] = args.fallback_center
        used_fallback = True
        if geo_points:
            center = (
                sum(p[0] for p in geo_points) / len(geo_points),
                sum(p[1] for p in geo_points) / len(geo_points),
            )
            used_fallback = False
        events = _rel_snapshot_events(args.events, os.path.dirname(args.out_path))
        m = _render_map(events, center, used_fallback)
        os.makedirs(os.path.dirname(args.out_path) or ".", exist_ok=True)
        m.save(args.out_path)
        return MapResult(
            path=args.out_path,
            event_count=len(args.events),
            used_fallback_center=used_fallback,
            ok=True,
        )
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning("[build_map] failed: %s", exc)
        return MapResult(path="", event_count=0, used_fallback_center=False, ok=False)
