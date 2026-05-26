"""The validated journal service: RPE validation, activity resolution, sRPE.

Covers JRNL-01/02 at the service level. We seed synthetic Strava-shaped activities
through the real transform path (raw -> structured via run_rederive), then exercise
``add_entry`` / ``resolve_activity`` directly. No network, no credentials.
"""

from __future__ import annotations

import sqlite3

import pytest

from tempo.connectors.base import RawWriter
from tempo.journal import (
    JournalError,
    MultipleActivitiesError,
    add_entry,
    compute_srpe,
    list_entries,
    resolve_activity,
)
from tempo.journal.service import _validate_rpe
from tempo.transforms.runner import run_rederive
from tests.strava_fakes import make_run


def _seed_activities(conn: sqlite3.Connection, runs: list[dict]) -> None:
    """Insert raw run payloads then transform them into the structured layer."""
    raw = RawWriter(conn, "strava")
    with conn:
        for payload in runs:
            raw.put("activity_summary", str(payload["id"]), payload)
    run_rederive(conn)


# ---- RPE validation (JRNL-01: reject 0, 11, non-int) ----------------------


@pytest.mark.parametrize("bad", [0, 11, -1, 100])
def test_rpe_out_of_range_rejected(conn: sqlite3.Connection, bad: int) -> None:
    with pytest.raises(JournalError, match="between 1 and 10"):
        add_entry(conn, day="2026-05-01", rpe=bad)


@pytest.mark.parametrize("bad", ["abc", "", "  ", 7.5, None, True, False])
def test_rpe_non_integer_rejected(conn: sqlite3.Connection, bad: object) -> None:
    with pytest.raises(JournalError):
        add_entry(conn, day="2026-05-01", rpe=bad)  # type: ignore[arg-type]


def test_rpe_string_integer_accepted(conn: sqlite3.Connection) -> None:
    # A clean integer-as-string is coerced (CLI / tool callers may pass strings).
    entry = add_entry(conn, day="2026-05-01", rpe="7")  # type: ignore[arg-type]
    assert entry.rpe == 7


def test_rpe_float_whole_number_accepted(conn: sqlite3.Connection) -> None:
    entry = add_entry(conn, day="2026-05-01", rpe=8.0)  # type: ignore[arg-type]
    assert entry.rpe == 8


@pytest.mark.parametrize("good", [1, 5, 10])
def test_rpe_boundaries_accepted(conn: sqlite3.Connection, good: int) -> None:
    entry = add_entry(conn, day="2026-05-15", rpe=good)
    assert entry.rpe == good


def test_validate_rpe_unit() -> None:
    assert _validate_rpe(5) == 5
    with pytest.raises(JournalError):
        _validate_rpe(0)


def test_invalid_day_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(JournalError, match="ISO YYYY-MM-DD"):
        add_entry(conn, day="not-a-date", rpe=5)


def test_negative_duration_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(JournalError, match="positive"):
        add_entry(conn, day="2026-05-01", rpe=5, duration_min=-10)


# ---- activity resolution by date + sport (none / one / many) --------------


def test_resolve_zero_matches_is_rest_day(conn: sqlite3.Connection) -> None:
    # No activities on this day -> unlinked rest-day reflection.
    match = resolve_activity(conn, day="2026-05-09", sport="Run")
    assert match is None
    entry = add_entry(conn, day="2026-05-09", rpe=4, notes="rest day, legs sore")
    assert entry.activity_id is None


def test_resolve_one_match_links_automatically(conn: sqlite3.Connection) -> None:
    _seed_activities(conn, [make_run(1, day="2026-05-10", moving_time=3600)])
    match = resolve_activity(conn, day="2026-05-10", sport="Run")
    assert match is not None
    assert match.activity_id == 1
    entry = add_entry(conn, day="2026-05-10", rpe=6, sport="Run")
    assert entry.activity_id == 1


def test_resolve_sport_filter_excludes_other_sports(conn: sqlite3.Connection) -> None:
    _seed_activities(
        conn,
        [
            make_run(1, day="2026-05-11", sport_type="Run"),
            make_run(2, day="2026-05-11", sport_type="Ride"),
        ],
    )
    # Filtering by Run leaves exactly one match even though two activities exist.
    match = resolve_activity(conn, day="2026-05-11", sport="Run")
    assert match is not None and match.activity_id == 1


def test_resolve_many_matches_raises(conn: sqlite3.Connection) -> None:
    _seed_activities(
        conn,
        [
            make_run(1, day="2026-05-12", sport_type="Run"),
            make_run(2, day="2026-05-12", sport_type="Run"),
        ],
    )
    with pytest.raises(MultipleActivitiesError) as exc:
        resolve_activity(conn, day="2026-05-12", sport="Run")
    assert {c.activity_id for c in exc.value.candidates} == {1, 2}
    # add_entry surfaces the same error rather than guessing.
    with pytest.raises(MultipleActivitiesError):
        add_entry(conn, day="2026-05-12", rpe=7, sport="Run")


def test_resolve_many_matches_disambiguated_by_explicit_id(conn: sqlite3.Connection) -> None:
    _seed_activities(
        conn,
        [
            make_run(1, day="2026-05-13", sport_type="Run"),
            make_run(2, day="2026-05-13", sport_type="Run"),
        ],
    )
    entry = add_entry(conn, day="2026-05-13", rpe=7, sport="Run", activity_id=2)
    assert entry.activity_id == 2


def test_explicit_activity_id_must_exist(conn: sqlite3.Connection) -> None:
    with pytest.raises(JournalError, match="no activity with id"):
        add_entry(conn, day="2026-05-13", rpe=7, activity_id=999)


def test_sport_matching_is_case_insensitive(conn: sqlite3.Connection) -> None:
    _seed_activities(conn, [make_run(1, day="2026-05-14", sport_type="TrailRun")])
    match = resolve_activity(conn, day="2026-05-14", sport="trailrun")
    assert match is not None and match.activity_id == 1


# ---- sRPE computation (linked duration vs explicit) -----------------------


def test_srpe_from_linked_activity_duration(conn: sqlite3.Connection) -> None:
    # 3600 s moving time = 60 min; RPE 7 -> sRPE 420.
    _seed_activities(conn, [make_run(1, day="2026-05-15", moving_time=3600)])
    entry = add_entry(conn, day="2026-05-15", rpe=7, sport="Run")
    assert entry.activity_id == 1
    assert entry.duration_min == pytest.approx(60.0)
    assert entry.srpe == pytest.approx(420.0)


def test_srpe_from_explicit_duration_no_activity(conn: sqlite3.Connection) -> None:
    # Cross-training with no Strava activity: explicit duration drives sRPE.
    entry = add_entry(conn, day="2026-05-16", rpe=5, sport="Strength", duration_min=45)
    assert entry.activity_id is None
    assert entry.srpe == pytest.approx(225.0)  # 5 * 45
    assert entry.sport == "Strength"


def test_explicit_duration_overrides_linked_activity(conn: sqlite3.Connection) -> None:
    _seed_activities(conn, [make_run(1, day="2026-05-17", moving_time=3600)])
    entry = add_entry(conn, day="2026-05-17", rpe=6, sport="Run", duration_min=30)
    assert entry.activity_id == 1
    assert entry.duration_min == pytest.approx(30.0)
    assert entry.srpe == pytest.approx(180.0)  # 6 * 30, not 6 * 60


def test_srpe_none_when_no_duration(conn: sqlite3.Connection) -> None:
    entry = add_entry(conn, day="2026-05-18", rpe=8, notes="felt great, forgot to time it")
    assert entry.srpe is None
    assert entry.duration_min is None


def test_compute_srpe_unit() -> None:
    assert compute_srpe(7, 60.0) == pytest.approx(420.0)
    assert compute_srpe(5, None) is None
    assert compute_srpe(5, 0) is None


# ---- persistence + listing ------------------------------------------------


def test_entry_persisted_and_listed(conn: sqlite3.Connection) -> None:
    add_entry(conn, day="2026-05-19", rpe=6, feel="ok", notes="steady")
    add_entry(conn, day="2026-05-20", rpe=8, feel="strong")
    entries = list_entries(conn)
    assert len(entries) == 2
    # Most recent day first.
    assert entries[0].day == "2026-05-20"
    assert entries[0].feel == "strong"
    assert entries[1].notes == "steady"


def test_empty_notes_become_none(conn: sqlite3.Connection) -> None:
    entry = add_entry(conn, day="2026-05-21", rpe=5, notes="   ", feel="")
    assert entry.notes is None
    assert entry.feel is None


def test_failed_validation_writes_nothing(conn: sqlite3.Connection) -> None:
    with pytest.raises(JournalError):
        add_entry(conn, day="2026-05-22", rpe=99)
    assert list_entries(conn) == []


def test_unlinked_entry_creates_spine_day(conn: sqlite3.Connection) -> None:
    # A rest-day reflection on a day with no activity must still create a spine
    # row so the FK holds and the entry appears in daily_summary.
    add_entry(conn, day="2026-06-01", rpe=3, notes="full rest")
    spine = conn.execute("SELECT COUNT(*) FROM date_spine WHERE day='2026-06-01'").fetchone()[0]
    assert spine == 1
