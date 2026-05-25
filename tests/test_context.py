"""Parsing of races.md / plan.md context files, including the missing-file path.

Covers PLAN-01/02. Parsing is lenient: unknown lines ignored, malformed fields
skipped, missing file -> empty result with ``present=False`` (analyses degrade).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tempo.analysis.context import (
    parse_distance,
    parse_goal_time,
    parse_plan,
    parse_races,
)

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


def test_parse_races_bad_date_is_dropped_not_fatal(tmp_path: Path) -> None:
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


# ---- plan.md --------------------------------------------------------------


def test_parse_plan_missing_file(tmp_path: Path) -> None:
    ctx = parse_plan(tmp_path / "nope.md")
    assert ctx.present is False
    assert ctx.text == ""
    assert ctx.fields == {}


def test_parse_plan_captures_headings_and_fields(tmp_path: Path) -> None:
    p = tmp_path / "plan.md"
    p.write_text(
        "# Training Plan\n\n"
        "Phase: Base building\n"
        "Week: 6 of 16\n"
        "Focus: aerobic volume\n\n"
        "## This week\n\n"
        "- Mon: rest\n",
        encoding="utf-8",
    )
    ctx = parse_plan(p)
    assert ctx.present is True
    assert "Training Plan" in ctx.headings
    assert "This week" in ctx.headings
    assert ctx.fields["phase"] == "Base building"
    assert ctx.fields["week"] == "6 of 16"
    assert ctx.fields["focus"] == "aerobic volume"
    assert "Mon: rest" in ctx.text


def test_parse_plan_first_field_wins(tmp_path: Path) -> None:
    p = tmp_path / "plan.md"
    p.write_text("Phase: Base\nPhase: Peak\n", encoding="utf-8")
    ctx = parse_plan(p)
    assert ctx.fields["phase"] == "Base"


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
