---
phase: 16-nutrition-tracker
plan: 03
subsystem: analysis/recovery
tags: [nutrition, recovery-report, NUTR-05]
requires: [16-01]
provides: [recovery-report-nutrition-section]
affects: [runos/analysis/recovery.py, runos/analysis/runner.py, runos/cli.py, tests/test_recovery.py]
tech_stack:
  added: []
  patterns:
    - "3-state degradation rule extended to a fourth tracker (heat/strength/weight/nutrition)"
    - "Unicode signed-delta glyph convention (`+` / `−` / `±`) mirrored from `_fmt_weight_delta` to `_fmt_kcal_delta`"
key_files:
  modified:
    - runos/analysis/recovery.py
    - runos/analysis/runner.py
    - runos/cli.py
    - tests/test_recovery.py
decisions:
  - "Staleness threshold for nutrition is >3 days (vs weight's >14) because food is logged daily — a 3-day gap already invalidates the 7-day rollup"
  - "Nutrition section placed AFTER weight so the tracker cluster reads Heat → Strength → Weight → Nutrition"
metrics:
  tests_added: 8
  commits: 2
completed: 2026-05-28
---

# Phase 16 Plan 03: Recovery-report Nutrition section Summary

Wired the Plan 16-01 nutrition parser into the recovery report so `food.md` surfaces alongside heat / strength / weight in the daily report.

## Changes

- **`runos/analysis/recovery.py`** — Added `NutritionRollup` + `parse_food` + `nutrition_rollup` imports. `RecoveryAssessment` gained `nutrition: NutritionRollup | None = None` + `nutrition_present: bool = False`. `assess_recovery_from_db` accepts `food_path: Path | None` + `target_kcal: int | None`; early-exit check updated to cover all four trackers; same single-reconstruction pattern now carries heat + strength + weight + nutrition. New module-level `_fmt_kcal_delta(int)` (`+110 kcal/day` / `−85 kcal/day` / `±0 kcal/day`) and `_render_nutrition_section` (3-state rule: absent / stale-over-3d / current 7-day rollup, with optional goal-delta line). `render_recovery` calls the new section after weight.
- **`runos/analysis/runner.py`** — `generate_recovery` accepts `food_path` + `target_kcal` and threads them to `assess_recovery_from_db`. `generate_all` (already had the kwargs from 16-02) now also passes them into the inner `generate_recovery` call.
- **`runos/cli.py`** — `runos analyze recovery` passes `food_path=settings.food_path` + `target_kcal=settings.target_kcal_default`. (`runos analyze` aggregator was already wired by 16-02.)
- **`tests/test_recovery.py`** — `_ok_assessment` extended with `nutrition` / `nutrition_present` kwargs. New `# ---- Nutrition section ----` block with 8 new tests covering: absent, present-but-empty, stale (>3d) nudge, current 7d rollup, goal-line append (both surplus + Unicode-minus deficit), mixed-format input (inline + block same day), full ordering (Heat → Strength → Weight → Nutrition), and `_fmt_kcal_delta` sign glyphs.

## Verification

- `uv run pytest tests/ --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` → **655 passed**
- `uv run ruff check runos/ tests/` → clean
- `tests/test_recovery.py`: 35 → 43 (+8)
- Confirmed: `assess_recovery_from_db`'s single-reconstruction now carries all four trackers (heat + strength + weight + nutrition).
- Confirmed: `runner.generate_all` + `analyze_main` already had `food_path` + `target_kcal` from 16-02 — this plan only added them to `generate_recovery` + `analyze_recovery` and wired the inner `generate_all → generate_recovery` call.

## Deviations from Plan

None — plan executed as written. Note one extra test was added beyond the planned 5 (an `_ok_assessment(nutrition=empty…)` "present but zero entries" test + a dedicated `_fmt_kcal_delta` boundary test in addition to the planned mixed-format test), yielding +8 instead of +5/+6. All are within the spec's scope.

## Commits

- `30cdd87` feat(16-03): surface nutrition rollup in the recovery report
- `09db16c` test(16-03): cover nutrition section in the recovery report

## Self-Check: PASSED

- `runos/analysis/recovery.py` modified — present
- `runos/analysis/runner.py` modified — present
- `runos/cli.py` modified — present
- `tests/test_recovery.py` modified — present
- Commits `30cdd87` + `09db16c` present in `git log`.
