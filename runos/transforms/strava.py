"""Pure transforms: raw Strava payloads -> structured ``activity`` / ``activity_stream``.

These functions read verbatim JSON from ``raw_response`` and upsert typed rows
into the structured tables. They are deterministic, idempotent (``ON CONFLICT``
upserts), and perform **no network I/O** -- the whole point of the raw->structured
split is that the structured layer can be rebuilt from stored raw at any time
(STORE-01/02; ARCHITECTURE Pattern 2).

Day attribution goes through :mod:`runos.transforms.bucketing` -- the local
calendar day comes from ``start_date_local`` (wall-clock, fake ``Z``), never from
``start_date`` (true UTC). See ``docs/DATE_BUCKETING.md``.

Endpoint labels match what the Strava connector writes:
``activity_summary`` (list endpoint), ``activity`` (detail), ``streams``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from runos.transforms.bucketing import local_day_from_strava_local
from runos.transforms.coerce import _opt_float, _opt_int, _opt_str

logger = logging.getLogger(__name__)

SOURCE = "strava"

EP_ACTIVITY_SUMMARY = "activity_summary"
EP_ACTIVITY = "activity"
EP_STREAMS = "streams"


@dataclass(frozen=True, slots=True)
class ActivityRow:
    """A typed, structured activity -- the projection of one raw Strava payload."""

    activity_id: int
    source: str
    day: str
    start_local: str | None
    start_utc: str | None
    utc_offset: float | None
    timezone: str | None
    name: str | None
    sport: str | None
    distance_m: float | None
    moving_s: int | None
    elapsed_s: int | None
    elev_gain_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_speed_ms: float | None
    avg_pace_s_km: float | None
    avg_watts: float | None
    avg_cadence: float | None
    suffer_score: float | None


@dataclass(frozen=True, slots=True)
class StreamRow:
    """A typed, structured stream series for one activity + type."""

    activity_id: int
    type: str
    data: str  # JSON array text
    original_size: int | None
    resolution: str | None


# ---------------------------------------------------------------------------
# Pure payload -> row functions (no DB, no network)
# ---------------------------------------------------------------------------


def _pace_s_km(avg_speed_ms: float | None) -> float | None:
    """Derive seconds-per-km from average speed in m/s (canonical pace unit)."""
    if not avg_speed_ms or avg_speed_ms <= 0:
        return None
    return 1000.0 / avg_speed_ms


def transform_activity(payload: dict[str, Any]) -> ActivityRow:
    """Project one raw Strava activity payload into an :class:`ActivityRow`.

    Pure: a function only of ``payload``. The local ``day`` is derived from
    ``start_date_local`` via the bucketing rule (wall-clock, ignore the fake
    ``Z``). Missing optional fields become ``None`` so the same transform works
    for both summary and detail payloads.
    """
    activity_id = int(payload["id"])
    start_local = payload.get("start_date_local")
    if not start_local:
        raise ValueError(f"activity {activity_id} has no start_date_local; cannot bucket")
    day = local_day_from_strava_local(str(start_local))

    avg_speed = _opt_float(payload.get("average_speed"))
    return ActivityRow(
        activity_id=activity_id,
        source=SOURCE,
        day=day,
        start_local=str(start_local),
        start_utc=_opt_str(payload.get("start_date")),
        utc_offset=_opt_float(payload.get("utc_offset")),
        timezone=_opt_str(payload.get("timezone")),
        name=_opt_str(payload.get("name")),
        sport=_opt_str(payload.get("sport_type") or payload.get("type")),
        distance_m=_opt_float(payload.get("distance")),
        moving_s=_opt_int(payload.get("moving_time")),
        elapsed_s=_opt_int(payload.get("elapsed_time")),
        elev_gain_m=_opt_float(payload.get("total_elevation_gain")),
        avg_hr=_opt_float(payload.get("average_heartrate")),
        max_hr=_opt_float(payload.get("max_heartrate")),
        avg_speed_ms=avg_speed,
        avg_pace_s_km=_pace_s_km(avg_speed),
        avg_watts=_opt_float(payload.get("average_watts")),
        avg_cadence=_opt_float(payload.get("average_cadence")),
        suffer_score=_opt_float(payload.get("suffer_score")),
    )


def transform_streams(activity_id: int, payload: dict[str, Any]) -> list[StreamRow]:
    """Project a raw key_by_type streams payload into one :class:`StreamRow` per type.

    The Strava streams endpoint (``key_by_type=True``) returns
    ``{type: {data: [...], original_size, resolution, ...}}``. We keep the compact
    ``data`` array as JSON text; the full raw payload is also retained in
    ``raw_response`` so nothing is lost.
    """
    rows: list[StreamRow] = []
    for stream_type, series in payload.items():
        if not isinstance(series, dict):
            continue
        data = series.get("data", [])
        rows.append(
            StreamRow(
                activity_id=int(activity_id),
                type=str(stream_type),
                data=json.dumps(data, ensure_ascii=False, default=str),
                original_size=_opt_int(series.get("original_size")),
                resolution=_opt_str(series.get("resolution")),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# DB upserts (read raw_response, write structured) -- still no network
# ---------------------------------------------------------------------------


def upsert_activity(conn: sqlite3.Connection, row: ActivityRow) -> None:
    """Idempotently upsert one structured activity row (caller owns the txn)."""
    conn.execute(
        """
        INSERT INTO activity (
            activity_id, source, day, start_local, start_utc, utc_offset, timezone,
            name, sport, distance_m, moving_s, elapsed_s, elev_gain_m,
            avg_hr, max_hr, avg_speed_ms, avg_pace_s_km, avg_watts, avg_cadence,
            suffer_score
        ) VALUES (
            :activity_id, :source, :day, :start_local, :start_utc, :utc_offset, :timezone,
            :name, :sport, :distance_m, :moving_s, :elapsed_s, :elev_gain_m,
            :avg_hr, :max_hr, :avg_speed_ms, :avg_pace_s_km, :avg_watts, :avg_cadence,
            :suffer_score
        )
        ON CONFLICT (activity_id) DO UPDATE SET
            source=excluded.source, day=excluded.day, start_local=excluded.start_local,
            start_utc=excluded.start_utc, utc_offset=excluded.utc_offset,
            timezone=excluded.timezone, name=excluded.name, sport=excluded.sport,
            distance_m=excluded.distance_m, moving_s=excluded.moving_s,
            elapsed_s=excluded.elapsed_s, elev_gain_m=excluded.elev_gain_m,
            avg_hr=excluded.avg_hr, max_hr=excluded.max_hr,
            avg_speed_ms=excluded.avg_speed_ms, avg_pace_s_km=excluded.avg_pace_s_km,
            avg_watts=excluded.avg_watts, avg_cadence=excluded.avg_cadence,
            suffer_score=excluded.suffer_score
        """,
        _row_params(row),
    )


def upsert_stream(conn: sqlite3.Connection, row: StreamRow) -> None:
    """Idempotently upsert one structured stream row (caller owns the txn)."""
    conn.execute(
        """
        INSERT INTO activity_stream (activity_id, type, data, original_size, resolution)
        VALUES (:activity_id, :type, :data, :original_size, :resolution)
        ON CONFLICT (activity_id, type) DO UPDATE SET
            data=excluded.data, original_size=excluded.original_size,
            resolution=excluded.resolution
        """,
        _row_params(row),
    )


def _activity_payloads(conn: sqlite3.Connection) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(entity_key, payload)`` for raw Strava activity rows.

    Prefers the richer ``activity`` (detail) payload over the ``activity_summary``
    when both exist for the same id, so re-derivation uses the most complete data.
    """
    by_id: dict[str, dict[str, Any]] = {}
    # Load summaries first, then let detail overwrite (detail is a superset).
    for endpoint in (EP_ACTIVITY_SUMMARY, EP_ACTIVITY):
        rows = conn.execute(
            "SELECT entity_key, payload FROM raw_response WHERE source=? AND endpoint=?",
            (SOURCE, endpoint),
        ).fetchall()
        for r in rows:
            by_id[str(r["entity_key"])] = json.loads(r["payload"])
    return list(by_id.items())


def rebuild_activities(conn: sqlite3.Connection) -> int:
    """Rebuild every structured ``activity`` row from stored raw payloads.

    Returns the number of activities written. Pure DB work (no network); safe to
    re-run. Each activity's local day is ensured to exist in ``date_spine`` *before*
    the row is inserted, so the ``activity.day`` foreign key always resolves (the
    full continuous zero-fill is then applied by :func:`spine.rebuild_spine`).
    """
    from runos.transforms import spine

    rows: list[ActivityRow] = []
    for entity_key, payload in _activity_payloads(conn):
        try:
            rows.append(transform_activity(payload))
        except (KeyError, ValueError) as exc:
            logger.warning("strava transform: skipping activity %s: %s", entity_key, exc)
            continue

    # Ensure every referenced day exists in the spine before inserting activities,
    # so the FK to date_spine(day) is satisfied without relying on deferred FK mode.
    spine.ensure_days(conn, sorted({row.day for row in rows}))
    for row in rows:
        upsert_activity(conn, row)
    return len(rows)


def rebuild_streams(conn: sqlite3.Connection) -> int:
    """Rebuild structured ``activity_stream`` rows from stored raw stream payloads.

    Only writes streams for activities that exist in the structured ``activity``
    table (the FK target), so an orphan stream payload is skipped rather than
    violating the foreign key. Returns the number of stream rows written.
    """
    known = {int(r[0]) for r in conn.execute("SELECT activity_id FROM activity").fetchall()}
    rows = conn.execute(
        "SELECT entity_key, payload FROM raw_response WHERE source=? AND endpoint=?",
        (SOURCE, EP_STREAMS),
    ).fetchall()
    count = 0
    for r in rows:
        activity_id = int(r["entity_key"])
        if activity_id not in known:
            logger.warning(
                "strava transform: streams for unknown activity %s; skipping", activity_id
            )
            continue
        for stream_row in transform_streams(activity_id, json.loads(r["payload"])):
            upsert_stream(conn, stream_row)
            count += 1
    return count


def _row_params(row: ActivityRow | StreamRow) -> dict[str, Any]:
    """Convert a frozen slotted dataclass into a named-parameter dict for SQL."""
    return {field: getattr(row, field) for field in row.__slots__}
