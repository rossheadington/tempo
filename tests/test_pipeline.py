"""Failure-isolation proofs for the Coros sync wrapper in ``runos.sync.pipeline``.

Mirrors the Garmin-isolation contract (see ``tests/test_garmin_isolation.py``):
the Coros source is an unofficial-API failure domain that must NEVER block
Strava or Garmin or the analysis layer. These tests drive
:func:`runos.sync.pipeline.run_coros_sync` with fake connectors that raise the
documented error types (and a bare ``Exception`` for the catch-all) and assert
that the pipeline returns a degraded :class:`SourceResult` rather than
propagating the failure upward.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from runos.connectors.base import RawWriter
from runos.connectors.coros import (
    SOURCE as COROS,
)
from runos.connectors.coros import (
    CorosAuthError,
    CorosSyncError,
)
from runos.connectors.garmin import SOURCE as GARMIN
from runos.connectors.strava import SOURCE as STRAVA
from runos.sync import pipeline

# ---- Fakes ----------------------------------------------------------------


class _FakeCorosOK:
    """Fake Coros connector whose ``sync`` succeeds and writes one raw row."""

    source = COROS

    def __init__(self, *, rows: int = 1) -> None:
        self._rows = rows
        self.sync_calls = 0

    def sync(self, raw: RawWriter, since: Any = None) -> None:
        self.sync_calls += 1
        with raw.conn:
            for i in range(self._rows):
                raw.put("evolab_dashboard", f"2026-05-{20 + i:02d}", {"day": i})


class _FakeCorosRaises:
    """Fake Coros connector whose ``sync`` raises a configurable exception."""

    source = COROS

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.sync_calls = 0

    def sync(self, raw: RawWriter, since: Any = None) -> None:
        self.sync_calls += 1
        raise self._exc


# ---- run_coros_sync: success path ----------------------------------------


def test_run_coros_sync_returns_ok_on_success(conn: sqlite3.Connection) -> None:
    """Happy path: sync runs cleanly, ``ok=True`` and rows counter reflects raw."""
    connector = _FakeCorosOK(rows=2)
    result = pipeline.run_coros_sync(conn, connector)
    assert result.ok is True
    assert result.source == COROS
    assert result.detail == "ok"
    assert result.rows == 2
    assert connector.sync_calls == 1


# ---- run_coros_sync: isolation paths -------------------------------------


def test_run_coros_sync_isolates_auth_error(conn: sqlite3.Connection) -> None:
    """A :class:`CorosAuthError` becomes ``ok=False`` and does NOT propagate."""
    connector = _FakeCorosRaises(CorosAuthError("no token; run `runos coros login`"))
    result = pipeline.run_coros_sync(conn, connector)
    assert result.ok is False
    assert result.source == COROS
    assert "not authenticated" in result.detail


def test_run_coros_sync_isolates_sync_error(conn: sqlite3.Connection) -> None:
    """A :class:`CorosSyncError` becomes ``ok=False`` and does NOT propagate."""
    connector = _FakeCorosRaises(CorosSyncError("transient 502 on /analyse/query"))
    result = pipeline.run_coros_sync(conn, connector)
    assert result.ok is False
    assert result.source == COROS
    assert "skipped" in result.detail


def test_run_coros_sync_isolates_unexpected_exception(conn: sqlite3.Connection) -> None:
    """A bare ``Exception`` (library blow-up) is still isolated as ``ok=False``."""
    connector = _FakeCorosRaises(RuntimeError("the unofficial API changed shape"))
    try:
        result = pipeline.run_coros_sync(conn, connector)
    except Exception as exc:  # noqa: BLE001 - test asserts isolation
        pytest.fail(f"pipeline raised instead of isolating: {exc}")
    assert result.ok is False
    assert result.source == COROS
    assert "error" in result.detail


# ---- Full pipeline: Coros failure leaves Strava + Garmin alone -----------


def test_run_coros_sync_failure_does_not_block_strava_or_garmin(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Coros sync failure inside ``run_full_sync`` does not stop the other sources.

    Strava lands a real raw row, Garmin lands a real raw row, Coros is recorded
    as ``ok=False`` and the call does not raise. The pipeline must report all
    three results in a deterministic Strava -> Garmin -> Coros order.
    """
    from runos.config import Settings

    settings = Settings(data_dir="/tmp/unused")

    class _FakeStrava:
        source = STRAVA

        def sync(self, raw: RawWriter, since: Any = None) -> None:
            with raw.conn:
                raw.put("activity_summary", "9001", {"id": 9001})

    class _FakeGarmin:
        source = GARMIN

        def sync(self, raw: RawWriter, since: Any = None) -> None:
            with raw.conn:
                raw.put("sleep", "2026-05-20", {"day": "2026-05-20"})

    fake_coros = _FakeCorosRaises(CorosSyncError("upstream 503"))

    monkeypatch.setattr(pipeline, "build_strava_connector", lambda s: _FakeStrava())
    monkeypatch.setattr(pipeline, "build_garmin_connector", lambda s: _FakeGarmin())
    monkeypatch.setattr(pipeline, "build_coros_connector", lambda s: fake_coros)

    results = pipeline.run_full_sync(conn, settings)

    by_source = {r.source: r for r in results}
    assert by_source[STRAVA].ok is True
    assert by_source[GARMIN].ok is True
    assert by_source[COROS].ok is False

    # And the OTHER sources' raw data really landed despite Coros failing.
    strava_n = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='strava'"
    ).fetchone()[0]
    garmin_n = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='garmin'"
    ).fetchone()[0]
    assert strava_n == 1
    assert garmin_n == 1
    assert fake_coros.sync_calls == 1


# ---- Sanity: error types are distinct ------------------------------------


def test_coros_error_types_are_distinct() -> None:
    assert not issubclass(CorosSyncError, CorosAuthError)
    assert not issubclass(CorosAuthError, CorosSyncError)
