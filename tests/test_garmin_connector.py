"""Tests for the Garmin connector: interface conformance, token reuse, no-retry-429.

All mocked: a :class:`FakeGarminClient` stands in for the fragile library, so no
network and no real credentials are ever needed.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from tempo.connectors.base import Connector, RawWriter
from tempo.connectors.garmin import (
    SOURCE,
    GarminAuthError,
    GarminConnector,
    GarminSyncError,
    _is_rate_limited,
)
from tempo.sync import state as sync_state
from tests.garmin_fakes import (
    FakeGarminClient,
    FakeTooManyRequests,
    make_day,
)


def _raw(conn: sqlite3.Connection) -> RawWriter:
    return RawWriter(conn, SOURCE)


def _seeded_client(days: list[str]) -> FakeGarminClient:
    return FakeGarminClient(days={d: make_day(d) for d in days})


# ---- Interface conformance (GRMN-01) --------------------------------------


def test_garmin_connector_implements_connector_protocol() -> None:
    """GarminConnector satisfies the SAME Connector protocol as Strava (GRMN-01)."""
    connector = GarminConnector("tok", client=FakeGarminClient())
    assert isinstance(connector, Connector)
    # The protocol methods exist with the expected names.
    assert hasattr(connector, "backfill")
    assert hasattr(connector, "sync")
    assert connector.source == "garmin"


# ---- Verbatim raw-only writes, keyed by calendarDate (GRMN-04) -------------


def test_sync_stores_verbatim_raw_per_endpoint_keyed_by_date(conn: sqlite3.Connection) -> None:
    """Each endpoint is stored verbatim under (garmin, <endpoint>, <ISO date>) (GRMN-04)."""
    days = ["2026-05-20", "2026-05-21", "2026-05-22"]
    client = _seeded_client(days)
    connector = GarminConnector("tok", client=client)

    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))

    rows = conn.execute(
        "SELECT endpoint, entity_key FROM raw_response WHERE source='garmin' "
        "ORDER BY entity_key, endpoint"
    ).fetchall()
    stored = {(r["endpoint"], r["entity_key"]) for r in rows}
    for d in days:
        assert ("sleep", d) in stored
        assert ("hrv", d) in stored
        assert ("stats", d) in stored
    # Connector writes ONLY to raw_response, never to wellness_day.
    assert conn.execute("SELECT COUNT(*) FROM wellness_day").fetchone()[0] == 0


def test_sync_advances_watermark_to_last_requested_day(conn: sqlite3.Connection) -> None:
    """The watermark advances to the newest day in the synced range (today)."""
    today = GarminConnector._today()
    start = today - timedelta(days=2)
    days = [(start + timedelta(days=i)).isoformat() for i in range(3)]
    connector = GarminConnector("tok", client=_seeded_client(days))
    connector.sync(_raw(conn), since=start)
    st = sync_state.read(conn, SOURCE)
    assert st.last_entity_ts == today.isoformat()
    assert st.last_sync_at is not None


def test_sync_skips_empty_payloads(conn: sqlite3.Connection) -> None:
    """A day Garmin has no data for produces no raw rows (not empty rows)."""
    client = FakeGarminClient(days={"2026-05-20": make_day("2026-05-20")})  # only one day seeded
    connector = GarminConnector("tok", client=client)
    connector.sync(_raw(conn), since=date.fromisoformat("2026-05-19"))  # 19th and 20th requested
    # 19th has no seeded data -> no rows; 20th has all three.
    keys_19 = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='garmin' AND entity_key='2026-05-19'"
    ).fetchone()[0]
    keys_20 = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='garmin' AND entity_key='2026-05-20'"
    ).fetchone()[0]
    assert keys_19 == 0
    assert keys_20 == 3


# ---- Token reuse: NO fresh login on a scheduled sync (GRMN-02) -------------


def test_sync_reuses_tokens_and_never_credential_logs(conn: sqlite3.Connection) -> None:
    """A sync via the token store performs NO credential (SSO) login (GRMN-02)."""
    client = _seeded_client(["2026-05-20"])
    # Build through the factory path: client_factory returns the token-only client.
    connector = GarminConnector(
        "tok",
        client_factory=lambda **_: client,
    )
    connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))

    assert client.token_login_calls == 1  # loaded from tokens
    assert client.credential_login_calls == 0  # NEVER a fresh SSO login


def test_sync_without_tokens_raises_auth_error_not_login(conn: sqlite3.Connection) -> None:
    """No persisted tokens -> GarminAuthError telling the user to run `garmin login`."""
    client = FakeGarminClient(tokens_present=False)
    connector = GarminConnector("tok", client_factory=lambda **_: client)
    with pytest.raises(GarminAuthError, match="garmin login"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))
    # It did NOT fall through to a credential login.
    assert client.credential_login_calls == 0


# ---- 429 = fail-log-skip with NO retry (GRMN-03; PITFALLS 2) ---------------


def test_429_on_data_call_raises_sync_error_no_retry(conn: sqlite3.Connection) -> None:
    """A 429 on a data call raises GarminSyncError immediately, with no retry loop."""
    client = FakeGarminClient(
        days={"2026-05-20": make_day("2026-05-20")},
        raise_429_on="hrv",
    )
    connector = GarminConnector("tok", client=client)
    with pytest.raises(GarminSyncError, match="429"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))

    # NO retry: 'hrv' was attempted exactly once (one call), not repeatedly.
    hrv_calls = [c for c in client.data_calls if c[0] == "hrv"]
    assert len(hrv_calls) == 1


def test_429_rolls_back_partial_writes(conn: sqlite3.Connection) -> None:
    """A 429 mid-pull leaves no half-written raw rows and no advanced watermark."""
    client = FakeGarminClient(
        days={
            "2026-05-20": make_day("2026-05-20"),
            "2026-05-21": make_day("2026-05-21"),
        },
        raise_429_on="stats",  # fails on every day's stats call
    )
    connector = GarminConnector("tok", client=client)
    with pytest.raises(GarminSyncError):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))

    # The whole pull was one transaction -> rolled back; nothing persisted.
    n = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='garmin'").fetchone()[0]
    assert n == 0
    st = sync_state.read(conn, SOURCE)
    assert st.last_entity_ts is None  # watermark never advanced (Anti-Pattern 3)


def test_429_on_token_login_raises_sync_error_no_retry(conn: sqlite3.Connection) -> None:
    """A 429 during token login is a sync error (skip), not an auth error, no retry."""
    client = FakeGarminClient(raise_429_on_login=True)
    connector = GarminConnector("tok", client_factory=lambda **_: client)
    with pytest.raises(GarminSyncError, match="429"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))
    assert client.login_calls == 1  # exactly one attempt, no retry


def test_generic_library_error_raises_sync_error(conn: sqlite3.Connection) -> None:
    """An unexpected library exception becomes GarminSyncError (caught upstream)."""
    client = FakeGarminClient(
        days={"2026-05-20": make_day("2026-05-20")},
        raise_error_on="sleep",
    )
    connector = GarminConnector("tok", client=client)
    with pytest.raises(GarminSyncError):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-20"))


# ---- Backfill (GRMN-04) ----------------------------------------------------


def test_backfill_walks_trailing_days_and_marks_complete(conn: sqlite3.Connection) -> None:
    today = GarminConnector._today()
    # Seed every day in the window so all produce rows.
    from datetime import timedelta

    days = [(today - timedelta(days=i)).isoformat() for i in range(3, -1, -1)]
    client = FakeGarminClient(days={d: make_day(d) for d in days})
    connector = GarminConnector("tok", client=client, backfill_days=3)

    connector.backfill(_raw(conn))

    n = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='garmin'").fetchone()[0]
    assert n == 3 * 4  # 4 days x 3 endpoints
    st = sync_state.read(conn, SOURCE)
    assert st.backfill_complete is True


# ---- Idempotency -----------------------------------------------------------


def test_sync_is_idempotent_on_rerun(conn: sqlite3.Connection) -> None:
    days = ["2026-05-20", "2026-05-21"]
    connector = GarminConnector("tok", client=_seeded_client(days))
    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))
    first = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='garmin'").fetchone()[0]

    connector2 = GarminConnector("tok", client=_seeded_client(days))
    connector2.sync(_raw(conn), since=date.fromisoformat(days[0]))
    second = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='garmin'").fetchone()[0]
    assert first == second  # upserts, no duplicate rows


# ---- 429 detection helper --------------------------------------------------


def test_is_rate_limited_detects_typed_and_string() -> None:
    assert _is_rate_limited(FakeTooManyRequests("x")) is True
    assert _is_rate_limited(RuntimeError("HTTP 429 Too Many Requests")) is True
    assert _is_rate_limited(RuntimeError("normal error")) is False
