"""Tests for raw Garmin -> wellness_day transforms (pure, no network)."""

from __future__ import annotations

import json
import sqlite3

from tempo.connectors.base import RawWriter
from tempo.connectors.garmin import SOURCE
from tempo.transforms import wellness
from tempo.transforms.runner import run_rederive, run_transform
from tests.garmin_fakes import make_hrv, make_sleep, make_stats


def _store_raw(conn: sqlite3.Connection, endpoint: str, day: str, payload: dict) -> None:
    raw = RawWriter(conn, SOURCE)
    with conn:
        raw.put(endpoint, day, payload)


# ---- Pure parse functions --------------------------------------------------


def test_parse_sleep_extracts_stages_and_score() -> None:
    fields = wellness.parse_sleep(make_sleep("2026-05-20", score=88))
    assert fields["sleep_seconds"] == 27000
    assert fields["deep_s"] == 5400
    assert fields["rem_s"] == 5400
    assert fields["light_s"] == 16200
    assert fields["awake_s"] == 600
    assert fields["sleep_score"] == 88


def test_parse_hrv_extracts_avg_and_status() -> None:
    fields = wellness.parse_hrv(make_hrv("2026-05-20", last_night_avg=62.5, status="LOW"))
    assert fields["hrv_last_night"] == 62.5
    assert fields["hrv_status"] == "LOW"


def test_parse_stats_extracts_rhr_steps_stress_battery() -> None:
    fields = wellness.parse_stats(
        make_stats("2026-05-20", resting_hr=46, steps=12000, stress_avg=25, bb_high=95, bb_low=15)
    )
    assert fields["resting_hr"] == 46
    assert fields["steps"] == 12000
    assert fields["stress_avg"] == 25
    assert fields["body_battery_high"] == 95
    assert fields["body_battery_low"] == 15


# ---- Collapse multiple endpoints into ONE row per calendarDate (GRMN-04) ----


def test_build_wellness_row_collapses_three_endpoints_into_one_day() -> None:
    day = "2026-05-20"
    row = wellness.build_wellness_row(
        day,
        sleep=make_sleep(day),
        hrv=make_hrv(day, last_night_avg=70.0),
        stats=make_stats(day, resting_hr=44),
    )
    assert row.day == day
    assert row.hrv_last_night == 70.0
    assert row.resting_hr == 44
    assert row.sleep_seconds == 27000


def test_build_wellness_row_uses_calendarDate_not_request_key() -> None:
    """The local day comes from the payload's calendarDate, not the raw entity key.

    Proves overnight attribution: even if the connector requested under one key,
    Garmin's calendarDate (the wake-up day) governs the bucket (PITFALLS 6).
    """
    # entity key '2026-05-21' but payloads carry calendarDate '2026-05-20'.
    row = wellness.build_wellness_row(
        "2026-05-21",
        sleep=make_sleep("2026-05-20"),
        hrv=make_hrv("2026-05-20"),
        stats=make_stats("2026-05-20"),
    )
    assert row.day == "2026-05-20"


def test_build_wellness_row_tolerates_missing_endpoints() -> None:
    """A day with only sleep (no HRV/stats) still produces a row; others are None."""
    row = wellness.build_wellness_row("2026-05-20", sleep=make_sleep("2026-05-20"))
    assert row.day == "2026-05-20"
    assert row.sleep_seconds == 27000
    assert row.hrv_last_night is None
    assert row.resting_hr is None


# ---- rebuild_wellness end-to-end over raw -> structured --------------------


def test_rebuild_wellness_one_row_per_day(conn: sqlite3.Connection) -> None:
    days = ["2026-05-20", "2026-05-21"]
    for d in days:
        _store_raw(conn, "sleep", d, make_sleep(d))
        _store_raw(conn, "hrv", d, make_hrv(d))
        _store_raw(conn, "stats", d, make_stats(d))

    # Spine must contain the days for the FK; rebuild ensures them.
    with conn:
        n = wellness.rebuild_wellness(conn)
    assert n == 2
    rows = conn.execute("SELECT day FROM wellness_day ORDER BY day").fetchall()
    assert [r["day"] for r in rows] == days


def test_rederive_rebuilds_wellness_with_zero_network(conn: sqlite3.Connection) -> None:
    """`rederive` rebuilds wellness_day purely from stored raw (STORE-02; GRMN-04)."""
    day = "2026-05-20"
    _store_raw(conn, "sleep", day, make_sleep(day))
    _store_raw(conn, "hrv", day, make_hrv(day, last_night_avg=58.0))
    _store_raw(conn, "stats", day, make_stats(day, resting_hr=47))

    result = run_transform(conn, fill_to=date_from(day))
    assert result.wellness_days == 1

    # Drop the structured row, then rederive purely from raw -> it returns.
    with conn:
        conn.execute("DELETE FROM wellness_day")
    assert conn.execute("SELECT COUNT(*) FROM wellness_day").fetchone()[0] == 0

    result2 = run_rederive(conn, fill_to=date_from(day))
    assert result2.wellness_days == 1
    got = conn.execute(
        "SELECT hrv_last_night, resting_hr FROM wellness_day WHERE day=?", (day,)
    ).fetchone()
    assert got["hrv_last_night"] == 58.0
    assert got["resting_hr"] == 47


def test_rebuild_wellness_idempotent(conn: sqlite3.Connection) -> None:
    day = "2026-05-20"
    _store_raw(conn, "sleep", day, make_sleep(day))
    with conn:
        wellness.rebuild_wellness(conn)
        wellness.rebuild_wellness(conn)
    assert conn.execute("SELECT COUNT(*) FROM wellness_day").fetchone()[0] == 1


def test_rebuild_wellness_skips_corrupt_payload(conn: sqlite3.Connection) -> None:
    """A non-dict / unparseable raw payload is skipped, not fatal."""
    raw = RawWriter(conn, SOURCE)
    with conn:
        # Store a deliberately malformed payload directly.
        conn.execute(
            "INSERT INTO raw_response (source, endpoint, entity_key, payload) VALUES (?,?,?,?)",
            (SOURCE, "stats", "2026-05-20", json.dumps([1, 2, 3])),  # list, not dict
        )
        raw.put("sleep", "2026-05-21", make_sleep("2026-05-21"))
    with conn:
        n = wellness.rebuild_wellness(conn)
    assert n == 1  # only the valid day


def date_from(day: str):
    from datetime import date

    return date.fromisoformat(day)
