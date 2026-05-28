"""Tests for runos.sync.pipeline.fetch_recent_streams.

Covers the query that picks candidate activities (recent + HR-recorded +
missing streams), the per-activity isolation when a fetch fails, the
connector-build failure path, and the no-candidates fast path.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from runos import db
from runos.sync import pipeline


def _conn_with_seed(tmp_path):
    """Open an initialised DB and seed date_spine for the last 30 days."""
    conn = db.init_db(tmp_path / "runos.db")
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)]
    conn.executemany("INSERT OR IGNORE INTO date_spine (day) VALUES (?)", [(d,) for d in days])
    conn.commit()
    return conn


def _insert_activity(conn, *, activity_id: int, day: str, avg_hr: float | None) -> None:
    conn.execute(
        "INSERT INTO activity (activity_id, source, day, avg_hr) VALUES (?, ?, ?, ?)",
        (activity_id, "strava", day, avg_hr),
    )
    conn.commit()


def _insert_stream(conn, *, activity_id: int, stream_type: str = "heartrate") -> None:
    conn.execute(
        "INSERT INTO activity_stream (activity_id, type, data) VALUES (?, ?, ?)",
        (activity_id, stream_type, "[]"),
    )
    conn.commit()


class _FakeConnector:
    """Records fetch_streams calls; returns True for each."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def fetch_streams(self, raw: Any, activity_id: int, *, force: bool = False) -> bool:
        self.calls.append(activity_id)
        return True


def test_no_candidates_returns_empty_result(tmp_path, monkeypatch) -> None:
    """No recent HR-recorded activities -> no connector build, no candidates."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)
    try:
        result = pipeline.fetch_recent_streams(conn, settings)
    finally:
        conn.close()
    assert result.candidates == 0
    assert result.fetched == 0
    assert result.activity_ids == ()
    assert result.error is None


def test_picks_recent_hr_recorded_activities_missing_streams(
    tmp_path, monkeypatch
) -> None:
    """Query restricts to (day >= cutoff) AND (avg_hr > 0) AND (no stream rows)."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_SECRET", "s")
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)

    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    week_ago = (today - timedelta(days=7)).isoformat()

    # Eligible: recent + HR + no stream
    _insert_activity(conn, activity_id=100, day=today.isoformat(), avg_hr=152.0)
    # Eligible (within lookback)
    _insert_activity(conn, activity_id=101, day=yesterday, avg_hr=140.0)
    # Ineligible: too old
    _insert_activity(conn, activity_id=102, day=week_ago, avg_hr=130.0)
    # Ineligible: no HR
    _insert_activity(conn, activity_id=103, day=today.isoformat(), avg_hr=None)
    # Ineligible: HR = 0
    _insert_activity(conn, activity_id=104, day=today.isoformat(), avg_hr=0.0)
    # Ineligible: already has a stream
    _insert_activity(conn, activity_id=105, day=today.isoformat(), avg_hr=160.0)
    _insert_stream(conn, activity_id=105)

    fake = _FakeConnector()
    monkeypatch.setattr(
        "runos.sync.pipeline.build_strava_connector", lambda s: fake
    )

    try:
        result = pipeline.fetch_recent_streams(conn, settings, lookback_days=1)
    finally:
        conn.close()

    assert result.candidates == 2
    assert result.fetched == 2
    # Both eligible activities were fetched, most-recent first.
    assert set(result.activity_ids) == {100, 101}
    assert set(fake.calls) == {100, 101}


def test_lookback_days_widens_window(tmp_path, monkeypatch) -> None:
    """lookback_days=7 pulls in the older HR-recorded activity that day=1 wouldn't."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_SECRET", "s")
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)

    today = date.today()
    five_days_ago = (today - timedelta(days=5)).isoformat()
    _insert_activity(conn, activity_id=200, day=five_days_ago, avg_hr=150.0)

    fake = _FakeConnector()
    monkeypatch.setattr("runos.sync.pipeline.build_strava_connector", lambda s: fake)

    try:
        result_1d = pipeline.fetch_recent_streams(conn, settings, lookback_days=1)
        assert result_1d.candidates == 0

        result_7d = pipeline.fetch_recent_streams(conn, settings, lookback_days=7)
    finally:
        conn.close()

    assert result_7d.candidates == 1
    assert result_7d.activity_ids == (200,)


def test_connector_build_failure_is_isolated(tmp_path, monkeypatch) -> None:
    """A connector build failure returns an error string, not a raise."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)
    _insert_activity(conn, activity_id=300, day=date.today().isoformat(), avg_hr=145.0)

    def _boom(s: Settings) -> Any:
        raise RuntimeError("credentials missing")

    monkeypatch.setattr("runos.sync.pipeline.build_strava_connector", _boom)

    try:
        result = pipeline.fetch_recent_streams(conn, settings)
    finally:
        conn.close()

    assert result.candidates == 1
    assert result.fetched == 0
    assert result.activity_ids == ()
    assert result.error is not None
    assert "credentials missing" in result.error


def test_per_activity_fetch_failure_is_isolated(tmp_path, monkeypatch) -> None:
    """A fetch_streams exception for one activity doesn't stop the rest."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_SECRET", "s")
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)

    today = date.today().isoformat()
    _insert_activity(conn, activity_id=400, day=today, avg_hr=150.0)
    _insert_activity(conn, activity_id=401, day=today, avg_hr=140.0)
    _insert_activity(conn, activity_id=402, day=today, avg_hr=130.0)

    class _PartialFailureConnector:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def fetch_streams(
            self, raw: Any, activity_id: int, *, force: bool = False
        ) -> bool:
            self.calls.append(activity_id)
            if activity_id == 401:
                raise RuntimeError("transient network error")
            return True

    fake = _PartialFailureConnector()
    monkeypatch.setattr("runos.sync.pipeline.build_strava_connector", lambda s: fake)

    try:
        result = pipeline.fetch_recent_streams(conn, settings)
    finally:
        conn.close()

    # All three were attempted; two fetched successfully.
    assert result.candidates == 3
    assert result.fetched == 2
    assert sorted(result.activity_ids) == [400, 402]
    assert sorted(fake.calls) == [400, 401, 402]


def test_fetch_streams_returning_false_doesnt_count(tmp_path, monkeypatch) -> None:
    """When fetch_streams returns False (already cached), it's not counted as fetched."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_SECRET", "s")
    from runos.config import Settings

    settings = Settings(_env_file=None)
    conn = _conn_with_seed(tmp_path)

    today = date.today().isoformat()
    _insert_activity(conn, activity_id=500, day=today, avg_hr=150.0)

    class _NoOpConnector:
        def fetch_streams(self, raw: Any, activity_id: int, *, force: bool = False) -> bool:
            return False  # "already cached in raw"

    monkeypatch.setattr("runos.sync.pipeline.build_strava_connector", lambda s: _NoOpConnector())

    try:
        result = pipeline.fetch_recent_streams(conn, settings)
    finally:
        conn.close()

    assert result.candidates == 1
    assert result.fetched == 0
    assert result.activity_ids == ()
