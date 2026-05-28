"""Render the standalone ``tempo analyze nutrition`` report (NUTR-05, Phase 16-02).

Writes ``reports/<YYYY-MM-DD>-nutrition.md`` with five sections in fixed order:

* ``## Today's totals`` -- P/C/F/cal + macro %% for today (or a placeholder if no
  entries yet).
* ``## Per-meal breakdown`` -- subheaders per ``(today, meal_name)``. Omitted
  entirely when today has zero entries.
* ``## 7-day rolling average`` -- trailing 7-day window mean (across days WITH
  entries only), with a macro-%% second line.
* ``## 28-day kcal mean`` -- single scalar kcal/day or insufficient-history.
* ``## Goal`` -- only when ``target_kcal`` is set; signed Unicode +/- delta.

When ``food.md`` is absent (``FoodContext.present is False``) the renderer
short-circuits to a single-paragraph body advising the user to create the file;
no sections are emitted.

Pure stdlib, no I/O -- the caller (``runner.generate_nutrition``) parses
``food.md`` and writes the resulting markdown via ``_write_report``.
"""

from __future__ import annotations

from datetime import date

from tempo.analysis.nutrition import (
    DailyNutrition,
    FoodContext,
    FoodEntry,
    MealBlock,
    NutritionRollup,
)


def _freshness_line(context: FoodContext) -> str:
    """Build the one-line ``Data:`` freshness banner for the report header.

    Three cases mirror the LOCKED spec in 16-CONTEXT.md:
    1. Absent file -> ``Data: food.md absent``.
    2. File present but no parseable entries -> ``... 0 entries -- ...``.
    3. File present with entries -> ``... N entries across D days, last YYYY-MM-DD``.
    """
    if not context.present:
        return "Data: food.md absent"
    if not context.entries:
        return "Data: food.md present (0 entries -- file exists but empty or all malformed)"
    distinct_days = len({e.date for e in context.entries})
    latest = max(e.date for e in context.entries)
    return (
        f"Data: food.md present ({len(context.entries)} entries across "
        f"{distinct_days} days, last {latest.isoformat()})"
    )


def _render_today_totals(out: list[str], today_breakdown: DailyNutrition) -> None:
    out.append("## Today's totals")
    out.append("")
    if today_breakdown.entry_count == 0:
        out.append("_No entries logged for today yet._")
    else:
        out.append(
            f"P:{today_breakdown.protein_g:.0f}g · "
            f"C:{today_breakdown.carbs_g:.0f}g · "
            f"F:{today_breakdown.fat_g:.0f}g · "
            f"cal:{today_breakdown.kcal} "
            f"({today_breakdown.macro_pct_protein:.0f}/"
            f"{today_breakdown.macro_pct_carbs:.0f}/"
            f"{today_breakdown.macro_pct_fat:.0f} P/C/F %)"
        )
    out.append("")


def _render_per_meal_breakdown(out: list[str], today_entries: tuple[FoodEntry, ...]) -> None:
    """Group today's entries by meal_name (file order) and emit subheaders."""
    out.append("## Per-meal breakdown")
    out.append("")
    # Preserve insertion order using a dict keyed by meal_name -> list of entries.
    # Sort first by source_line so file order drives meal-group order via first-seen.
    by_meal: dict[str | None, list[FoodEntry]] = {}
    for entry in sorted(today_entries, key=lambda e: e.source_line):
        by_meal.setdefault(entry.meal_name, []).append(entry)

    for meal_name, entries in by_meal.items():
        header = meal_name if meal_name else "(unspecified)"
        out.append(f"### {header}")
        for e in entries:
            out.append(
                f"- {e.food_label} — "
                f"P:{e.protein_g:.1f}g · C:{e.carbs_g:.1f}g · "
                f"F:{e.fat_g:.1f}g · cal:{e.kcal}"
            )
        out.append("")


def _render_7d_rolling(out: list[str], rollup: NutritionRollup) -> None:
    out.append("## 7-day rolling average")
    out.append("")
    if rollup.avg_7d is None:
        out.append("_No entries in the last 7 days._")
        out.append("")
        return
    avg = rollup.avg_7d
    out.append(
        f"P:{avg.protein_g:.0f}g · C:{avg.carbs_g:.0f}g · "
        f"F:{avg.fat_g:.0f}g · cal:{avg.kcal} "
        f"({rollup.days_logged_7d} days logged of 7)"
    )
    out.append(
        f"Macro split: {avg.macro_pct_protein:.0f}% P · "
        f"{avg.macro_pct_carbs:.0f}% C · {avg.macro_pct_fat:.0f}% F"
    )
    out.append("")


def _render_28d_kcal(out: list[str], rollup: NutritionRollup) -> None:
    out.append("## 28-day kcal mean")
    out.append("")
    if rollup.avg_28d_kcal is None:
        out.append("_Insufficient history._")
    else:
        out.append(f"{rollup.avg_28d_kcal} kcal/day")
    out.append("")


def _render_goal(out: list[str], rollup: NutritionRollup) -> None:
    """Goal section -- only called when ``rollup.target_kcal is not None``."""
    assert rollup.target_kcal is not None  # guarded at call site
    out.append("## Goal")
    out.append("")
    if rollup.deficit_surplus_7d is None:
        out.append(
            f"Target {rollup.target_kcal} kcal/day · "
            "7d delta n/a (insufficient 7d history)"
        )
    else:
        delta = rollup.deficit_surplus_7d
        if delta > 0:
            sign = "+"
        elif delta < 0:
            sign = "−"  # U+2212 (Unicode minus, NOT ASCII hyphen)
        else:
            sign = "±"
        out.append(
            f"Target {rollup.target_kcal} kcal/day · "
            f"7d delta {sign}{abs(delta)} kcal/day"
        )
    out.append("")


def render_nutrition(
    today: date,
    rollup: NutritionRollup,
    today_breakdown: DailyNutrition,
    blocks_today: tuple[MealBlock, ...],
    context: FoodContext,
) -> str:
    """Build the markdown body for the standalone nutrition report.

    ``blocks_today`` is accepted for signature uniformity with future per-block
    rendering but is unused -- per-meal grouping is driven off ``context.entries``
    filtered to ``today`` so Format-A inline entries and Format-B block entries
    render identically.
    """
    out: list[str] = []
    out.append(f"# Nutrition — {today.isoformat()}")
    out.append("")
    out.append(_freshness_line(context))
    out.append("")

    # Absent-file early exit: no sections, just a placeholder body.
    if not context.present:
        out.append(
            "_No food log found. Create <content_root>/food.md to enable "
            "nutrition tracking. See docs/NUTRITION.md for the format._"
        )
        out.append("")
        return "\n".join(out)

    _render_today_totals(out, today_breakdown)

    today_entries = tuple(e for e in context.entries if e.date == today)
    if today_breakdown.entry_count > 0:
        _render_per_meal_breakdown(out, today_entries)

    _render_7d_rolling(out, rollup)
    _render_28d_kcal(out, rollup)

    if rollup.target_kcal is not None:
        _render_goal(out, rollup)

    return "\n".join(out)
