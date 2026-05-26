"""Journal entries in daily_summary + sRPE as a fallback load track (JRNL-03).

Asserts:

* journal rows appear in ``daily_summary`` (rpe/feel/srpe/has_journal/has_notes)
  without dropping or duplicating any spine day (the one-row-per-day invariant);
* a day whose pace/HR load is *insufficient* gets an sRPE-based load via the
  analysis load series, clearly flagged method ``sRPE``;
* objective (rTSS/hrTSS) load still wins when available -- sRPE never overrides it.

No network, no credentials.
"""

from __future__ import annotations

import sqlite3

import pytest

from tempo.analysis.load import (
    DayLoad,
    LoadConfig,
    LoadMethod,
    apply_srpe_fallback,
)
from tempo.analysis.runner import build_load_series
from tempo.connectors.base import RawWriter
from tempo.journal import add_entry
from tempo.transforms.runner import run_rederive
from tests.strava_fakes import make_run


def _seed_activities(conn: sqlite3.Connection, runs: list[dict]) -> None:
    raw = RawWriter(conn, "strava")
    with conn:
        for payload in runs:
            raw.put("activity_summary", str(payload["id"]), payload)
    run_rederive(conn)


# ---- daily_summary join invariant -----------------------------------------


def test_journal_appears_in_daily_summary(conn: sqlite3.Connection) -> None:
    _seed_activities(conn, [make_run(1, day="2026-05-10", moving_time=3600)])
    add_entry(conn, day="2026-05-10", rpe=7, feel="strong", notes="great tempo", sport="Run")

    row = conn.execute(
        "SELECT rpe, feel, srpe, has_journal, has_notes FROM daily_summary WHERE day='2026-05-10'"
    ).fetchone()
    assert row["rpe"] == 7
    assert row["feel"] == "strong"
    assert row["srpe"] == pytest.approx(420.0)
    assert row["has_journal"] == 1
    assert row["has_notes"] == 1


def test_daily_summary_one_row_per_spine_day(conn: sqlite3.Connection) -> None:
    _seed_activities(
        conn,
        [
            make_run(1, day="2026-05-01", moving_time=3600),
            make_run(2, day="2026-05-05", moving_time=1800),
        ],
    )
    add_entry(conn, day="2026-05-03", rpe=4, notes="rest day note")  # day with no activity
    add_entry(conn, day="2026-05-05", rpe=6, sport="Run")

    spine = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
    summary = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
    # One row per spine day, no drops, no duplicates.
    assert summary == spine
    # The rest-day journal day exists as a spine row and surfaces in the summary.
    assert "2026-05-03" in {r["day"] for r in conn.execute("SELECT day FROM daily_summary")}


def test_days_without_journal_have_null_journal_columns(conn: sqlite3.Connection) -> None:
    _seed_activities(conn, [make_run(1, day="2026-05-02", moving_time=3600)])
    # No journal entry on this day.
    row = conn.execute(
        "SELECT rpe, feel, srpe, has_journal, has_notes FROM daily_summary WHERE day='2026-05-02'"
    ).fetchone()
    assert row["rpe"] is None
    assert row["feel"] is None
    assert row["srpe"] is None
    assert row["has_journal"] == 0
    assert row["has_notes"] == 0


def test_multiple_entries_sum_srpe_and_take_latest_rpe(conn: sqlite3.Connection) -> None:
    # Two entries on one day: srpe sums, rpe/feel come from the latest.
    add_entry(conn, day="2026-05-04", rpe=5, sport="Strength", duration_min=30)  # sRPE 150
    add_entry(conn, day="2026-05-04", rpe=8, feel="cooked", sport="Run", duration_min=40)  # 320
    row = conn.execute(
        "SELECT rpe, feel, srpe, has_journal FROM daily_summary WHERE day='2026-05-04'"
    ).fetchone()
    assert row["srpe"] == pytest.approx(470.0)  # 150 + 320
    assert row["rpe"] == 8  # latest entry
    assert row["feel"] == "cooked"
    assert row["has_journal"] == 1


# ---- sRPE as fallback load (unit) -----------------------------------------


def test_srpe_fills_insufficient_day() -> None:
    insufficient = DayLoad(
        day="2026-05-01",
        load=0.0,
        method=LoadMethod.INSUFFICIENT.value,
        n_activities=1,
        n_insufficient=1,
    )
    out = apply_srpe_fallback(insufficient, 300.0)
    assert out.load == pytest.approx(300.0)
    assert out.method == LoadMethod.SRPE.value


def test_srpe_fills_rest_day_crosstraining() -> None:
    rest = DayLoad(day="2026-05-01", load=0.0, method="rest", n_activities=0, n_insufficient=0)
    out = apply_srpe_fallback(rest, 225.0)
    assert out.load == pytest.approx(225.0)
    assert out.method == LoadMethod.SRPE.value


def test_srpe_does_not_override_objective_load() -> None:
    objective = DayLoad(
        day="2026-05-01",
        load=80.0,
        method=LoadMethod.RTSS.value,
        n_activities=1,
        n_insufficient=0,
    )
    out = apply_srpe_fallback(objective, 500.0)
    assert out.load == pytest.approx(80.0)  # rTSS wins
    assert out.method == LoadMethod.RTSS.value


def test_srpe_fallback_noop_without_srpe() -> None:
    rest = DayLoad(day="2026-05-01", load=0.0, method="rest", n_activities=0, n_insufficient=0)
    assert apply_srpe_fallback(rest, None) is rest
    assert apply_srpe_fallback(rest, 0.0) is rest


# ---- sRPE as fallback load (integration through build_load_series) ---------


def test_load_series_uses_srpe_when_load_insufficient(conn: sqlite3.Connection) -> None:
    # An activity with NO pace/HR usable data + no load config -> insufficient.
    _seed_activities(
        conn,
        [
            make_run(
                1,
                day="2026-05-20",
                moving_time=3600,
                average_speed=None,
                average_heartrate=None,
            )
        ],
    )
    add_entry(conn, day="2026-05-20", rpe=7, sport="Run")  # links, sRPE = 7*60 = 420

    series = build_load_series(conn, LoadConfig())  # no threshold pace / HR config
    by_day = {dl.day: dl for dl in series.day_loads}
    dl = by_day["2026-05-20"]
    assert dl.method == LoadMethod.SRPE.value
    assert dl.load == pytest.approx(420.0)


def test_load_series_objective_wins_over_srpe(conn: sqlite3.Connection) -> None:
    # Activity with pace data + threshold config -> rTSS; sRPE must NOT override.
    _seed_activities(conn, [make_run(1, day="2026-05-21", moving_time=3600, average_speed=4.167)])
    add_entry(conn, day="2026-05-21", rpe=9, sport="Run", duration_min=60)  # sRPE 540

    cfg = LoadConfig(threshold_pace_s_per_km=240.0)  # pace 1000/4.167 ~= 240 -> IF~1 -> ~100
    series = build_load_series(conn, cfg)
    dl = {d.day: d for d in series.day_loads}["2026-05-21"]
    assert dl.method == LoadMethod.RTSS.value
    assert dl.load == pytest.approx(100.0, abs=1.0)


def test_load_series_crosstraining_rest_day_gets_srpe(conn: sqlite3.Connection) -> None:
    # No activity at all on a day, but a journaled strength session -> sRPE load.
    _seed_activities(conn, [make_run(1, day="2026-05-22", moving_time=3600)])  # anchors spine range
    add_entry(conn, day="2026-05-23", rpe=6, sport="Strength", duration_min=50)  # sRPE 300

    series = build_load_series(conn, LoadConfig(threshold_pace_s_per_km=240.0))
    dl = {d.day: d for d in series.day_loads}["2026-05-23"]
    assert dl.method == LoadMethod.SRPE.value
    assert dl.load == pytest.approx(300.0)
