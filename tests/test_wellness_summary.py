"""Tests that daily_summary LEFT-JOINs wellness without dropping spine days (GRMN-04; STORE-04)."""

from __future__ import annotations

import sqlite3
from datetime import date

from tempo.connectors.base import RawWriter
from tempo.connectors.garmin import SOURCE as GARMIN
from tempo.transforms.runner import run_transform
from tests.garmin_fakes import make_hrv, make_sleep, make_stats
from tests.strava_fakes import make_run


def _store_garmin(conn: sqlite3.Connection, day: str) -> None:
    raw = RawWriter(conn, GARMIN)
    with conn:
        raw.put("sleep", day, make_sleep(day))
        raw.put("hrv", day, make_hrv(day, last_night_avg=66.0))
        raw.put("stats", day, make_stats(day, resting_hr=49, steps=8000))


def _store_strava(conn: sqlite3.Connection, activity_id: int, day: str) -> None:
    raw = RawWriter(conn, "strava")
    with conn:
        raw.put("activity_summary", str(activity_id), make_run(activity_id, day=day))


def test_daily_summary_includes_wellness_columns(conn: sqlite3.Connection) -> None:
    day = "2026-05-20"
    _store_garmin(conn, day)
    run_transform(conn, fill_to=date.fromisoformat(day))

    row = conn.execute(
        "SELECT hrv_last_night, resting_hr, steps, has_wellness FROM daily_summary WHERE day=?",
        (day,),
    ).fetchone()
    assert row["hrv_last_night"] == 66.0
    assert row["resting_hr"] == 49
    assert row["steps"] == 8000
    assert row["has_wellness"] == 1


def test_daily_summary_one_row_per_spine_day_with_wellness(conn: sqlite3.Connection) -> None:
    """The wellness LEFT JOIN never duplicates or drops a spine day (invariant)."""
    # A run on day 1, wellness on day 2, nothing on day 3 (gap/rest).
    _store_strava(conn, 1, "2026-05-20")
    _store_garmin(conn, "2026-05-21")
    run_transform(conn, fill_to=date.fromisoformat("2026-05-22"))

    # Every spine day appears exactly once in daily_summary.
    spine = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
    summary = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
    assert summary == spine

    distinct = conn.execute("SELECT COUNT(DISTINCT day) FROM daily_summary").fetchone()[0]
    assert distinct == summary  # no duplicate days


def test_wellness_only_day_is_preserved(conn: sqlite3.Connection) -> None:
    """A rest day with ONLY Garmin wellness (no activity) is a first-class summary row.

    This is the case an inner join would drop (ARCHITECTURE Anti-Pattern 2).
    """
    day = "2026-05-21"
    _store_garmin(conn, day)
    run_transform(conn, fill_to=date.fromisoformat(day))

    row = conn.execute(
        "SELECT n_activities, has_wellness, hrv_last_night FROM daily_summary WHERE day=?",
        (day,),
    ).fetchone()
    assert row is not None
    assert row["n_activities"] == 0  # no run
    assert row["has_wellness"] == 1  # but wellness present
    assert row["hrv_last_night"] == 66.0


def test_activity_only_day_has_null_wellness(conn: sqlite3.Connection) -> None:
    """A day with a run but no Garmin sync has NULL wellness, not a dropped row."""
    day = "2026-05-20"
    _store_strava(conn, 7, day)
    run_transform(conn, fill_to=date.fromisoformat(day))
    row = conn.execute(
        "SELECT n_activities, has_wellness, hrv_last_night FROM daily_summary WHERE day=?",
        (day,),
    ).fetchone()
    assert row["n_activities"] == 1
    assert row["has_wellness"] == 0
    assert row["hrv_last_night"] is None


def test_wellness_only_day_extends_spine(conn: sqlite3.Connection) -> None:
    """A wellness-only day contributes its day to the spine bounds (no activity needed)."""
    _store_garmin(conn, "2026-05-21")
    run_transform(conn)  # no fill_to: spine must come from wellness day alone
    days = conn.execute("SELECT day FROM date_spine").fetchall()
    assert "2026-05-21" in {r["day"] for r in days}
