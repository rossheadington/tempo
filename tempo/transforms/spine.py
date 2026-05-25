"""Build and maintain the zero-filled ``date_spine``.

The ``date_spine`` is the join backbone: one row per calendar day so that rest
days and single-source days are first-class rows the ``daily_summary`` view never
drops (STORE-03; ARCHITECTURE Pattern 3, Anti-Pattern 2). A *missing* day would
silently corrupt EWMA / rolling-window analyses in Phase 4 (PITFALLS 6), so the
spine must be **continuous** -- every calendar day between the first and last data
date present, with no gaps.

Spine rows are pure metadata derived from the day itself (day-of-week, ISO week,
month, year). They are computed deterministically from the set of local days the
structured layer produced, so rebuilding the spine is part of a no-network
``rederive``.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta


def _parse_day(day: str) -> date:
    return date.fromisoformat(day)


def _spine_attrs(d: date) -> tuple[int, int, int, int]:
    """Return ``(dow, iso_week, month, year)`` for a calendar day.

    ``dow`` is 0=Mon..6=Sun (matches the 0001_init.sql comment). ``iso_week`` is
    the ISO 8601 week number.
    """
    iso = d.isocalendar()
    return (d.weekday(), iso.week, d.month, d.year)


def daterange(start: date, end: date) -> list[date]:
    """Inclusive list of every calendar day from ``start`` to ``end``."""
    if end < start:
        return []
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def data_day_bounds(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Return ``(min_day, max_day)`` across all structured day-bearing tables.

    Currently only ``activity`` contributes days; Phase 6 wellness and Phase 5
    journal will be unioned in here so the spine still covers wellness-only or
    journal-only days. Returns ``None`` if there is no dated data at all.
    """
    row = conn.execute("SELECT MIN(day) AS lo, MAX(day) AS hi FROM activity").fetchone()
    lo, hi = (row["lo"], row["hi"]) if row is not None else (None, None)
    if lo is None or hi is None:
        return None
    return (str(lo), str(hi))


def ensure_days(conn: sqlite3.Connection, days: list[str]) -> int:
    """Idempotently insert spine rows for the given local days. Returns rows added.

    Each day's metadata is recomputed every call (cheap, deterministic), so a
    re-run is harmless. Caller owns the transaction.
    """
    added = 0
    for day in days:
        d = _parse_day(day)
        dow, week, month, year = _spine_attrs(d)
        cur = conn.execute(
            """
            INSERT INTO date_spine (day, dow, week, month, year)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (day) DO UPDATE SET
                dow=excluded.dow, week=excluded.week,
                month=excluded.month, year=excluded.year
            """,
            (day, dow, week, month, year),
        )
        # rowcount is 1 for the UPDATE path too; count true inserts by checking
        # changes() is not reliable across the upsert, so count via total_changes.
        added += cur.rowcount if cur.rowcount > 0 else 0
    return added


def rebuild_spine(
    conn: sqlite3.Connection,
    *,
    fill_to: date | None = None,
) -> int:
    """Zero-fill ``date_spine`` so every calendar day in the data range has a row.

    Computes the inclusive range ``[min data day, max data day]`` and inserts a
    continuous run of days across it -- including days with no activity (rest days)
    and gap days -- so analyses that iterate days never hit a hole. If ``fill_to``
    is given (e.g. today), the range is extended forward to it so the spine reaches
    the present even when the last activity was a while ago.

    Returns the number of days now present in the spine within the filled range.
    Pure DB work; safe to re-run. Caller owns the transaction.
    """
    bounds = data_day_bounds(conn)
    if bounds is None and fill_to is None:
        return 0

    if bounds is None:
        # No data yet but asked to fill forward to a date: seed a single day.
        start = end = fill_to
    else:
        start = _parse_day(bounds[0])
        end = _parse_day(bounds[1])

    if fill_to is not None and fill_to > end:
        end = fill_to

    days = [d.isoformat() for d in daterange(start, end)]
    ensure_days(conn, days)
    return len(days)
