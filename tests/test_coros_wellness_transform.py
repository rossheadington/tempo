"""Tests for the Coros wellness transform: projection + per-(day, metric) priority resolver.

Mirrors :mod:`tests.test_wellness_transforms` (the Garmin equivalent) but
focuses on the LOCKED Phase-18 resolver contract: Coros wins where it has a
non-NULL value; Garmin's prior write survives in columns Coros doesn't fill.

The six tests cover the dual-source matrix from CONTEXT.md § Test scope:

- Coros-only day: all Coros metric columns populated, others NULL.
- Garmin-only day: Coros transform leaves the row untouched.
- Both sources, Coros wins per metric: Coros values overwrite Garmin's.
- Both sources, Coros only fills SOME columns: Garmin fills the rest.
- Empty raw layer: zero rows written, no errors.
- Malformed payload: warning logged, the bad day skipped, others continue.

All inputs are mocked at the ``raw_response`` table; no network, no
``requests``. Garmin payloads are constructed via the existing
``tests.garmin_fakes`` builders so the Garmin transform pass is exercised
exactly as production would do.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from runos.connectors.base import RawWriter
from runos.transforms import coros_wellness as coros_tf
from runos.transforms import wellness as garmin_tf
from runos.transforms.coros_wellness import (
    EP_HEART_RATE,
    EP_HRV,
    EP_SLEEP,
)
from runos.transforms.coros_wellness import (
    SOURCE as COROS_SOURCE,
)
from runos.transforms.runner import run_transform
from tests.garmin_fakes import make_hrv, make_sleep, make_stats

# ---------------------------------------------------------------------------
# Helpers: store raw payloads under the Coros / Garmin source labels
# ---------------------------------------------------------------------------


def _store_coros_raw(
    conn: sqlite3.Connection, endpoint: str, day: str, payload: dict
) -> None:
    """Insert one Coros raw row under the given endpoint label, keyed by ISO day."""
    raw = RawWriter(conn, COROS_SOURCE)
    with conn:
        raw.put(endpoint, day, payload)


def _store_garmin_raw(
    conn: sqlite3.Connection, endpoint: str, day: str, payload: dict
) -> None:
    """Insert one Garmin raw row (mirrors ``tests/test_wellness_transforms``)."""
    raw = RawWriter(conn, garmin_tf.SOURCE)
    with conn:
        raw.put(endpoint, day, payload)


def _coros_hrv_payload(day: str, *, avg: float | None = 65.0) -> dict:
    """Build one ``sleepHrvList`` entry shape as produced by the connector."""
    return {
        "happenDay": int(day.replace("-", "")),
        "avgSleepHrv": avg,
        "sleepHrvBase": 60,
    }


def _coros_day_detail_payload(
    day: str, *, rhr: int | None = 48, avg_hrv: float | None = 65.0
) -> dict:
    """Build one ``dayList`` entry shape (used for both EP_SLEEP and EP_HEART_RATE)."""
    payload: dict = {"happenDay": int(day.replace("-", ""))}
    if rhr is not None:
        payload["rhr"] = rhr
    if avg_hrv is not None:
        payload["avgSleepHrv"] = avg_hrv
    return payload


def _wellness_row(conn: sqlite3.Connection, day: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM wellness_day WHERE day = ?", (day,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Test 1: Coros-only day projects all metrics it can populate
# ---------------------------------------------------------------------------


def test_coros_only_day_projects_all_metrics(conn: sqlite3.Connection) -> None:
    """A day with only Coros raw rows produces a fresh ``wellness_day`` row.

    Coros fills the columns it currently surfaces (resting_hr, hrv_last_night);
    every other column stays NULL because Coros's v1.7 endpoint surface doesn't
    expose them and Garmin had nothing to contribute.
    """
    day = "2026-05-20"
    _store_coros_raw(conn, EP_HRV, day, _coros_hrv_payload(day, avg=72.5))
    _store_coros_raw(
        conn, EP_SLEEP, day, _coros_day_detail_payload(day, rhr=44, avg_hrv=72.5)
    )
    _store_coros_raw(
        conn, EP_HEART_RATE, day, _coros_day_detail_payload(day, rhr=44, avg_hrv=72.5)
    )

    # Run the full transform so the spine FK + ordering are exercised end-to-end.
    run_transform(conn, fill_to=None)

    row = _wellness_row(conn, day)
    assert row is not None, "expected a wellness_day row for the Coros-only day"
    assert row["resting_hr"] == 44
    assert row["hrv_last_night"] == 72.5
    # Columns Coros can't populate stay NULL on a Coros-only day.
    assert row["hrv_status"] is None
    assert row["sleep_score"] is None
    assert row["sleep_seconds"] is None
    assert row["deep_s"] is None
    assert row["body_battery_high"] is None
    assert row["steps"] is None


# ---------------------------------------------------------------------------
# Test 2: A Garmin-only day is untouched by the Coros transform
# ---------------------------------------------------------------------------


def test_garmin_only_day_remains_unchanged(conn: sqlite3.Connection) -> None:
    """If only Garmin has raw rows for a day, Coros's transform leaves them intact."""
    day = "2026-05-20"
    _store_garmin_raw(conn, "sleep", day, make_sleep(day, score=85))
    _store_garmin_raw(conn, "hrv", day, make_hrv(day, last_night_avg=58.0))
    _store_garmin_raw(
        conn, "stats", day, make_stats(day, resting_hr=49, steps=10500)
    )

    run_transform(conn, fill_to=None)

    row = _wellness_row(conn, day)
    assert row is not None, "Garmin transform should have inserted the row"
    # Every Garmin-provided field is intact -- Coros transform did NOT clobber.
    assert row["resting_hr"] == 49
    assert row["hrv_last_night"] == 58.0
    assert row["sleep_score"] == 85
    assert row["sleep_seconds"] == 27000
    assert row["steps"] == 10500


# ---------------------------------------------------------------------------
# Test 3: Dual-source day -- Coros wins per metric where it has a value
# ---------------------------------------------------------------------------


def test_dual_source_day_coros_wins_per_metric(conn: sqlite3.Connection) -> None:
    """Coros overrides Garmin on every column where Coros provides a non-NULL value.

    Order in the runner: Garmin transform writes first (HRV 58, RHR 49); Coros
    transform writes second with HRV 72.5 + RHR 44 via COALESCE-on-update, so
    those columns land on the Coros values.
    """
    day = "2026-05-20"
    # Garmin says HRV=58, RHR=49.
    _store_garmin_raw(conn, "hrv", day, make_hrv(day, last_night_avg=58.0))
    _store_garmin_raw(conn, "stats", day, make_stats(day, resting_hr=49))
    # Coros says HRV=72.5, RHR=44 -- both should win.
    _store_coros_raw(conn, EP_HRV, day, _coros_hrv_payload(day, avg=72.5))
    _store_coros_raw(
        conn, EP_HEART_RATE, day, _coros_day_detail_payload(day, rhr=44, avg_hrv=72.5)
    )

    run_transform(conn, fill_to=None)

    row = _wellness_row(conn, day)
    assert row is not None
    assert row["hrv_last_night"] == 72.5, "Coros HRV should override Garmin's"
    assert row["resting_hr"] == 44, "Coros RHR should override Garmin's"


# ---------------------------------------------------------------------------
# Test 4: Garmin fills metric gaps Coros doesn't provide
# ---------------------------------------------------------------------------


def test_dual_source_day_garmin_fills_metric_gaps_coros_doesnt_provide(
    conn: sqlite3.Connection,
) -> None:
    """Where Coros has nothing for a column, the Garmin value survives via COALESCE.

    Coros currently only surfaces ``resting_hr`` + ``hrv_last_night`` from the
    18-01 endpoints. Sleep stages, sleep score, body battery, stress, and steps
    are NOT in any Coros payload, so the Garmin writes for those columns must
    survive the Coros transform pass untouched.
    """
    day = "2026-05-20"
    _store_garmin_raw(conn, "sleep", day, make_sleep(day, score=90))
    _store_garmin_raw(conn, "hrv", day, make_hrv(day, last_night_avg=58.0))
    _store_garmin_raw(
        conn,
        "stats",
        day,
        make_stats(day, resting_hr=49, steps=11000, stress_avg=24, bb_high=92, bb_low=18),
    )
    # Coros writes HRV + RHR; everything else from Garmin must survive.
    _store_coros_raw(conn, EP_HRV, day, _coros_hrv_payload(day, avg=72.5))
    _store_coros_raw(
        conn, EP_HEART_RATE, day, _coros_day_detail_payload(day, rhr=44, avg_hrv=72.5)
    )

    run_transform(conn, fill_to=None)

    row = _wellness_row(conn, day)
    assert row is not None
    # Coros wins on the columns it provides.
    assert row["hrv_last_night"] == 72.5
    assert row["resting_hr"] == 44
    # Garmin fills the gaps Coros has nothing to say about.
    assert row["sleep_score"] == 90
    assert row["sleep_seconds"] == 27000
    assert row["deep_s"] == 5400
    assert row["rem_s"] == 5400
    assert row["light_s"] == 16200
    assert row["awake_s"] == 600
    assert row["body_battery_high"] == 92
    assert row["body_battery_low"] == 18
    assert row["stress_avg"] == 24
    assert row["steps"] == 11000


# ---------------------------------------------------------------------------
# Test 5: An empty raw layer produces no rows
# ---------------------------------------------------------------------------


def test_empty_raw_produces_no_rows(conn: sqlite3.Connection) -> None:
    """With zero Coros (and zero Garmin) raw rows, no ``wellness_day`` rows appear."""
    with conn:
        n = coros_tf.rebuild_coros_wellness(conn)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM wellness_day").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 6: Malformed payload is logged + skipped, other days continue
# ---------------------------------------------------------------------------


def test_malformed_payload_skipped_with_log(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-dict / unparseable Coros raw payload is skipped, never raised.

    Mirrors :func:`tests.test_wellness_transforms.test_rebuild_wellness_skips_corrupt_payload`
    -- one poisoned raw row never breaks the whole transform pass; the rest of
    the days still land in ``wellness_day``. A WARNING is logged so the bad row
    surfaces in observability without taking the pipeline down.
    """
    bad_day = "2026-05-20"
    good_day = "2026-05-21"

    # Bad payload: not a JSON dict (a list, which the transform must skip).
    # Also a separate bad-JSON row to exercise the JSONDecodeError branch.
    with conn:
        conn.execute(
            "INSERT INTO raw_response (source, endpoint, entity_key, payload) "
            "VALUES (?, ?, ?, ?)",
            (COROS_SOURCE, EP_SLEEP, bad_day, json.dumps([1, 2, 3])),
        )
        conn.execute(
            "INSERT INTO raw_response (source, endpoint, entity_key, payload) "
            "VALUES (?, ?, ?, ?)",
            (COROS_SOURCE, EP_HRV, bad_day, "{not valid json"),
        )
    # Good payload for a different day -- this one MUST land.
    _store_coros_raw(
        conn, EP_HEART_RATE, good_day, _coros_day_detail_payload(good_day, rhr=46)
    )

    with caplog.at_level(logging.WARNING, logger="runos.transforms.coros_wellness"), conn:
        n = coros_tf.rebuild_coros_wellness(conn)

    # Only the good day produced a row; the bad day is skipped.
    assert n == 1
    good = _wellness_row(conn, good_day)
    assert good is not None
    assert good["resting_hr"] == 46
    assert _wellness_row(conn, bad_day) is None

    # Observability: at least one WARNING must be emitted naming the transform.
    assert any(
        "coros wellness transform" in rec.message.lower() for rec in caplog.records
    ), f"expected a warning record, got: {[r.message for r in caplog.records]}"
