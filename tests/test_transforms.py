"""Raw -> structured transform correctness, the date spine, and daily_summary.

Covers Phase-3 success criteria STORE-01/03/04 and the bucketing edge cases as
applied through the FULL transform (STORE-05): we insert Strava-shaped fake raw
rows into a temp DB, run the transform, and assert the resulting structured rows,
zero-filled spine, and one-row-per-day daily_summary. No network, no credentials.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from runos.connectors.base import RawWriter
from runos.transforms import strava as strava_tf
from runos.transforms.runner import run_rederive
from tests.strava_fakes import make_activity, make_activity_tz, make_streams


def _seed_activity(
    conn: sqlite3.Connection, payload: dict, endpoint: str = "activity_summary"
) -> None:
    raw = RawWriter(conn, "strava")
    with conn:
        raw.put(endpoint, str(payload["id"]), payload)


def _seed_streams(conn: sqlite3.Connection, activity_id: int, payload: dict) -> None:
    raw = RawWriter(conn, "strava")
    with conn:
        raw.put("streams", str(activity_id), payload)


# ---- STORE-01: pure activity transform -----------------------------------


def test_transform_activity_pure_projection() -> None:
    payload = make_activity(7, start_utc="2026-05-20T22:10:00Z", start_local="2026-05-20T23:10:00Z")
    row = strava_tf.transform_activity(payload)
    assert row.activity_id == 7
    assert row.source == "strava"
    assert row.day == "2026-05-20"  # local wall-clock day
    assert row.start_local == "2026-05-20T23:10:00Z"
    assert row.start_utc == "2026-05-20T22:10:00Z"
    assert row.sport == "Run"
    assert row.distance_m == pytest.approx(10007.0)
    assert row.moving_s == 3000
    assert row.avg_hr == pytest.approx(150.0)
    # avg_pace_s_km is derived from average_speed (3.33 m/s -> ~300 s/km).
    assert row.avg_pace_s_km == pytest.approx(1000.0 / 3.33)


def test_transform_activity_missing_optionals_become_none() -> None:
    payload = {
        "id": 9,
        "sport_type": "Run",
        "start_date_local": "2026-05-21T07:00:00Z",
        "start_date": "2026-05-21T07:00:00Z",
        # no hr/watts/cadence/speed/distance
    }
    row = strava_tf.transform_activity(payload)
    assert row.day == "2026-05-21"
    assert row.avg_hr is None
    assert row.avg_watts is None
    assert row.avg_pace_s_km is None  # no average_speed -> no derived pace
    assert row.distance_m is None


def test_transform_activity_without_start_local_raises() -> None:
    with pytest.raises(ValueError, match="start_date_local"):
        strava_tf.transform_activity({"id": 1, "start_date": "2026-05-20T10:00:00Z"})


def test_streams_transform_one_row_per_type(conn: sqlite3.Connection) -> None:
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-20T10:00:00Z", start_local="2026-05-20T11:00:00Z")
    )
    _seed_streams(conn, 1, make_streams())
    run_rederive(conn)
    rows = conn.execute(
        "SELECT type, data, original_size, resolution FROM activity_stream WHERE activity_id=1"
    ).fetchall()
    types = {r["type"] for r in rows}
    assert {
        "time",
        "latlng",
        "heartrate",
        "watts",
        "cadence",
        "altitude",
        "distance",
        "velocity_smooth",
    } <= types
    hr = next(r for r in rows if r["type"] == "heartrate")
    assert json.loads(hr["data"]) == [120, 140, 155, 160]
    assert hr["original_size"] == 4
    assert hr["resolution"] == "high"


def test_streams_for_unknown_activity_are_skipped(conn: sqlite3.Connection) -> None:
    # streams present but no matching activity -> skipped, no FK violation.
    _seed_streams(conn, 999, make_streams())
    result = run_rederive(conn)
    assert result.activities == 0
    assert result.streams == 0


# ---- STORE-03: zero-filled, continuous date spine ------------------------


def test_spine_zero_fills_every_day_including_rest_days(conn: sqlite3.Connection) -> None:
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z")
    )
    _seed_activity(
        conn, make_activity(2, start_utc="2026-05-05T10:00:00Z", start_local="2026-05-05T11:00:00Z")
    )
    run_rederive(conn)
    days = [r[0] for r in conn.execute("SELECT day FROM date_spine ORDER BY day")]
    # Continuous: every day from the 1st to the 5th, including the 3 rest days.
    assert days == ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05"]


def test_spine_extends_forward_to_fill_to(conn: sqlite3.Connection) -> None:
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z")
    )
    run_rederive(conn, fill_to=date(2026, 5, 4))
    days = [r[0] for r in conn.execute("SELECT day FROM date_spine ORDER BY day")]
    assert days == ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]


def test_spine_metadata_is_correct(conn: sqlite3.Connection) -> None:
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-26T10:00:00Z", start_local="2026-05-26T11:00:00Z")
    )
    run_rederive(conn)
    row = conn.execute(
        "SELECT dow, week, month, year FROM date_spine WHERE day='2026-05-26'"
    ).fetchone()
    # 2026-05-26 is a Tuesday (dow=1), ISO week 22, May, 2026.
    assert (row["dow"], row["month"], row["year"]) == (1, 5, 2026)
    assert row["week"] == date(2026, 5, 26).isocalendar().week


def test_no_data_no_fill_yields_empty_spine(conn: sqlite3.Connection) -> None:
    result = run_rederive(conn)
    assert result.spine_days == 0
    assert conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0] == 0


# ---- STORE-04: daily_summary, one row per day, left join -----------------


def test_daily_summary_one_row_per_day_rest_days_first_class(conn: sqlite3.Connection) -> None:
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z")
    )
    _seed_activity(
        conn, make_activity(2, start_utc="2026-05-03T10:00:00Z", start_local="2026-05-03T11:00:00Z")
    )
    run_rederive(conn)
    rows = conn.execute(
        "SELECT day, n_activities, total_distance_m FROM daily_summary ORDER BY day"
    ).fetchall()
    # One row per spine day (1st..3rd), including the rest day on the 2nd.
    assert [r["day"] for r in rows] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    rest = next(r for r in rows if r["day"] == "2026-05-02")
    assert rest["n_activities"] == 0  # rest day present with zero activities
    assert rest["total_distance_m"] is None
    active = next(r for r in rows if r["day"] == "2026-05-01")
    assert active["n_activities"] == 1


def test_daily_summary_rolls_up_multiple_activities_same_day(conn: sqlite3.Connection) -> None:
    a = make_activity(1, start_utc="2026-05-01T08:00:00Z", start_local="2026-05-01T09:00:00Z")
    b = make_activity(2, start_utc="2026-05-01T17:00:00Z", start_local="2026-05-01T18:00:00Z")
    _seed_activity(conn, a)
    _seed_activity(conn, b)
    run_rederive(conn)
    row = conn.execute(
        "SELECT n_activities, total_distance_m, total_moving_s "
        "FROM daily_summary WHERE day='2026-05-01'"
    ).fetchone()
    assert row["n_activities"] == 2
    assert row["total_distance_m"] == pytest.approx(a["distance"] + b["distance"])
    assert row["total_moving_s"] == a["moving_time"] + b["moving_time"]


def test_daily_summary_never_drops_a_spine_day(conn: sqlite3.Connection) -> None:
    # Left join from the spine: the count of summary rows equals the spine size.
    _seed_activity(
        conn, make_activity(1, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z")
    )
    _seed_activity(
        conn, make_activity(2, start_utc="2026-05-10T10:00:00Z", start_local="2026-05-10T11:00:00Z")
    )
    run_rederive(conn)
    spine_count = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
    summary_count = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
    assert summary_count == spine_count == 10  # 1st..10th inclusive


# ---- STORE-05: bucketing edge cases applied through the full transform ----


def test_full_transform_late_night_run_lands_on_local_day(conn: sqlite3.Connection) -> None:
    # 23:10 local on the 26th; true UTC instant rolled into the 27th. The
    # structured row and its spine attribution must use the LOCAL day (26th).
    payload = make_activity(
        50, start_utc="2026-05-27T03:10:00Z", start_local="2026-05-26T23:10:00Z"
    )
    _seed_activity(conn, payload)
    run_rederive(conn)
    row = conn.execute("SELECT day FROM activity WHERE activity_id=50").fetchone()
    assert row["day"] == "2026-05-26"
    # And it appears on the 26th in daily_summary, not the 27th.
    n26 = conn.execute("SELECT n_activities FROM daily_summary WHERE day='2026-05-26'").fetchone()
    assert n26["n_activities"] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM daily_summary WHERE day='2026-05-27'").fetchone()[0] == 0
    )


def test_full_transform_timezone_travel_pair(conn: sqlite3.Connection) -> None:
    london = make_activity_tz(
        60,
        start_utc="2026-03-10T23:30:00Z",
        start_local="2026-03-10T23:30:00Z",
        timezone="(GMT+00:00) Europe/London",
        utc_offset=0.0,
    )
    tokyo = make_activity_tz(
        61,
        start_utc="2026-03-10T22:00:00Z",  # ~same real-time window, far east
        start_local="2026-03-11T07:00:00Z",
        timezone="(GMT+09:00) Asia/Tokyo",
        utc_offset=32400.0,
    )
    _seed_activity(conn, london)
    _seed_activity(conn, tokyo)
    run_rederive(conn)
    days = {
        r["activity_id"]: r["day"] for r in conn.execute("SELECT activity_id, day FROM activity")
    }
    assert days[60] == "2026-03-10"
    assert days[61] == "2026-03-11"
    # utc_offset preserved verbatim for re-derivation.
    off = conn.execute("SELECT utc_offset FROM activity WHERE activity_id=61").fetchone()
    assert off["utc_offset"] == pytest.approx(32400.0)


def test_full_transform_dst_transition_day(conn: sqlite3.Connection) -> None:
    # A run on the US spring-forward night; local date must be the 8th.
    payload = make_activity_tz(
        70,
        start_utc="2026-03-08T07:50:00Z",
        start_local="2026-03-08T23:50:00Z",
        timezone="(GMT-05:00) America/New_York",
        utc_offset=-18000.0,
    )
    _seed_activity(conn, payload)
    run_rederive(conn)
    assert (
        conn.execute("SELECT day FROM activity WHERE activity_id=70").fetchone()["day"]
        == "2026-03-08"
    )


def test_full_transform_fake_z_not_treated_as_utc(conn: sqlite3.Connection) -> None:
    # start_date (true UTC) is on the 27th, start_date_local (fake Z) on the 26th.
    payload = make_activity(
        80, start_utc="2026-05-27T01:00:00Z", start_local="2026-05-26T23:00:00Z"
    )
    _seed_activity(conn, payload)
    run_rederive(conn)
    assert (
        conn.execute("SELECT day FROM activity WHERE activity_id=80").fetchone()["day"]
        == "2026-05-26"
    )


# ---- detail payload preferred over summary -------------------------------


def test_detail_payload_preferred_over_summary(conn: sqlite3.Connection) -> None:
    summary = make_activity(
        90, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z"
    )
    summary["name"] = "summary-name"
    detail = make_activity(90, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z")
    detail["name"] = "detail-name"
    _seed_activity(conn, summary, endpoint="activity_summary")
    _seed_activity(conn, detail, endpoint="activity")
    run_rederive(conn)
    name = conn.execute("SELECT name FROM activity WHERE activity_id=90").fetchone()["name"]
    assert name == "detail-name"
    # Still only one structured row for the id.
    assert conn.execute("SELECT COUNT(*) FROM activity WHERE activity_id=90").fetchone()[0] == 1
