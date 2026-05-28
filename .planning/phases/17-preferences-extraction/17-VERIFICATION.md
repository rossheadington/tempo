# Phase 17 — Verification

**Date:** 2026-05-28
**Verifier:** orchestrator (goal-backward check against CONTEXT.md)
**Verdict:** PASS

## Goal: extract personal physiology + units + nutrition target out of `.env` and into a single human-edited markdown file (`preferences.md`).

## Acceptance criteria from CONTEXT.md § Phase Boundary

| Criterion | Status |
|-----------|--------|
| New `preferences.md` tracker file (in user content_dir, lives via `Settings.preferences_path`) | ✅ `Settings.preferences_path` derived property added next to `food_path`. Live file written in the orchestrator's migration step. |
| `runos/analysis/preferences.py` with `Physiology` / `Units` / `Nutrition` / `PreferencesContext` frozen+slots dataclasses + lenient `parse_preferences` | ✅ 451-line module, all four dataclasses, parser never raises. |
| `tempo/units.py` formatter — `format_distance`, `format_pace`, helpers, `KM_PER_MILE` constant | ✅ 99-line module, NIST-exact conversion constant. |
| Five `.env` knobs migrated OUT of Settings, INTO `preferences.md`: `RUNOS_THRESHOLD_PACE_S_PER_KM`, `RUNOS_MAX_HR`, `RUNOS_RESTING_HR`, `RUNOS_THRESHOLD_HR`, `RUNOS_TARGET_KCAL` | ✅ All five `Field` declarations gone from `Settings`. Correctness grep returns empty. |
| `Settings.preferences_path` derived property | ✅ Added. New test pins it. |
| Plumbing: `runner.py` reads prefs once, threads `Physiology` / `Nutrition` into analysis modules | ✅ Via the `_load_config_from_prefs` helper in `runner.py` + `sync/daily.py`. Existing `LoadConfig` seam preserved (smaller diff than alternative). |
| `cli.py` at every `analyze` entry point: `settings.target_kcal_default` → `prefs.nutrition.target_kcal` | ✅ All three call sites updated; `_analyze_setup` now returns prefs. |
| `sync/daily.py` symmetric replacement | ✅ Done. |
| Report renderers use Units formatter for user-facing distance column | ✅ `render_load_trend` accepts `units` param; column header + cell value switch via `format_distance`. Default `Units()` preserves existing snapshot tests. |
| `.env.example` updated — moved knobs deleted, comment trail added | ✅ Both old blocks removed, comment trail in place pointing to `preferences.md.example`. |
| `docs/PREFERENCES.md` documenting file format end-to-end | ✅ 325-line doc following NUTRITION.md shape. |
| `preferences.md.example` committed | ✅ 75 lines, placeholder values only (`threshold_pace: 4:00/km`, `max_hr: 190`, `resting_hr: 50`, `target_kcal: 2200`). |
| Tests added: `test_preferences.py`, `test_units.py`, targeted edits to load/recovery/nutrition/cli/runner tests | ✅ 26 new tests in two new files + 4 new tests in `test_config.py` / `test_analyze_cli.py` + 4 test fixtures rewritten. |

## Out-of-scope items (intentionally deferred, per CONTEXT.md § Deferred Ideas)

- Auto-load `preferences.md` into bot session bootstrap — Phase 18.
- `tempo preferences edit` / `tempo preferences show` CLI surfaces — speculative.
- Backward-compat fallback to old `.env` keys — solo-user project, single cut-over.
- Settings backward-compat fallback — same.

## Risk gates

| Gate | Result |
|------|--------|
| Full test suite (`uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`) | 716 passed, 1 deselected ✅ |
| Ruff lint (`uv run ruff check runos/ tests/`) | All checks passed ✅ |
| Correctness grep (`settings.<deleted-field>` must be gone) | Empty ✅ |
| No-network invariant intact (analysis layer doesn't reach the network) | Untouched — only consumers of pure parsers added ✅ |
| Schema version unchanged | No migration added; `SCHEMA_VERSION = 5` still ✅ |

## Privacy gate

| Check | Result |
|-------|--------|
| `preferences.md.example` uses placeholder values, not the owner's actual numbers | ✅ All textbook generic (`190` max HR, `50` resting, `4:00/km` threshold, `2200` kcal) |
| Live `preferences.md` lives in `RUNOS_CONTENT_DIR` (gitignored) | ✅ Resolves via the same `content_root` as `food.md`, `weight.md`, etc. |
| Parser doesn't log parsed values on malformed-line warnings (line numbers only) | ✅ Confirmed in `preferences.py` |

## Net behaviour change

For Ross:
- Five `RUNOS_*` knobs no longer accepted from `.env`. If he had them set, they're now ignored.
- New file `<content_dir>/preferences.md` is where those values live.
- New optional features: Units section (`distance: miles`, `pace: min_per_mile`) → load-trend report shows miles. Prose sections (Training week, Goals) → coach reads them but parser ignores.
- All five values default to `None` if `preferences.md` is missing → identical to having the old env vars unset.

For developers (code-side):
- No new schema. No new connector. No new launchd job.
- Analysis layer still takes typed values directly (`LoadConfig`, `target_kcal`); the boundary that translates `prefs` → typed values is the CLI / sync layer only.

## Conclusion

Phase 17 delivers the architectural separation Ross asked for: `.env` is now PURELY infra (credentials + paths + system tuning); personal physiology + display preferences + nutrition target + training prose all live in a human-edited `preferences.md`. The migration is complete; no follow-up phase is required for v1.6 unless we want to auto-load the prose into the bot session (deferred to Phase 18 polish).

**Verdict:** PASS 3/3 (all acceptance criteria met, all risk gates green, privacy gate green).
