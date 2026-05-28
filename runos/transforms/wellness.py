"""Pure transforms: raw Garmin payloads -> structured ``wellness_day``.

Garmin maps to *multiple* raw rows per day -- one each for the ``sleep``, ``hrv``,
and ``stats`` endpoints, all keyed by the ISO calendar date. These functions read
those verbatim payloads from ``raw_response`` and collapse them into a single
``wellness_day`` row per LOCAL calendar day (Garmin's ``calendarDate``, the
wake-up day it assigns to overnight sleep/HRV so the cross-midnight ambiguity is
removed -- see ``docs/DATE_BUCKETING.md`` and PITFALLS Pitfall 6).

Like the Strava transforms, they are deterministic, idempotent, and perform **no
network I/O** -- the whole point of the raw->structured split is that the
structured layer can be rebuilt from stored raw at any time (GRMN-04; STORE-02;
ARCHITECTURE Anti-Pattern 1). This is also the isolation seam: if the
``garminconnect`` library breaks, transforms and analysis still run on the
already-stored raw.

Garmin's own proprietary scores (sleep score, HRV status, body battery) are
consumed verbatim as *inputs*; we do not attempt to re-derive them (out of scope
per REQUIREMENTS).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from runos.transforms.bucketing import BucketingError, local_day_from_calendar_date
from runos.transforms.coerce import _opt_int

logger = logging.getLogger(__name__)

SOURCE = "garmin"

EP_SLEEP = "sleep"
EP_HRV = "hrv"
EP_STATS = "stats"


@dataclass(frozen=True, slots=True)
class WellnessRow:
    """A typed, structured wellness day -- the projection of one day's raw Garmin payloads."""

    day: str
    resting_hr: int | None
    hrv_last_night: float | None
    hrv_status: str | None
    sleep_score: int | None
    sleep_seconds: int | None
    deep_s: int | None
    rem_s: int | None
    light_s: int | None
    awake_s: int | None
    body_battery_high: int | None
    body_battery_low: int | None
    stress_avg: int | None
    steps: int | None


# ---------------------------------------------------------------------------
# Pure payload -> partial-field functions (no DB, no network)
# ---------------------------------------------------------------------------


def _opt_real(value: Any) -> float | None:
    """Coerce to ``float`` or ``None`` (Garmin HRV averages can be float)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sleep(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract sleep fields from a ``get_sleep_data`` payload.

    Garmin nests the per-day summary under ``dailySleepDTO`` and the 0-100 score
    under ``sleepScores.overall.value``. Missing fields become ``None``. Returns a
    partial-field dict (the keys that this endpoint contributes).
    """
    dto = payload.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    return {
        "sleep_seconds": _opt_int(dto.get("sleepTimeSeconds")),
        "deep_s": _opt_int(dto.get("deepSleepSeconds")),
        "light_s": _opt_int(dto.get("lightSleepSeconds")),
        "rem_s": _opt_int(dto.get("remSleepSeconds")),
        "awake_s": _opt_int(dto.get("awakeSleepSeconds")),
        "sleep_score": _opt_int(overall.get("value")),
    }


def parse_hrv(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract overnight HRV fields from a ``get_hrv_data`` payload.

    Garmin nests the night summary under ``hrvSummary`` with ``lastNightAvg`` (ms)
    and a ``status`` (e.g. ``'BALANCED'``). A ``None`` payload (no HRV that night)
    is handled by the caller; here we just read what's present.
    """
    summary = payload.get("hrvSummary") or {}
    return {
        "hrv_last_night": _opt_real(summary.get("lastNightAvg")),
        "hrv_status": summary.get("status") if summary.get("status") else None,
    }


def parse_stats(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract daily-summary fields from a ``get_stats`` payload.

    The user-summary payload carries resting HR, steps, average stress, and the
    day's body-battery high/low. Field names follow Garmin's user-summary schema.
    """
    return {
        "resting_hr": _opt_int(payload.get("restingHeartRate")),
        "steps": _opt_int(payload.get("totalSteps")),
        "stress_avg": _opt_int(payload.get("averageStressLevel")),
        "body_battery_high": _opt_int(payload.get("bodyBatteryHighestValue")),
        "body_battery_low": _opt_int(payload.get("bodyBatteryLowestValue")),
    }


def _calendar_date(*payloads: dict[str, Any], fallback: str) -> str:
    """Resolve the local day from any payload's ``calendarDate``, else the raw key.

    Garmin stamps ``calendarDate`` on each payload (sleep nests it under
    ``dailySleepDTO``); we prefer that explicit value, falling back to the
    raw_response ``entity_key`` (which the connector set to the ISO date it
    requested). Routed through the shared bucketing rule so one rule governs every
    source.
    """
    candidates: list[str] = []
    for payload in payloads:
        cd = payload.get("calendarDate")
        if not cd:
            dto = payload.get("dailySleepDTO") or {}
            cd = dto.get("calendarDate")
        if not cd:
            summary = payload.get("hrvSummary") or {}
            cd = summary.get("calendarDate")
        if cd:
            candidates.append(str(cd))
    for cd in candidates:
        try:
            return local_day_from_calendar_date(cd)
        except BucketingError:
            continue
    # Fall back to the entity_key the connector used (already an ISO date).
    return local_day_from_calendar_date(fallback)


def build_wellness_row(
    day_key: str,
    *,
    sleep: dict[str, Any] | None = None,
    hrv: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
) -> WellnessRow:
    """Collapse one day's sleep/hrv/stats payloads into a single :class:`WellnessRow`.

    Pure: a function only of the inputs. ``day_key`` is the raw_response entity key
    (the ISO date the connector requested); the authoritative local day is taken
    from the payloads' ``calendarDate`` when present, else from ``day_key``. Any
    endpoint may be absent (a day with sleep but no HRV, or stats but no sleep) --
    its fields are simply ``None``.
    """
    present = [p for p in (sleep, hrv, stats) if p]
    day = _calendar_date(*present, fallback=day_key)

    fields: dict[str, Any] = {
        "resting_hr": None,
        "hrv_last_night": None,
        "hrv_status": None,
        "sleep_score": None,
        "sleep_seconds": None,
        "deep_s": None,
        "rem_s": None,
        "light_s": None,
        "awake_s": None,
        "body_battery_high": None,
        "body_battery_low": None,
        "stress_avg": None,
        "steps": None,
    }
    if sleep:
        fields.update(parse_sleep(sleep))
    if hrv:
        fields.update(parse_hrv(hrv))
    if stats:
        fields.update(parse_stats(stats))
    return WellnessRow(day=day, **fields)


# ---------------------------------------------------------------------------
# DB upserts (read raw_response, write structured) -- still no network
# ---------------------------------------------------------------------------


def upsert_wellness(conn: sqlite3.Connection, row: WellnessRow) -> None:
    """Idempotently upsert one structured wellness row (caller owns the txn)."""
    conn.execute(
        """
        INSERT INTO wellness_day (
            day, resting_hr, hrv_last_night, hrv_status,
            sleep_score, sleep_seconds, deep_s, rem_s, light_s, awake_s,
            body_battery_high, body_battery_low, stress_avg, steps, updated_at
        ) VALUES (
            :day, :resting_hr, :hrv_last_night, :hrv_status,
            :sleep_score, :sleep_seconds, :deep_s, :rem_s, :light_s, :awake_s,
            :body_battery_high, :body_battery_low, :stress_avg, :steps, datetime('now')
        )
        ON CONFLICT (day) DO UPDATE SET
            resting_hr=excluded.resting_hr, hrv_last_night=excluded.hrv_last_night,
            hrv_status=excluded.hrv_status, sleep_score=excluded.sleep_score,
            sleep_seconds=excluded.sleep_seconds, deep_s=excluded.deep_s,
            rem_s=excluded.rem_s, light_s=excluded.light_s, awake_s=excluded.awake_s,
            body_battery_high=excluded.body_battery_high,
            body_battery_low=excluded.body_battery_low,
            stress_avg=excluded.stress_avg, steps=excluded.steps,
            updated_at=datetime('now')
        """,
        {field: getattr(row, field) for field in row.__slots__},
    )


def _raw_by_day(conn: sqlite3.Connection, endpoint: str) -> dict[str, dict[str, Any]]:
    """Return ``{entity_key: payload}`` for raw Garmin rows of one endpoint."""
    rows = conn.execute(
        "SELECT entity_key, payload FROM raw_response WHERE source=? AND endpoint=?",
        (SOURCE, endpoint),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except json.JSONDecodeError:
            logger.warning(
                "garmin transform: bad %s payload for %s; skipping",
                endpoint,
                r["entity_key"],
            )
            continue
        if isinstance(payload, dict):
            out[str(r["entity_key"])] = payload
    return out


def rebuild_wellness(conn: sqlite3.Connection) -> int:
    """Rebuild every structured ``wellness_day`` row from stored raw payloads (GRMN-04).

    Collects the sleep/hrv/stats raw rows, groups them by their raw entity key
    (the ISO date), and collapses each day into one ``wellness_day`` row. Each
    day's local date is ensured to exist in ``date_spine`` *before* the row is
    inserted, so the ``wellness_day.day`` foreign key always resolves (the full
    continuous zero-fill is then applied by :func:`spine.rebuild_spine`). Pure DB
    work (no network); safe to re-run. Returns the number of wellness rows written.
    """
    from runos.transforms import spine

    sleep_by_day = _raw_by_day(conn, EP_SLEEP)
    hrv_by_day = _raw_by_day(conn, EP_HRV)
    stats_by_day = _raw_by_day(conn, EP_STATS)

    all_keys = set(sleep_by_day) | set(hrv_by_day) | set(stats_by_day)
    rows: list[WellnessRow] = []
    for key in sorted(all_keys):
        try:
            rows.append(
                build_wellness_row(
                    key,
                    sleep=sleep_by_day.get(key),
                    hrv=hrv_by_day.get(key),
                    stats=stats_by_day.get(key),
                )
            )
        except BucketingError as exc:
            logger.warning("garmin transform: skipping day %s: %s", key, exc)
            continue

    # Two raw keys could resolve to the same local day (rare); last write wins via
    # the upsert. Ensure every resolved day exists in the spine before inserting.
    spine.ensure_days(conn, sorted({row.day for row in rows}))
    for row in rows:
        upsert_wellness(conn, row)
    return len(rows)
