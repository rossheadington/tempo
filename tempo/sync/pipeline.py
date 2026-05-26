"""The daily sync pipeline: Strava first, then Garmin as an ISOLATED failure domain.

This is where Garmin's fragility is contained (GRMN-01/03; ARCHITECTURE
Anti-Pattern 5). The rule:

* Strava sync runs and its result is authoritative -- a Garmin problem must never
  affect it.
* Garmin sync is then *attempted inside a try/except*. Any failure (a 429
  account-throttle, a broken unofficial-library auth flow, a transform-shape
  change, a missing-token "you never logged in") is **caught, logged, and
  skipped** -- it returns a degraded result instead of raising. The daily run
  therefore still succeeds for Strava, and ``tempo analyze`` runs afterwards on
  whatever data already exists.
* Critically there is **no retry / backoff on a Garmin 429** -- retries compound an
  account-level lockout (PITFALLS 2). The connector raises immediately and this
  layer simply records the skip.

The result objects let the CLI report per-source status honestly ("Strava: ok,
Garmin: skipped (429)") so a partial sync is never silently reported as complete
(PITFALLS UX: silent partial sync).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from tempo.config import Settings
from tempo.connectors.base import RawWriter
from tempo.connectors.factory import build_garmin_connector, build_strava_connector
from tempo.connectors.garmin import SOURCE as GARMIN_SOURCE
from tempo.connectors.garmin import GarminAuthError, GarminConnector, GarminSyncError
from tempo.connectors.strava import SOURCE as STRAVA_SOURCE

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
    (no retry), a missing-token auth error (user never ran ``tempo garmin login``),
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


def run_full_sync(conn: sqlite3.Connection, settings: Settings) -> list[SourceResult]:
    """Run Strava, then attempt Garmin in isolation. Returns per-source results.

    Strava runs first and unconditionally; its failure modes (missing credentials)
    are handled by the CLI as before. Garmin is attempted only if its credentials
    /tokens machinery can be built, and any Garmin failure is caught so Strava's
    result stands and analysis can still run (GRMN-01/03). This is the function the
    ``tempo sync`` command calls.
    """
    results: list[SourceResult] = []

    # ---- Strava (authoritative; not isolated -- it is the robust source) ----
    strava = build_strava_connector(settings)
    strava_raw = RawWriter(conn, STRAVA_SOURCE)
    strava.sync(strava_raw, since=None)
    strava_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
            (STRAVA_SOURCE,),
        ).fetchone()[0]
    )
    results.append(SourceResult(STRAVA_SOURCE, ok=True, detail="ok", rows=strava_rows))

    # ---- Garmin (isolated failure domain) ----
    try:
        garmin = build_garmin_connector(settings)
    except Exception as exc:  # noqa: BLE001 - building the connector must not break Strava
        logger.warning("garmin connector unavailable; skipping: %s", exc)
        results.append(SourceResult(GARMIN_SOURCE, ok=False, detail=f"unavailable: {exc}"))
        return results

    results.append(run_garmin_sync(conn, garmin))
    return results
