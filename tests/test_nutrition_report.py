"""Tests for the standalone ``tempo analyze nutrition`` report (NUTR-05, Phase 16-02).

Covers the four locked tests from 16-CONTEXT.md `### Test scope (LOCKED)`
lines 230-233:

1. ``tempo analyze nutrition`` writes a dated markdown file under reports_dir.
2. Today-no-entries case emits the placeholder + omits the per-meal section.
3. Per-meal breakdown subheaders only appear when today has >=1 entry.
4. ``## Goal`` section is omitted entirely when ``target_kcal`` is None and
   present (with signed delta) when set.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from tempo.analysis import runner
from tempo.analysis.load import LoadConfig
from tempo.analysis.nutrition import (
    FoodContext,
    FoodEntry,
    daily_nutrition,
    nutrition_rollup,
)
from tempo.analysis.nutrition_report import render_nutrition

TODAY = date(2026, 5, 28)
CFG = LoadConfig(threshold_pace_s_per_km=None, max_hr=None, resting_hr=None, threshold_hr=None)


def _entry(
    day: date,
    meal: str | None,
    label: str,
    p: float,
    c: float,
    f: float,
    kcal: int,
    line: int,
) -> FoodEntry:
    return FoodEntry(
        date=day,
        meal_name=meal,
        food_label=label,
        protein_g=p,
        carbs_g=c,
        fat_g=f,
        kcal=kcal,
        source_line=line,
        source_format="inline",
    )


def test_nutrition_report_writes_dated_file_to_reports_dir(tmp_path: Path) -> None:
    food = tmp_path / "food.md"
    food.write_text(
        "- 2026-05-28 breakfast: 80g oats | p:13 c:54 f:6 cal:303\n"
        "- 2026-05-28 lunch: chicken salad | p:38 c:22 f:18 cal:404\n",
        encoding="utf-8",
    )
    reports = tmp_path / "reports"
    path = runner.generate_nutrition(
        None,
        cfg=CFG,
        reports_dir=reports,
        generated_on=TODAY,
        food_path=food,
        target_kcal=None,
    )

    assert path == reports / "2026-05-28-nutrition.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# Nutrition" in text
    assert "## Today's totals" in text
    assert "## 7-day rolling average" in text
    assert "## 28-day kcal mean" in text


def test_nutrition_report_today_no_entries_emits_placeholder() -> None:
    # All entries on dates OTHER than today.
    earlier = TODAY - timedelta(days=2)
    entries = (
        _entry(earlier, "breakfast", "oats", 13, 54, 6, 303, line=1),
        _entry(earlier, "lunch", "salad", 38, 22, 18, 404, line=2),
    )
    ctx = FoodContext(
        present=True, entries=entries, blocks=(), path=Path("food.md"), malformed_lines=()
    )
    today_breakdown = daily_nutrition(entries, TODAY)
    rollup = nutrition_rollup(entries, TODAY)

    text = render_nutrition(TODAY, rollup, today_breakdown, (), ctx)

    assert "## Today's totals" in text
    assert "_No entries logged for today yet._" in text
    # Per-meal section MUST be omitted when today has no entries.
    assert "## Per-meal breakdown" not in text


def test_nutrition_report_per_meal_breakdown_present_when_today_has_entries() -> None:
    entries = (
        _entry(TODAY, "breakfast", "80g oats", 13, 54, 6, 303, line=1),
        _entry(TODAY, "breakfast", "1 banana", 1.3, 27, 0.4, 105, line=2),
        _entry(TODAY, "lunch", "chicken bowl", 38, 22, 18, 404, line=3),
        _entry(TODAY, "lunch", "vinaigrette", 0, 1, 9, 84, line=4),
    )
    ctx = FoodContext(
        present=True, entries=entries, blocks=(), path=Path("food.md"), malformed_lines=()
    )
    today_breakdown = daily_nutrition(entries, TODAY)
    rollup = nutrition_rollup(entries, TODAY)

    text = render_nutrition(TODAY, rollup, today_breakdown, (), ctx)

    assert "## Per-meal breakdown" in text
    assert "### breakfast" in text
    assert "### lunch" in text
    # Each food label should appear in the per-meal listing.
    assert "80g oats" in text
    assert "1 banana" in text
    assert "chicken bowl" in text
    assert "vinaigrette" in text


def test_nutrition_report_omits_goal_section_when_target_unset() -> None:
    entries = (_entry(TODAY, "breakfast", "oats", 13, 54, 6, 303, line=1),)
    ctx = FoodContext(
        present=True, entries=entries, blocks=(), path=Path("food.md"), malformed_lines=()
    )
    today_breakdown = daily_nutrition(entries, TODAY)

    # Target unset -> ## Goal omitted entirely.
    rollup_no_target = nutrition_rollup(entries, TODAY, target_kcal=None)
    text_no_goal = render_nutrition(TODAY, rollup_no_target, today_breakdown, (), ctx)
    assert "## Goal" not in text_no_goal

    # Target set -> ## Goal present, with signed delta. avg_7d kcal = 303 (one day),
    # target 2200 -> deficit_surplus_7d = -1897.
    rollup_with_target = nutrition_rollup(entries, TODAY, target_kcal=2200)
    text_with_goal = render_nutrition(TODAY, rollup_with_target, today_breakdown, (), ctx)
    assert "## Goal" in text_with_goal
    assert "Target 2200 kcal/day" in text_with_goal
    # Unicode minus (U+2212) for deficit; abs(-1897) = 1897.
    assert "−1897" in text_with_goal
    assert "kcal/day" in text_with_goal


def test_nutrition_report_omits_all_sections_when_food_file_absent(tmp_path: Path) -> None:
    """Bonus coverage: absent-file early-exit path writes a placeholder body."""
    missing = tmp_path / "food.md"  # never created
    reports = tmp_path / "reports"
    path = runner.generate_nutrition(
        None,
        cfg=CFG,
        reports_dir=reports,
        generated_on=TODAY,
        food_path=missing,
        target_kcal=None,
    )
    text = path.read_text(encoding="utf-8")
    assert "food.md absent" in text
    assert "## Today's totals" not in text
    assert "## Per-meal breakdown" not in text
    assert "## Goal" not in text


def test_nutrition_report_included_in_generate_all_aggregate(tmp_path: Path) -> None:
    """generate_all should write the nutrition report when food_path is provided."""
    import sqlite3

    from tempo import db

    food = tmp_path / "food.md"
    food.write_text(
        "- 2026-05-28 lunch: chicken bowl | p:38 c:22 f:18 cal:404\n",
        encoding="utf-8",
    )
    races = tmp_path / "races.md"
    races.write_text("", encoding="utf-8")
    heat = tmp_path / "heat.md"
    heat.write_text("", encoding="utf-8")
    reports = tmp_path / "reports"

    db_path = tmp_path / "tempo.db"
    conn: sqlite3.Connection = db.init_db(db_path)
    try:
        result = runner.generate_all(
            conn,
            cfg=CFG,
            races_path=races,
            heat_path=heat,
            reports_dir=reports,
            generated_on=TODAY,
            food_path=food,
            target_kcal=None,
        )
    finally:
        conn.close()

    assert result.nutrition is not None
    assert result.nutrition.exists()
    assert result.nutrition in result.paths()
