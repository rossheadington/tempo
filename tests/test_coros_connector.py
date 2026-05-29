"""Tests for the Coros connector: auth, atomic token store, refresh-on-401, raw writes.

All mocked: a :class:`FakeCorosHttp` stands in for the real
``requests.Session`` via the connector's ``CorosHttpClient`` Protocol seam, so
no network and no real credentials are needed. The fake records every request
URL/method/headers/params/body so tests can assert on what the connector
*actually* sent (e.g. that the MD5 password landed in the login body, that the
``accessToken`` header carried the persisted token, that a 401 triggered
exactly one re-login).

Why a fake client and not the ``responses`` library: the Coros connector
exposes an HTTP-client Protocol exactly like the Garmin connector exposes a
``GarminClient`` Protocol -- the seam is at the client object, not at the
``requests`` module. Mirroring ``tests/test_garmin_connector.py``'s fake
pattern keeps both connectors' tests consistent and avoids monkey-patching the
global ``requests`` namespace.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from runos.connectors.base import Connector, RawWriter
from runos.connectors.coros import (
    COROS_BASE_URL,
    DEFAULT_BACKFILL_DAYS,
    EP_EVOLAB,
    EP_HEART_RATE,
    EP_HRV,
    EP_SLEEP,
    PATH_ANALYSE,
    PATH_DASHBOARD,
    PATH_DAY_DETAIL,
    PATH_LOGIN,
    SOURCE,
    SYNC_LOOKBACK_DAYS,
    CorosAuthError,
    CorosConnector,
    CorosSyncError,
    CorosTokenStore,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for a ``requests.Response`` (only what the connector reads)."""

    def __init__(self, *, status_code: int = 200, body: Any | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        # ``text`` is unused by the connector but kept for parity with the real type.
        if isinstance(self._body, (dict, list)):
            self.text = json.dumps(self._body)
        else:
            self.text = str(self._body)

    def json(self) -> Any:
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class FakeCorosHttp:
    """A scriptable stand-in for a ``requests.Session``.

    Hand it a dict mapping ``(METHOD, PATH)`` to either a single
    :class:`FakeResponse` or a list of them (consumed in order — useful for
    "first call returns 401, second call returns 200" 401-refresh scenarios).
    Every request is recorded in ``calls`` so tests can assert on the wire.
    """

    def __init__(
        self,
        responses: dict[tuple[str, str], FakeResponse | list[FakeResponse]] | None = None,
        *,
        raise_on: tuple[str, str] | None = None,
    ) -> None:
        self._responses: dict[tuple[str, str], list[FakeResponse]] = {}
        for key, value in (responses or {}).items():
            self._responses[key] = list(value) if isinstance(value, list) else [value]
        self._raise_on = raise_on
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        # Strip the base URL so tests can write keys like ("POST", "/account/login").
        path = url[len(COROS_BASE_URL):] if url.startswith(COROS_BASE_URL) else url
        self.calls.append(
            {
                "method": method,
                "url": url,
                "path": path,
                "headers": dict(kwargs.get("headers") or {}),
                "params": dict(kwargs.get("params") or {}),
                "json": kwargs.get("json"),
            }
        )
        key = (method, path)
        if self._raise_on == key:
            raise RuntimeError(f"network blew up on {method} {path}")
        queue = self._responses.get(key)
        if not queue:
            # Default-success body for any unscripted call -- keeps tests
            # focused on what they're actually asserting.
            return FakeResponse(status_code=200, body={"result": "0000", "data": {}})
        # Pop the first response unless it's the only one (then it repeats).
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)


def _login_response(*, access_token: str = "tok-1", user_id: str = "user-42") -> FakeResponse:
    return FakeResponse(
        status_code=200,
        body={"result": "0000", "data": {"accessToken": access_token, "userId": user_id}},
    )


def _dashboard_response(days: Iterable[str]) -> FakeResponse:
    """Build a /dashboard/query response with one HRV entry per ISO day."""
    items = [
        {
            "happenDay": int(d.replace("-", "")),
            "avgSleepHrv": 65,
            "sleepHrvBase": 60,
        }
        for d in days
    ]
    return FakeResponse(
        status_code=200,
        body={
            "result": "0000",
            "data": {"summaryInfo": {"sleepHrvData": {"sleepHrvList": items}}},
        },
    )


def _analyse_response(days: Iterable[str]) -> FakeResponse:
    """Build a /analyse/query response with one EvoLab entry per ISO day."""
    items = [
        {
            "happenDay": int(d.replace("-", "")),
            "vo2max": 56.4,
            "staminaLevel": 72,
            "trainingLoad": 350,
        }
        for d in days
    ]
    return FakeResponse(
        status_code=200,
        body={"result": "0000", "data": {"t7dayList": items}},
    )


def _day_detail_response(days: Iterable[str]) -> FakeResponse:
    """Build a /analyse/dayDetail/query response with one entry per ISO day."""
    items = [
        {
            "happenDay": int(d.replace("-", "")),
            "rhr": 48,
            "avgSleepHrv": 65,
            "sleepHrvBase": 60,
        }
        for d in days
    ]
    return FakeResponse(
        status_code=200,
        body={"result": "0000", "data": {"dayList": items}},
    )


def _raw(conn: sqlite3.Connection) -> RawWriter:
    return RawWriter(conn, SOURCE)


def _all_endpoints_seeded(
    days: list[str], *, login_first: bool = False
) -> dict[tuple[str, str], FakeResponse | list[FakeResponse]]:
    """Build a complete response map covering the three wellness GETs (+ optional login)."""
    resp: dict[tuple[str, str], FakeResponse | list[FakeResponse]] = {
        ("GET", PATH_DASHBOARD): _dashboard_response(days),
        ("GET", PATH_ANALYSE): _analyse_response(days),
        ("GET", PATH_DAY_DETAIL): _day_detail_response(days),
    }
    if login_first:
        resp[("POST", PATH_LOGIN)] = _login_response()
    return resp


def _seed_token(token_dir: Path, *, access_token: str = "tok-1", user_id: str = "user-42") -> None:
    """Pre-seed a persisted token bundle so the connector skips the login path."""
    store = CorosTokenStore(token_dir)
    store.save(access_token, user_id)


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------


def test_coros_connector_implements_connector_protocol(tmp_path: Path) -> None:
    """CorosConnector satisfies the same Connector protocol as Strava/Garmin."""
    connector = CorosConnector(tmp_path / "coros", http_client=FakeCorosHttp())
    assert isinstance(connector, Connector)
    assert hasattr(connector, "backfill")
    assert hasattr(connector, "sync")
    assert connector.source == SOURCE == "coros"


# ---------------------------------------------------------------------------
# 1. Login MD5-hashes the password before sending it on the wire
# ---------------------------------------------------------------------------


def test_login_md5_hashes_password(tmp_path: Path) -> None:
    """The login POST body carries MD5(password) -- the raw password NEVER goes on the wire."""
    raw_password = "s3cretPassword!"
    http = FakeCorosHttp({("POST", PATH_LOGIN): _login_response()})
    connector = CorosConnector(
        tmp_path / "coros",
        email="ross@example.com",
        password=raw_password,
        http_client=http,
    )
    # _login is the canonical entry; calling it directly is what `runos coros login` does.
    connector._login()

    login_calls = [c for c in http.calls if c["path"] == PATH_LOGIN]
    assert len(login_calls) == 1
    body = login_calls[0]["json"]
    expected_hash = hashlib.md5(raw_password.encode("utf-8")).hexdigest()
    assert body["account"] == "ross@example.com"
    assert body["accountType"] == 2
    assert body["pwd"] == expected_hash
    # Defence in depth: the raw password literally must not appear anywhere in the request.
    assert raw_password not in json.dumps(login_calls[0])


# ---------------------------------------------------------------------------
# 2. Login persists the token bundle atomically with mode 0600
# ---------------------------------------------------------------------------


def test_login_persists_token_atomically_mode_0600(tmp_path: Path) -> None:
    """After login the token file exists, is 0600, and decodes back to the saved bundle."""
    token_dir = tmp_path / "coros"
    http = FakeCorosHttp(
        {("POST", PATH_LOGIN): _login_response(access_token="tok-X", user_id="user-Y")}
    )
    connector = CorosConnector(
        token_dir, email="ross@example.com", password="pw", http_client=http
    )
    connector._login()

    token_file = token_dir / "token"
    assert token_file.exists()
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode == 0o600, f"expected token file 0600, got {oct(mode)}"
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved == {"access_token": "tok-X", "user_id": "user-Y"}
    # No stray temp files should be left behind from the atomic-write dance.
    leftovers = [p for p in token_dir.iterdir() if p.name.startswith(".token.")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# 3. Sync reuses a persisted token (no re-login on every call)
# ---------------------------------------------------------------------------


def test_sync_reuses_persisted_token(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A sync with a pre-seeded token must NOT POST to /account/login."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir, access_token="tok-seed", user_id="user-seed")
    days = ["2026-05-26", "2026-05-27", "2026-05-28"]
    http = FakeCorosHttp(_all_endpoints_seeded(days))
    connector = CorosConnector(
        token_dir, email="ross@example.com", password="pw", http_client=http
    )
    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))

    login_calls = [c for c in http.calls if c["path"] == PATH_LOGIN]
    assert login_calls == [], "sync must not perform a fresh login when a token is persisted"

    # The persisted token + user id surfaced in every authenticated request's headers.
    authed_calls = [c for c in http.calls if c["path"] != PATH_LOGIN]
    assert authed_calls
    for call in authed_calls:
        assert call["headers"].get("accessToken") == "tok-seed"
        assert "user-seed" in (call["headers"].get("yfheader") or "")


# ---------------------------------------------------------------------------
# 4. On a 401, the connector re-authenticates exactly ONCE and retries
# ---------------------------------------------------------------------------


def test_sync_refreshes_token_on_401_once(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A 401 triggers exactly one re-login + retry; the new token is persisted."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir, access_token="stale-token", user_id="user-old")
    days = ["2026-05-27", "2026-05-28"]

    # First /dashboard/query returns 401; after re-login the second call succeeds.
    http = FakeCorosHttp(
        {
            ("GET", PATH_DASHBOARD): [
                FakeResponse(status_code=401, body={"result": "0102"}),
                _dashboard_response(days),
            ],
            ("POST", PATH_LOGIN): _login_response(access_token="fresh-token", user_id="user-new"),
            ("GET", PATH_ANALYSE): _analyse_response(days),
            ("GET", PATH_DAY_DETAIL): _day_detail_response(days),
        }
    )
    connector = CorosConnector(
        token_dir, email="ross@example.com", password="pw", http_client=http
    )
    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))

    # Exactly ONE re-login attempt.
    login_calls = [c for c in http.calls if c["path"] == PATH_LOGIN]
    assert len(login_calls) == 1, "exactly one one-shot re-login expected"

    # The new token was persisted (so the next sync uses it without another login).
    store = CorosTokenStore(token_dir)
    assert store.load() == ("fresh-token", "user-new")

    # Post-refresh calls used the new token.
    dashboard_calls = [c for c in http.calls if c["path"] == PATH_DASHBOARD]
    assert len(dashboard_calls) == 2
    assert dashboard_calls[0]["headers"].get("accessToken") == "stale-token"
    assert dashboard_calls[1]["headers"].get("accessToken") == "fresh-token"


def test_sync_raises_auth_error_when_refresh_also_401s(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A second 401 after the one-shot refresh raises CorosAuthError (no busy loop)."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    http = FakeCorosHttp(
        {
            ("GET", PATH_DASHBOARD): [
                FakeResponse(status_code=401, body={"result": "0102"}),
                FakeResponse(status_code=401, body={"result": "0102"}),
            ],
            ("POST", PATH_LOGIN): _login_response(),
        }
    )
    connector = CorosConnector(
        token_dir, email="ross@example.com", password="pw", http_client=http
    )
    with pytest.raises(CorosAuthError, match="one-shot"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-28"))

    # NEVER more than one re-login attempt.
    login_calls = [c for c in http.calls if c["path"] == PATH_LOGIN]
    assert len(login_calls) == 1


# ---------------------------------------------------------------------------
# 5. No token + no credentials = immediate CorosAuthError (don't even try)
# ---------------------------------------------------------------------------


def test_sync_raises_auth_error_when_no_token_and_no_creds(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """With no persisted token AND no credentials, sync fails fast with CorosAuthError."""
    http = FakeCorosHttp()  # the request would never be made.
    connector = CorosConnector(tmp_path / "coros", http_client=http)
    with pytest.raises(CorosAuthError, match="run `runos coros login`"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-28"))
    # No requests at all -- not even login.
    assert http.calls == []


# ---------------------------------------------------------------------------
# 6. An unexpected HTTP failure surfaces as CorosSyncError (not auth, not raw)
# ---------------------------------------------------------------------------


def test_sync_raises_sync_error_on_unexpected_http_error(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A transport-level failure (or a non-200/non-401 status) becomes CorosSyncError."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    http = FakeCorosHttp(
        {("GET", PATH_DASHBOARD): FakeResponse(status_code=503, body={"result": "9999"})},
    )
    connector = CorosConnector(
        token_dir, email="ross@example.com", password="pw", http_client=http
    )
    with pytest.raises(CorosSyncError, match="503"):
        connector.sync(_raw(conn), since=date.fromisoformat("2026-05-28"))


# ---------------------------------------------------------------------------
# 7. The EvoLab endpoint writes to raw_response under EP_EVOLAB
# ---------------------------------------------------------------------------


def test_evolab_endpoint_writes_to_raw_response(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A successful sync stores /analyse/query rows under (coros, evolab_dashboard, <ISO day>)."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    days = ["2026-05-26", "2026-05-27", "2026-05-28"]
    http = FakeCorosHttp(_all_endpoints_seeded(days))
    connector = CorosConnector(token_dir, http_client=http)
    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))

    rows = conn.execute(
        "SELECT entity_key, payload FROM raw_response "
        "WHERE source=? AND endpoint=? ORDER BY entity_key",
        (SOURCE, EP_EVOLAB),
    ).fetchall()
    stored_keys = {r["entity_key"] for r in rows}
    assert stored_keys == set(days)
    # Verbatim raw: the stored payload retains the upstream field names.
    body = json.loads(rows[0]["payload"])
    assert "vo2max" in body
    assert body["vo2max"] == 56.4
    # Connector writes ONLY to raw_response -- never to wellness_day.
    assert conn.execute("SELECT COUNT(*) FROM wellness_day").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 8. Wellness endpoints write per-day keyed by ISO YYYY-MM-DD
# ---------------------------------------------------------------------------


def test_wellness_endpoints_write_per_day_keyed_iso_date(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Sleep / HRV / heart_rate rows are keyed by ISO YYYY-MM-DD (NOT YYYYMMDD)."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    days = ["2026-05-26", "2026-05-27", "2026-05-28"]
    http = FakeCorosHttp(_all_endpoints_seeded(days))
    connector = CorosConnector(token_dir, http_client=http)
    connector.sync(_raw(conn), since=date.fromisoformat(days[0]))

    for endpoint in (EP_SLEEP, EP_HRV, EP_HEART_RATE):
        keys = {
            r["entity_key"]
            for r in conn.execute(
                "SELECT entity_key FROM raw_response WHERE source=? AND endpoint=?",
                (SOURCE, endpoint),
            ).fetchall()
        }
        for d in days:
            assert d in keys, f"missing day {d!r} for endpoint {endpoint!r}"
        # No YYYYMMDD-shaped keys (would be a bucketing bug downstream).
        for k in keys:
            assert "-" in k, f"unexpected key shape {k!r} for endpoint {endpoint!r}"

    # The /analyse/dayDetail/query call used YYYYMMDD params on the wire.
    day_detail_calls = [c for c in http.calls if c["path"] == PATH_DAY_DETAIL]
    assert len(day_detail_calls) == 1
    params = day_detail_calls[0]["params"]
    # Sync without an explicit since defaults to today - SYNC_LOOKBACK_DAYS, but
    # this test passes since=days[0], so startDay should be 20260526.
    assert params == {"startDay": 20260526, "endDay": int(days[-1].replace("-", ""))} or (
        params["startDay"] == 20260526
    )


# ---------------------------------------------------------------------------
# 9. Backfill walks the default N-day window
# ---------------------------------------------------------------------------


def test_backfill_walks_n_days_default(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Backfill uses DEFAULT_BACKFILL_DAYS as the window size (today - N .. today)."""
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    http = FakeCorosHttp(_all_endpoints_seeded(["2026-05-28"]))
    connector = CorosConnector(token_dir, http_client=http, backfill_days=10)
    connector.backfill(_raw(conn))

    # The day-detail call covers the requested 10-day window.
    day_detail_calls = [c for c in http.calls if c["path"] == PATH_DAY_DETAIL]
    assert len(day_detail_calls) == 1
    params = day_detail_calls[0]["params"]
    start_day = date(
        int(str(params["startDay"])[:4]),
        int(str(params["startDay"])[4:6]),
        int(str(params["startDay"])[6:8]),
    )
    end_day = date(
        int(str(params["endDay"])[:4]),
        int(str(params["endDay"])[4:6]),
        int(str(params["endDay"])[6:8]),
    )
    assert (end_day - start_day) == timedelta(days=10)

    # Default factory constant is honoured (sanity check on the constant itself).
    assert DEFAULT_BACKFILL_DAYS == 60


# ---------------------------------------------------------------------------
# 10. Incremental sync overlaps the lookback window for revision safety
# ---------------------------------------------------------------------------


def test_sync_lookback_overlaps_for_revision_safety(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Sync without an explicit ``since`` re-pulls the last SYNC_LOOKBACK_DAYS days.

    This handles Coros's late-finalised days (HRV / stamina sometimes update
    hours after midnight) -- idempotent raw upserts make the overlap cheap.
    """
    token_dir = tmp_path / "coros"
    _seed_token(token_dir)
    # Seed a watermark "today" so sync's start = today - SYNC_LOOKBACK_DAYS.
    today = CorosConnector._today()
    from runos.sync import state as sync_state

    sync_state.mark_synced(conn, SOURCE, last_entity_ts=today.isoformat())
    days_in_window = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(SYNC_LOOKBACK_DAYS, -1, -1)
    ]
    http = FakeCorosHttp(_all_endpoints_seeded(days_in_window))
    connector = CorosConnector(token_dir, http_client=http)
    connector.sync(_raw(conn), since=None)

    # The day-detail call window starts at today - SYNC_LOOKBACK_DAYS.
    day_detail_calls = [c for c in http.calls if c["path"] == PATH_DAY_DETAIL]
    assert len(day_detail_calls) == 1
    params = day_detail_calls[0]["params"]
    start_day_str = str(params["startDay"])
    start_day = date(
        int(start_day_str[:4]), int(start_day_str[4:6]), int(start_day_str[6:8])
    )
    assert start_day == today - timedelta(days=SYNC_LOOKBACK_DAYS)
    # And the constant itself is stable (downstream waves rely on this value).
    assert SYNC_LOOKBACK_DAYS == 3
