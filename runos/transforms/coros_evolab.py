"""Pure transform: raw Coros ``evolab_dashboard`` payloads -> structured ``coros_evolab_day``.

The Coros connector (Phase 18, wave 18-01) writes one verbatim ``t7dayList``
entry per day to ``raw_response`` under ``(source='coros', endpoint='evolab_dashboard',
entity_key=<ISO YYYY-MM-DD>)``. Each entry has the shape::

    {
        "happenDay": 20260529,        # YYYYMMDD int
        "vo2max":    56.4,            # ml/kg/min, may be 0/null on first-ever day
        "staminaLevel": 62,           # 0-100 base-fitness score
        "trainingLoad": 412,          # Coros's load score
        "lthr":      172,             # lactate-threshold HR (bpm)
        "ltsp":      245,             # lactate-threshold *pace*, seconds-per-km
        ...
    }

This module is the silver-layer projection: read all raw rows, group by
``happenDay`` (the authoritative local day), upsert one
``coros_evolab_day`` row per day. Deterministic, idempotent, **no network**
(STORE-02; ARCHITECTURE Anti-Pattern 1) -- ``runos rederive`` rebuilds the
table purely from raw.

**``ltsp`` unit -- LOCKED, no conversion applied.** The unofficial Coros API
reports ``ltsp`` already as **seconds-per-kilometre** (verified against the
cygnusb/coros-mcp reference implementation -- ``DailyRecord.ltsp`` field
comment: ``"lactate threshold pace (s/km)"``). A real runner's threshold
pace falls in roughly 180-450 s/km (3:00/km to 7:30/km); these values are
nowhere near the m/s (1-10) or km/h (5-30) bands that would suggest a
different unit. We therefore pass it straight through as ``int`` and store
it as ``ltsp_s_per_km``. If a future API surface emits a different unit, the
column rename + a coercion helper here is the only change needed.

Fields *not* parsed (per 18-CONTEXT.md, after wave 18-01 surfaced the real
payload): ``recovery_pct`` and race predictions don't exist in
``/analyse/query``. Don't fabricate; don't add columns; revisit if Coros
exposes them later.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from runos.transforms.coerce import _opt_float, _opt_int

logger = logging.getLogger(__name__)

SOURCE = "coros"

# Raw endpoint label written by runos.connectors.coros (wave 18-01). The
# constant is duplicated here intentionally rather than imported from the
# connector module: the transform layer must never import from the connector
# layer (network-bearing import) so a transform/rederive stays guaranteed
# zero-network.
EP_EVOLAB = "evolab_dashboard"


@dataclass(frozen=True, slots=True)
class EvoLabRow:
    """A typed, structured Coros EvoLab day -- the projection of one t7dayList entry."""

    day: str
    vo2max: float | None
    stamina_level: int | None
    training_load: int | None
    lthr: int | None
    ltsp_s_per_km: int | None
    fetched_at: str


# ---------------------------------------------------------------------------
# Pure payload -> row (no DB, no network)
# ---------------------------------------------------------------------------


def _happen_day_to_iso(value: Any) -> str | None:
    """Convert Coros's ``happenDay`` (YYYYMMDD int/str) to ISO ``YYYY-MM-DD``.

    Mirrors ``runos.connectors.coros._coros_day_to_iso`` but kept local so the
    transform layer carries no dependency on the connector module. Returns
    ``None`` if the value cannot be parsed -- callers skip those entries.
    """
    if value is None:
        return None
    s = str(value)
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def transform_evolab_entry(payload: dict[str, Any], *, fetched_at: str) -> EvoLabRow | None:
    """Project one raw ``t7dayList`` entry into an :class:`EvoLabRow`.

    Pure: a function only of ``payload`` + ``fetched_at``. Missing optional
    metric fields become ``None`` so the row still lands and the recovery
    report (wave 18-04) renders whatever is present. Returns ``None`` when
    ``happenDay`` is absent or unparseable (the row has nowhere to land
    without a day).
    """
    if not isinstance(payload, dict):
        return None

    day = _happen_day_to_iso(payload.get("happenDay"))
    if day is None:
        return None

    return EvoLabRow(
        day=day,
        vo2max=_opt_float(payload.get("vo2max")),
        stamina_level=_opt_int(payload.get("staminaLevel")),
        training_load=_opt_int(payload.get("trainingLoad")),
        lthr=_opt_int(payload.get("lthr")),
        # `ltsp` already in s/km per the upstream API contract (see module
        # docstring). Pass through verbatim as int; no conversion.
        ltsp_s_per_km=_opt_int(payload.get("ltsp")),
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# DB upserts (read raw_response, write structured) -- still no network
# ---------------------------------------------------------------------------


def upsert_evolab(conn: sqlite3.Connection, row: EvoLabRow) -> None:
    """Idempotently upsert one structured EvoLab row (caller owns the txn).

    ``fetched_at`` refreshes on every upsert so the recovery report's 3-state
    staleness check can detect a stalled connector.
    """
    conn.execute(
        """
        INSERT INTO coros_evolab_day (
            day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at
        ) VALUES (
            :day, :vo2max, :stamina_level, :training_load, :lthr, :ltsp_s_per_km, :fetched_at
        )
        ON CONFLICT (day) DO UPDATE SET
            vo2max=excluded.vo2max,
            stamina_level=excluded.stamina_level,
            training_load=excluded.training_load,
            lthr=excluded.lthr,
            ltsp_s_per_km=excluded.ltsp_s_per_km,
            fetched_at=excluded.fetched_at
        """,
        {field: getattr(row, field) for field in row.__slots__},
    )


def _evolab_payloads(conn: sqlite3.Connection) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(entity_key, payload)`` for raw Coros EvoLab rows.

    Malformed (non-JSON or non-dict) payloads are skipped with a log warning
    rather than raising -- the transform must degrade gracefully so one corrupt
    raw row never blocks the rebuild.
    """
    rows = conn.execute(
        "SELECT entity_key, payload FROM raw_response WHERE source=? AND endpoint=?",
        (SOURCE, EP_EVOLAB),
    ).fetchall()
    out: list[tuple[str, dict[str, Any]]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except json.JSONDecodeError:
            logger.warning(
                "coros evolab transform: bad JSON for raw key %s; skipping",
                r["entity_key"],
            )
            continue
        if not isinstance(payload, dict):
            logger.warning(
                "coros evolab transform: non-dict payload for raw key %s; skipping",
                r["entity_key"],
            )
            continue
        out.append((str(r["entity_key"]), payload))
    return out


def rebuild_evolab(conn: sqlite3.Connection) -> int:
    """Rebuild every structured ``coros_evolab_day`` row from stored raw payloads.

    Iterates raw ``evolab_dashboard`` rows, projects each onto an
    :class:`EvoLabRow`, ensures the referenced day exists in ``date_spine``
    (so the FK resolves), and upserts. Pure DB work (no network); safe to
    re-run. Returns the number of EvoLab rows written.

    On a re-run, ``fetched_at`` is refreshed to mark this pass's timestamp --
    the row content otherwise matches the previous rebuild byte-for-byte for
    unchanged input.
    """
    from runos.transforms import spine

    fetched_at = datetime.now(UTC).isoformat()

    rows: list[EvoLabRow] = []
    for entity_key, payload in _evolab_payloads(conn):
        row = transform_evolab_entry(payload, fetched_at=fetched_at)
        if row is None:
            logger.warning(
                "coros evolab transform: skipping raw key %s (no happenDay)",
                entity_key,
            )
            continue
        rows.append(row)

    # Ensure every referenced day exists in the spine before inserting EvoLab
    # rows, so the FK to date_spine(day) is satisfied without relying on
    # deferred FK mode.
    spine.ensure_days(conn, sorted({row.day for row in rows}))
    for row in rows:
        upsert_evolab(conn, row)
    return len(rows)
