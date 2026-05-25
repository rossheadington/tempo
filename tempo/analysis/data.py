"""Read the analysis inputs from the structured/gold layer + per-source freshness.

This is the only place the analysis layer touches SQLite. It reads:

* the per-activity rows (id, day, duration, pace, HR) needed to compute load;
* the zero-filled ``date_spine`` so the daily load series is continuous (rest
  days included) -- the EWMA/ACWR windows depend on this (LOAD-02/03);
* the per-source ``sync_state`` so every report can state its own data freshness
  (ANL-05; PITFALLS: never trust stale data silently).

It is read-only: no writes, no network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """The per-activity fields the load computation needs."""

    activity_id: int
    day: str
    sport: str | None
    distance_m: float | None
    moving_s: int | None
    avg_pace_s_km: float | None
    avg_hr: float | None


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """Per-source sync recency, for the report freshness header (ANL-05)."""

    source: str
    last_sync_at: str | None
    last_entity_ts: str | None
    days_stale: int | None  # full days since last successful sync (None if never)


def read_activities(conn: sqlite3.Connection) -> list[ActivityRecord]:
    """Return all structured activities ordered by day then id."""
    rows = conn.execute(
        """
        SELECT activity_id, day, sport, distance_m, moving_s, avg_pace_s_km, avg_hr
        FROM activity
        ORDER BY day, activity_id
        """
    ).fetchall()
    return [
        ActivityRecord(
            activity_id=int(r["activity_id"]),
            day=str(r["day"]),
            sport=r["sport"],
            distance_m=r["distance_m"],
            moving_s=r["moving_s"],
            avg_pace_s_km=r["avg_pace_s_km"],
            avg_hr=r["avg_hr"],
        )
        for r in rows
    ]


def read_spine_days(conn: sqlite3.Connection) -> list[str]:
    """Return every calendar day in the zero-filled spine, chronologically."""
    rows = conn.execute("SELECT day FROM date_spine ORDER BY day").fetchall()
    return [str(r["day"]) for r in rows]


def activities_by_day(conn: sqlite3.Connection) -> dict[str, list[ActivityRecord]]:
    """Group activities by their local day."""
    grouped: dict[str, list[ActivityRecord]] = {}
    for rec in read_activities(conn):
        grouped.setdefault(rec.day, []).append(rec)
    return grouped


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def source_freshness(
    conn: sqlite3.Connection, *, as_of: date | None = None
) -> list[SourceFreshness]:
    """Return freshness for every source with a ``sync_state`` row.

    ``days_stale`` is the number of whole days between the last successful sync and
    ``as_of`` (defaulting to today). A source that has never synced has
    ``days_stale = None`` so the report can say "never synced" rather than imply
    fresh data.
    """
    reference = as_of or date.today()
    rows = conn.execute(
        "SELECT source, last_sync_at, last_entity_ts FROM sync_state ORDER BY source"
    ).fetchall()
    out: list[SourceFreshness] = []
    for r in rows:
        last_sync = _parse_iso_dt(r["last_sync_at"])
        days_stale = (reference - last_sync.date()).days if last_sync is not None else None
        out.append(
            SourceFreshness(
                source=str(r["source"]),
                last_sync_at=r["last_sync_at"],
                last_entity_ts=r["last_entity_ts"],
                days_stale=days_stale,
            )
        )
    return out


def data_date_range(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Return ``(first_day, last_day)`` covered by activity data, or ``None``."""
    row = conn.execute("SELECT MIN(day) AS lo, MAX(day) AS hi FROM activity").fetchone()
    if row is None or row["lo"] is None:
        return None
    return (str(row["lo"]), str(row["hi"]))
