"""Failure-isolation proof: a Garmin 429/auth/library failure never blocks Strava.

This is the headline Phase-6 requirement (GRMN-01/03; ARCHITECTURE Anti-Pattern 5):
the fragile Garmin source is a contained failure domain. These tests drive the
real ``runos.sync.pipeline`` with a 429-raising fake Garmin connector and assert
that (a) Strava data still lands, (b) the pipeline does NOT raise, (c) Garmin is
reported skipped, and (d) analysis still runs on the existing data.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from runos.connectors.base import RawWriter
from runos.connectors.garmin import (
    SOURCE as GARMIN,
)
from runos.connectors.garmin import (
    GarminAuthError,
    GarminConnector,
    GarminSyncError,
)
from runos.connectors.strava import SOURCE as STRAVA
from runos.sync import pipeline
from runos.transforms.runner import run_transform
from tests.garmin_fakes import FakeGarminClient, make_day
from tests.strava_fakes import make_run


def _seed_strava(conn: sqlite3.Connection, day: str = "2026-05-20") -> None:
    """Put a Strava activity in raw + transform it, as if a prior sync ran."""
    raw = RawWriter(conn, STRAVA)
    with conn:
        raw.put("activity_summary", "1001", make_run(1001, day=day))
    run_transform(conn, fill_to=date.fromisoformat(day))


# ---- run_garmin_sync catches and skips, never raising ----------------------


def test_garmin_429_is_caught_and_skipped(conn: sqlite3.Connection) -> None:
    """A 429 returns a not-ok SourceResult instead of raising (GRMN-03)."""
    client = FakeGarminClient(
        days={d: make_day(d) for d in _recent_days()},
        raise_429_on="sleep",
    )
    connector = GarminConnector("tok", client=client)
    result = pipeline.run_garmin_sync(conn, connector)
    assert result.ok is False
    assert "429" in result.detail
    assert result.source == GARMIN


def test_garmin_auth_error_is_caught_and_skipped(conn: sqlite3.Connection) -> None:
    """No tokens (never logged in) -> skipped, not raised."""
    client = FakeGarminClient(tokens_present=False)
    connector = GarminConnector("tok", client_factory=lambda **_: client)
    result = pipeline.run_garmin_sync(conn, connector)
    assert result.ok is False
    assert "not authenticated" in result.detail


def test_garmin_unexpected_error_is_caught_and_skipped(conn: sqlite3.Connection) -> None:
    """An arbitrary library blow-up is still isolated."""
    client = FakeGarminClient(
        days={d: make_day(d) for d in _recent_days()},
        raise_error_on="stats",
    )
    connector = GarminConnector("tok", client=client)
    result = pipeline.run_garmin_sync(conn, connector)
    assert result.ok is False


# ---- The full pipeline: Strava survives a Garmin 429 -----------------------


def test_full_sync_strava_survives_garmin_429(conn: sqlite3.Connection, monkeypatch) -> None:
    """Strava data lands and the pipeline does NOT raise when Garmin 429s (GRMN-01/03)."""
    from runos.config import Settings

    settings = Settings(data_dir="/tmp/unused")

    # Fake Strava connector: stores an activity, ok.
    class _FakeStrava:
        source = STRAVA

        def sync(self, raw: RawWriter, since=None) -> None:
            with raw.conn:
                raw.put("activity_summary", "2002", make_run(2002, day="2026-05-20"))

    # Fake Garmin connector that 429s on sync.
    garmin_client = FakeGarminClient(
        days={d: make_day(d) for d in _recent_days()}, raise_429_on="sleep"
    )
    fake_garmin = GarminConnector("tok", client=garmin_client)

    monkeypatch.setattr(pipeline, "build_strava_connector", lambda s: _FakeStrava())
    monkeypatch.setattr(pipeline, "build_garmin_connector", lambda s: fake_garmin)

    results = pipeline.run_full_sync(conn, settings)  # must NOT raise

    by_source = {r.source: r for r in results}
    assert by_source[STRAVA].ok is True
    assert by_source[STRAVA].rows == 1  # Strava activity stored
    assert by_source[GARMIN].ok is False  # Garmin skipped
    assert "429" in by_source[GARMIN].detail

    # And the Strava raw data is genuinely present despite Garmin failing.
    n = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='strava' AND endpoint='activity_summary'"
    ).fetchone()[0]
    assert n == 1


def test_analysis_runs_after_garmin_429(conn: sqlite3.Connection) -> None:
    """Transforms + a daily_summary read still work on existing data after a Garmin 429.

    Proves the fragile source cannot block the analysis layer (GRMN-03).
    """
    _seed_strava(conn, "2026-05-20")

    # Garmin 429s, caught.
    client = FakeGarminClient(days={d: make_day(d) for d in _recent_days()}, raise_429_on="hrv")
    connector = GarminConnector("tok", client=client)
    result = pipeline.run_garmin_sync(conn, connector)
    assert result.ok is False

    # Analysis-layer read still works: the Strava day is in daily_summary, with
    # NULL wellness (Garmin contributed nothing) -- no exception, no missing day.
    row = conn.execute(
        "SELECT n_activities, has_wellness FROM daily_summary WHERE day='2026-05-20'"
    ).fetchone()
    assert row["n_activities"] == 1
    assert row["has_wellness"] == 0


def _recent_days() -> list[str]:
    from datetime import timedelta

    today = GarminConnector._today()
    return [(today - timedelta(days=i)).isoformat() for i in range(5, -1, -1)]


# ---- Sanity: GarminSyncError / GarminAuthError are distinct types ----------


def test_error_types_are_distinct() -> None:
    assert not issubclass(GarminSyncError, GarminAuthError)
    assert not issubclass(GarminAuthError, GarminSyncError)


def test_run_garmin_sync_does_not_raise_on_any_failure(conn: sqlite3.Connection) -> None:
    """Belt-and-braces: every failure mode returns, none propagates."""
    for kwargs in (
        {"raise_429_on": "sleep"},
        {"raise_error_on": "sleep"},
        {"tokens_present": False},
    ):
        c = FakeGarminClient(days={d: make_day(d) for d in _recent_days()}, **kwargs)
        if "tokens_present" in kwargs:
            connector = GarminConnector("tok", client_factory=lambda client=c, **_: client)
        else:
            connector = GarminConnector("tok", client=c)
        try:
            res = pipeline.run_garmin_sync(conn, connector)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"pipeline raised instead of isolating: {exc}")
        assert res.ok is False
