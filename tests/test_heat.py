"""Parsing of heat.md plus the rolling-window heat_rollup.

Lenient parser (TRACK-04); inclusive closed-interval windows (TRACK-05). All
tests use only stdlib + pytest's tmp_path fixture; the rollup tests pin a
fixed reference ``today`` so the windows are deterministic.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo.analysis.heat import (
    HeatContext,
    HeatRollup,
    HeatSession,
    heat_rollup,
    parse_heat,
)

# Fixed reference date for all rollup tests so the closed-interval windows
# (today-6 .. today, today-13 .. today, today-27 .. today) are deterministic.
TODAY = date(2026, 5, 27)


def _session(d: date, duration_min: float | None = 20.0) -> HeatSession:
    """Build a minimal HeatSession with the given date / duration."""
    return HeatSession(
        date=d,
        type="sauna",
        duration_min=duration_min,
        temp_c=None,
        hr_avg=None,
        notes=None,
    )


# ---- parse_heat ----------------------------------------------------------


def test_parse_heat_missing_file(tmp_path: Path) -> None:
    ctx = parse_heat(tmp_path / "nope.md")
    assert isinstance(ctx, HeatContext)
    assert ctx.present is False
    assert ctx.sessions == []


def test_parse_heat_basic(tmp_path: Path) -> None:
    p = tmp_path / "heat.md"
    p.write_text(
        "# Heat sessions\n\n"
        "- 2026-05-26 - type: sauna | duration_min: 20 | temp_c: 85 | hr_avg: 105 | "
        "notes: post-run felt easier\n"
        "- 2026-05-24 - type: hot-bath | duration_min: 25 | temp_c: 41\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert ctx.present is True
    assert len(ctx.sessions) == 2

    first = ctx.sessions[0]
    assert first.date == date(2026, 5, 26)
    assert first.type == "sauna"
    assert first.duration_min == pytest.approx(20.0)
    assert first.temp_c == pytest.approx(85.0)
    assert first.hr_avg == pytest.approx(105.0)
    assert first.notes == "post-run felt easier"

    second = ctx.sessions[1]
    assert second.date == date(2026, 5, 24)
    assert second.type == "hot-bath"
    assert second.duration_min == pytest.approx(25.0)
    assert second.temp_c == pytest.approx(41.0)
    assert second.hr_avg is None
    assert second.notes is None


def test_parse_heat_ignores_prose_and_headings(tmp_path: Path) -> None:
    # Documentation-style bullets (no recognised heat fields) must not become
    # sessions, and ``#`` heading lines are skipped outright.
    p = tmp_path / "heat.md"
    p.write_text(
        "# Format\n\n"
        "Recognised keys:\n\n"
        "- `date` - ISO date YYYY-MM-DD\n"
        "- `type` - free-form label\n\n"
        "## Sessions\n\n"
        "- 2026-05-26 - type: sauna | duration_min: 20\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert len(ctx.sessions) == 1
    assert ctx.sessions[0].type == "sauna"
    assert ctx.sessions[0].date == date(2026, 5, 26)


def test_parse_heat_lenient_partial_fields(tmp_path: Path) -> None:
    # A bullet carrying only date + type + duration_min parses cleanly; the
    # optional fields land as None instead of raising or defaulting silently.
    p = tmp_path / "heat.md"
    p.write_text(
        "- 2026-05-18 - type: steam-room | duration_min: 15\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.date == date(2026, 5, 18)
    assert s.type == "steam-room"
    assert s.duration_min == pytest.approx(15.0)
    assert s.temp_c is None
    assert s.hr_avg is None
    assert s.notes is None


def test_parse_heat_unknown_keys_silently_ignored(tmp_path: Path) -> None:
    # ``hr_max:`` is not in the recognised set; the bullet still parses and
    # the unknown field is dropped without affecting anything else.
    p = tmp_path / "heat.md"
    p.write_text(
        "- 2026-05-20 - type: sauna | duration_min: 30 | hr_max: 130\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.date == date(2026, 5, 20)
    assert s.type == "sauna"
    assert s.duration_min == pytest.approx(30.0)
    # Unrecognised hr_max is not silently mapped onto hr_avg.
    assert s.hr_avg is None


def test_parse_heat_bad_date_drops_session(tmp_path: Path) -> None:
    # Inverse of races.md behaviour: a heat entry with an unparseable date AND
    # no leading-date prefix is dropped entirely (the rollup is date-keyed).
    p = tmp_path / "heat.md"
    p.write_text(
        "- bogus - date: tomorrow | type: sauna | duration_min: 20\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert ctx.present is True
    assert ctx.sessions == []


def test_parse_heat_leading_date_prefix_used(tmp_path: Path) -> None:
    # No ``date:`` key, but the bullet leads with an ISO date -- the parser
    # should fall back to that prefix.
    p = tmp_path / "heat.md"
    p.write_text(
        "- 2026-05-20 - type: sauna | duration_min: 20\n",
        encoding="utf-8",
    )
    ctx = parse_heat(p)
    assert len(ctx.sessions) == 1
    assert ctx.sessions[0].date == date(2026, 5, 20)
    assert ctx.sessions[0].type == "sauna"


# ---- heat_rollup ---------------------------------------------------------


def test_heat_rollup_empty_sessions() -> None:
    roll = heat_rollup([], TODAY)
    assert isinstance(roll, HeatRollup)
    assert roll.today == TODAY
    assert roll.last_7d_count == 0
    assert roll.last_7d_minutes == 0.0
    assert roll.last_14d_count == 0
    assert roll.last_14d_minutes == 0.0
    assert roll.last_28d_count == 0
    assert roll.last_28d_minutes == 0.0
    assert roll.last_session_date is None
    assert roll.last_session_days_ago is None


def test_heat_rollup_window_edges_inclusive() -> None:
    # 7d window = [today-6 .. today]: today and today-6 IN, today-7 OUT.
    # 14d window includes today-13 IN, today-14 OUT.
    # 28d window includes today-27 IN, today-28 OUT.
    sessions = [
        _session(TODAY),
        _session(TODAY - timedelta(days=6)),
        _session(TODAY - timedelta(days=7)),
        _session(TODAY - timedelta(days=13)),
        _session(TODAY - timedelta(days=14)),
        _session(TODAY - timedelta(days=27)),
        _session(TODAY - timedelta(days=28)),
    ]
    roll = heat_rollup(sessions, TODAY)
    # 7d: today + today-6 only.
    assert roll.last_7d_count == 2
    # 14d: today, today-6, today-7, today-13 (today-14 excluded).
    assert roll.last_14d_count == 4
    # 28d: everything from today back to today-27 inclusive (today-28 excluded).
    assert roll.last_28d_count == 6


def test_heat_rollup_minutes_sum_skips_unparseable_duration() -> None:
    # One session inside the 7d window with no duration: counted, contributes
    # 0 to minutes (real session, missing the duration field).
    sessions = [_session(TODAY, duration_min=None)]
    roll = heat_rollup(sessions, TODAY)
    assert roll.last_7d_count == 1
    assert roll.last_7d_minutes == 0.0
    assert roll.last_session_date == TODAY
    assert roll.last_session_days_ago == 0


def test_heat_rollup_last_session_today_is_zero_days_ago() -> None:
    # Boundary: a session dated today yields days_ago == 0, not None.
    roll = heat_rollup([_session(TODAY)], TODAY)
    assert roll.last_session_date == TODAY
    assert roll.last_session_days_ago == 0


def test_heat_rollup_filters_future_dated_sessions() -> None:
    # A future-dated session (typo, e.g. ``2027-`` instead of ``2026-``) is
    # filtered out of every window AND does not become the last_session.
    sessions = [_session(TODAY + timedelta(days=5))]
    roll = heat_rollup(sessions, TODAY)
    assert roll.last_7d_count == 0
    assert roll.last_14d_count == 0
    assert roll.last_28d_count == 0
    assert roll.last_session_date is None
    assert roll.last_session_days_ago is None


def test_heat_rollup_multiple_sessions_same_day_each_counted() -> None:
    # Two sessions on the same day (e.g. morning sauna + evening hot-bath)
    # are each counted -- not deduplicated by date.
    sessions = [
        _session(TODAY, duration_min=20.0),
        _session(TODAY, duration_min=15.0),
    ]
    roll = heat_rollup(sessions, TODAY)
    assert roll.last_7d_count == 2
    assert roll.last_7d_minutes == pytest.approx(35.0)
    assert roll.last_session_date == TODAY
    assert roll.last_session_days_ago == 0
