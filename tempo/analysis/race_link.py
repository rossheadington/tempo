"""Join races (parsed from ``races.md``) to Strava activities by local date.

This is a small read-only composition layer above :mod:`tempo.analysis.data`
that implements the race-to-activity auto-link for TRACK-03. Given the list of
:class:`~tempo.analysis.races.Race` objects parsed from the user's
``races.md`` and a SQLite connection over the structured store, it produces a
parallel list of :class:`RaceLink` results -- one per input race, in the same
order -- classifying each race against the activities on its local date.

The 0/1/N convention mirrors :mod:`tempo.journal.service` (Phase 5) with one
deliberate behavioural difference:

* journal-service **raises** :class:`MultipleActivitiesError` when several
  activities match a day (because writes must be unambiguous);
* race-link **returns** ``link_status='unlinked_ambiguous'`` instead, because
  analyses must never crash on a multi-activity race day -- the renderer
  decides how to phrase the ambiguity.

There is deliberately **no sport filter**: a race might be ridden, swum, or
run, so the linker matches whatever activity sits on the race's local date.
The renderer (Plan D) decides what to say if the linked activity's sport
looks "wrong" for the race distance.

The single ``link_races_to_activities`` call performs at most ONE SQL query
regardless of how many races are passed in: it loads every ``(day,
activity_id)`` pair into an in-memory dict and classifies in pure Python.
Pure stdlib, no network, no writes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from tempo.analysis.races import Race


@dataclass(frozen=True, slots=True)
class RaceLink:
    """Result of joining a Race to the Strava activity on its local date (TRACK-03)."""

    race: Race
    activity_id: int | None
    link_status: str  # 'linked' | 'unlinked_no_match' | 'unlinked_ambiguous' | 'unlinked_no_date'


def link_races_to_activities(races: list[Race], conn: sqlite3.Connection) -> list[RaceLink]:
    """Classify each race against the activities on its local date.

    Returns a list parallel to ``races`` (same length, same order). Performs at
    most one ``SELECT day, activity_id FROM activity`` query and groups the rows
    by day in memory; classification is then pure Python.

    Per race the result is:

    * ``race_date`` is ``None`` -> ``unlinked_no_date``;
    * 0 activities on that day -> ``unlinked_no_match`` (past or future, same status);
    * exactly 1 -> ``linked`` with ``activity_id`` populated;
    * 2+ -> ``unlinked_ambiguous`` (refuse to guess; mirrors journal-service 0/1/N).

    Defensively: if the ``activity`` table does not yet exist (fresh DB
    pre-Phase-3), the function treats the day map as empty rather than crashing,
    so every dated race becomes ``unlinked_no_match`` (mirrors the defensive
    table-existence probe in :func:`tempo.analysis.data.srpe_by_day`).
    """
    if not races:
        return []

    has_activity = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='activity'"
    ).fetchone()
    day_to_activities: dict[str, list[int]] = {}
    if has_activity is not None:
        rows = conn.execute("SELECT day, activity_id FROM activity").fetchall()
        for row in rows:
            day = str(row[0])
            activity_id = int(row[1])
            day_to_activities.setdefault(day, []).append(activity_id)

    out: list[RaceLink] = []
    for race in races:
        if race.race_date is None:
            out.append(RaceLink(race=race, activity_id=None, link_status="unlinked_no_date"))
            continue
        matches = day_to_activities.get(race.race_date.isoformat(), [])
        if not matches:
            out.append(RaceLink(race=race, activity_id=None, link_status="unlinked_no_match"))
        elif len(matches) == 1:
            out.append(RaceLink(race=race, activity_id=matches[0], link_status="linked"))
        else:
            out.append(RaceLink(race=race, activity_id=None, link_status="unlinked_ambiguous"))
    return out
