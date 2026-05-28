# Wave 17-03: Config + analysis wire-up — Summary

**Status:** Complete
**Files modified:** 11
**New tests:** 4
**Tests modified:** 4 (fixtures rewritten)
**Total test count:** 716 passed, 1 deselected (the slow whisper fixture)
**ruff:** clean
**Correctness gate:** `grep -rn "settings\.\(target_kcal_default\|threshold_pace\|max_hr\|resting_hr\|threshold_hr\)" runos/ tests/` returns empty ✅

## Files modified

| File | What changed |
|------|--------------|
| `runos/config.py` | Deleted 5 `Field` declarations (`target_kcal_default`, `threshold_pace_s_per_km`, `max_hr`, `resting_hr`, `threshold_hr`); added `preferences_path` derived `@property` next to `food_path`; left a NOTE block explaining the Phase-17 migration. |
| `runos/analysis/runner.py` | Imported `PreferencesContext` + `Units`; renamed `_load_config_from_settings` → `_load_config_from_prefs`; changed `WeeklyRollup` construction to use raw `distance_m`; added `units: Units \| None = None` param to `generate_load_trend` + `generate_all` and threaded through. |
| `runos/analysis/report.py` | Imported `Units` + `format_distance`; renamed `WeeklyRollup.distance_km` → `WeeklyRollup.distance_m`; `render_load_trend` accepts `units` (default `Units()`) and switches column header `"Distance (km)"` ↔ `"Distance (mi)"` + cell value via `format_distance`; updated the no-config warning to point at `preferences.md`. |
| `runos/analysis/nutrition.py` | Stale docstring reference updated. |
| `runos/cli.py` | `_analyze_setup` now returns `(settings, conn, cfg, prefs)`; all five `analyze` subcommands pull `target_kcal=prefs.nutrition.target_kcal` + `units=prefs.units` from the parsed preferences. |
| `runos/sync/daily.py` | Imported `PreferencesContext` + `parse_preferences`; renamed `_load_config_from_settings` → `_load_config_from_prefs`; loads prefs once and passes through. |
| `.env.example` | Deleted both old blocks; added comment trail pointing at `preferences.md.example`. |
| `tests/test_config.py` | Added `test_preferences_path_derived_under_content_root` + `test_phase17_migrated_settings_fields_removed`. |
| `tests/test_analyze_cli.py` | Fixture switched from `monkeypatch.setenv` to writing `preferences.md`; added `test_analyze_nutrition_reads_target_kcal_from_preferences_md` + `test_analyze_load_trend_renders_miles_when_preferences_says_miles`. |
| `tests/test_phase7_cli.py` | Fixture switched from env-var setup to writing `preferences.md`. |
| `tests/test_daily_run.py` | `_settings()` helper rewritten — was constructing `Settings(threshold_pace_s_per_km=..., max_hr=..., ...)` (which would now fail); writes `preferences.md` to content root instead. |

## Decisions made within the locked scope

1. **Wizard threshold-pace step turned out to be a non-issue.** Grep confirmed the Phase 14 wizard never wrote `RUNOS_THRESHOLD_PACE_S_PER_KM` / `RUNOS_MAX_HR` / `RUNOS_RESTING_HR` / `RUNOS_THRESHOLD_HR` / `RUNOS_TARGET_KCAL`. PLAN's sub-decision about no-op'ing a wizard step is moot.

2. **Generator signature — kept the existing seam.** PLAN suggested each `generate_*` take `prefs: PreferencesContext | None = None`. The executor instead kept the existing `cfg: LoadConfig` / `target_kcal: int | None` / `food_path: Path` seam (which the tests already use directly) and only added the new `units: Units | None = None` param. The CLI + `sync/daily.py` are the load-bearing points that now construct `cfg` from `prefs.physiology`, pass `target_kcal=prefs.nutrition.target_kcal`, and pass `units=prefs.units`. End-to-end behaviour is identical; the diff against existing test patterns is much smaller.

3. **`WeeklyRollup.distance_km` → `distance_m`** — safe because no test asserted on the field name.

4. **`render_load_trend` back-compat** — `units` defaults to `Units()` (km), preserving existing snapshot-style test content.

5. **Distance cell format** — column header carries the unit; cell value strips the suffix from `format_distance` output to keep the table tight.

## Coverage of new behaviour

Two new CLI-level tests pin the end-to-end intent:

- `test_analyze_nutrition_reads_target_kcal_from_preferences_md` — writes `preferences.md` with `target_kcal: 2400`, runs `runos analyze nutrition`, asserts the report includes the goal-delta line.
- `test_analyze_load_trend_renders_miles_when_preferences_says_miles` — writes `preferences.md` with `distance: miles`, runs `runos analyze load-trend`, asserts the rendered report uses `"Distance (mi)"` column.

These two tests are the migration's behavioural acceptance criteria.
