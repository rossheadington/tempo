"""The Strava connector: OAuth, resumable backfill, incremental sync, streams.

Implements the :class:`~tempo.connectors.base.Connector` protocol for Strava's
v3 REST API. Design notes that matter:

**Verbatim raw storage.** Fetches go through stravalib's low-level transport
(``Client.protocol.get``), which returns the *raw* JSON dict/list straight from
the API. We store exactly that in ``raw_response`` -- no model round-tripping --
so no field is ever silently dropped and the structured layer can derive new
metrics later (STRV-06; ARCHITECTURE Pattern 2).

**Rotating tokens, persisted atomically.** stravalib handles the OAuth code
exchange and refresh; *we* own persistence. After every refresh the new
(rotating) refresh token is written via :class:`~tempo.connectors.tokens.TokenStore`
using temp-write -> fsync -> rename, so a crash can never strand the user back
in the browser flow (STRV-01/02; PITFALLS 4).

**Resumable, rate-limit-safe backfill.** The all-time pull walks the activity
list newest-first in pages, committing each page's raw rows *and* a
``backfill_cursor`` in one transaction. If a 429 / rate-limit or a crash
interrupts the run, re-running resumes from the cursor and idempotent upserts
mean already-stored activities are never re-fetched (STRV-03; PITFALLS 5).

**Lazy streams.** Detailed time-series (HR, pace, GPS, power, cadence,
elevation) cost >=1 extra call each, so they are fetched *on demand* via
:meth:`StravaConnector.fetch_streams`, never eagerly for all-time history. Each
activity's streams are stored once and skipped on re-run (STRV-04).

**Incremental sync.** :meth:`sync` pulls only activities with a start newer than
the persisted watermark using Strava's ``after`` parameter (STRV-05).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from stravalib import Client
from stravalib.exc import ObjectNotFound, RateLimitExceeded
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tempo.connectors.base import RawWriter
from tempo.connectors.tokens import TokenSet, TokenStore
from tempo.sync import state as sync_state

logger = logging.getLogger(__name__)

SOURCE = "strava"

# Raw-store endpoint labels (the ``endpoint`` column in raw_response).
EP_ACTIVITY = "activity"  # detailed single activity
EP_ACTIVITY_SUMMARY = "activity_summary"  # summary from the list endpoint
EP_STREAMS = "streams"  # all stream types for one activity

# Strava list endpoint hard cap.
PER_PAGE = 200

# All stream types Phase 2 cares about (HR, pace, GPS, power, cadence, elevation).
STREAM_TYPES = [
    "time",
    "latlng",
    "distance",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "temp",
    "moving",
    "grade_smooth",
]

# Refresh when within this many seconds of expiry, not on every call (PITFALLS 4).
_REFRESH_SKEW_SECONDS = 300

# Default scope for the one-time handshake: read all activities incl. private.
DEFAULT_SCOPE = ["read", "activity:read_all", "profile:read_all"]

# Rate-limit retry tuning. Module-level so tests can shrink the waits to keep the
# suite fast without touching the production backoff behaviour.
RETRY_ATTEMPTS = 4
RETRY_WAIT_MULTIPLIER = 2.0
RETRY_WAIT_MIN = 2.0
RETRY_WAIT_MAX = 60.0


class StravaAuthError(RuntimeError):
    """Raised when Strava credentials are missing or auth cannot proceed."""


def _retry_on_rate_limit() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Tenacity policy: back off on transient rate limits, then give up cleanly.

    A 429 from Strava is *not* fatal -- it means "pause until the window
    resets". We retry a few times with exponential backoff (capped), and if the
    limit persists we let :class:`RateLimitExceeded` propagate so the backfill
    can checkpoint and exit, to be resumed later, rather than hammering the API
    (PITFALLS 5: never charge into a 429).
    """
    return retry(
        retry=retry_if_exception_type(RateLimitExceeded),
        wait=wait_exponential(
            multiplier=RETRY_WAIT_MULTIPLIER, min=RETRY_WAIT_MIN, max=RETRY_WAIT_MAX
        ),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        reraise=True,
    )


class StravaConnector:
    """Strava source connector (implements ``Connector``)."""

    source = SOURCE

    def __init__(
        self,
        client_id: int,
        client_secret: str,
        token_store: TokenStore,
        *,
        client: Client | None = None,
        backfill_page_budget: int | None = None,
    ) -> None:
        """Create a connector.

        ``client`` is injectable so tests can supply a fully-mocked stravalib
        client; in production it defaults to a real, rate-limiting
        :class:`stravalib.Client`. ``backfill_page_budget`` optionally caps the
        number of list pages fetched per :meth:`backfill` invocation, so a large
        history can be spread across runs/days within Strava's daily budget.
        """
        self._client_id = int(client_id)
        self._client_secret = client_secret
        self._tokens = token_store
        # rate_limit_requests=True makes stravalib sleep within the 15-min window.
        self._client = client if client is not None else Client(rate_limit_requests=True)
        self._page_budget = backfill_page_budget

    # ---- OAuth handshake (one-time) -------------------------------------

    def authorization_url(self, redirect_uri: str, scope: list[str] | None = None) -> str:
        """Return the Strava consent URL the user opens once in a browser."""
        return self._client.authorization_url(
            client_id=self._client_id,
            redirect_uri=redirect_uri,
            scope=scope or DEFAULT_SCOPE,
        )

    def exchange_code(self, code: str) -> TokenSet:
        """Exchange a one-time OAuth ``code`` for tokens and persist them.

        This is the completion of the one-time handshake (STRV-01). The returned
        token set is written atomically before we return, so the very first
        token is durable.
        """
        info = self._client.exchange_code_for_token(
            client_id=self._client_id,
            client_secret=self._client_secret,
            code=code,
        )
        tokens = TokenSet(
            access_token=str(info["access_token"]),
            refresh_token=str(info["refresh_token"]),
            expires_at=int(info["expires_at"]),
        )
        self._tokens.save(tokens)
        return tokens

    # ---- Token lifecycle ------------------------------------------------

    def _now(self) -> int:
        return int(datetime.now(UTC).timestamp())

    def ensure_authenticated(self) -> TokenSet:
        """Load tokens, refresh if near expiry, and arm the client.

        Refreshes only within ``_REFRESH_SKEW_SECONDS`` of expiry (not every
        call). On refresh the rotated refresh token is persisted atomically
        *before* any API call, so the new token can never be lost (PITFALLS 4).
        Returns the live token set.
        """
        tokens = self._tokens.load()
        if tokens.expires_at - self._now() <= _REFRESH_SKEW_SECONDS:
            tokens = self._refresh(tokens)
        # Arm the underlying client for data calls.
        self._client.access_token = tokens.access_token
        self._client.refresh_token = tokens.refresh_token
        self._client.token_expires = tokens.expires_at
        return tokens

    def _refresh(self, tokens: TokenSet) -> TokenSet:
        info = self._client.refresh_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret,
            refresh_token=tokens.refresh_token,
        )
        rotated = TokenSet(
            access_token=str(info["access_token"]),
            refresh_token=str(info["refresh_token"]),
            expires_at=int(info["expires_at"]),
        )
        # Persist the rotated refresh token atomically BEFORE returning, so the
        # old (now-invalidated) token is never the only thing on disk.
        self._tokens.save(rotated)
        logger.info("strava: refreshed access token (rotated refresh token persisted)")
        return rotated

    # ---- Low-level fetch (verbatim) -------------------------------------

    def _get(self, url: str, **params: Any) -> Any:
        """Rate-limit-aware GET returning the raw JSON dict/list."""

        @_retry_on_rate_limit()
        def _call() -> Any:
            return self._client.protocol.get(url, **params)

        return _call()

    def _list_activities_page(self, page: int, after: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"page": page, "per_page": PER_PAGE}
        if after is not None:
            params["after"] = after
        result = self._get("/athlete/activities", **params)
        return list(result) if result else []

    # ---- Backfill (resumable, all-time) ---------------------------------

    def backfill(self, raw: RawWriter) -> None:
        """Pull all-time activity summaries into raw, resumably (STRV-03).

        Walks the activity list in pages (newest-first; Strava's default order),
        storing each summary verbatim. After each page it commits the raw rows
        and the ``backfill_cursor`` (the next page to fetch) in one transaction,
        so an interruption at any page resumes exactly where it stopped without
        re-fetching. On hitting the per-run page budget it returns cleanly,
        leaving the cursor set for the next run.

        Streams are intentionally NOT fetched here -- they are lazy
        (:meth:`fetch_streams`) to protect the rate limit (STRV-04).
        """
        self.ensure_authenticated()
        conn = raw.conn  # connector owns the transaction for batch+cursor atomicity

        st = sync_state.read(conn, SOURCE)
        if st.backfill_complete:
            logger.info("strava: backfill already complete; nothing to do")
            return

        cursor = st.backfill_cursor or {}
        page = int(cursor.get("next_page", 1))
        pages_this_run = 0

        while True:
            if self._page_budget is not None and pages_this_run >= self._page_budget:
                logger.info(
                    "strava: backfill page budget (%d) reached; resume next run at page %d",
                    self._page_budget,
                    page,
                )
                return

            try:
                activities = self._list_activities_page(page, after=None)
            except RateLimitExceeded:
                # Cursor already points at this page from the previous commit;
                # exit cleanly so a later run resumes here (PITFALLS 5).
                logger.warning(
                    "strava: rate limit hit during backfill at page %d; will resume later",
                    page,
                )
                raise

            if not activities:
                # Reached the end of history: mark complete in one final txn.
                with conn:
                    sync_state.save_backfill_cursor(conn, SOURCE, None, complete=True)
                logger.info("strava: backfill complete at page %d", page)
                return

            with conn:  # raw rows + cursor advance commit together
                for activity in activities:
                    raw.put(EP_ACTIVITY_SUMMARY, str(activity["id"]), activity)
                sync_state.save_backfill_cursor(conn, SOURCE, {"next_page": page + 1})

            logger.info("strava: backfilled page %d (%d activities)", page, len(activities))
            page += 1
            pages_this_run += 1

    # ---- Incremental sync (daily) ---------------------------------------

    def sync(self, raw: RawWriter, since: date | None) -> None:
        """Pull only activities newer than the watermark into raw (STRV-05).

        Uses Strava's ``after`` (epoch seconds) so the API returns only new
        activities. The watermark advances to the newest activity's start time
        *only after* the page batch is stored, so a failure mid-sync never skips
        data (ARCHITECTURE Anti-Pattern 3). Idempotent upserts make the slight
        overlap from ``after`` being inclusive-ish harmless.
        """
        self.ensure_authenticated()
        conn = raw.conn

        st = sync_state.read(conn, SOURCE)
        after = self._resolve_after(st, since)

        page = 1
        newest_ts: str | None = st.last_entity_ts
        total = 0
        while True:
            activities = self._list_activities_page(page, after=after)
            if not activities:
                break
            with conn:
                for activity in activities:
                    raw.put(EP_ACTIVITY_SUMMARY, str(activity["id"]), activity)
                    start = activity.get("start_date")  # true UTC instant
                    if start and (newest_ts is None or start > newest_ts):
                        newest_ts = start
            total += len(activities)
            page += 1

        with conn:
            sync_state.mark_synced(conn, SOURCE, last_entity_ts=newest_ts)
        logger.info("strava: incremental sync stored %d activities (after=%s)", total, after)

    def _resolve_after(self, st: sync_state.SyncState, since: date | None) -> int | None:
        """Compute the ``after`` epoch from the watermark or an explicit date."""
        if st.last_entity_ts:
            try:
                # last_entity_ts is an ISO UTC instant (Strava start_date).
                dt = datetime.fromisoformat(st.last_entity_ts.replace("Z", "+00:00"))
                return int(dt.timestamp())
            except ValueError:
                logger.warning("strava: unparseable watermark %r; ignoring", st.last_entity_ts)
        if since is not None:
            return int(datetime(since.year, since.month, since.day, tzinfo=UTC).timestamp())
        return None

    # ---- Lazy streams ---------------------------------------------------

    def fetch_streams(
        self,
        raw: RawWriter,
        activity_id: int,
        *,
        force: bool = False,
    ) -> bool:
        """Fetch and store all stream types for one activity (STRV-04).

        Lazy and idempotent: if streams for this activity are already in raw and
        ``force`` is False, returns ``False`` without a network call -- so
        re-running over a list of activities never re-spends rate-limit budget on
        ones already done. Returns ``True`` if a fetch+store happened.
        """
        key = str(activity_id)
        if not force and raw.has(EP_STREAMS, key):
            return False

        self.ensure_authenticated()
        try:
            payload = self._get(
                f"/activities/{activity_id}/streams",
                keys=",".join(STREAM_TYPES),
                key_by_type=True,
            )
        except ObjectNotFound:
            # Some activities (manual entries, indoor sessions, very old uploads)
            # have no stream data and 404 on this endpoint. Store an empty marker
            # so we don't re-spend rate-limit budget retrying them on every run;
            # transform_streams iterates the payload and yields nothing for {}.
            with raw.conn:
                raw.put(EP_STREAMS, key, {})
            logger.info("strava: no streams for activity %s (404); marked done", activity_id)
            return True
        with raw.conn:
            raw.put(EP_STREAMS, key, payload)
        logger.info("strava: stored streams for activity %s", activity_id)
        return True

    def fetch_detail(self, raw: RawWriter, activity_id: int, *, force: bool = False) -> bool:
        """Fetch and store the *detailed* activity payload for one activity.

        Like :meth:`fetch_streams`, lazy and idempotent. The list endpoint
        returns summaries; the detail endpoint adds fields (splits, gear, full
        description) some analyses may want.
        """
        key = str(activity_id)
        if not force and raw.has(EP_ACTIVITY, key):
            return False
        self.ensure_authenticated()
        payload = self._get(f"/activities/{activity_id}")
        with raw.conn:
            raw.put(EP_ACTIVITY, key, payload)
        logger.info("strava: stored detail for activity %s", activity_id)
        return True

    def stored_activity_ids(self, conn: sqlite3.Connection) -> list[int]:
        """Return activity ids known from stored summaries (for lazy stream loops)."""
        rows = conn.execute(
            "SELECT entity_key FROM raw_response WHERE source=? AND endpoint=?",
            (SOURCE, EP_ACTIVITY_SUMMARY),
        ).fetchall()
        return sorted(int(r[0]) for r in rows)
