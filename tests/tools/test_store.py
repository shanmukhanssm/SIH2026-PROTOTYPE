"""Feature 07 eval — store_upsert_events + store_query_events: registry 4 cases + round-trips."""

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from roadfix.state import Event, PublishedEvent, Verdict
from roadfix.tools.store import (
    StoreQueryArgs,
    UpsertArgs,
    store_query_events,
    store_upsert_events,
)

Kind = Literal["POTHOLE", "KIDS_CROSSING", "TRAFFIC_JAM"]


def make_published(
    run_id: str,
    kind: Kind,
    frame_id: str,
    *,
    n: int = 0,
    t_seconds: float = 1.0,
    severity: Literal["low", "medium", "high"] = "high",
    confidence: float = 0.9,
    confirmed: bool = True,
    lat: float | None = 17.385,
    lon: float | None = 78.4867,
    reason: str = "stub verdict: object present",
) -> PublishedEvent:
    """Build one PublishedEvent with event_id `{run_id}-{kind}-{frame_id}-{n}` (registry format)."""
    event_id = f"{run_id}-{kind}-{frame_id}-{n}"
    return PublishedEvent(
        event=Event(
            event_id=event_id,
            kind=kind,
            frame_id=frame_id,
            t_seconds=t_seconds,
            lat=lat,
            lon=lon,
            snapshot_path=f"/data/runs/{run_id}/snapshots/{frame_id}_{n}.jpg",
            evidence={"consecutive_count": 2},
        ),
        verdict=Verdict(
            event_id=event_id,
            kind=kind,
            attempt=0,
            confirmed=confirmed,
            severity=severity,
            confidence=confidence,
            reason=reason,
            verifier="stub",
        ),
    )


def test_insert_roundtrip(tmp_path: Path) -> None:
    """Registry case 1 — 3 inserts land and read back with fields + derived run_id intact."""
    db = str(tmp_path / "events.db")
    # run_id contains hyphens → proves derivation stops at the FIRST `-{kind}-` marker.
    events = [
        make_published("run-2026-01", "POTHOLE", "f00010", t_seconds=1.0),
        make_published(
            "run-2026-01",
            "TRAFFIC_JAM",
            "f00030",
            t_seconds=2.0,
            severity="medium",
            confidence=0.8,
            confirmed=False,
        ),
        make_published(
            "run-2026-01",
            "KIDS_CROSSING",
            "f00020",
            n=1,
            t_seconds=2.5,
            lat=None,
            lon=None,
        ),
    ]
    res = store_upsert_events(UpsertArgs(events=events, db_path=db))
    assert res.ok and res.upserted == 3 and res.error is None
    assert os.path.exists(db)

    got = store_query_events(StoreQueryArgs(db_path=db))
    assert got.ok and got.error is None
    assert len(got.events) == 3
    # ORDER BY t_seconds ASC
    assert [e.t_seconds for e in got.events] == [1.0, 2.0, 2.5]

    by_id = {e.event_id: e for e in got.events}
    pothole = by_id[events[0].event.event_id]
    assert pothole.kind == "POTHOLE"
    assert pothole.severity == "high"
    assert pothole.confirmed is True
    assert pothole.confidence == 0.9
    assert pothole.t_seconds == 1.0
    assert pothole.lat == 17.385 and pothole.lon == 78.4867
    assert pothole.snapshot_path == events[0].event.snapshot_path
    assert pothole.run_id == "run-2026-01"  # derived from event_id prefix
    assert pothole.reason == events[0].verdict.reason
    published = datetime.fromisoformat(pothole.published_at)
    assert published.tzinfo is not None  # ISO-8601 UTC timestamp of the write

    jam = by_id[events[1].event.event_id]
    assert jam.kind == "TRAFFIC_JAM"
    assert jam.severity == "medium"
    assert jam.confirmed is False
    assert jam.confidence == 0.8


def test_replace_same_id(tmp_path: Path) -> None:
    """Registry case 2 — same event_id upserted twice: 1 row, latest fields win."""
    db = str(tmp_path / "events.db")
    first = make_published("run-1", "POTHOLE", "f00042", severity="high", confidence=0.9)
    second = make_published("run-1", "POTHOLE", "f00042", severity="low", confidence=0.5)
    assert first.event.event_id == second.event.event_id

    r1 = store_upsert_events(UpsertArgs(events=[first], db_path=db))
    r2 = store_upsert_events(UpsertArgs(events=[second], db_path=db))
    assert r1.ok and r2.ok
    assert r1.upserted == 1 and r2.upserted == 1

    got = store_query_events(StoreQueryArgs(db_path=db))
    assert got.ok and len(got.events) == 1
    row = got.events[0]
    assert row.event_id == first.event.event_id
    assert row.severity == "low"
    assert row.confidence == 0.5


def test_query_filters(tmp_path: Path) -> None:
    """Registry case 3 — run_id / kind / combined / limit filters each return the right rows."""
    db = str(tmp_path / "events.db")
    alpha_pothole = make_published("alpha", "POTHOLE", "f00001", t_seconds=1.0)
    alpha_kids = make_published("alpha", "KIDS_CROSSING", "f00002", t_seconds=2.0)
    beta_pothole = make_published("beta", "POTHOLE", "f00003", t_seconds=3.0)
    assert store_upsert_events(
        UpsertArgs(events=[alpha_pothole, alpha_kids, beta_pothole], db_path=db)
    ).ok

    by_run = store_query_events(StoreQueryArgs(db_path=db, run_id="alpha"))
    assert by_run.ok
    assert {e.event_id for e in by_run.events} == {
        alpha_pothole.event.event_id,
        alpha_kids.event.event_id,
    }

    by_kind = store_query_events(StoreQueryArgs(db_path=db, kind="POTHOLE"))
    assert by_kind.ok
    assert {e.event_id for e in by_kind.events} == {
        alpha_pothole.event.event_id,
        beta_pothole.event.event_id,
    }

    combined = store_query_events(
        StoreQueryArgs(db_path=db, run_id="alpha", kind="POTHOLE")
    )
    assert combined.ok
    assert [e.event_id for e in combined.events] == [alpha_pothole.event.event_id]

    limited = store_query_events(StoreQueryArgs(db_path=db, limit=1))
    assert limited.ok and len(limited.events) == 1
    assert limited.events[0].event_id == alpha_pothole.event.event_id  # earliest t_seconds


def test_db_unwritable(tmp_path: Path) -> None:
    """Registry case 4 — unwritable/missing db paths degrade to ok=False, zero raises."""
    missing_dir = str(tmp_path / "no-such-dir" / "events.db")
    res = store_upsert_events(
        UpsertArgs(events=[make_published("run-1", "POTHOLE", "f00001")], db_path=missing_dir)
    )
    assert res.ok is False and res.upserted == 0
    assert res.error is not None and res.error != ""

    dir_as_db = store_upsert_events(UpsertArgs(events=[], db_path=str(tmp_path)))
    assert dir_as_db.ok is False and dir_as_db.error

    query = store_query_events(StoreQueryArgs(db_path=str(tmp_path / "missing.db")))
    assert query.ok is False and query.events == []
    assert query.error is not None and query.error != ""


def test_replay_idempotent(tmp_path: Path) -> None:
    """Same list upserted twice: upserted counts each call, row count never grows."""
    db = str(tmp_path / "events.db")
    events = [
        make_published("run-1", "POTHOLE", "f00001", t_seconds=1.0),
        make_published("run-1", "KIDS_CROSSING", "f00002", t_seconds=2.0),
        make_published("run-1", "TRAFFIC_JAM", "f00003", t_seconds=3.0),
    ]
    r1 = store_upsert_events(UpsertArgs(events=events, db_path=db))
    r2 = store_upsert_events(UpsertArgs(events=events, db_path=db))  # replay
    assert r1.ok and r2.ok
    assert r1.upserted == 3 and r2.upserted == 3
    got = store_query_events(StoreQueryArgs(db_path=db))
    assert got.ok and len(got.events) == 3


def test_null_gps_roundtrip(tmp_path: Path) -> None:
    """lat/lon None → NULL in sqlite → None on the read model (honest GPS gap)."""
    db = str(tmp_path / "events.db")
    event = make_published("run-1", "TRAFFIC_JAM", "f00007", t_seconds=9.0, lat=None, lon=None)
    assert store_upsert_events(UpsertArgs(events=[event], db_path=db)).ok
    got = store_query_events(StoreQueryArgs(db_path=db))
    assert got.ok and len(got.events) == 1
    assert got.events[0].lat is None and got.events[0].lon is None


def test_empty_events_creates_db(tmp_path: Path) -> None:
    """Empty batch → ok=True, upserted=0, and the db file + schema exist for the read side."""
    db = str(tmp_path / "events.db")
    res = store_upsert_events(UpsertArgs(events=[], db_path=db))
    assert res.ok and res.upserted == 0 and res.error is None
    assert os.path.exists(db)
    got = store_query_events(StoreQueryArgs(db_path=db))
    assert got.ok and got.events == []


def test_corrupt_db_ok_false(tmp_path: Path) -> None:
    """A non-sqlite file at db_path → ok=False on both sides, zero raises."""
    db = str(tmp_path / "events.db")
    Path(db).write_bytes(b"definitely not a sqlite database" * 8)
    res = store_upsert_events(
        UpsertArgs(events=[make_published("run-1", "POTHOLE", "f00001")], db_path=db)
    )
    assert res.ok is False and res.error
    query = store_query_events(StoreQueryArgs(db_path=db))
    assert query.ok is False and query.events == [] and query.error
