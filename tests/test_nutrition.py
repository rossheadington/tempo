"""Parsing of food.md (both formats) plus daily_nutrition + nutrition_rollup.

Lenient parser (NUTR-03); left-open right-closed (today-N, today] windows;
optional kcal goal via target_kcal (NUTR-04). All tests use only stdlib +
pytest's tmp_path fixture; the rollup tests pin a fixed reference ``today``
so the windows are deterministic. Mirrors tests/test_weight.py shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo.analysis.nutrition import (
    DailyNutrition,
    FoodContext,
    FoodEntry,
    MealBlock,
    NutritionRollup,
    _parse_macros,
    daily_nutrition,
    nutrition_rollup,
    parse_food,
)

# Fixed reference date for all rollup tests so the left-open right-closed
# windows (today-7, today] and (today-28, today] are deterministic.
TODAY = date(2026, 5, 28)


def _write_food(tmp_path: Path, body: str) -> Path:
    """Helper: write ``body`` to ``tmp_path / "food.md"`` and return the path."""
    p = tmp_path / "food.md"
    p.write_text(body, encoding="utf-8")
    return p


def _entry(
    *,
    d: date,
    meal: str | None,
    label: str,
    p: float,
    c: float,
    f: float,
    cal: int,
    line: int = 1,
    fmt: str = "inline",
) -> FoodEntry:
    """Helper: construct a FoodEntry inline for rollup tests."""
    return FoodEntry(
        date=d,
        meal_name=meal,
        food_label=label,
        protein_g=p,
        carbs_g=c,
        fat_g=f,
        kcal=cal,
        source_line=line,
        source_format=fmt,
    )


# ---- Parser tests (13) -----------------------------------------------------


def test_parse_food_missing_file_returns_absent_context(tmp_path: Path) -> None:
    ctx = parse_food(tmp_path / "nope.md")
    assert isinstance(ctx, FoodContext)
    assert ctx.present is False
    assert ctx.entries == ()
    assert ctx.blocks == ()
    assert ctx.path is None
    assert ctx.malformed_lines == ()


def test_parse_food_inline_format_happy_path(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303\n"
        "- 2026-05-28 lunch: chicken salad bowl | p:38 c:22 f:18 cal:404\n"
        "- 2026-05-28 dinner: salmon + rice + greens | p:42 c:60 f:14 cal:558\n"
        "- 2026-05-28 snack: 1 banana | p:1.3 c:27 f:0.4 cal:105\n",
    )
    ctx = parse_food(p)
    assert ctx.present is True
    assert ctx.path == p
    assert ctx.malformed_lines == ()
    assert ctx.blocks == ()
    assert len(ctx.entries) == 4
    assert all(e.source_format == "inline" for e in ctx.entries)
    meals = [e.meal_name for e in ctx.entries]
    assert meals == ["breakfast", "lunch", "dinner", "snack"]
    kcals = [e.kcal for e in ctx.entries]
    assert kcals == [303, 404, 558, 105]


def test_parse_food_block_format_happy_path(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "## 2026-05-28 breakfast\n"
        "- 80g rolled oats: p:13 c:54 f:6 cal:303\n"
        "- 1 banana: p:1.3 c:27 f:0.4 cal:105\n"
        "\n"
        "## 2026-05-28 lunch\n"
        "- chicken (200g): p:38 c:0 f:8 cal:230\n"
        "- mixed greens (150g): p:3 c:8 f:0.5 cal:45\n"
        "- vinaigrette (1 tbsp): p:0 c:1 f:9 cal:84\n"
        "- rice (100g cooked): p:2.4 c:28 f:0.3 cal:130\n",
    )
    ctx = parse_food(p)
    assert ctx.present is True
    assert ctx.malformed_lines == ()
    assert len(ctx.entries) == 6
    assert all(e.source_format == "block" for e in ctx.entries)
    meals = sorted({e.meal_name for e in ctx.entries})
    assert meals == ["breakfast", "lunch"]
    # 2 blocks in file order, with correct entry counts.
    assert len(ctx.blocks) == 2
    assert ctx.blocks[0].meal_name == "breakfast"
    assert len(ctx.blocks[0].entries) == 2
    assert ctx.blocks[1].meal_name == "lunch"
    assert len(ctx.blocks[1].entries) == 4
    total_kcal = sum(e.kcal for e in ctx.entries)
    assert total_kcal == 303 + 105 + 230 + 45 + 84 + 130


def test_parse_food_both_formats_in_same_file(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303\n"
        "- 2026-05-28 snack: 1 banana | p:1.3 c:27 f:0.4 cal:105\n"
        "\n"
        "## 2026-05-27 lunch\n"
        "- chicken: p:38 c:0 f:8 cal:230\n"
        "- greens: p:3 c:8 f:0.5 cal:45\n"
        "- rice: p:2.4 c:28 f:0.3 cal:130\n",
    )
    ctx = parse_food(p)
    assert ctx.present is True
    assert ctx.malformed_lines == ()
    assert len(ctx.entries) == 5
    # Sorted by (date, source_line); the 2026-05-27 block entries come first.
    formats = [e.source_format for e in ctx.entries]
    assert formats == ["block", "block", "block", "inline", "inline"]
    # Format-B blocks tuple has exactly 1 element.
    assert len(ctx.blocks) == 1
    assert ctx.blocks[0].meal_name == "lunch"
    assert ctx.blocks[0].date == date(2026, 5, 27)


def test_parse_food_inline_entry_after_block_keeps_its_own_meal_name(
    tmp_path: Path,
) -> None:
    """Inline entries appended after a ``## breakfast`` block keep their own meal.

    Regression: the parser previously stayed in "block mode" once a ``##``
    header opened, classifying every subsequent ``- `` bullet as a block
    child of that meal even when the bullet was clearly inline-shaped
    (carried its own ``YYYY-MM-DD <meal>:`` prefix AND ``|`` macro
    separator). That made a ``cat >> food.md`` append misattribute the
    snack to breakfast. The bullet's ``|`` distinguishes inline-from-block
    cleanly; we try inline first inside an active block.
    """
    p = _write_food(
        tmp_path,
        "## 2026-05-28 breakfast\n"
        "- bagel: p:8 c:40 f:2 cal:214\n"
        "\n"
        "- 2026-05-28 snack: 500ml monster | p:0 c:60 f:0 cal:237\n",
    )
    ctx = parse_food(p)
    assert ctx.malformed_lines == ()
    assert len(ctx.entries) == 2
    meals = {(e.meal_name, e.food_label) for e in ctx.entries}
    assert meals == {
        ("breakfast", "bagel"),
        ("snack", "500ml monster"),
    }
    # The active block stays a block; the inline entry is not in `blocks`.
    assert len(ctx.blocks) == 1
    assert ctx.blocks[0].meal_name == "breakfast"
    # Block has exactly one entry (the bagel) — the inline snack didn't
    # leak into the block's `entries` tuple.
    assert len(ctx.blocks[0].entries) == 1


def test_parse_food_inline_and_block_produce_equivalent_entries(tmp_path: Path) -> None:
    """The equivalence guarantee: same meal in both formats produces identical
    FoodEntry records modulo date / source_line / source_format."""
    p = _write_food(
        tmp_path,
        "- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303\n"
        "\n"
        "## 2026-05-27 breakfast\n"
        "- 80g rolled oats: p:13 c:54 f:6 cal:303\n",
    )
    ctx = parse_food(p)
    assert ctx.present is True
    assert ctx.malformed_lines == ()
    assert len(ctx.entries) == 2

    inline_e = next(e for e in ctx.entries if e.source_format == "inline")
    block_e = next(e for e in ctx.entries if e.source_format == "block")

    assert inline_e.meal_name == block_e.meal_name == "breakfast"
    assert inline_e.food_label == block_e.food_label == "80g rolled oats"
    assert inline_e.protein_g == block_e.protein_g == 13.0
    assert inline_e.carbs_g == block_e.carbs_g == 54.0
    assert inline_e.fat_g == block_e.fat_g == 6.0
    assert inline_e.kcal == block_e.kcal == 303

    # Only differences are date, source_line, source_format.
    assert inline_e.date != block_e.date
    assert inline_e.source_format != block_e.source_format


def test_parse_food_unordered_macro_keys(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 dinner: salmon | cal:558 p:42 c:60 f:14\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 1
    e = ctx.entries[0]
    assert e.protein_g == 42.0
    assert e.carbs_g == 60.0
    assert e.fat_g == 14.0
    assert e.kcal == 558


def test_parse_food_case_insensitive_keys(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 dinner: salmon | P:42 C:60 F:14 CAL:558\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 1
    e = ctx.entries[0]
    assert e.protein_g == 42.0
    assert e.carbs_g == 60.0
    assert e.fat_g == 14.0
    assert e.kcal == 558


def test_parse_food_tolerates_rounding_on_kcal(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 snack: banana1 | p:1.3 c:27 f:0.4 cal:105.4\n"
        "- 2026-05-28 snack: banana2 | p:1.3 c:27 f:0.4 cal:105.6\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 2
    by_label = {e.food_label: e for e in ctx.entries}
    assert by_label["banana1"].kcal == 105
    assert by_label["banana2"].kcal == 106


def test_parse_food_skips_entries_missing_required_macros(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 breakfast: oats | p:13 c:54 f:6 cal:303\n"
        "- 2026-05-28 lunch: chicken | p:38 c:22 cal:404\n"
        "- 2026-05-28 dinner: salmon | p:42 c:60 f:14\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 1
    assert ctx.entries[0].food_label == "oats"
    assert ctx.malformed_lines == (2, 3)


def test_parse_food_unknown_keys_ignored(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 snack: nuts | p:5 c:8 f:14 cal:170 fibre:3 sodium:120\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 1
    assert ctx.malformed_lines == ()
    e = ctx.entries[0]
    assert e.protein_g == 5.0
    assert e.kcal == 170


def test_parse_food_latest_wins_on_same_date_meal_food(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "- 2026-05-28 breakfast: 80g rolled oats | p:10 c:50 f:5 cal:280\n"
        "- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303\n"
        "- 2026-05-28 breakfast: 1 banana | p:1.3 c:27 f:0.4 cal:105\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 2
    by_label = {e.food_label: e for e in ctx.entries}
    # Latest-wins on the (date, breakfast, oats) triple: the second occurrence.
    assert by_label["80g rolled oats"].kcal == 303
    assert by_label["80g rolled oats"].protein_g == 13.0
    # Different food_label under the same (date, meal) -> separate entry.
    assert by_label["1 banana"].kcal == 105


def test_parse_food_block_with_malformed_header_skipped(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "## not-a-date breakfast\n"
        "- 80g rolled oats: p:13 c:54 f:6 cal:303\n"
        "- 1 banana: p:1.3 c:27 f:0.4 cal:105\n"
        "\n"
        "## 2026-05-28 lunch\n"
        "- chicken: p:38 c:0 f:8 cal:230\n",
    )
    ctx = parse_food(p)
    # Only the valid lunch block survives.
    assert len(ctx.entries) == 1
    assert ctx.entries[0].food_label == "chicken"
    assert ctx.entries[0].meal_name == "lunch"
    # The header line (1) and the two nested bullets (2, 3) are all malformed.
    assert 1 in ctx.malformed_lines
    assert 2 in ctx.malformed_lines
    assert 3 in ctx.malformed_lines


def test_parse_food_ignores_headers_blanks_and_comments(tmp_path: Path) -> None:
    p = _write_food(
        tmp_path,
        "# Food log\n"
        "\n"
        "Some notes about today's eating.\n"
        "\n"
        "- 2026-05-28 breakfast: oats | p:13 c:54 f:6 cal:303\n"
        "\n"
        "Another prose paragraph.\n"
        "\n"
        "- 2026-05-28 lunch: chicken | p:38 c:22 f:18 cal:404\n",
    )
    ctx = parse_food(p)
    assert len(ctx.entries) == 2
    assert ctx.malformed_lines == ()


# ---- daily_nutrition tests (3) ---------------------------------------------


def test_daily_nutrition_sums_entries() -> None:
    entries = (
        _entry(d=TODAY, meal="breakfast", label="a", p=13, c=54, f=6, cal=303, line=1),
        _entry(d=TODAY, meal="lunch", label="b", p=38, c=22, f=18, cal=404, line=2),
        _entry(d=TODAY, meal="dinner", label="c", p=42, c=60, f=14, cal=558, line=3),
    )
    daily = daily_nutrition(entries, TODAY)
    assert daily.date == TODAY
    assert daily.protein_g == pytest.approx(93.0)
    assert daily.carbs_g == pytest.approx(136.0)
    assert daily.fat_g == pytest.approx(38.0)
    assert daily.kcal == 1265
    assert daily.entry_count == 3


def test_daily_nutrition_macro_percentages_kcal_share() -> None:
    entries = (
        _entry(d=TODAY, meal="breakfast", label="a", p=13, c=54, f=6, cal=303, line=1),
        _entry(d=TODAY, meal="lunch", label="b", p=38, c=22, f=18, cal=404, line=2),
        _entry(d=TODAY, meal="dinner", label="c", p=42, c=60, f=14, cal=558, line=3),
    )
    daily = daily_nutrition(entries, TODAY)
    # kcal=1265, P=93, C=136, F=38.
    expected_p = (93.0 * 4) / 1265 * 100
    expected_c = (136.0 * 4) / 1265 * 100
    expected_f = (38.0 * 9) / 1265 * 100
    assert abs(daily.macro_pct_protein - expected_p) < 0.1
    assert abs(daily.macro_pct_carbs - expected_c) < 0.1
    assert abs(daily.macro_pct_fat - expected_f) < 0.1


def test_daily_nutrition_zero_kcal_degenerate() -> None:
    entries = (
        _entry(d=TODAY, meal="snack", label="water", p=0, c=0, f=0, cal=0, line=1),
    )
    daily = daily_nutrition(entries, TODAY)
    assert daily.kcal == 0
    assert daily.macro_pct_protein == 0.0
    assert daily.macro_pct_carbs == 0.0
    assert daily.macro_pct_fat == 0.0


# ---- nutrition_rollup tests (5) --------------------------------------------


def test_nutrition_rollup_empty_returns_all_none_with_zero_days_logged() -> None:
    rollup = nutrition_rollup((), TODAY)
    assert isinstance(rollup, NutritionRollup)
    assert rollup.today == TODAY
    assert rollup.latest_day is None
    assert rollup.days_since_last is None
    assert rollup.avg_7d is None
    assert rollup.days_logged_7d == 0
    assert rollup.avg_28d_kcal is None
    assert rollup.target_kcal is None
    assert rollup.deficit_surplus_7d is None


def test_nutrition_rollup_7d_window_left_open() -> None:
    # (today-7, today] excludes TODAY-7 itself; TODAY-8 also out.
    entries = (
        _entry(
            d=TODAY - timedelta(days=8), meal="b", label="x",
            p=10, c=10, f=10, cal=1000, line=1,
        ),
        _entry(
            d=TODAY - timedelta(days=7), meal="b", label="y",
            p=10, c=10, f=10, cal=1500, line=2,
        ),
        _entry(
            d=TODAY, meal="b", label="z",
            p=20, c=20, f=20, cal=2000, line=3,
        ),
    )
    rollup = nutrition_rollup(entries, TODAY)
    assert rollup.days_logged_7d == 1
    assert rollup.avg_7d is not None
    assert rollup.avg_7d.kcal == 2000


def test_nutrition_rollup_averages_across_days_with_entries_only() -> None:
    entries = (
        _entry(
            d=TODAY - timedelta(days=5), meal="d", label="x",
            p=80, c=200, f=50, cal=1800, line=1,
        ),
        _entry(
            d=TODAY - timedelta(days=2), meal="d", label="y",
            p=120, c=300, f=70, cal=2500, line=2,
        ),
        _entry(
            d=TODAY, meal="d", label="z",
            p=100, c=250, f=60, cal=2000, line=3,
        ),
    )
    rollup = nutrition_rollup(entries, TODAY)
    assert rollup.days_logged_7d == 3
    assert rollup.avg_7d is not None
    # Mean kcal = (1800 + 2500 + 2000) / 3 = 2100.
    assert rollup.avg_7d.kcal == 2100
    assert rollup.avg_28d_kcal == 2100


def test_nutrition_rollup_target_deficit_surplus_when_set() -> None:
    entries = (
        _entry(
            d=TODAY - timedelta(days=5), meal="d", label="x",
            p=80, c=200, f=50, cal=1800, line=1,
        ),
        _entry(
            d=TODAY - timedelta(days=2), meal="d", label="y",
            p=120, c=300, f=70, cal=2500, line=2,
        ),
        _entry(
            d=TODAY, meal="d", label="z",
            p=100, c=250, f=60, cal=2000, line=3,
        ),
    )
    # Mean kcal = 2100. Target 2200 -> deficit -100.
    rollup_deficit = nutrition_rollup(entries, TODAY, target_kcal=2200)
    assert rollup_deficit.target_kcal == 2200
    assert rollup_deficit.deficit_surplus_7d == -100

    # Target 1900 -> surplus +200.
    rollup_surplus = nutrition_rollup(entries, TODAY, target_kcal=1900)
    assert rollup_surplus.target_kcal == 1900
    assert rollup_surplus.deficit_surplus_7d == 200


def test_nutrition_rollup_target_none_when_unset() -> None:
    entries = (
        _entry(d=TODAY, meal="d", label="z", p=100, c=250, f=60, cal=2000, line=1),
    )
    rollup = nutrition_rollup(entries, TODAY)
    assert rollup.target_kcal is None
    assert rollup.deficit_surplus_7d is None


# ---- _parse_macros direct check (sanity for the helper's contract) ---------


def test_parse_macros_canonical_and_unordered() -> None:
    """Direct smoke for _parse_macros — referenced from <verify> in the plan."""
    m = _parse_macros("p:13 c:54 f:6 cal:303")
    assert m == (13.0, 54.0, 6.0, 303)
    m2 = _parse_macros("cal:303.4 P:13 C:54 F:6")
    assert m2 == (13.0, 54.0, 6.0, 303)
    # Missing required key -> None.
    assert _parse_macros("p:13 c:54 cal:303") is None
    # Unknown keys ignored, all required present -> ok.
    m3 = _parse_macros("p:5 c:8 f:14 cal:170 fibre:3 sodium:120")
    assert m3 == (5.0, 8.0, 14.0, 170)


# Defensive: re-importing MealBlock + DailyNutrition is checked at module load,
# but the must_haves contract specifies a single from-import that pulls all
# public + private names. The presence of these references prevents
# "imported but unused" lint regressions.
_ = MealBlock
_ = DailyNutrition
