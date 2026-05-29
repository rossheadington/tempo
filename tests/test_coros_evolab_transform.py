"""Tests for raw Coros EvoLab -> coros_evolab_day transform (pure, no network).

Verifies the silver-layer projection from raw ``evolab_dashboard`` payloads:
all-fields projection, NULL-tolerance for missing metrics, idempotence under
rederive, and the ``ltsp`` unit pass-through (Coros already reports s/km --
the cygnusb/coros-mcp reference confirms this -- so the transform must NOT
apply a conversion).
"""

from __future__ import annotations

import json
import sqlite3

from runos.connectors.base import RawWriter
from runos.transforms import coros_evolab
from runos.transforms.coros_evolab import EP_EVOLAB, SOURCE


def _store_raw(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    """Write one verbatim t7dayList entry into the raw store."""
    raw = RawWriter(conn, SOURCE)
    with conn:
        raw.put(EP_EVOLAB, key, payload)


def _evolab_entry(
    happen_day: int = 20260529,
    *,
    vo2max: float | None = 56.4,
    stamina_level: int | None = 62,
    training_load: int | None = 412,
    lthr: int | None = 172,
    ltsp: int | None = 238,
) -> dict:
    """Build a t7dayList entry shaped like the real /analyse/query payload."""
    return {
        "happenDay": happen_day,
        "vo2max": vo2max,
        "staminaLevel": stamina_level,
        "trainingLoad": training_load,
        "lthr": lthr,
        "ltsp": ltsp,
    }


# ---------------------------------------------------------------------------
# All fields project from a full payload
# ---------------------------------------------------------------------------


def test_evolab_payload_projects_all_fields(conn: sqlite3.Connection) -> None:
    """A complete t7dayList entry projects every metric onto coros_evolab_day."""
    _store_raw(conn, "2026-05-29", _evolab_entry())

    with conn:
        n = coros_evolab.rebuild_evolab(conn)
    assert n == 1

    row = conn.execute(
        """
        SELECT day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at
        FROM coros_evolab_day
        """
    ).fetchone()
    assert row["day"] == "2026-05-29"
    assert row["vo2max"] == 56.4
    assert row["stamina_level"] == 62
    assert row["training_load"] == 412
    assert row["lthr"] == 172
    assert row["ltsp_s_per_km"] == 238
    # fetched_at is ISO 8601 UTC stamped at transform time. We don't pin the
    # value, just assert it round-trips through datetime.fromisoformat (the
    # analysis-layer reader relies on that).
    from datetime import datetime

    assert datetime.fromisoformat(str(row["fetched_at"])).tzinfo is not None


# ---------------------------------------------------------------------------
# Missing metric fields remain NULL (not fabricated)
# ---------------------------------------------------------------------------


def test_evolab_missing_fields_remain_none(conn: sqlite3.Connection) -> None:
    """Absent metric keys become NULL in the silver row, never invented."""
    sparse_payload = {"happenDay": 20260528}  # day only; no metrics
    _store_raw(conn, "2026-05-28", sparse_payload)

    with conn:
        n = coros_evolab.rebuild_evolab(conn)
    assert n == 1

    row = conn.execute(
        """
        SELECT vo2max, stamina_level, training_load, lthr, ltsp_s_per_km
        FROM coros_evolab_day WHERE day=?
        """,
        ("2026-05-28",),
    ).fetchone()
    assert row["vo2max"] is None
    assert row["stamina_level"] is None
    assert row["training_load"] is None
    assert row["lthr"] is None
    assert row["ltsp_s_per_km"] is None


# ---------------------------------------------------------------------------
# Rederive is idempotent: same raw -> same row (with refreshed fetched_at)
# ---------------------------------------------------------------------------


def test_evolab_rederive_idempotent(conn: sqlite3.Connection) -> None:
    """Re-running rebuild on the same raw layer yields the same row content.

    ``fetched_at`` may advance (it stamps the rebuild time) but every metric
    column is unchanged; the row count stays at 1.
    """
    _store_raw(conn, "2026-05-29", _evolab_entry(vo2max=55.0, lthr=170))

    with conn:
        n1 = coros_evolab.rebuild_evolab(conn)
        n2 = coros_evolab.rebuild_evolab(conn)
    assert n1 == 1
    assert n2 == 1

    rows = conn.execute(
        "SELECT day, vo2max, lthr, stamina_level FROM coros_evolab_day"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-05-29"
    assert rows[0]["vo2max"] == 55.0
    assert rows[0]["lthr"] == 170
    assert rows[0]["stamina_level"] == 62


# ---------------------------------------------------------------------------
# ltsp unit: Coros already reports s/km. No conversion applied.
# ---------------------------------------------------------------------------


def test_evolab_threshold_pace_parsed_to_s_per_km(conn: sqlite3.Connection) -> None:
    """``ltsp`` in the payload is verbatim s/km; the transform must not convert it.

    Per the cygnusb/coros-mcp reference (``DailyRecord.ltsp`` field comment:
    "lactate threshold pace (s/km)"), the Coros API already reports threshold
    pace as seconds-per-kilometre. A realistic 4:00/km threshold runner emits
    ``ltsp=240``; that value MUST land in ``ltsp_s_per_km`` unchanged.
    """
    # A 4:00/km threshold (240 s/km) -- a realistic value for a serious runner.
    _store_raw(conn, "2026-05-29", _evolab_entry(ltsp=240))
    # A 3:30/km threshold (210 s/km) on a different day.
    _store_raw(conn, "2026-05-28", _evolab_entry(happen_day=20260528, ltsp=210))

    with conn:
        coros_evolab.rebuild_evolab(conn)

    rows = {
        r["day"]: r["ltsp_s_per_km"]
        for r in conn.execute(
            "SELECT day, ltsp_s_per_km FROM coros_evolab_day"
        ).fetchall()
    }
    # Pass-through, no conversion.
    assert rows["2026-05-29"] == 240
    assert rows["2026-05-28"] == 210


# ---------------------------------------------------------------------------
# Defensive: corrupt / un-dated entries are skipped, not fatal.
# (Bonus coverage to match the transform's lenient behaviour.)
# ---------------------------------------------------------------------------


def test_evolab_skips_entries_without_happen_day(conn: sqlite3.Connection) -> None:
    """A raw entry missing ``happenDay`` is skipped, not stored."""
    _store_raw(conn, "unkeyed", {"vo2max": 55.0})  # no happenDay
    _store_raw(conn, "2026-05-29", _evolab_entry())  # valid

    with conn:
        n = coros_evolab.rebuild_evolab(conn)

    # Only the valid entry lands.
    assert n == 1
    rows = conn.execute("SELECT day FROM coros_evolab_day").fetchall()
    assert [r["day"] for r in rows] == ["2026-05-29"]


def test_evolab_skips_non_dict_payload(conn: sqlite3.Connection) -> None:
    """A non-dict raw payload (e.g. a stray list) is logged-and-skipped."""
    with conn:
        conn.execute(
            "INSERT INTO raw_response (source, endpoint, entity_key, payload) "
            "VALUES (?, ?, ?, ?)",
            (SOURCE, EP_EVOLAB, "broken", json.dumps([1, 2, 3])),
        )
    _store_raw(conn, "2026-05-29", _evolab_entry())

    with conn:
        n = coros_evolab.rebuild_evolab(conn)
    assert n == 1
