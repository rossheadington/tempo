"""End-to-end Strava connector tests, fully mocked (no network, no real creds).

Covers each Phase-2 success criterion:
* token rotation persisted atomically on refresh (STRV-02)
* one-time OAuth code exchange stores tokens (STRV-01)
* resumable backfill via cursor, surviving a mid-run 429 (STRV-03)
* idempotent re-run never re-fetches stored activities (STRV-03)
* lazy stream fetch, skipped when already stored (STRV-04)
* incremental watermark sync pulls only new activities (STRV-05)
* connector writes only to raw_response (STRV-06)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from stravalib.exc import RateLimitExceeded

from tempo.connectors.base import RawWriter
from tempo.connectors.strava import StravaConnector
from tempo.connectors.tokens import TokenSet, TokenStore
from tempo.sync import state as sync_state
from tests.strava_fakes import FakeStravaClient, make_activity, make_streams


def _store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens", "strava")


def _seed_valid_tokens(store: TokenStore, *, expires_at: int = 9_999_999_999) -> None:
    store.save(TokenSet("access0", "refresh0", expires_at))


def _connector(
    tmp_path: Path,
    client: FakeStravaClient,
    *,
    page_budget: int | None = None,
) -> tuple[StravaConnector, TokenStore]:
    store = _store(tmp_path)
    c = StravaConnector(
        client_id=123,
        client_secret="secret",
        token_store=store,
        client=client,
        backfill_page_budget=page_budget,
    )
    return c, store


def _raw_count(conn: sqlite3.Connection, endpoint: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source='strava' AND endpoint=?",
        (endpoint,),
    ).fetchone()[0]


# ---- STRV-01: one-time OAuth handshake ------------------------------------


def test_exchange_code_stores_tokens(tmp_path: Path) -> None:
    client = FakeStravaClient(
        exchange_result={"access_token": "A", "refresh_token": "R", "expires_at": 1234}
    )
    connector, store = _connector(tmp_path, client)
    tokens = connector.exchange_code("the-code")
    assert client.exchange_calls == 1
    assert tokens.refresh_token == "R"
    # Persisted to disk atomically.
    assert store.load() == TokenSet("A", "R", 1234)


def test_authorization_url_includes_scopes(tmp_path: Path) -> None:
    connector, _ = _connector(tmp_path, FakeStravaClient())
    url = connector.authorization_url("http://localhost")
    assert "client_id=123" in url
    assert "activity:read_all" in url


# ---- STRV-02: rotating refresh token persisted atomically -----------------


def test_refresh_rotates_and_persists_new_token(tmp_path: Path) -> None:
    """A near-expiry token triggers a refresh; the rotated token is persisted."""
    client = FakeStravaClient(
        refresh_sequence=[
            {"access_token": "A2", "refresh_token": "R2", "expires_at": 9_999_999_999}
        ]
    )
    connector, store = _connector(tmp_path, client)
    # Seed an already-expired token so ensure_authenticated must refresh.
    store.save(TokenSet("A1", "R1", expires_at=0))

    tokens = connector.ensure_authenticated()
    assert client.refresh_calls == 1
    assert tokens.refresh_token == "R2"
    # The NEW rotating refresh token is on disk (old one would strand the user).
    assert store.load().refresh_token == "R2"


def test_no_refresh_when_token_still_valid(tmp_path: Path) -> None:
    client = FakeStravaClient()
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)  # far-future expiry
    connector.ensure_authenticated()
    assert client.refresh_calls == 0  # must NOT refresh on every call


def test_refresh_persists_before_returning(tmp_path: Path) -> None:
    """Even across two refreshes the latest rotating token is always on disk."""
    client = FakeStravaClient(
        refresh_sequence=[
            {"access_token": "A2", "refresh_token": "R2", "expires_at": 0},  # still expired
            {"access_token": "A3", "refresh_token": "R3", "expires_at": 9_999_999_999},
        ]
    )
    connector, store = _connector(tmp_path, client)
    store.save(TokenSet("A1", "R1", expires_at=0))
    connector.ensure_authenticated()  # first refresh -> R2 (still expired on disk)
    assert store.load().refresh_token == "R2"
    connector.ensure_authenticated()  # second refresh -> R3
    assert store.load().refresh_token == "R3"


# ---- STRV-03: resumable, checkpointed backfill ----------------------------


def _three_pages() -> dict[int, list[dict]]:
    return {
        1: [
            make_activity(
                101, start_utc="2026-05-10T08:00:00Z", start_local="2026-05-10T08:00:00Z"
            ),
            make_activity(
                102, start_utc="2026-05-09T08:00:00Z", start_local="2026-05-09T08:00:00Z"
            ),
        ],
        2: [
            make_activity(
                103, start_utc="2026-05-08T08:00:00Z", start_local="2026-05-08T08:00:00Z"
            ),
            make_activity(
                104, start_utc="2026-05-07T08:00:00Z", start_local="2026-05-07T08:00:00Z"
            ),
        ],
        3: [],  # end of history
    }


def test_backfill_pulls_all_pages_and_completes(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(pages=_three_pages())
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)

    connector.backfill(RawWriter(conn, "strava"))

    assert _raw_count(conn, "activity_summary") == 4
    st = sync_state.read(conn, "strava")
    assert st.backfill_complete is True
    assert st.backfill_cursor is None


def test_backfill_resumes_after_rate_limit_without_refetch(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A 429 mid-backfill leaves a cursor; resuming continues, never re-fetching.

    The retry policy gives up after a few attempts and re-raises
    RateLimitExceeded. We make EVERY get on the second page raise, so the first
    page commits + checkpoints, then the run aborts. A fresh connector then
    resumes from the cursor with the page now served.
    """
    pages = _three_pages()
    # First client: page 1 succeeds, page 2 onward always 429s (persistent window).
    client1 = FakeStravaClient(pages={1: pages[1]}, rate_limit_from_page=2)
    connector1, store = _connector(tmp_path, client1)
    _seed_valid_tokens(store)

    with pytest.raises(RateLimitExceeded):
        connector1.backfill(RawWriter(conn, "strava"))

    # Page 1 stored; cursor parked at page 2; not complete.
    assert _raw_count(conn, "activity_summary") == 2
    st = sync_state.read(conn, "strava")
    assert st.backfill_cursor == {"next_page": 2}
    assert st.backfill_complete is False

    # Resume with a healthy client serving pages 2 and 3.
    client2 = FakeStravaClient(pages=pages)
    connector2, _ = _connector(tmp_path, client2)
    connector2.backfill(RawWriter(conn, "strava"))

    # The resumed run must start at page 2 (never re-request page 1).
    requested_pages = [p[1].get("page") for p in client2.get_calls]
    assert 1 not in requested_pages
    assert _raw_count(conn, "activity_summary") == 4
    assert sync_state.read(conn, "strava").backfill_complete is True


def test_backfill_rerun_when_complete_is_noop(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(pages=_three_pages())
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    connector.backfill(RawWriter(conn, "strava"))
    calls_after_first = len(client.get_calls)

    # Re-running once complete must make zero further GETs (no re-fetch).
    connector.backfill(RawWriter(conn, "strava"))
    assert len(client.get_calls) == calls_after_first


def test_backfill_page_budget_spreads_across_runs(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pages = _three_pages()
    client = FakeStravaClient(pages=pages)
    connector, store = _connector(tmp_path, client, page_budget=1)
    _seed_valid_tokens(store)

    connector.backfill(RawWriter(conn, "strava"))  # only page 1 this run
    assert _raw_count(conn, "activity_summary") == 2
    st = sync_state.read(conn, "strava")
    assert st.backfill_cursor == {"next_page": 2}
    assert st.backfill_complete is False

    # Next run continues from page 2 and finishes.
    connector2, _ = _connector(tmp_path, client, page_budget=10)
    connector2.backfill(RawWriter(conn, "strava"))
    assert _raw_count(conn, "activity_summary") == 4
    assert sync_state.read(conn, "strava").backfill_complete is True


def test_backfill_idempotent_rerun_no_duplicate_rows(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Re-storing the same activities (overlap) must not create duplicate rows."""
    pages = _three_pages()
    client = FakeStravaClient(pages=pages, rate_limit_from_page=2)  # 429 on page 2
    connector, store = _connector(tmp_path, client, page_budget=10)
    _seed_valid_tokens(store)
    with pytest.raises(RateLimitExceeded):
        connector.backfill(RawWriter(conn, "strava"))

    # Manually reset cursor to page 1 to force overlap on resume, simulating a
    # conservative re-fetch; idempotent upsert must dedupe.
    sync_state.save_backfill_cursor(conn, "strava", {"next_page": 1})
    conn.commit()
    client2 = FakeStravaClient(pages=pages)
    connector2, _ = _connector(tmp_path, client2)
    connector2.backfill(RawWriter(conn, "strava"))
    assert _raw_count(conn, "activity_summary") == 4  # not 6


# ---- STRV-04: lazy streams -------------------------------------------------


def test_fetch_streams_stores_all_types(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(streams={101: make_streams()})
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)

    fetched = connector.fetch_streams(RawWriter(conn, "strava"), 101)
    assert fetched is True
    assert _raw_count(conn, "streams") == 1
    import json

    payload = json.loads(
        conn.execute(
            "SELECT payload FROM raw_response WHERE endpoint='streams' AND entity_key='101'"
        ).fetchone()[0]
    )
    # HR, GPS, power, cadence, elevation, pace all present.
    for key in ("heartrate", "latlng", "watts", "cadence", "altitude", "velocity_smooth"):
        assert key in payload


def test_fetch_streams_is_lazy_skips_when_present(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(streams={101: make_streams()})
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    raw = RawWriter(conn, "strava")
    connector.fetch_streams(raw, 101)
    calls_after_first = len(client.get_calls)

    # Second call: already stored -> no network call, returns False.
    fetched = connector.fetch_streams(raw, 101)
    assert fetched is False
    assert len(client.get_calls) == calls_after_first  # no extra GET


def test_fetch_streams_force_refetches(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(streams={101: make_streams()})
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    raw = RawWriter(conn, "strava")
    connector.fetch_streams(raw, 101)
    fetched = connector.fetch_streams(raw, 101, force=True)
    assert fetched is True
    assert _raw_count(conn, "streams") == 1  # still one row (upsert)


def test_stored_activity_ids_lists_backfilled_ids(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(pages=_three_pages())
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    connector.backfill(RawWriter(conn, "strava"))
    assert connector.stored_activity_ids(conn) == [101, 102, 103, 104]


# ---- STRV-05: incremental watermark sync ----------------------------------


def test_sync_pulls_all_when_no_watermark(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(pages=_three_pages())
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    connector.sync(RawWriter(conn, "strava"), since=None)
    assert _raw_count(conn, "activity_summary") == 4
    # Watermark advanced to the newest activity's start_date.
    st = sync_state.read(conn, "strava")
    assert st.last_entity_ts == "2026-05-10T08:00:00Z"
    assert st.last_sync_at is not None


def test_sync_only_pulls_new_since_watermark(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """After a first sync, a second sync with a newer activity pulls only it."""
    first_pages = {
        1: [
            make_activity(201, start_utc="2026-05-01T08:00:00Z", start_local="2026-05-01T08:00:00Z")
        ],
        2: [],
    }
    client1 = FakeStravaClient(pages=first_pages)
    connector1, store = _connector(tmp_path, client1)
    _seed_valid_tokens(store)
    connector1.sync(RawWriter(conn, "strava"), since=None)
    assert _raw_count(conn, "activity_summary") == 1

    # New day: the activity list now includes an older (already-have) and a new one.
    # The connector passes `after` = watermark, so the fake filters out the old one.
    second_pages = {
        1: [
            make_activity(
                202, start_utc="2026-05-05T08:00:00Z", start_local="2026-05-05T08:00:00Z"
            ),
            make_activity(
                201, start_utc="2026-05-01T08:00:00Z", start_local="2026-05-01T08:00:00Z"
            ),
        ],
        2: [],
    }
    client2 = FakeStravaClient(pages=second_pages)
    connector2, _ = _connector(tmp_path, client2)
    connector2.sync(RawWriter(conn, "strava"), since=None)

    # `after` was sent on the list call (only-new behaviour).
    assert any("after" in params for _url, params in client2.get_calls)
    assert _raw_count(conn, "activity_summary") == 2  # 201 + 202, no dup of 201
    assert sync_state.read(conn, "strava").last_entity_ts == "2026-05-05T08:00:00Z"


def test_sync_empty_does_not_rewind_watermark(tmp_path: Path, conn: sqlite3.Connection) -> None:
    first_pages = {
        1: [
            make_activity(301, start_utc="2026-05-01T08:00:00Z", start_local="2026-05-01T08:00:00Z")
        ],
        2: [],
    }
    client1 = FakeStravaClient(pages=first_pages)
    connector1, store = _connector(tmp_path, client1)
    _seed_valid_tokens(store)
    connector1.sync(RawWriter(conn, "strava"), since=None)

    client2 = FakeStravaClient(pages={1: []})  # nothing new
    connector2, _ = _connector(tmp_path, client2)
    connector2.sync(RawWriter(conn, "strava"), since=None)
    assert sync_state.read(conn, "strava").last_entity_ts == "2026-05-01T08:00:00Z"


# ---- STRV-06: connector writes ONLY to raw --------------------------------


def test_connector_writes_only_raw_response(tmp_path: Path, conn: sqlite3.Connection) -> None:
    client = FakeStravaClient(pages=_three_pages(), streams={101: make_streams()})
    connector, store = _connector(tmp_path, client)
    _seed_valid_tokens(store)
    raw = RawWriter(conn, "strava")
    connector.backfill(raw)
    connector.fetch_streams(raw, 101)

    # date_spine must be untouched (structured tables come in Phase 3).
    assert conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0] == 0
    # Only raw_response and sync_state (bookkeeping) hold connector-written rows.
    raw_rows = conn.execute("SELECT COUNT(*) FROM raw_response").fetchone()[0]
    assert raw_rows == 5  # 4 summaries + 1 streams
