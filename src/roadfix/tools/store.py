"""Feature 07 — store_upsert_events + store_query_events: the events.db system of record.

Table schema (tool-registry.md, verbatim):
    events(event_id PK, kind, severity, confirmed, confidence, t_seconds,
           lat, lon, snapshot_path, run_id, reason, published_at)

Writes are INSERT OR REPLACE keyed on event_id — replay/resume never duplicates rows.
run_id derivation: PublishedEvent carries no run_id, but event_id follows
`{run_id}-{kind}-{frame_id}-{n}` and kind is one of the three known literals, so
run_id = the event_id prefix before the FIRST occurrence of `-{kind}-`. This keeps
the declared tool signature unchanged. (A run_id containing `-{kind}-` would
mis-split — malformed input by construction; if the marker is absent the whole
event_id is kept as run_id: honest fallback, no crash.)
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from roadfix.state import PublishedEvent

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    confirmed INTEGER NOT NULL,
    confidence REAL NOT NULL,
    t_seconds REAL NOT NULL,
    lat REAL,
    lon REAL,
    snapshot_path TEXT NOT NULL,
    run_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    published_at TEXT NOT NULL
)
"""

_INSERT_SQL = (
    "INSERT OR REPLACE INTO events "
    "(event_id, kind, severity, confirmed, confidence, t_seconds, lat, lon, "
    "snapshot_path, run_id, reason, published_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_SELECT_SQL = (
    "SELECT event_id, kind, severity, confirmed, confidence, t_seconds, lat, lon, "
    "snapshot_path, run_id, reason, published_at FROM events"
)


class UpsertArgs(BaseModel):
    """Accepted events to persist, plus where the db lives."""

    events: list[PublishedEvent]
    db_path: str


class UpsertResult(BaseModel):
    """Rows written (re-writes counted); ok=False on db failure, error set."""

    upserted: int
    ok: bool
    error: str | None = None


class StoreQueryArgs(BaseModel):
    """Dashboard read filters; every filter optional."""

    db_path: str
    run_id: str | None = None       # None → all runs
    kind: str | None = None         # None → all kinds
    limit: int | None = None        # None → no limit


class StoredEvent(BaseModel):
    """Flat read-model row — the dashboard's view of one accepted event."""

    event_id: str
    kind: str
    severity: str
    confirmed: bool
    confidence: float
    t_seconds: float
    lat: float | None = None
    lon: float | None = None
    snapshot_path: str
    run_id: str
    reason: str
    published_at: str


class QueryResult(BaseModel):
    """Rows in t_seconds order; ok=False when the db is missing/unreadable."""

    events: list[StoredEvent]
    ok: bool
    error: str | None = None


def _derive_run_id(event_id: str, kind: str) -> str:
    """run_id = event_id prefix before the first `-{kind}-` (module docstring has the rule)."""
    return event_id.split(f"-{kind}-", 1)[0]


def store_upsert_events(args: UpsertArgs) -> UpsertResult:
    """SYSTEM OF RECORD — upsert accepted events into events.db by event_id. Never raises."""
    try:
        # Creates db file + schema on every call — even empty batches leave a
        # readable db behind, so the read side always works.
        with sqlite3.connect(args.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            published_at = datetime.now(UTC).isoformat()
            for pe in args.events:
                conn.execute(
                    _INSERT_SQL,
                    (
                        pe.event.event_id,
                        pe.event.kind,
                        pe.verdict.severity,
                        int(pe.verdict.confirmed),
                        pe.verdict.confidence,
                        pe.event.t_seconds,
                        pe.event.lat,  # None → NULL
                        pe.event.lon,
                        pe.event.snapshot_path,
                        _derive_run_id(pe.event.event_id, pe.event.kind),
                        pe.verdict.reason,
                        published_at,
                    ),
                )
        return UpsertResult(upserted=len(args.events), ok=True)
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning(
            "[store_upsert_events] db write failed: %s: %s", type(exc).__name__, exc
        )
        return UpsertResult(upserted=0, ok=False, error=f"{type(exc).__name__}: {exc}")


def store_query_events(args: StoreQueryArgs) -> QueryResult:
    """READ SIDE — query stored events for the dashboard with filters. Never raises."""
    if not Path(args.db_path).is_file():
        logger.warning("[store_query_events] db not found: %s", args.db_path)
        return QueryResult(events=[], ok=False, error=f"db file not found: {args.db_path}")
    try:
        # Read-only connection — queries must never create or mutate the db.
        uri = Path(args.db_path).absolute().as_uri() + "?mode=ro"
        clauses: list[str] = []
        params: list[str | int] = []
        if args.run_id is not None:
            clauses.append("run_id = ?")
            params.append(args.run_id)
        if args.kind is not None:
            clauses.append("kind = ?")
            params.append(args.kind)
        sql = _SELECT_SQL
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY t_seconds ASC"
        if args.limit is not None:
            sql += " LIMIT ?"
            params.append(args.limit)
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        events = [
            StoredEvent(
                event_id=row[0],
                kind=row[1],
                severity=row[2],
                confirmed=bool(row[3]),
                confidence=row[4],
                t_seconds=row[5],
                lat=row[6],
                lon=row[7],
                snapshot_path=row[8],
                run_id=row[9],
                reason=row[10],
                published_at=row[11],
            )
            for row in rows
        ]
        return QueryResult(events=events, ok=True)
    except Exception as exc:  # boundary — translate, never propagate
        logger.warning(
            "[store_query_events] db read failed: %s: %s", type(exc).__name__, exc
        )
        return QueryResult(events=[], ok=False, error=f"{type(exc).__name__}: {exc}")
