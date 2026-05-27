"""Parsing of races.md: missing-file path, result field, upcoming/completed helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tempo.analysis.races import parse_distance, parse_goal_time, parse_races

# ---- races.md -------------------------------------------------------------


def test_parse_races_missing_file(tmp_path: Path) -> None:
    ctx = parse_races(tmp_path / "nope.md")
    assert ctx.present is False
    assert ctx.races == []


def test_parse_races_basic(tmp_path: Path) -> None:
    p = tmp_path / "races.md"
    p.write_text(
        "# My races\n\n"
        "- Berlin Marathon - date: 2026-09-27 | distance: marathon | goal: 3:15:00 | priority: A\n"
        "- Club 10k - date: 2026-06-07 | distance: 10k | goal: 39:30 | priority: C\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    assert ctx.present is True
    assert len(ctx.races) == 2
    berlin = ctx.races[0]
    assert berlin.name == "Berlin Marathon"
    assert berlin.race_date == date(2026, 9, 27)
    assert berlin.distance_m == pytest.approx(42195.0)
    assert berlin.goal_time_s == pytest.approx(3 * 3600 + 15 * 60)
    assert berlin.priority == "A"
    assert berlin.result is None


def test_parse_races_ignores_prose_and_headings(tmp_path: Path) -> None:
    # Documentation-style bullets (no recognised race fields) must not become races.
    p = tmp_path / "races.md"
    p.write_text(
        "# Format\n\n"
        "Recognised keys:\n\n"
        "- `date` - ISO date YYYY-MM-DD\n"
        "- `distance` - marathon, half, 10k\n\n"
        "## Races\n\n"
        "- Spring Half - date: 2026-03-15 | distance: half\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    assert len(ctx.races) == 1
    assert ctx.races[0].name == "Spring Half"


def test_parse_races_lenient_partial_fields(tmp_path: Path) -> None:
    p = tmp_path / "races.md"
    p.write_text("- Mystery Race - distance: 10k\n", encoding="utf-8")
    ctx = parse_races(p)
    assert len(ctx.races) == 1
    r = ctx.races[0]
    assert r.race_date is None
    assert r.goal is None
    assert r.distance_m == pytest.approx(10000.0)


def test_parse_races_bad_date_keeps_race(tmp_path: Path) -> None:
    # Unparseable date keeps the race with race_date=None (existing lenient behaviour;
    # contrast with the heat tracker which drops entries lacking a parseable date).
    p = tmp_path / "races.md"
    p.write_text("- Race X - date: not-a-date | distance: 5k\n", encoding="utf-8")
    ctx = parse_races(p)
    assert len(ctx.races) == 1
    assert ctx.races[0].race_date is None


def test_upcoming_sorts_and_filters(tmp_path: Path) -> None:
    p = tmp_path / "races.md"
    p.write_text(
        "- Past - date: 2026-01-01 | distance: 5k\n"
        "- Later - date: 2026-12-01 | distance: marathon\n"
        "- Soon - date: 2026-06-01 | distance: 10k\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    upcoming = ctx.upcoming(date(2026, 5, 1))
    assert [r.name for r in upcoming] == ["Soon", "Later"]


# ---- result field (NEW: Res1, Res2) ---------------------------------------


def test_parse_races_result_field_verbatim(tmp_path: Path) -> None:
    """`result: 1:31:48` lands verbatim on Race.result -- no parsing into seconds."""
    p = tmp_path / "races.md"
    p.write_text(
        "- Local Half - date: 2026-04-12 | distance: half | goal: 1:32:00"
        " | priority: B | result: 1:31:48\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    assert len(ctx.races) == 1
    r = ctx.races[0]
    assert r.result == "1:31:48"


def test_parse_races_result_freeform_strings(tmp_path: Path) -> None:
    """`result:` accepts arbitrary verbatim strings with no normalisation."""
    p = tmp_path / "races.md"
    p.write_text(
        "- DNF Race - date: 2026-04-12 | distance: marathon | result: DNF\n"
        "- PB Race - date: 2026-05-01 | distance: 10k | result: 39:42 (course PB)\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    by_name = {r.name: r for r in ctx.races}
    assert by_name["DNF Race"].result == "DNF"
    assert by_name["PB Race"].result == "39:42 (course PB)"


# ---- completed(today) (NEW: C1, C2, C3) -----------------------------------


def test_completed_sorts_most_recent_first(tmp_path: Path) -> None:
    """Three past-dated races returned in reverse-chronological order."""
    p = tmp_path / "races.md"
    p.write_text(
        "- A Oldest - date: 2026-01-01 | distance: 5k | result: 22:10\n"
        "- B Middle - date: 2026-03-15 | distance: half | result: 1:32:11\n"
        "- C Newest - date: 2026-04-12 | distance: 10k | result: 39:42\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    completed = ctx.completed(date(2026, 5, 1))
    assert [r.name for r in completed] == ["C Newest", "B Middle", "A Oldest"]


def test_completed_excludes_future_and_today(tmp_path: Path) -> None:
    """A race dated today is NOT completed (strict <); future races excluded."""
    p = tmp_path / "races.md"
    p.write_text(
        "- Today Race - date: 2026-05-01 | distance: 10k\n"
        "- Yesterday Race - date: 2026-04-30 | distance: 5k\n"
        "- Tomorrow Race - date: 2026-05-02 | distance: half\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    completed = ctx.completed(date(2026, 5, 1))
    assert [r.name for r in completed] == ["Yesterday Race"]


def test_completed_excludes_undated_races(tmp_path: Path) -> None:
    """A Race with race_date=None does not appear in completed."""
    p = tmp_path / "races.md"
    p.write_text(
        "- Undated - distance: marathon | result: 3:17:42\n"
        "- Past - date: 2026-04-30 | distance: 10k | result: 39:30\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    completed = ctx.completed(date(2026, 5, 1))
    assert [r.name for r in completed] == ["Past"]


# ---- helpers --------------------------------------------------------------


def test_parse_goal_time_variants() -> None:
    assert parse_goal_time("3:15:00") == pytest.approx(11700.0)
    assert parse_goal_time("39:30") == pytest.approx(2370.0)
    assert parse_goal_time("sub-3") is None


def test_parse_distance_named_and_numeric() -> None:
    assert parse_distance("marathon")[0] == pytest.approx(42195.0)
    assert parse_distance("half")[0] == pytest.approx(21097.5)
    assert parse_distance("10k")[0] == pytest.approx(10000.0)
    assert parse_distance("42.195km")[0] == pytest.approx(42195.0)
    assert parse_distance("21100m")[0] == pytest.approx(21100.0)
    assert parse_distance("13.1mi")[0] == pytest.approx(13.1 * 1609.34)
    assert parse_distance("gibberish")[0] is None
