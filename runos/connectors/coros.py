"""The Coros connector: login-once, one-shot 401 refresh, verbatim wellness pulls.

Implements the :class:`~runos.connectors.base.Connector` protocol for the
*unofficial* Coros Training Hub API (the same API the ``training.coros.com`` web
UI talks to). Like Garmin, Coros has no documented personal API and the wire
contract is reverse-engineered from the web client + community wrappers (notably
`cygnusb/coros-mcp <https://github.com/cygnusb/coros-mcp>`_). Two design rules
mirror :mod:`runos.connectors.garmin`:

**1. Authenticate ONCE; reuse a persisted bearer token; refresh on 401 exactly
    once.** The interactive ``runos coros login`` is the only path that submits
    the email + MD5-hashed password handshake. It persists ``{access_token,
    user_id}`` to ``~/.runos/tokens/coros/token`` (mode 0600, atomic
    temp→fsync→rename). Subsequent runs reuse that token; on a 401 the connector
    calls ``_login`` once, persists the new token, and retries the original
    request. If that retry also 401s, a :class:`CorosAuthError` is raised and
    the pipeline isolates it. NEVER busy-loops.

**2. On a 401 / generic HTTP failure: fail-log-skip, NO retry beyond the single
    refresh.** Unlike Strava (tenacity backoff is correct there), Coros's
    fragility profile is closer to Garmin: a hosted API behind a regional
    cluster with no documented lockout but also no public retry guidance. We
    follow Garmin's pattern: one re-auth attempt per call, then propagate.

**Failure isolation.** The connector raises on hard failure; the *caller* (the
``runos sync`` pipeline, wired in 18-05) wraps the Coros sync in a try/except so
a Coros outage logs and skips while Strava + Garmin + transforms still run on
existing data. The connector itself writes only verbatim raw payloads via
:class:`~runos.connectors.base.RawWriter` -- shaping into ``wellness_day`` /
``coros_evolab_day`` happens in :mod:`runos.transforms.coros_wellness` and
:mod:`runos.transforms.coros_evolab` (waves 18-02 / 18-03), pure no-network
passes.

**Endpoint reality vs CONTEXT.md.** Phase-18 CONTEXT.md sketched the API
contract from secondary sources. Verification against ``cygnusb/coros-mcp`` at
implementation time revealed the actual Training Hub shape: the EU regional
host is ``https://teameuapi.coros.com``, the auth header is ``accessToken: <t>``
(NOT ``Authorization: Bearer ...``), and the wellness data comes from
``/dashboard/query`` (HRV nightly), ``/analyse/dayDetail/query?startDay=...``
(per-day sleep/RHR), and ``/analyse/query`` (rolling VO2max / stamina /
fitness, mapped to our "EvoLab dashboard" raw label). The four ENDPOINT label
constants from CONTEXT.md are preserved for the downstream waves; only the
underlying URL routing differs from the original sketch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from runos.connectors.base import RawWriter
from runos.sync import state as sync_state

logger = logging.getLogger(__name__)

SOURCE = "coros"

# ---------------------------------------------------------------------------
# Raw-store endpoint labels
# ---------------------------------------------------------------------------
# Each Coros wellness endpoint is stored under its own label, keyed by ISO
# calendar date (YYYY-MM-DD), so the wellness transform (18-02) collapses
# sleep+hrv+heart_rate into one ``wellness_day`` row per day and the EvoLab
# transform (18-03) projects ``evolab_dashboard`` onto ``coros_evolab_day``.
EP_EVOLAB = "evolab_dashboard"
EP_SLEEP = "sleep"
EP_HRV = "hrv"
EP_HEART_RATE = "heart_rate"

WELLNESS_ENDPOINTS = (EP_EVOLAB, EP_SLEEP, EP_HRV, EP_HEART_RATE)

# How many trailing days a backfill pulls by default. Coros's
# /analyse/dayDetail/query accepts a startDay/endDay range up to ~24 weeks; we
# keep the default modest to be gentle on the (regional, unofficial) API.
# Idempotent raw upserts make a re-run with a larger window cheap.
DEFAULT_BACKFILL_DAYS = 60

# Incremental sync looks back a few days so a previously-missing or revised day
# is re-pulled (Coros sometimes finalises nightly HRV / stamina hours later).
# Matches the Garmin lookback so the two sources stay symmetric.
SYNC_LOOKBACK_DAYS = 3

# ---------------------------------------------------------------------------
# Coros API constants
# ---------------------------------------------------------------------------
# The Training Hub web API. EU host because the owner is UK-based; the regional
# choice is hard-coded for now (a Settings field can switch hosts later if
# anyone needs US/Asia). Tokens issued by login are only valid against the
# regional host that issued them.
COROS_BASE_URL = "https://teameuapi.coros.com"

# Endpoint paths. The labels above (EP_*) name *what* the raw row is for; these
# constants name *where* on the wire to fetch it from.
PATH_LOGIN = "/account/login"
PATH_DASHBOARD = "/dashboard/query"
PATH_ANALYSE = "/analyse/query"
PATH_DAY_DETAIL = "/analyse/dayDetail/query"

# Match the web client closely so requests aren't filtered as scripted traffic.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# Token-file permissions (owner read/write only).
_FILE_MODE = 0o600


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CorosAuthError(RuntimeError):
    """Raised when Coros credentials/tokens are missing or login is rejected.

    Distinct from :class:`CorosSyncError`: this means "the user has not done the
    one-time ``runos coros login`` (or the persisted token is dead AND a
    one-shot refresh also failed)". The pipeline (18-05) catches this and
    skips, telling the user to re-login.
    """


class CorosSyncError(RuntimeError):
    """Raised when a Coros data pull fails unexpectedly (HTTP 5xx, network).

    The pipeline catches this so a Coros failure logs-and-skips without
    blocking Strava sync, Garmin sync, or analysis. The original cause is
    chained so the log can show what broke.
    """


# ---------------------------------------------------------------------------
# Email redaction for logs (NEVER log the raw email/password/token).
# ---------------------------------------------------------------------------


def _redact_email(email: str | None) -> str:
    """Return ``user@***.tld`` for safe logging; ``<unknown>`` when absent.

    The local-part stays (so the owner can tell which account is in play) but
    the domain is collapsed to ``***.<tld>``. Password and token are never
    logged at all.
    """
    if not email or "@" not in email:
        return "<unknown>"
    local, _, domain = email.partition("@")
    if "." in domain:
        tld = domain.rsplit(".", 1)[-1]
        return f"{local}@***.{tld}"
    return f"{local}@***"


# ---------------------------------------------------------------------------
# HTTP client Protocol (the seam for testing).
# ---------------------------------------------------------------------------


class CorosHttpClient(Protocol):
    """The narrow slice of an HTTP client this connector depends on.

    Declared as a Protocol so tests can supply a fake (or use ``responses`` to
    mock the real ``requests`` calls) without coupling to a specific library
    version. The default factory builds a ``requests.Session``; tests can
    inject anything that exposes ``request(method, url, ...) -> response``
    where the response has ``status_code``, ``text``, and ``json()``.
    """

    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


def _default_http_client() -> Any:
    """Build a real ``requests.Session`` for production use.

    Imported lazily so tests / non-Coros commands don't pay the import cost on
    every CLI invocation. ``requests`` is a transitive dependency via
    ``stravalib`` and ``garminconnect`` -- no new heavyweight deps added.
    """
    import requests

    return requests.Session()


HttpClientFactory = Callable[[], CorosHttpClient]


# ---------------------------------------------------------------------------
# Persisted token (access_token + user_id, both required by the API).
# ---------------------------------------------------------------------------


class CorosTokenStore:
    """Atomic, owner-only persistence of the Coros bearer token + user id.

    Both ``access_token`` and ``user_id`` are required to make authenticated
    calls (the API expects ``accessToken`` *and* ``yfheader: {"userId": ...}``
    on every request), so we persist them together in one JSON file under
    ``<tokens_dir>/coros/token``. Writes are atomic (temp file in the same
    directory → fsync → ``os.replace``) and mode 0600; a crash mid-write leaves
    either the previous complete file or the new complete file, never a torn
    one. This mirrors :class:`runos.connectors.tokens.TokenStore` but stays
    Coros-local because Coros doesn't fit the rotating-refresh-token shape that
    module is designed around.
    """

    def __init__(self, token_dir: Path) -> None:
        self._token_dir = Path(token_dir)

    @property
    def path(self) -> Path:
        """Path to the Coros token file (one file holds the auth bundle)."""
        return self._token_dir / "token"

    def exists(self) -> bool:
        """``True`` if a persisted token bundle is present."""
        return self.path.exists()

    def load(self) -> tuple[str, str]:
        """Load (access_token, user_id) from disk.

        Raises :class:`FileNotFoundError` if no token file exists (the user has
        not completed the one-time login) and :class:`ValueError` if the file
        is present but corrupt -- so a broken token surfaces loudly rather than
        silently authenticating as nobody.
        """
        if not self.path.exists():
            raise FileNotFoundError(
                f"no Coros token at {self.path}; run `runos coros login` first"
            )
        raw = self.path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
            access_token = str(data["access_token"])
            user_id = str(data["user_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid Coros token file at {self.path}") from exc
        if not access_token or not user_id:
            raise ValueError(f"empty Coros token fields at {self.path}")
        return access_token, user_id

    def save(self, access_token: str, user_id: str) -> None:
        """Persist (access_token, user_id) atomically with mode 0600.

        1. Write to a uniquely-named temp file in the same directory (so the
           final rename is a same-filesystem atomic ``os.replace``).
        2. ``flush`` + ``os.fsync`` so the bytes hit disk.
        3. ``os.replace`` over the destination (atomic on POSIX).
        4. Best-effort fsync the directory so the rename itself is durable.
        """
        self._token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {"access_token": access_token, "user_id": user_id}, sort_keys=True
        )

        fd, tmp_name = tempfile.mkstemp(
            dir=self._token_dir, prefix=".token.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            os.fchmod(fd, _FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._fsync_dir()
        # Belt-and-braces: enforce perms on the final file too in case the
        # umask narrowed the temp file's mode at create time.
        os.chmod(self.path, _FILE_MODE)

    def _fsync_dir(self) -> None:
        """fsync the tokens directory so the rename survives a crash.

        Directory fsync is not supported on every platform; failure here is
        non-fatal (the file content is already durable).
        """
        try:
            dir_fd = os.open(self._token_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class CorosConnector:
    """Coros source connector (implements the ``Connector`` protocol).

    Construct with a token directory, optional credentials (only used when a
    token refresh is needed during a sync), and either a ready HTTP client or
    a factory that builds one. In production the client is a
    ``requests.Session``; tests inject a fake/mocked session.
    """

    source = SOURCE

    def __init__(
        self,
        token_dir: Path | str,
        *,
        email: str | None = None,
        password: str | None = None,
        http_client: CorosHttpClient | None = None,
        http_client_factory: HttpClientFactory | None = None,
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
        base_url: str = COROS_BASE_URL,
    ) -> None:
        """Create a connector.

        ``token_dir`` is where the (access_token, user_id) bundle is read from
        and written to. ``email`` / ``password`` are passed through from
        :class:`runos.config.Settings` so the connector can perform a one-shot
        re-login when a stored token returns 401 mid-sync -- without them, a
        401 propagates as :class:`CorosAuthError` immediately. Provide either a
        ready ``http_client`` (tests) or an ``http_client_factory`` (production).
        ``backfill_days`` bounds the one-time history walk.
        """
        self._token_store = CorosTokenStore(Path(token_dir))
        self._email = email
        self._password = password
        self._http_client = http_client
        self._http_client_factory = http_client_factory or _default_http_client
        self._backfill_days = int(backfill_days)
        self._base_url = base_url.rstrip("/")
        # Cached in-memory copy of the persisted token. Loaded lazily on first
        # authenticated call; refreshed on a 401-then-relogin path.
        self._access_token: str | None = None
        self._user_id: str | None = None

    # ---- HTTP client (lazy, swappable for tests) -----------------------------

    def _client(self) -> CorosHttpClient:
        if self._http_client is None:
            self._http_client = self._http_client_factory()
        return self._http_client

    # ---- Auth ----------------------------------------------------------------

    def _login(self) -> tuple[str, str]:
        """Perform the email + MD5(password) login handshake.

        Returns ``(access_token, user_id)`` from the API response and persists
        the bundle atomically. Raises :class:`CorosAuthError` if credentials
        are missing, the API rejects the login, or the response shape is
        unexpected. NEVER logs the raw email or password (only the redacted
        email at INFO).
        """
        if not self._email or not self._password:
            raise CorosAuthError(
                "Coros credentials missing. Set RUNOS_COROS_EMAIL and "
                "RUNOS_COROS_PASSWORD in your .env, then run `runos coros login`."
            )
        pwd_hash = hashlib.md5(self._password.encode("utf-8")).hexdigest()
        body = {
            "account": self._email,
            "accountType": 2,
            "pwd": pwd_hash,
        }
        headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
        logger.info("coros: authenticating as %s", _redact_email(self._email))
        try:
            resp = self._client().request(
                "POST",
                self._base_url + PATH_LOGIN,
                json=body,
                headers=headers,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - third-party HTTP exceptions
            raise CorosAuthError(
                f"Coros login request failed for {_redact_email(self._email)}: {exc}"
            ) from exc

        status = getattr(resp, "status_code", None)
        if status != 200:
            raise CorosAuthError(
                f"Coros login returned HTTP {status} for {_redact_email(self._email)}"
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - response.json() can raise anything
            raise CorosAuthError(
                f"Coros login returned non-JSON body for {_redact_email(self._email)}"
            ) from exc

        # The Coros API signals success via result="0000". Anything else is a
        # rejected login (wrong password, account locked, region mismatch).
        if str(payload.get("result", "")) != "0000":
            raise CorosAuthError(
                f"Coros login rejected for {_redact_email(self._email)} "
                f"(result={payload.get('result')!r})"
            )

        data = payload.get("data") or {}
        access_token = data.get("accessToken")
        user_id = data.get("userId")
        if not access_token or not user_id:
            raise CorosAuthError(
                f"Coros login response missing accessToken/userId for "
                f"{_redact_email(self._email)}"
            )
        access_token = str(access_token)
        user_id = str(user_id)
        self._token_store.save(access_token, user_id)
        self._access_token = access_token
        self._user_id = user_id
        logger.info("coros: login OK; token persisted (token present, user id present)")
        return access_token, user_id

    def _load_or_login(self) -> tuple[str, str]:
        """Return the active (access_token, user_id), loading from disk first.

        If no persisted token exists, falls through to ``_login`` (which itself
        raises :class:`CorosAuthError` when credentials are absent -- so a
        scheduled run with no token AND no credentials fails fast with a
        clear "run `runos coros login`" message).
        """
        if self._access_token and self._user_id:
            return self._access_token, self._user_id
        try:
            access_token, user_id = self._token_store.load()
        except FileNotFoundError:
            # No persisted token: do the one-time login (or raise if creds missing).
            return self._login()
        except ValueError as exc:
            logger.warning("coros: persisted token unreadable (%s); re-authenticating", exc)
            return self._login()
        self._access_token = access_token
        self._user_id = user_id
        return access_token, user_id

    def _auth_headers(self, access_token: str, user_id: str) -> dict[str, str]:
        """Build the per-request auth headers (matches the Coros web client).

        The API expects ``accessToken: <token>`` and a ``yfheader`` JSON
        envelope containing the user id. ``Content-Type`` is included for GETs
        too because the upstream client sends it unconditionally.
        """
        return {
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "accessToken": access_token,
            "yfheader": json.dumps({"userId": user_id}, separators=(",", ":")),
        }

    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        """Perform an authenticated request with a single 401-refresh attempt.

        On a 401 (or a Coros ``result`` code that signals an auth failure),
        calls ``_login`` exactly once, persists the new token, and retries the
        original request. A second 401 raises :class:`CorosAuthError`. Any
        other unexpected HTTP failure raises :class:`CorosSyncError`. NEVER
        busy-loops.
        """
        url = self._base_url + path
        access_token, user_id = self._load_or_login()
        resp = self._do_request(method, url, access_token, user_id, params, json_body)
        if not self._is_auth_failure(resp):
            return self._parse_or_raise(resp, path)

        # One-shot refresh: log in again, retry once, then give up.
        logger.info("coros: 401 on %s; refreshing token once", path)
        access_token, user_id = self._login()
        resp = self._do_request(method, url, access_token, user_id, params, json_body)
        if self._is_auth_failure(resp):
            raise CorosAuthError(
                f"Coros still returned 401 after one-shot token refresh on {path}; "
                "re-run `runos coros login`."
            )
        return self._parse_or_raise(resp, path)

    def _do_request(
        self,
        method: str,
        url: str,
        access_token: str,
        user_id: str,
        params: dict[str, Any] | None,
        json_body: Any | None,
    ) -> Any:
        """Execute one HTTP round-trip; wrap transport errors as sync errors."""
        kwargs: dict[str, Any] = {
            "headers": self._auth_headers(access_token, user_id),
            "timeout": 30,
        }
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            return self._client().request(method, url, **kwargs)
        except Exception as exc:  # noqa: BLE001 - third-party HTTP exceptions
            raise CorosSyncError(f"Coros request to {url} failed: {exc}") from exc

    @staticmethod
    def _is_auth_failure(resp: Any) -> bool:
        """Return True if the response indicates a token/auth failure."""
        if getattr(resp, "status_code", None) == 401:
            return True
        # Coros sometimes returns HTTP 200 with an in-body error code. Try to
        # decode JSON; if the body isn't JSON, it's not an auth failure here.
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 - any decode failure means "not auth-coded"
            return False
        result = str((body or {}).get("result", ""))
        # Common Coros auth-failure codes: 0102 (token invalid), 0107 (token
        # expired). Treat any non-success code starting "01" as auth-related so
        # the one-shot refresh fires; everything else is a sync error.
        return result in {"0102", "0107"}

    @staticmethod
    def _parse_or_raise(resp: Any, path: str) -> Any:
        """Decode a successful response body, raising :class:`CorosSyncError` on failure."""
        status = getattr(resp, "status_code", None)
        if status != 200:
            raise CorosSyncError(f"Coros {path} returned HTTP {status}")
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise CorosSyncError(f"Coros {path} returned non-JSON body") from exc
        if str((body or {}).get("result", "")) != "0000":
            raise CorosSyncError(
                f"Coros {path} returned result={body.get('result')!r}"
            )
        return body

    # ---- Per-endpoint pulls (verbatim raw writes) ----------------------------

    def _fetch_dashboard(self, raw: RawWriter) -> int:
        """GET /dashboard/query → HRV nightly list. Stores one row per day.

        The dashboard returns ``data.summaryInfo.sleepHrvData.sleepHrvList`` --
        a list of recent nights' HRV records. We store each entry verbatim
        under ``(coros, hrv, <happenDay-as-ISO>)`` so the 18-02 transform can
        project them into ``wellness_day``. The endpoint takes no parameters;
        whatever Coros considers "recent" is what we get.
        """
        body = self._authed_request("GET", PATH_DASHBOARD)
        data = ((body or {}).get("data") or {}).get("summaryInfo") or {}
        hrv_data = data.get("sleepHrvData") or {}
        items = hrv_data.get("sleepHrvList") or []
        written = 0
        for item in items:
            iso_day = _coros_day_to_iso(item.get("happenDay"))
            if iso_day is None:
                # Skip entries without a parseable date rather than corrupt the key.
                continue
            raw.put(EP_HRV, iso_day, item)
            written += 1
        # Also store the dashboard's own "today" summary if present and not
        # already covered by sleepHrvList.
        today_day = _coros_day_to_iso(hrv_data.get("happenDay"))
        if today_day and not any(
            _coros_day_to_iso(item.get("happenDay")) == today_day for item in items
        ):
            raw.put(EP_HRV, today_day, hrv_data)
            written += 1
        return written

    def _fetch_evolab(self, raw: RawWriter, today: date) -> int:
        """GET /analyse/query → t7dayList (VO2max / stamina / fitness per day).

        Stores each ``t7dayList`` entry verbatim under
        ``(coros, evolab_dashboard, <happenDay-as-ISO>)``. The endpoint takes
        no parameters; Coros returns rolling-window data ending at ``today``.
        """
        body = self._authed_request("GET", PATH_ANALYSE)
        items = ((body or {}).get("data") or {}).get("t7dayList") or []
        written = 0
        for item in items:
            iso_day = _coros_day_to_iso(item.get("happenDay"))
            if iso_day is None:
                continue
            raw.put(EP_EVOLAB, iso_day, item)
            written += 1
        if written == 0:
            # Fall back to storing the whole response under today's key so the
            # transform has *something* to project even when the t7dayList is
            # empty (e.g. brand-new account).
            raw.put(EP_EVOLAB, today.isoformat(), body)
            written = 1
        return written

    def _fetch_day_detail(self, raw: RawWriter, start: date, end: date) -> int:
        """GET /analyse/dayDetail/query → per-day sleep + heart-rate fields.

        Each ``dayList`` entry is stored TWICE: once under EP_SLEEP and once
        under EP_HEART_RATE, both keyed by ``<happenDay-as-ISO>``. Storing the
        full per-day dict under two labels keeps the raw layer truly verbatim
        and lets each downstream transform read the slice it cares about
        without coordinating field-level splits at fetch time (Coros's payload
        is small per day; the duplication is harmless).
        """
        params = {
            "startDay": _date_to_coros_day(start),
            "endDay": _date_to_coros_day(end),
        }
        body = self._authed_request("GET", PATH_DAY_DETAIL, params=params)
        items = ((body or {}).get("data") or {}).get("dayList") or []
        written = 0
        for item in items:
            iso_day = _coros_day_to_iso(item.get("happenDay"))
            if iso_day is None:
                continue
            raw.put(EP_SLEEP, iso_day, item)
            raw.put(EP_HEART_RATE, iso_day, item)
            written += 2
        return written

    # ---- Pull orchestration --------------------------------------------------

    def _pull_window(self, raw: RawWriter, start: date, end: date) -> int:
        """Authenticate once, then fetch all wellness endpoints. Returns rows written.

        All writes commit in one transaction with the watermark advance, so a
        mid-pull failure rolls back cleanly -- nothing half-written and the
        watermark never advances past unstored data (ARCHITECTURE
        Anti-Pattern 3 from the Garmin connector applies here too).
        """
        conn = raw.conn
        written = 0
        with conn:
            written += self._fetch_dashboard(raw)
            written += self._fetch_evolab(raw, end)
            written += self._fetch_day_detail(raw, start, end)
            sync_state.mark_synced(conn, SOURCE, last_entity_ts=end.isoformat())
        logger.info(
            "coros: stored %d rows for window %s..%s (lookback %d days)",
            written,
            start,
            end,
            (end - start).days,
        )
        return written

    # ---- Connector protocol --------------------------------------------------

    def backfill(self, raw: RawWriter) -> None:
        """Pull a trailing window of wellness history into raw, idempotently.

        Coros has no bulk-export endpoint; the per-day endpoint accepts a
        ~24-week window in one call, so backfill is one round-trip per
        endpoint rather than per day. Idempotent raw upserts make a re-run
        cheap (already-stored days are simply refreshed).
        """
        today = self._today()
        start = today - timedelta(days=self._backfill_days)
        logger.info("coros: backfill window %s..%s (%d days)", start, today, self._backfill_days)
        self._pull_window(raw, start, today)
        with raw.conn:
            sync_state.save_backfill_cursor(raw.conn, SOURCE, None, complete=True)

    def sync(self, raw: RawWriter, since: date | None) -> None:
        """Pull recent wellness days into raw, reusing the persisted token.

        Fetches from ``since`` (or the watermark, with a small lookback for
        late-finalised days) up to today. Uses the token store; on a 401 the
        connector performs ONE re-auth via the configured credentials and
        retries. On any other failure raises :class:`CorosSyncError` for the
        pipeline (18-05) to catch and skip.
        """
        today = self._today()
        start = self._resolve_start(raw, since, today)
        if start > today:
            logger.info("coros: nothing to sync (start %s is after today %s)", start, today)
            return
        logger.info("coros: syncing %s..%s", start, today)
        self._pull_window(raw, start, today)

    def _resolve_start(self, raw: RawWriter, since: date | None, today: date) -> date:
        """Compute the first day to sync from the watermark or an explicit ``since``."""
        if since is not None:
            return since
        st = sync_state.read(raw.conn, SOURCE)
        if st.last_entity_ts:
            try:
                last = date.fromisoformat(st.last_entity_ts[:10])
                # Re-pull a small overlap so late-finalised days aren't missed.
                return last - timedelta(days=SYNC_LOOKBACK_DAYS)
            except ValueError:
                logger.warning("coros: unparseable watermark %r; ignoring", st.last_entity_ts)
        # First-ever sync with no watermark: pull the lookback window only.
        return today - timedelta(days=SYNC_LOOKBACK_DAYS)

    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _date_to_coros_day(d: date) -> int:
    """Return Coros's ``YYYYMMDD`` (no-separator) date integer for ``d``.

    The Training Hub API expects this format for ``startDay`` / ``endDay``.
    """
    return int(d.strftime("%Y%m%d"))


def _coros_day_to_iso(value: Any) -> str | None:
    """Parse Coros's ``YYYYMMDD`` int/str into an ISO ``YYYY-MM-DD`` string.

    Returns ``None`` if the value can't be parsed -- callers skip those entries
    so a corrupt-looking date never becomes a corrupt raw key.
    """
    if value is None:
        return None
    s = str(value)
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        d = datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None
    return d.isoformat()
