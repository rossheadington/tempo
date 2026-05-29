"""Pure transforms: raw Coros payloads -> structured ``wellness_day``.

The Coros wellness transform is the second half of the per-day-per-metric
priority resolver established in Phase 18. Garmin's transform (see
:mod:`runos.transforms.wellness`) writes first; Coros's transform (this module)
writes second. The resolver is implemented at the SQL layer: every Coros
``UPDATE`` uses ``column = COALESCE(?, column)`` so a NULL Coros value preserves
whatever Garmin wrote, while a non-NULL Coros value overwrites Garmin's. When no
``wellness_day`` row exists yet, an ``INSERT`` lands a Coros-only row.

**Endpoints consumed (raw layer, from 18-01's Coros connector):**

- ``coros / hrv``  -- each entry from ``data.summaryInfo.sleepHrvData.sleepHrvList``
  of ``/dashboard/query``. Provides ``avgSleepHrv`` -> ``hrv_last_night``.
- ``coros / sleep`` -- each ``dayList[]`` entry from ``/analyse/dayDetail/query``.
  Provides ``avgSleepHrv`` and ``rhr``.
- ``coros / heart_rate`` -- a verbatim duplicate of the ``sleep`` payload (the
  connector stores it twice under different labels so transforms can read what
  they need without coordination). Provides ``rhr``.

**`wellness_day` columns Coros currently populates:**

- ``resting_hr``      <- ``rhr`` (from sleep / heart_rate)
- ``hrv_last_night``  <- ``avgSleepHrv`` (from hrv / sleep)

All other ``wellness_day`` columns (sleep stages, sleep score, body battery,
stress, steps) are NOT exposed by the Coros endpoints surfaced in 18-01. They
remain NULL in the Coros payload, which means the COALESCE preserves Garmin's
value if Garmin wrote one. Future endpoints (e.g. the mobile-app
``/coros/data/statistic/daily``) could extend this list; the transform is
already wired to absorb any extra column by name.

Like the Garmin wellness transform, this module is deterministic, idempotent,
and performs **no network I/O** -- the whole point of the raw->structured split
is that the structured layer is rebuildable from stored raw at any time via
``runos rederive``. Malformed payloads are logged and skipped, never raised, so
one bad raw row never breaks a transform pass.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from runos.transforms.coerce import _opt_float, _opt_int

logger = logging.getLogger(__name__)

SOURCE = "coros"

# Endpoint label constants (mirror the connector's EP_* names so this module is
# the canonical reader for them and the names stay aligned).
EP_HRV = "hrv"
EP_SLEEP = "sleep"
EP_HEART_RATE = "heart_rate"


# Columns this transform can populate. Anything not in this list survives the
# Coros pass untouched (Garmin's value, or NULL if neither source wrote it).
# Listed here in one place so the UPSERT, the COALESCE list, and the dataclass
# all stay aligned by construction. If a future Coros endpoint provides a new
# column (e.g. ``sleep_score`` from the mobile API), add it once here and to
# :class:`CorosWellnessRow`, and extend the relevant ``parse_*`` function.
COROS_WELLNESS_COLUMNS: tuple[str, ...] = (
    "resting_hr",
    "hrv_last_night",
)


@dataclass(frozen=True, slots=True)
class CorosWellnessRow:
    """One day's projection of Coros raw payloads onto wellness_day columns.

    Field set is intentionally narrow -- only what the 18-01 Coros endpoints
    can populate. Every value is ``None`` until at least one raw payload sets
    it; ``has_any_value`` reports whether the row carries any signal at all
    (an all-None row is skipped at write time so the COALESCE-UPDATE doesn't
    perform a no-op update against an existing Garmin row).
    """

    day: str
    resting_hr: int | None
    hrv_last_night: float | None

    @property
    def has_any_value(self) -> bool:
        """``True`` if at least one metric column carries a non-NULL value."""
        return any(getattr(self, col) is not None for col in COROS_WELLNESS_COLUMNS)


# ---------------------------------------------------------------------------
# Pure payload -> partial-field functions (no DB, no network)
# ---------------------------------------------------------------------------


def parse_hrv(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract HRV fields from one entry of the dashboard's ``sleepHrvList``.

    Each entry carries ``avgSleepHrv`` (the overnight average HRV value, ms)
    and the per-night baseline. We only project ``avgSleepHrv`` onto
    ``wellness_day.hrv_last_night``; the baseline is informational and not
    stored in ``wellness_day``.
    """
    return {
        "hrv_last_night": _opt_float(payload.get("avgSleepHrv")),
    }


def parse_sleep(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract sleep-side fields from one ``dayList[]`` entry of dayDetail.

    The ``/analyse/dayDetail/query`` payload doesn't surface sleep-stage
    breakdowns or a sleep score (those live behind the mobile-app endpoint,
    out of v1.7 scope). It does report ``avgSleepHrv`` -- so this transform
    treats the dayDetail payload as a secondary HRV source: if the dashboard
    didn't capture a day, the dayDetail entry can still fill ``hrv_last_night``.
    """
    return {
        "hrv_last_night": _opt_float(payload.get("avgSleepHrv")),
    }


def parse_heart_rate(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract resting heart rate from one ``dayList[]`` entry.

    The connector stores the dayDetail payload twice (under ``sleep`` and
    ``heart_rate``) so each transform reads the slice it cares about. Here we
    take ``rhr``, the daily resting heart rate that Coros computes from the
    overnight HR series.
    """
    return {
        "resting_hr": _opt_int(payload.get("rhr")),
    }


def build_coros_wellness_row(
    day: str,
    *,
    hrv: dict[str, Any] | None = None,
    sleep: dict[str, Any] | None = None,
    heart_rate: dict[str, Any] | None = None,
) -> CorosWellnessRow:
    """Collapse one day's Coros raw payloads into a :class:`CorosWellnessRow`.

    Pure: a function only of its inputs. Any endpoint may be absent -- its
    fields are simply ``None``. Where two endpoints provide the same field
    (HRV: dashboard vs dayDetail), the **dashboard wins** -- it is the dedicated
    HRV endpoint and tends to have the finalised nightly figure; dayDetail's
    HRV is only used as a fallback when the dashboard didn't include the day.
    """
    fields: dict[str, Any] = {col: None for col in COROS_WELLNESS_COLUMNS}

    # Sleep first (so dashboard HRV can override the dayDetail fallback below).
    if sleep:
        fields.update(parse_sleep(sleep))
    if hrv:
        # Dashboard HRV is the canonical source; overrides any dayDetail HRV.
        hrv_fields = parse_hrv(hrv)
        if hrv_fields.get("hrv_last_night") is not None:
            fields["hrv_last_night"] = hrv_fields["hrv_last_night"]
    if heart_rate:
        fields.update(parse_heart_rate(heart_rate))

    return CorosWellnessRow(day=day, **fields)


# ---------------------------------------------------------------------------
# DB upserts (read raw_response, write structured) -- still no network
# ---------------------------------------------------------------------------


def upsert_coros_wellness(conn: sqlite3.Connection, row: CorosWellnessRow) -> None:
    """Apply Coros's per-(day, metric) priority resolver against ``wellness_day``.

    Two paths:

    1. **No existing row for the day** -> ``INSERT`` a fresh row with only the
       Coros-provided columns set; everything else stays NULL (Garmin can fill
       it later if a subsequent transform pass writes data for the same day,
       though in practice the order is Garmin-first-then-Coros within one pass).

    2. **Existing row for the day** -> ``UPDATE`` each Coros-targeted column to
       ``COALESCE(?, column)``. SQL semantics:

       - Coros value is non-NULL -> COALESCE returns the Coros value -> Coros
         wins.
       - Coros value is NULL     -> COALESCE returns the existing column -> the
         prior Garmin write (or NULL) is preserved.

       This is the LOCKED priority resolver from CONTEXT.md (Coros > Garmin per
       metric).

    Caller owns the transaction. If the row carries no Coros signal at all
    (``has_any_value`` is False), the write is skipped entirely -- nothing to
    resolve, no point churning ``updated_at``.
    """
    if not row.has_any_value:
        return

    existing = conn.execute(
        "SELECT 1 FROM wellness_day WHERE day = ? LIMIT 1", (row.day,)
    ).fetchone()

    if existing is None:
        # Fresh Coros-only day: straight INSERT. Garmin's transform either had
        # no data or already wrote a different day; either way no resolver
        # collision is possible here.
        col_list = ", ".join(["day", *COROS_WELLNESS_COLUMNS])
        value_placeholders = ", ".join(["?"] * (1 + len(COROS_WELLNESS_COLUMNS)))
        values: list[Any] = [row.day]
        values.extend(getattr(row, col) for col in COROS_WELLNESS_COLUMNS)
        conn.execute(
            f"INSERT INTO wellness_day ({col_list}, updated_at) "
            f"VALUES ({value_placeholders}, datetime('now'))",
            values,
        )
        return

    # Existing row -> per-column COALESCE update. The COALESCE pattern IS the
    # priority resolver: a non-NULL Coros value overrides whatever's there; a
    # NULL Coros value preserves the existing (Garmin) value.
    set_clauses = ", ".join(
        f"{col} = COALESCE(?, {col})" for col in COROS_WELLNESS_COLUMNS
    )
    params: list[Any] = [getattr(row, col) for col in COROS_WELLNESS_COLUMNS]
    params.append(row.day)
    conn.execute(
        f"UPDATE wellness_day SET {set_clauses}, updated_at = datetime('now') "
        "WHERE day = ?",
        params,
    )


# ---------------------------------------------------------------------------
# Raw readers (read raw_response, no network)
# ---------------------------------------------------------------------------


def _raw_by_day(conn: sqlite3.Connection, endpoint: str) -> dict[str, dict[str, Any]]:
    """Return ``{entity_key: payload}`` for raw Coros rows of one endpoint.

    Malformed payloads (bad JSON, non-dict body) are logged and skipped so one
    poisoned raw row never breaks the whole transform pass. Mirrors the
    defensive parsing in :mod:`runos.transforms.wellness`.
    """
    rows = conn.execute(
        "SELECT entity_key, payload FROM raw_response WHERE source=? AND endpoint=?",
        (SOURCE, endpoint),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "coros wellness transform: bad %s payload for %s; skipping",
                endpoint,
                r["entity_key"],
            )
            continue
        if not isinstance(payload, dict):
            logger.warning(
                "coros wellness transform: non-dict %s payload for %s; skipping",
                endpoint,
                r["entity_key"],
            )
            continue
        out[str(r["entity_key"])] = payload
    return out


def rebuild_coros_wellness(conn: sqlite3.Connection) -> int:
    """Project every Coros raw wellness row onto ``wellness_day`` via the resolver.

    Reads the three Coros wellness endpoints, groups by ISO calendar day
    (already the raw entity key from 18-01's connector), collapses each day
    into a :class:`CorosWellnessRow`, and upserts via the per-column COALESCE
    resolver. Days that exist in Coros's raw layer but yield no metric value
    are skipped (the connector occasionally stores empty-shell payloads when
    a brand-new account has no EvoLab history).

    Idempotent: re-running yields the same ``wellness_day`` state. Pure DB
    work, no network -- safe inside ``runos rederive``. Caller owns the
    transaction (the runner wraps both wellness transforms in one txn so the
    priority resolver is atomic with the underlying Garmin writes).

    Returns the number of ``wellness_day`` rows touched (insert + update).
    """
    from runos.transforms import spine

    hrv_by_day = _raw_by_day(conn, EP_HRV)
    sleep_by_day = _raw_by_day(conn, EP_SLEEP)
    hr_by_day = _raw_by_day(conn, EP_HEART_RATE)

    all_keys = set(hrv_by_day) | set(sleep_by_day) | set(hr_by_day)
    rows: list[CorosWellnessRow] = []
    for key in sorted(all_keys):
        # Defensive: the entity_key must be a valid ISO date (the connector
        # always writes ISO YYYY-MM-DD; skip anything that isn't, never raise).
        if not _looks_like_iso_date(key):
            logger.warning(
                "coros wellness transform: skipping non-ISO day key %r", key
            )
            continue
        row = build_coros_wellness_row(
            key,
            hrv=hrv_by_day.get(key),
            sleep=sleep_by_day.get(key),
            heart_rate=hr_by_day.get(key),
        )
        if row.has_any_value:
            rows.append(row)

    # Ensure every resolved day exists in the spine before inserting (the
    # wellness_day FK to date_spine would otherwise reject a Coros-only day
    # that Garmin didn't already cover). Idempotent.
    if rows:
        spine.ensure_days(conn, sorted({row.day for row in rows}))
    written = 0
    for row in rows:
        upsert_coros_wellness(conn, row)
        written += 1
    return written


def _looks_like_iso_date(value: str) -> bool:
    """Cheap shape check: ``YYYY-MM-DD`` with valid components.

    The connector always writes ISO dates, but the transform is defensive: a
    legacy or hand-edited raw row with a non-ISO key is skipped with a warning
    rather than allowed to corrupt ``wellness_day.day``.
    """
    if not isinstance(value, str) or len(value) != 10:
        return False
    if value[4] != "-" or value[7] != "-":
        return False
    try:
        from datetime import date

        date.fromisoformat(value)
    except ValueError:
        return False
    return True
