"""The daily sync pipeline: each source is an ISOLATED failure domain.

This is where source fragility is contained (GRMN-01/03; ARCHITECTURE
Anti-Pattern 5). The rule:

* Each source's sync is *attempted inside a try/except*. Any failure (a 429
  account-throttle, a broken unofficial-library auth flow, a transform-shape
  change, a missing-token "you never logged in", a network blip) is **caught,
  logged, and recorded** as a degraded :class:`SourceResult` instead of
  raising. The daily run therefore still proceeds to subsequent sources and
  to ``runos analyze`` on whatever data already exists.
* Garmin is the canonical fragile source; Strava is robust in practice but
  symmetrically wrapped so a transient Strava failure (token glitch, 503)
  doesn't prevent the Garmin attempt OR block the transform/analyze step in
  ``run_daily``. The analysis layer can always re-derive from existing raw.
* Critically there is **no retry / backoff on a Garmin 429** -- retries
  compound an account-level lockout (PITFALLS 2). The connector raises
  immediately and this layer records the skip.

The result objects let the CLI report per-source status honestly ("Strava: ok,
Garmin: skipped (429)") so a partial sync is never silently reported as complete
(PITFALLS UX: silent partial sync).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from runos.config import Settings
from runos.connectors.base import RawWriter
from runos.connectors.factory import build_garmin_connector, build_strava_connector
from runos.connectors.garmin import SOURCE as GARMIN_SOURCE
from runos.connectors.garmin import GarminAuthError, GarminConnector, GarminSyncError
from runos.connectors.strava import SOURCE as STRAVA_SOURCE

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceResult:
    """The outcome of one source's sync within the pipeline."""

    source: str
    ok: bool
    detail: str
    rows: int = 0


def run_garmin_sync(
    conn: sqlite3.Connection,
    connector: GarminConnector,
    *,
    since=None,
) -> SourceResult:
    """Attempt a Garmin sync, catching ALL failures so they never propagate (GRMN-03).

    Returns a :class:`SourceResult` with ``ok=False`` on any failure -- a 429
    (no retry), a missing-token auth error (user never ran ``runos garmin login``),
    or any library/transform exception. Strava sync + analysis are unaffected
    because this function does not re-raise. Counts wellness raw rows present after
    the attempt so the report can show what landed.
    """
    raw = RawWriter(conn, GARMIN_SOURCE)
    try:
        connector.sync(raw, since=since)
    except GarminSyncError as exc:
        logger.warning("garmin sync skipped (no retry): %s", exc)
        return SourceResult(GARMIN_SOURCE, ok=False, detail=f"skipped: {exc}")
    except GarminAuthError as exc:
        logger.warning("garmin sync skipped (not authenticated): %s", exc)
        return SourceResult(GARMIN_SOURCE, ok=False, detail=f"not authenticated: {exc}")
    except Exception as exc:  # noqa: BLE001 - the WHOLE point: isolate fragile Garmin
        # A library/site change throws something unexpected: still isolate it.
        logger.warning("garmin sync skipped (unexpected error): %s", exc, exc_info=True)
        return SourceResult(GARMIN_SOURCE, ok=False, detail=f"error: {exc}")

    rows = _garmin_raw_rows(conn)
    return SourceResult(GARMIN_SOURCE, ok=True, detail="ok", rows=rows)


def _garmin_raw_rows(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM raw_response WHERE source=?",
            (GARMIN_SOURCE,),
        ).fetchone()[0]
    )


def run_strava_sync(conn: sqlite3.Connection, settings: Settings) -> SourceResult:
    """Attempt a Strava sync, returning a :class:`SourceResult` rather than raising.

    Strava is robust in practice but transient failures (network blip, 503,
    token edge cases) shouldn't prevent the Garmin attempt or block the
    transform/analyze step in :func:`runos.sync.daily.run_daily`. Missing
    credentials still surface as ``ValueError`` so the CLI's ``runos sync``
    can print a clear remediation rather than swallow it; that's the only
    case re-raised.
    """
    try:
        strava = build_strava_connector(settings)
    except ValueError:
        # Missing creds: surface to caller (CLI maps to red remediation).
        raise
    except Exception as exc:  # noqa: BLE001 - isolate any other connector-build failure
        logger.warning("strava connector unavailable; skipping: %s", exc, exc_info=True)
        return SourceResult(STRAVA_SOURCE, ok=False, detail=f"unavailable: {exc}")

    strava_raw = RawWriter(conn, STRAVA_SOURCE)
    try:
        strava.sync(strava_raw, since=None)
    except Exception as exc:  # noqa: BLE001 - isolate so Garmin still runs + analyses still rederive
        logger.warning("strava sync skipped (unexpected error): %s", exc, exc_info=True)
        return SourceResult(STRAVA_SOURCE, ok=False, detail=f"error: {exc}")

    strava_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
            (STRAVA_SOURCE,),
        ).fetchone()[0]
    )
    return SourceResult(STRAVA_SOURCE, ok=True, detail="ok", rows=strava_rows)


@dataclass(frozen=True, slots=True)
class StreamFetchResult:
    """Outcome of a recent-streams fetch pass within the sync."""

    candidates: int                #: Recent HR-recorded activities missing streams
    fetched: int                   #: How many actually got pulled this call
    activity_ids: tuple[int, ...]  #: Ids of activities streams were fetched for
    error: str | None = None       #: Set when the connector build / fetch failed terminally


def fetch_recent_streams(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    lookback_days: int = 1,
) -> StreamFetchResult:
    """Fetch HR streams for recent HR-recorded Strava activities still missing them.

    Looks at the structured ``activity`` table for rows with
    ``day >= today - lookback_days`` AND ``avg_hr > 0`` (i.e. the activity
    was recorded with a HR monitor paired) AND no existing stream rows in
    ``activity_stream``. For each, calls
    :meth:`runos.connectors.strava.StravaConnector.fetch_streams` -- which
    is lazy + idempotent, so re-running over the same set is safe.

    **Requires the structured layer to be populated.** The caller should run
    ``runos transform`` before this function so newly-synced activities are
    visible in ``activity``, and again after so the fetched stream raw rows
    are transformed into ``activity_stream``.

    Rate-limit budget: 1 Strava request per activity. For a typical day with
    1-3 sessions, this is well under the 200/15min cap. For a catch-up
    after a multi-day Mac sleep, ``lookback_days`` bounds the worst case.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT activity_id FROM activity
        WHERE day >= ?
          AND avg_hr IS NOT NULL AND avg_hr > 0
          AND activity_id NOT IN (SELECT activity_id FROM activity_stream)
        ORDER BY day DESC, activity_id DESC
        """,
        (cutoff,),
    ).fetchall()
    candidates = [int(r[0]) for r in rows]
    if not candidates:
        return StreamFetchResult(candidates=0, fetched=0, activity_ids=())

    try:
        connector = build_strava_connector(settings)
    except Exception as exc:  # noqa: BLE001 - building must not crash the sync
        logger.warning("recent-streams: strava connector unavailable; skipping: %s", exc)
        return StreamFetchResult(
            candidates=len(candidates),
            fetched=0,
            activity_ids=(),
            error=f"connector unavailable: {exc}",
        )

    raw = RawWriter(conn, STRAVA_SOURCE)
    fetched_ids: list[int] = []
    for aid in candidates:
        try:
            if connector.fetch_streams(raw, aid):
                fetched_ids.append(aid)
        except Exception as exc:  # noqa: BLE001 - per-activity failure stays isolated
            logger.warning(
                "recent-streams: fetch failed for activity %d: %s", aid, exc
            )
            continue
    logger.info(
        "recent-streams: %d candidates, %d fetched, %d skipped (already cached or failed)",
        len(candidates),
        len(fetched_ids),
        len(candidates) - len(fetched_ids),
    )
    return StreamFetchResult(
        candidates=len(candidates),
        fetched=len(fetched_ids),
        activity_ids=tuple(fetched_ids),
    )


def run_full_sync(conn: sqlite3.Connection, settings: Settings) -> list[SourceResult]:
    """Run each source as an isolated attempt. Returns per-source results.

    Both Strava and Garmin are wrapped: a transient failure in either source
    produces a degraded :class:`SourceResult` and the other source still runs.
    Missing credentials still surface as a ``ValueError`` from the Strava
    branch so the CLI can report a clear remediation; Garmin's missing-creds
    case is folded into the catch-all `unavailable:` result because Garmin
    is optional. This is the function the ``runos sync`` command calls.
    """
    results: list[SourceResult] = []

    # ---- Strava ----
    results.append(run_strava_sync(conn, settings))

    # ---- Garmin ----
    try:
        garmin = build_garmin_connector(settings)
    except Exception as exc:  # noqa: BLE001 - building the connector must not break the run
        logger.warning("garmin connector unavailable; skipping: %s", exc)
        results.append(SourceResult(GARMIN_SOURCE, ok=False, detail=f"unavailable: {exc}"))
        return results

    results.append(run_garmin_sync(conn, garmin))
    return results
