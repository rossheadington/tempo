"""Tests for the Coros EvoLab analysis-layer reader (pure stdlib, no network)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from runos.analysis.coros_evolab import EvoLabContext, EvoLabDay, read_evolab


def _seed_row(
    conn: sqlite3.Connection,
    *,
    day: str,
    vo2max: float | None = 55.0,
    stamina_level: int | None = 60,
    training_load: int | None = 400,
    lthr: int | None = 170,
    ltsp_s_per_km: int | None = 240,
    fetched_at: str | None = None,
) -> None:
    """Insert one row into coros_evolab_day, also seeding date_spine for the FK."""
    fetched_at = fetched_at or datetime.now(UTC).isoformat()
    d = date.fromisoformat(day)
    iso = d.isocalendar()
    with conn:
        # date_spine needs the day or the FK fails.
        conn.execute(
            """
            INSERT INTO date_spine (day, dow, week, month, year)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (day) DO NOTHING
            """,
            (day, d.weekday(), iso.week, d.month, d.year),
        )
        conn.execute(
            """
            INSERT INTO coros_evolab_day (
                day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at),
        )


# ---------------------------------------------------------------------------
# Empty table -> absent context
# ---------------------------------------------------------------------------


def test_read_evolab_empty_returns_absent_context(conn: sqlite3.Connection) -> None:
    """An empty coros_evolab_day table degrades to ``present=False``, no rows."""
    ctx = read_evolab(conn)
    assert isinstance(ctx, EvoLabContext)
    assert ctx.present is False
    assert ctx.days == ()
    assert ctx.latest is None


# ---------------------------------------------------------------------------
# Latest is the most recent day with at least one non-NULL metric
# ---------------------------------------------------------------------------


def test_read_evolab_returns_latest_correctly(conn: sqlite3.Connection) -> None:
    """``latest`` resolves to the most recent day with usable data.

    Tail row that is all-NULL doesn't qualify -- the recovery report would
    render a hollow block, which is worse than omitting the section. The
    next-most-recent row with at least one non-NULL metric wins.
    """
    _seed_row(conn, day="2026-05-26", vo2max=54.0, stamina_level=58)
    _seed_row(conn, day="2026-05-27", vo2max=55.5, stamina_level=60)
    _seed_row(
        conn,
        day="2026-05-28",
        vo2max=None,
        stamina_level=None,
        training_load=None,
        lthr=None,
        ltsp_s_per_km=None,
    )

    ctx = read_evolab(conn)
    assert ctx.present is True
    assert ctx.latest is not None
    assert isinstance(ctx.latest, EvoLabDay)
    # All-NULL tail row is skipped; latest is 2026-05-27.
    assert ctx.latest.day == date(2026, 5, 27)
    assert ctx.latest.vo2max == 55.5
    assert ctx.latest.stamina_level == 60


# ---------------------------------------------------------------------------
# Days are returned sorted ascending by date
# ---------------------------------------------------------------------------


def test_read_evolab_sorted_ascending_by_day(conn: sqlite3.Connection) -> None:
    """``days`` is sorted ascending so the recovery report can compute 7d deltas."""
    # Insert in non-chronological order to prove the ordering comes from the read.
    _seed_row(conn, day="2026-05-27", vo2max=55.0)
    _seed_row(conn, day="2026-05-25", vo2max=54.0)
    _seed_row(conn, day="2026-05-26", vo2max=54.5)

    ctx = read_evolab(conn)
    assert ctx.present is True
    assert [d.day for d in ctx.days] == [
        date(2026, 5, 25),
        date(2026, 5, 26),
        date(2026, 5, 27),
    ]
    # Latest is the chronological tail.
    assert ctx.latest is not None
    assert ctx.latest.day == date(2026, 5, 27)
