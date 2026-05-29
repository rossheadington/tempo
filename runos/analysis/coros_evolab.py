"""Read structured ``coros_evolab_day`` rows for the recovery report.

Pure stdlib, **no network** (the analysis layer never calls out -- enforced
by the ``socket``-blocking test on ``rederive``). Reads the silver table
written by :mod:`runos.transforms.coros_evolab` and returns frozen+slots
dataclasses for the downstream renderer (wave 18-04).

The table is small (one row per day, lifetime measured in hundreds-to-low-
thousands of rows) so a "load everything, sort ascending, expose convenience
``latest``" pattern is the right call -- mirroring
:mod:`runos.analysis.heat` / :mod:`runos.analysis.weight`.

"Latest" semantics: the most recent day where *at least one* metric column
is non-NULL. A row that exists in the table but carries only NULLs (e.g. a
day Coros wrote a sentinel for but had no actual reading) is treated as
absent -- the recovery report shouldn't render a hollow block.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class EvoLabDay:
    """One day's Coros EvoLab metrics -- the silver-layer projection.

    All metric fields are nullable: a brand-new account or a missed-watch day
    may produce partial data, which the recovery report still renders for
    whatever IS present.
    """

    day: date
    vo2max: float | None
    stamina_level: int | None
    training_load: int | None
    lthr: int | None
    ltsp_s_per_km: int | None
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class EvoLabContext:
    """The parsed ``coros_evolab_day`` table for the recovery report.

    ``present`` is ``False`` when the table contains zero rows OR when every
    row is all-NULL (no usable metric anywhere). ``days`` is sorted ascending
    by day. ``latest`` is the most recent day with any non-NULL metric, or
    ``None`` if none exists.
    """

    present: bool
    days: tuple[EvoLabDay, ...]
    latest: EvoLabDay | None


def _parse_fetched_at(value: str) -> datetime:
    """Parse the transform's ISO 8601 UTC ``fetched_at`` back into a datetime.

    The transform writes ``datetime.now(UTC).isoformat()`` which round-trips
    cleanly through :meth:`datetime.fromisoformat` on Python 3.11+.
    """
    return datetime.fromisoformat(value)


def _row_has_any_metric(row: EvoLabDay) -> bool:
    """True if any of the metric columns carry a value (vs all-NULL)."""
    return any(
        v is not None
        for v in (
            row.vo2max,
            row.stamina_level,
            row.training_load,
            row.lthr,
            row.ltsp_s_per_km,
        )
    )


def read_evolab(conn: sqlite3.Connection) -> EvoLabContext:
    """Read ALL ``coros_evolab_day`` rows; return an :class:`EvoLabContext`.

    Sorted ascending by day. ``latest`` is the most recent day with at least
    one non-NULL metric column (an all-NULL row doesn't qualify -- see
    ``EvoLabContext`` docstring). Returns an absent context if the table is
    empty.

    Defensive: if the ``coros_evolab_day`` table doesn't exist (pre-Phase-18
    schema, e.g. mid-migration in a test), returns an absent context rather
    than raising.
    """
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='coros_evolab_day'"
    ).fetchone()
    if has_table is None:
        return EvoLabContext(present=False, days=(), latest=None)

    rows = conn.execute(
        """
        SELECT day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at
        FROM coros_evolab_day
        ORDER BY day ASC
        """
    ).fetchall()

    days: list[EvoLabDay] = []
    for r in rows:
        days.append(
            EvoLabDay(
                day=date.fromisoformat(str(r["day"])),
                vo2max=r["vo2max"],
                stamina_level=r["stamina_level"],
                training_load=r["training_load"],
                lthr=r["lthr"],
                ltsp_s_per_km=r["ltsp_s_per_km"],
                fetched_at=_parse_fetched_at(str(r["fetched_at"])),
            )
        )

    # Latest = most recent day with at least one non-NULL metric. Iterate from
    # the tail so the first hit wins.
    latest: EvoLabDay | None = None
    for row in reversed(days):
        if _row_has_any_metric(row):
            latest = row
            break

    present = latest is not None
    return EvoLabContext(present=present, days=tuple(days), latest=latest)
