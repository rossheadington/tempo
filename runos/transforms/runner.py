"""Orchestrate the raw -> structured projection (the ``transform`` / ``rederive`` engine).

This module wires the pure transforms together into the two CLI operations:

* :func:`run_transform`  -- bring structured tables up to date from raw, upserting.
* :func:`run_rederive`   -- drop and fully rebuild structured tables from raw.

Both are **pure DB passes with zero network I/O** (STORE-02). The only difference
is that ``rederive`` first clears the structured tables so a removed raw row or a
changed transform can't leave a stale structured row behind, whereas ``transform``
is an incremental upsert. Both produce identical state for a given raw layer.

**Ordering matters.** ``activity.day`` has a foreign key to ``date_spine(day)`` and
``activity_stream.activity_id`` references ``activity``. The rebuild respects this:
``rebuild_activities`` ensures each activity's local day exists in the spine before
inserting the activity, ``rebuild_spine`` then zero-fills the continuous date range,
and ``rebuild_streams`` only writes streams for already-inserted activities. The
whole pass runs in one transaction so it commits atomically (and a ``rederive``
failure leaves the previous structured state intact).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

from runos.journal.service import link_orphan_entries
from runos.transforms import coros_wellness as coros_wellness_tf
from runos.transforms import spine
from runos.transforms import strava as strava_tf
from runos.transforms import wellness as wellness_tf

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TransformResult:
    """Counts from a transform/rederive pass, for CLI reporting and tests."""

    activities: int
    streams: int
    spine_days: int
    wellness_days: int = 0


def _rebuild(conn: sqlite3.Connection, *, fill_to: date | None) -> TransformResult:
    """Project raw -> structured inside one transaction. Caller-agnostic core.

    Order respects the foreign keys: ``rebuild_activities`` and
    ``rebuild_wellness`` each ensure their own spine days before inserting (so the
    ``day`` foreign keys resolve), ``rebuild_spine`` then zero-fills the continuous
    range to cover both activity-only and wellness-only days, and
    ``rebuild_streams`` references only already-inserted activities.

    **Wellness ordering matters (Phase 18 priority resolver).** The Garmin
    wellness transform runs FIRST so its values land in ``wellness_day``; the
    Coros wellness transform runs SECOND and applies per-column ``COALESCE``
    so a non-NULL Coros value overrides Garmin's while a NULL Coros value
    preserves it. Swapping the order would invert the "Coros wins" contract.
    The transform result reports the combined row count touched -- whichever
    transform was last to write a given day is the row attributed to it for
    counting purposes; the actual stored row reflects the resolver outcome.
    """
    activities = strava_tf.rebuild_activities(conn)
    wellness_days = _rebuild_wellness_if_present(conn)
    spine_days = spine.rebuild_spine(conn, fill_to=fill_to)
    streams = strava_tf.rebuild_streams(conn)
    return TransformResult(
        activities=activities,
        streams=streams,
        spine_days=spine_days,
        wellness_days=wellness_days,
    )


def _rebuild_wellness_if_present(conn: sqlite3.Connection) -> int:
    """Rebuild wellness only if the table exists (Phase 6+ schema).

    Keeps transform/rederive working on a pre-Phase-6 DB (e.g. mid-migration in a
    test) by returning 0 when ``wellness_day`` is absent. Runs both wellness
    transforms in order (Garmin FIRST, then Coros) so the COALESCE-based
    priority resolver in :mod:`runos.transforms.coros_wellness` works
    deterministically: Garmin's writes land first, Coros's second write
    overrides where it has values and preserves Garmin's writes where it
    doesn't. Returns the total wellness-row writes across both passes.
    """
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='wellness_day'"
    ).fetchone()
    if has_table is None:
        return 0
    # Garmin first: it populates the row (or creates a fresh one). Coros
    # second: it uses COALESCE-on-update so non-NULL values override Garmin's,
    # NULL values preserve Garmin's writes. Order is the resolver.
    garmin_written = wellness_tf.rebuild_wellness(conn)
    coros_written = coros_wellness_tf.rebuild_coros_wellness(conn)
    return garmin_written + coros_written


def run_transform(conn: sqlite3.Connection, *, fill_to: date | None = None) -> TransformResult:
    """Upsert structured tables from the current raw layer (incremental, no network).

    Idempotent: re-running over the same raw layer yields the same structured
    state. ``fill_to`` (typically today) extends the spine forward to that day so
    recent rest days exist even with no new activity.
    """
    with conn:  # one atomic transaction
        result = _rebuild(conn, fill_to=fill_to)
    # Post-transform hook: link any orphan journal entries (recorded via the
    # Telegram bot before Strava had synced) to the activities that just
    # arrived. Idempotent; cheap on a fully-linked DB. Lives outside the
    # transform transaction so a failure here doesn't roll back the structured
    # rebuild -- the orphan stays linkable on the next transform.
    try:
        linked = link_orphan_entries(conn)
        if linked:
            logger.info("orphan journal entries linked: %d", linked)
    except sqlite3.Error as exc:  # noqa: BLE001 - log + swallow; transforms succeeded
        logger.warning("orphan-link pass failed (non-fatal): %s", exc)
    logger.info(
        "transform: %d activities, %d streams, %d wellness days, %d spine days",
        result.activities,
        result.streams,
        result.wellness_days,
        result.spine_days,
    )
    return result


def run_rederive(conn: sqlite3.Connection, *, fill_to: date | None = None) -> TransformResult:
    """Fully rebuild ALL structured tables from raw, with zero network calls (STORE-02).

    Clears the structured tables first so the result depends only on the raw layer
    (a deleted raw row or a changed transform can never leave a stale structured
    row). The whole drop-and-rebuild runs in one transaction, so a failure leaves
    the previous structured state intact.
    """
    with conn:  # one atomic transaction: clear + rebuild commit together
        # Order: children before parents to satisfy foreign keys.
        conn.execute("DELETE FROM activity_stream;")
        conn.execute("DELETE FROM activity;")
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='wellness_day'"
        ).fetchone():
            conn.execute("DELETE FROM wellness_day;")
        # date_spine is rebuilt from data bounds; clearing it keeps rederive a pure
        # function of raw (no orphan spine days from a previous, larger dataset).
        conn.execute("DELETE FROM date_spine;")
        result = _rebuild(conn, fill_to=fill_to)
    logger.info(
        "rederive: rebuilt %d activities, %d streams, %d wellness days, %d spine days (no network)",
        result.activities,
        result.streams,
        result.wellness_days,
        result.spine_days,
    )
    return result
