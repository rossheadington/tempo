"""The daily sync pipeline: each source is an ISOLATED failure domain.

This is where source fragility is contained (GRMN-01/03; ARCHITECTURE
Anti-Pattern 5). The rule:

* Each source's sync is *attempted inside a try/except*. Any failure (a 429
  account-throttle, a broken unofficial-library auth flow, a transform-shape
  change, a missing-token "you never logged in", a network blip) is **caught,
  logged, and recorded** as a degraded :class:`SourceResult` instead of
  raising. The daily run therefore still proceeds to subsequent sources and
  to ``tempo analyze`` on whatever data already exists.
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


def run_strava_sync(conn: sqlite3.Connection, settings: Settings) -> SourceResult:
    """Attempt a Strava sync, returning a :class:`SourceResult` rather than raising.

    Strava is robust in practice but transient failures (network blip, 503,
    token edge cases) shouldn't prevent the Garmin attempt or block the
    transform/analyze step in :func:`tempo.sync.daily.run_daily`. Missing
    credentials still surface as ``ValueError`` so the CLI's ``tempo sync``
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


def run_full_sync(conn: sqlite3.Connection, settings: Settings) -> list[SourceResult]:
    """Run each source as an isolated attempt. Returns per-source results.

    Both Strava and Garmin are wrapped: a transient failure in either source
    produces a degraded :class:`SourceResult` and the other source still runs.
    Missing credentials still surface as a ``ValueError`` from the Strava
    branch so the CLI can report a clear remediation; Garmin's missing-creds
    case is folded into the catch-all `unavailable:` result because Garmin
    is optional. This is the function the ``tempo sync`` command calls.
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
