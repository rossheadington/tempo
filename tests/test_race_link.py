"""Edge-case coverage for race <-> Strava activity auto-link (TRACK-03).

Each test pins one row of the edge-case table in
``.planning/phases/08-modular-trackers-heat-adaptation/08-RESEARCH.md`` section 3.
The 7 cases (L1-L7) collectively cover the 0/1/N decision plus the two
"linker doesn't care" rules: no sport filter (L5) and no notion of "today"
(L6 future-dated). L7 covers the defensive table-existence probe.

Tests use a bare ``sqlite3.connect(":memory:")`` plus an inline activity
table seed (the minimal subset of the production schema this linker reads)
so they do not depend on the migration runner or the date_spine FK. The
production code path is exercised verbatim -- only the surrounding plumbing
is stripped away.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from tempo.analysis.races import Race
from tempo.analysis.race_link import RaceLink, link_races_to_activities

# Minimal subset of the activity columns the linker reads (day + activity_id),
# plus `sport` so the no-sport-filter test (L5) can assert a Ride row links
# just as readily as a Run row. The real schema is in
# tempo/migrations/0002_structured.sql; this is a hand-rolled stand-in that
# avoids the date_spine FK requirement.
_ACTIVITY_DDL = """
CREATE TABLE activity (
    activity_id INTEGER PRIMARY KEY,
    day         TEXT NOT NULL,
    sport       TEXT
)
"""


def _fresh_conn() -> sqlite3.Connection:
    """An in-memory SQLite connection with no tables yet."""
    return sqlite3.connect(":memory:")


def _seed_activity_table(conn: sqlite3.Connection, rows: list[tuple[str, int, str]]) -> None:
    """Create the activity table (if absent) and insert ``(day, activity_id, sport)`` rows."""
    conn.execute(_ACTIVITY_DDL)
    conn.executemany(
        "INSERT INTO activity (day, activity_id, sport) VALUES (?, ?, ?)",
        [(day, activity_id, sport) for day, activity_id, sport in rows],
    )


# ---- L1: race day has exactly 1 activity -> linked --------------------------


def test_link_race_with_single_activity_links() -> None:
    conn = _fresh_conn()
    _seed_activity_table(conn, [("2026-04-12", 42, "Run")])
    race = Race(name="Local Half", race_date=date(2026, 4, 12))

    [result] = link_races_to_activities([race], conn)

    assert isinstance(result, RaceLink)
    assert result.race is race
    assert result.link_status == "linked"
    assert result.activity_id == 42


# ---- L2: race day has 0 activities -> unlinked_no_match ---------------------


def test_link_race_with_no_activity_unlinked_no_match() -> None:
    conn = _fresh_conn()
    # Activity table exists but is empty.
    _seed_activity_table(conn, [])
    race = Race(name="Local Half", race_date=date(2026, 4, 12))

    [result] = link_races_to_activities([race], conn)

    assert result.link_status == "unlinked_no_match"
    assert result.activity_id is None


# ---- L3: race day has 2+ activities -> unlinked_ambiguous -------------------


def test_link_race_with_multiple_activities_ambiguous() -> None:
    conn = _fresh_conn()
    _seed_activity_table(
        conn,
        [
            ("2026-04-12", 10, "Run"),
            ("2026-04-12", 11, "Run"),
            ("2026-04-12", 12, "WeightTraining"),
        ],
    )
    race = Race(name="Local Half", race_date=date(2026, 4, 12))

    [result] = link_races_to_activities([race], conn)

    assert result.link_status == "unlinked_ambiguous"
    assert result.activity_id is None


# ---- L4: race has no date -> unlinked_no_date -------------------------------


def test_link_race_with_no_date_unlinked_no_date() -> None:
    conn = _fresh_conn()
    _seed_activity_table(conn, [("2026-04-12", 42, "Run")])
    race = Race(name="Mystery Race", race_date=None)

    [result] = link_races_to_activities([race], conn)

    assert result.link_status == "unlinked_no_date"
    assert result.activity_id is None


# ---- L5: non-Run activity still links (no sport filter) ---------------------


def test_link_race_links_non_run_activities() -> None:
    conn = _fresh_conn()
    # The only activity on race day is a Ride. The linker is sport-agnostic
    # (per CONTEXT.md): the user could be racing a triathlon, a sportive, etc.
    _seed_activity_table(conn, [("2026-07-04", 99, "Ride")])
    race = Race(name="Summer Sportive", race_date=date(2026, 7, 4))

    [result] = link_races_to_activities([race], conn)

    assert result.link_status == "linked"
    assert result.activity_id == 99


# ---- L6: future-dated race with no activity -> unlinked_no_match ------------


def test_link_race_future_dated_unlinked_no_match() -> None:
    conn = _fresh_conn()
    # Some unrelated activity exists -- but not on the future race day.
    _seed_activity_table(conn, [("2026-04-12", 42, "Run")])
    future = date.today() + timedelta(days=365)
    race = Race(name="Berlin Marathon", race_date=future)

    [result] = link_races_to_activities([race], conn)

    # Linker does NOT know about "today" -- past and future no-match collapse
    # to the same status. The renderer decides how to phrase it.
    assert result.link_status == "unlinked_no_match"
    assert result.activity_id is None


# ---- L7: no activity table at all -> unlinked_no_match (defensive) ----------


def test_link_race_no_activity_table_returns_unlinked() -> None:
    # Bare in-memory connection: NO CREATE TABLE executed at all. The
    # defensive sqlite_master probe must skip the SELECT and return
    # unlinked_no_match for every dated race rather than crashing with
    # "no such table: activity" -- mirrors data.srpe_by_day on a pre-Phase-3 DB.
    conn = _fresh_conn()
    race = Race(name="Local Half", race_date=date(2026, 4, 12))

    [result] = link_races_to_activities([race], conn)

    assert result.link_status == "unlinked_no_match"
    assert result.activity_id is None


# ---- Bonus: empty input -> empty output (no scan needed) --------------------


def test_link_empty_races_returns_empty_list() -> None:
    conn = _fresh_conn()
    # No activity table either; the early-return must skip the DB probe.
    assert link_races_to_activities([], conn) == []


# ---- Single DB scan: many races -> one query --------------------------------


def test_link_races_performs_single_db_scan() -> None:
    """Pin the parallel-list + single-scan contract end-to-end."""
    conn = _fresh_conn()
    _seed_activity_table(
        conn,
        [
            ("2026-04-12", 42, "Run"),
            ("2026-05-01", 50, "Ride"),
            ("2026-06-10", 60, "Run"),
            ("2026-06-10", 61, "Run"),
        ],
    )
    races = [
        Race(name="A", race_date=date(2026, 4, 12)),  # linked -> 42
        Race(name="B", race_date=date(2026, 5, 1)),  # linked -> 50
        Race(name="C", race_date=date(2026, 6, 10)),  # ambiguous
        Race(name="D", race_date=date(2026, 9, 1)),  # no_match
        Race(name="E", race_date=None),  # no_date
    ]

    # Subclass to count the SELECT-on-activity calls. The probe is one extra
    # SELECT against sqlite_master, so the activity SELECT itself must happen
    # exactly once. (sqlite3.Connection.execute is read-only on instances --
    # subclassing is the supported override path.)
    activity_selects = 0

    class CountingConn(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):  # type: ignore[override]
            nonlocal activity_selects
            if "FROM activity" in sql and "sqlite_master" not in sql:
                activity_selects += 1
            return super().execute(sql, *args, **kwargs)

    counting_conn = sqlite3.connect(":memory:", factory=CountingConn)
    _seed_activity_table(
        counting_conn,
        [
            ("2026-04-12", 42, "Run"),
            ("2026-05-01", 50, "Ride"),
            ("2026-06-10", 60, "Run"),
            ("2026-06-10", 61, "Run"),
        ],
    )
    # Reset the counter so the seed INSERTs (no SELECT) aren't counted; the
    # _seed_activity_table CREATE+INSERTs don't hit the "FROM activity" branch
    # anyway, but be explicit so the assertion is unambiguous.
    activity_selects = 0

    results = link_races_to_activities(races, counting_conn)

    assert activity_selects == 1, f"expected exactly 1 SELECT on activity, got {activity_selects}"
    assert [r.link_status for r in results] == [
        "linked",
        "linked",
        "unlinked_ambiguous",
        "unlinked_no_match",
        "unlinked_no_date",
    ]
    assert [r.activity_id for r in results] == [42, 50, None, None, None]
    # Parallel ordering: result[i] corresponds to races[i].
    assert [r.race.name for r in results] == ["A", "B", "C", "D", "E"]
