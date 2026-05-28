"""The Garmin connector: login-once, no-retry-on-429, verbatim wellness pulls.

Implements the :class:`~tempo.connectors.base.Connector` protocol for Garmin
Connect via the *unofficial* ``garminconnect`` library. Garmin is the single most
fragile dependency in Tempo (no official personal API; reverse-engineered SSO
behind Cloudflare; the ``garth`` foundation was deprecated 2026-03-27 when Garmin
changed auth). Two design rules dominate this module
(see ``.planning/research/PITFALLS.md`` Pitfalls 2, 3 and ARCHITECTURE
Anti-Pattern 5):

**1. Authenticate ONCE; reuse persisted tokens; NEVER log in on a scheduled run.**
The interactive ``tempo garmin login`` is the *only* path that submits the
email/password (and an MFA code via a prompt callback). It persists Garmin's DI
OAuth tokens to a token directory (``garminconnect`` writes them there, mode
0600) which subsequent runs reuse. :meth:`GarminConnector.sync` /
:meth:`GarminConnector.backfill` load *only* from that token store and refuse to
fall back to credentials -- so the daily job can never trigger a fresh SSO login,
which is what trips Garmin's per-account 429 lockout.

**2. On a 429 / auth failure: fail-log-skip, NO retry, NO backoff loop.**
Unlike Strava (where tenacity backoff on a 429 is correct), a Garmin 429 is an
*account-level* throttle that retries only deepen, blocking the real Garmin
Connect app for 48h+. So a 429 (or any auth/library error) during a sync is
caught, logged, and surfaced as a :class:`GarminSyncError` -- never retried.

**Failure isolation (the headline requirement, GRMN-01/03).** The connector
itself raises on failure, but the *caller* (the ``tempo sync`` pipeline, see
:func:`tempo.sync.pipeline.run_garmin_sync`) wraps Garmin in a try/except so a
Garmin outage logs and skips while Strava sync + transforms + analysis still run
on existing data. The connector never writes anything but verbatim raw payloads
(GRMN-04; ARCHITECTURE Anti-Pattern 1) -- shaping into ``wellness_day`` happens in
:mod:`tempo.transforms.wellness`, a pure no-network pass.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from tempo.connectors.base import RawWriter
from tempo.sync import state as sync_state

logger = logging.getLogger(__name__)

SOURCE = "garmin"

# Raw-store endpoint labels (the ``endpoint`` column in raw_response). Each Garmin
# daily endpoint is stored under its own label, keyed by the ISO calendar date, so
# one transform later collapses sleep+hrv+stats into one wellness_day row.
EP_SLEEP = "sleep"
EP_HRV = "hrv"
EP_STATS = "stats"

# All wellness endpoints we pull per day. Sleep/HRV/stats together cover the
# Phase-6 metrics: HRV, sleep (score/duration/stages), resting HR, body battery,
# stress, steps (the daily user-summary "stats" payload carries RHR / steps /
# stress / body battery; sleep carries duration+stages+score; hrv carries the
# overnight HRV average + status).
WELLNESS_ENDPOINTS = (EP_SLEEP, EP_HRV, EP_STATS)

# How many trailing days a backfill pulls by default. Garmin has no bulk export;
# we walk day-by-day. A modest default keeps the first pull gentle on the
# (Cloudflare-fronted, rate-limited) endpoints; raise it deliberately if you want
# more history. Idempotent raw upserts make re-runs cheap.
DEFAULT_BACKFILL_DAYS = 60

# Incremental sync looks back a few days so a previously-missing or revised day
# (Garmin sometimes finalises sleep/HRV hours later) is re-pulled. Idempotent
# upserts make the small overlap harmless (ARCHITECTURE Anti-Pattern 3).
SYNC_LOOKBACK_DAYS = 3


class GarminAuthError(RuntimeError):
    """Raised when Garmin credentials/tokens are missing or login cannot proceed.

    Distinct from :class:`GarminSyncError`: this means "the user has not done the
    one-time ``tempo garmin login``" (no persisted tokens, or login rejected),
    whereas a sync error is a runtime failure of an already-authenticated client.
    """


class GarminSyncError(RuntimeError):
    """Raised when a Garmin data pull fails (429, auth break, library exception).

    The pipeline catches this so a Garmin failure logs-and-skips without blocking
    Strava sync or analysis (GRMN-03; ARCHITECTURE Anti-Pattern 5). The original
    cause is chained so the log can show what broke.
    """


class GarminClient(Protocol):
    """The narrow slice of ``garminconnect.Garmin`` this connector depends on.

    Declared as a Protocol so tests can supply a fake (``tests/garmin_fakes.py``)
    with zero network and no real credentials, and so the connector is decoupled
    from the exact library version (the library is fragile; this is the seam).
    """

    def login(self, tokenstore: str | None = ...) -> Any: ...
    def get_sleep_data(self, cdate: str) -> Any: ...
    def get_hrv_data(self, cdate: str) -> Any: ...
    def get_stats(self, cdate: str) -> Any: ...


# A factory that builds a *credentialed* client for the interactive login. Kept
# injectable so login tests never import the real library or hit the network.
ClientFactory = Callable[..., GarminClient]


def _is_rate_limited(exc: BaseException) -> bool:
    """Return True if an exception represents a Garmin 429 / too-many-requests.

    We match the library's typed exception when available and fall back to a
    string check, so the no-retry policy holds even if the library reshapes its
    exception hierarchy (it is unofficial and changes).
    """
    name = type(exc).__name__
    if name == "GarminConnectTooManyRequestsError":
        return True
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


class GarminConnector:
    """Garmin source connector (implements the ``Connector`` protocol).

    Construct with a token directory and a way to obtain an *authenticated*
    client. In production the client is built lazily from persisted tokens via the
    library's ``login(tokenstore=...)`` (which loads tokens and never submits
    credentials when ``email``/``password`` are absent). Tests inject a fake
    client directly so no network/credentials are needed.
    """

    source = SOURCE

    def __init__(
        self,
        token_dir: str,
        *,
        client: GarminClient | None = None,
        client_factory: ClientFactory | None = None,
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
    ) -> None:
        """Create a connector.

        ``token_dir`` is the directory ``garminconnect`` reads/writes DI tokens
        to (set as ``GARMINTOKENS`` / passed to ``login``). Provide either a
        ready ``client`` (tests) or a ``client_factory`` that returns an
        authenticated client given the token dir (production). ``backfill_days``
        bounds the one-time history walk.
        """
        self._token_dir = token_dir
        self._client = client
        self._client_factory = client_factory
        self._backfill_days = int(backfill_days)

    # ---- Authenticated client (token reuse only; NO credential login) --------

    def _authenticated_client(self) -> GarminClient:
        """Return an authenticated client, loading ONLY from persisted tokens.

        This is the no-fresh-login guarantee for the scheduled path (GRMN-02): it
        builds a client with no credentials and logs in via the token store, so
        the library cannot fall through to an SSO credential login. A missing or
        dead token store surfaces as :class:`GarminAuthError` telling the user to
        run ``tempo garmin login`` -- it never retries or prompts.
        """
        if self._client is not None:
            return self._client
        if self._client_factory is None:
            raise GarminAuthError(
                "Garmin connector has no client; provide a client or client_factory."
            )
        # Build a token-only client (no email/password). The factory is expected
        # to construct garminconnect.Garmin() with no credentials.
        client = self._client_factory()
        try:
            client.login(self._token_dir)
        except FileNotFoundError as exc:
            raise GarminAuthError(
                "No persisted Garmin tokens found. Run `tempo garmin login` once first."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - library is unofficial/fragile
            if _is_rate_limited(exc):
                # A 429 even on a token-reuse path: do NOT retry (PITFALLS 2).
                raise GarminSyncError(
                    "Garmin returned 429 (too many requests) during token login; "
                    "skipping without retry to avoid an account lockout."
                ) from exc
            raise GarminAuthError(
                "Garmin token login failed; the session may be expired. "
                "Run `tempo garmin login` to re-authenticate."
            ) from exc
        self._client = client
        return client

    # ---- Per-day verbatim fetch ---------------------------------------------

    def _fetch_day(self, raw: RawWriter, client: GarminClient, day: str) -> int:
        """Fetch + store all wellness endpoints for one ISO ``day``. Returns rows written.

        Each endpoint is stored verbatim under ``(garmin, <endpoint>, <day>)``. A
        ``None`` / empty payload (Garmin had no data that day) is skipped rather
        than stored, so transforms never see an empty wellness row. A 429 from any
        endpoint aborts immediately with NO retry (PITFALLS 2).
        """
        calls = (
            (EP_SLEEP, client.get_sleep_data),
            (EP_HRV, client.get_hrv_data),
            (EP_STATS, client.get_stats),
        )
        written = 0
        for endpoint, fn in calls:
            try:
                payload = fn(day)
            except Exception as exc:  # noqa: BLE001 - fragile library
                if _is_rate_limited(exc):
                    raise GarminSyncError(
                        f"Garmin 429 fetching {endpoint} for {day}; "
                        "fail-log-skip without retry (no backoff loop)."
                    ) from exc
                raise GarminSyncError(f"Garmin error fetching {endpoint} for {day}: {exc}") from exc
            if payload is None or payload == {} or payload == []:
                continue
            raw.put(endpoint, day, payload)
            written += 1
        return written

    def _pull_days(self, raw: RawWriter, days: list[str]) -> int:
        """Authenticate once, then fetch each day verbatim. Returns total rows written.

        Days are committed in one transaction with the watermark advance so a
        partial failure never advances the watermark past unstored data
        (ARCHITECTURE Anti-Pattern 3). Because the whole pull is in a single
        transaction, a 429 mid-pull rolls back cleanly -- nothing half-written.
        """
        client = self._authenticated_client()
        conn = raw.conn
        written = 0
        newest_day: str | None = None
        with conn:
            for day in days:
                written += self._fetch_day(raw, client, day)
                if newest_day is None or day > newest_day:
                    newest_day = day
            sync_state.mark_synced(conn, SOURCE, last_entity_ts=newest_day)
        logger.info(
            "garmin: stored %d wellness rows across %d days (up to %s)",
            written,
            len(days),
            newest_day,
        )
        return written

    # ---- Connector protocol --------------------------------------------------

    def backfill(self, raw: RawWriter) -> None:
        """Pull a trailing window of wellness history into raw, idempotently (GRMN-04).

        Garmin has no bulk export, so the backfill walks back ``backfill_days``
        calendar days from today, fetching sleep/hrv/stats per day. Idempotent raw
        upserts make a re-run cheap (already-stored days are simply refreshed). On
        a 429 the run aborts with NO retry, to be resumed manually later.
        """
        today = self._today()
        days = [
            (today - timedelta(days=offset)).isoformat()
            for offset in range(self._backfill_days, -1, -1)
        ]
        logger.info("garmin: backfill walking %d days back from %s", self._backfill_days, today)
        self._pull_days(raw, days)
        with raw.conn:
            sync_state.save_backfill_cursor(raw.conn, SOURCE, None, complete=True)

    def sync(self, raw: RawWriter, since: date | None) -> None:
        """Pull recent wellness days into raw (GRMN-04), reusing persisted tokens only.

        Fetches from ``since`` (or the watermark, with a small lookback for
        late-finalised days) up to today. NEVER triggers a fresh login: it uses
        only the token store, so a scheduled run cannot cause an SSO 429 lockout
        (GRMN-02). On any error raises :class:`GarminSyncError` / :class:`GarminAuthError`
        for the pipeline to catch and skip (GRMN-03).
        """
        today = self._today()
        start = self._resolve_start(raw, since, today)
        if start > today:
            logger.info("garmin: nothing to sync (start %s is after today %s)", start, today)
            return
        days = [d.isoformat() for d in _daterange(start, today)]
        logger.info("garmin: syncing %d days (%s..%s)", len(days), start, today)
        self._pull_days(raw, days)

    def _resolve_start(self, raw: RawWriter, since: date | None, today: date) -> date:
        """Compute the first day to sync from the watermark or an explicit ``since``."""
        if since is not None:
            return since
        st = sync_state.read(raw.conn, SOURCE)
        if st.last_entity_ts:
            try:
                last = date.fromisoformat(st.last_entity_ts[:10])
                # Re-pull a small overlap so late-finalised days are not missed.
                return last - timedelta(days=SYNC_LOOKBACK_DAYS)
            except ValueError:
                logger.warning("garmin: unparseable watermark %r; ignoring", st.last_entity_ts)
        # First-ever sync with no watermark: pull the lookback window only (a full
        # history is the job of `backfill`, not the daily sync).
        return today - timedelta(days=SYNC_LOOKBACK_DAYS)

    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()


def _daterange(start: date, end: date) -> list[date]:
    """Inclusive list of every calendar day from ``start`` to ``end``."""
    if end < start:
        return []
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out
