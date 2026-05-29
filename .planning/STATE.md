---
gsd_state_version: 1.0
milestone: v1.7
milestone_name: coros-integration
status: shipped
stopped_at: v1.7 (Phase 18) complete on `main`; v1.5 + v1.6 still in place underneath. Ready for next iteration. Live use requires Ross to add RUNOS_COROS_EMAIL + RUNOS_COROS_PASSWORD to .env then run `runos coros login`.
last_updated: "2026-05-29T22:00:00.000Z"
last_activity: 2026-05-29 — Phase 18 (Coros Integration, v1.7) shipped end-to-end across 5 waves. New `runos/connectors/coros.py` (email + MD5-hashed password → bearer+userId token, persisted to `~/.runos/tokens/coros/`, atomic write, mode 0600; one-shot refresh-on-401; symmetric isolation in `sync/pipeline.py`); new `runos/transforms/coros_wellness.py` with per-(day, metric) COALESCE priority resolver — Coros wins on non-NULL, Garmin's writes preserved on NULL via column-level COALESCE-on-update (in v1.7 Coros's endpoint surface only populates `resting_hr` + `hrv_last_night`; other wellness columns continue Garmin-fed); new structured table `coros_evolab_day` (migration 0006, `SCHEMA_VERSION` 5→6) with `vo2max` / `stamina_level` / `training_load` / `lthr` / `ltsp_s_per_km`; new `runos/transforms/coros_evolab.py` + `runos/analysis/coros_evolab.py` (`EvoLabDay` / `EvoLabContext` / `read_evolab`); recovery report gains `## Coros (EvoLab)` section after Nutrition with 3-state degradation rule (absent → omit / stale >3d → nudge / current → block) rendering VO2max / Stamina (with 7d delta) / Training load / Threshold HR / Threshold pace (`ltsp_s_per_km` honours user `Units` preference via `format_pace`); `runos coros login` + `runos coros sync` CLI commands mirror Garmin pattern; `Settings.coros_email` + `Settings.coros_password: SecretStr`; `.env.example` Coros block; `docs/COROS.md` 168 lines. Garmin connector remains installed as safety net. Activities still flow from Strava. **API endpoint surface for Coros (verified vs cygnusb/coros-mcp during 18-01)**: base `https://teameuapi.coros.com`; auth via `accessToken` header + `yfheader.userId`; HRV from `/dashboard/query`, EvoLab from `/analyse/query` (returns `data.t7dayList[]`), sleep+RHR from `/analyse/dayDetail/query`; `result: 0000` success / `0102` or `0107` auth-fail. 760 tests green (+44 since Phase 17, 41 new -- pre-existing fixture rewrites accounted for), ruff clean, no Garmin regression, schema migration verified against fresh tmp DB. Verifier PASS 3/3. **Live use requires Ross to add credentials to .env and run `runos coros login`** — separate hand-off task. New `runos/analysis/preferences.py` (4 frozen+slots dataclasses — `Physiology`/`Units`/`Nutrition`/`PreferencesContext`; lenient parser with H2-section grammar, threshold-pace M:SS/mi + M:SS/km + bare s/km parsing, units alias normalisation, prose sections captured verbatim, malformed lines captured) + `tempo/units.py` formatter (`format_distance`/`format_pace`, NIST-exact `KM_PER_MILE = 1.609344`, en-dash sentinel for None/<=0/NaN). Five `.env`-sourced knobs DELETED from `Settings`: `RUNOS_THRESHOLD_PACE_S_PER_KM` / `RUNOS_MAX_HR` / `RUNOS_RESTING_HR` / `RUNOS_THRESHOLD_HR` / `RUNOS_TARGET_KCAL`. New `Settings.preferences_path` derived property. `runner.py` + `cli.py` + `sync/daily.py` now load `prefs = parse_preferences(settings.preferences_path)` once per invocation and pass typed values (`prefs.physiology.*`, `prefs.nutrition.target_kcal`, `prefs.units`) into the analysis seam (which preserved its existing `LoadConfig` / `target_kcal` / `units` typed parameters — smaller diff than threading `prefs` everywhere). `render_load_trend` now takes `units: Units` and switches column header `"Distance (km)"` ↔ `"Distance (mi)"` + cell value via `format_distance` (storage stays SI throughout). `preferences.md.example` (75 lines, placeholder values) + `docs/PREFERENCES.md` (325 lines) + `.env.example` updated with the Phase-17 migration comment trail. 716 tests green (+30 from Phase 16's 655 + ~31 new — 17 preferences parser + 9 units formatter + 2 CLI honour-preferences + 2 config — minus a few rewritten fixtures), ruff clean, `grep settings.<deleted>` returns empty. Verifier PASS 3/3.
progress:
  total_phases: 1
  completed_phases: 1
  completed: [18]
  total_plans: 5
  completed_plans: 5
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** Between milestones. v1.0 + v1.1 + v1.2 + v1.3 + v1.4 + v1.5 all shipped. Significant post-v1.5 ad-hoc operational hardening done on `main` (see § Post-v1.5 hardening below). Next big iteration awaits `/gsd-new-milestone`.

## Post-v1.5 hardening (2026-05-28, ad-hoc — not a tracked phase)

These changes ship on `main` but have NO `phases/` folder. Captured here so a fresh future session knows what's in the code beyond Phase 16.

- **Stash integration of the long-parked bot WIP.** Eight discrete improvements landed in one commit: SDK 0.2.x message-shape robustness (`AssistantMessage` class-name check, `TextBlock.text` fallback); SQLite cross-thread fix in `bot/app.py::_post_init`; empty-reply guard in `_run_agent_turn`; `/new` → `/clear` rename; indefinite session lifetime (no 4hr window); new `/sync` command in the bot; `setMyCommands` menu publish; voice transcript echoed back as `<i>📝 Heard: …</i>`; Markdown-tables → `<pre>` block rendering in `agent.py`.
- **Orphan-journal-link auto-hook.** `runos/journal/service.py::link_orphan_entries` + post-transform hook in `transforms/runner.py` + `runos journal link-orphans` CLI. Journal entries captured before Strava sync now auto-link once the activity arrives.
- **Code-review simplify pass.** 15 HIGH-severity findings actioned across the codebase: strength header bug (`## YYYY-MM-DD Name` without separator no longer drops the session); voice cleanup leak (single try/finally guarantees `_cleanup_voice_file` runs even on download failure); 5 Py2-style `except` clauses parenthesised; Garmin commands catch `ValueError` (missing creds → red remediation, not traceback); symmetric pipeline isolation (Strava now wrapped like Garmin); nutrition `blocks` ↔ `entries` dedup mismatch fixed; setup wizard `--only` + `--skip` precedence validated.
- **Operational model simplification.**
  - Dropped the daily-report schedule. `runos run-daily` is kept as a manual command but the launchd plist is gone.
  - New hourly `runos sync --notify-on-failure --with-recent-streams` via `com.runos.hourly-sync.plist` (launchd `StartInterval=3600`). Silent on success, Telegram message on any source failure or crash.
  - New `runos install-hourly-sync` CLI command renders the plist and prints bootout commands for the old daily plist.
  - Reports are now generated on-demand via the bot (`generate-report` skill) or directly via `runos analyze <type>`.
- **HR stream backfill targeting.** `runos strava streams --prefer-with-hr --limit N` filters the lazy-fetch queue to activities with `avg_hr > 0`, most-recent first. Plus `--with-recent-streams` opportunistically pulls HR streams for activities in the last 24h as part of every hourly sync (1-3 extra Strava requests, well under rate limit).
- **Documentation split: `CLAUDE.md` = coach, `ENGINEERING.md` = engineering reference.** The bot's Claude Code session loads CLAUDE.md by default (coach persona, available skills, how to talk to Ross). When the session recognises an engineering task, it reads ENGINEERING.md (the technical content that previously lived in CLAUDE.md plus the simplify-pass updates).
- **Eight `.claude/skills/*` SKILL.md files** backing the coach persona: `log-run-journal`, `log-strength-session`, `log-heat-session`, `log-weight`, `log-food`, `update-race-result`, `generate-report`, `coach-readout`. Each has trigger phrases, exact CLI/file operations, edge cases, and example replies in the coaching voice.
- **`runos/sync/notify.py`** — stdlib-only Telegram notifier (urllib, no python-telegram-bot dependency) for unattended jobs.

**Total test count after hardening:** 686 passing, 1 deselected (the slow Whisper integration test), ruff clean.

**Commits since v1.5 verification commit (`f9fe9bf`):** ~25 commits, all on `main`, pushed to origin.

## Current Position

Phase: Phase 16 (Nutrition Tracker, v1.5) — COMPLETE; post-v1.5 hardening also on `main`
Plans: 16-01 + 16-02 + 16-03 + 16-04 — all COMPLETE
Status: v1.5 SHIPPED. New `food.md` markdown tracker accepts TWO interchangeable formats — inline single-line (`- YYYY-MM-DD <meal>: <food> | p:<g> c:<g> f:<g> cal:<n>`) AND block-per-meal (`## YYYY-MM-DD <meal>` header + nested `- <food>: <macros>` bullets) — parsed by one lenient regex pipeline (case-insensitive macro keys, unordered, unknown keys silently ignored, missing required keys → entry skipped + line recorded in `malformed_lines`, never raises). Daily P/C/F/cal rollup with macro-% split (kcal-share formula, zero-kcal degenerate → 0.0 not crash). 7d trailing rollup averaged across days WITH entries only (a partial log doesn't drag the average down); 28d scalar kcal mean; optional `target_kcal` deficit/surplus when `RUNOS_TARGET_KCAL` is set in `.env`. Plan 16-01 added the parser + rollup + `Settings.food_path` + `Settings.target_kcal_default` + 22 unit tests. Plan 16-02 added `runos analyze nutrition` CLI + `nutrition_report.render_nutrition` with header banner + 5 sections (today's totals / per-meal breakdown / 7d rolling / 28d kcal / optional goal) + 6 report tests + aggregate `runos analyze` integration. Plan 16-03 wired the rollup into `RecoveryAssessment` + `_render_nutrition_section` (3-state rule mirroring weight, threshold 3d not 14d since food is daily) + `runner.generate_recovery`/`generate_all` + CLI; added 7 recovery-integration tests; section placed after `## Weight` so the non-running-context cluster reads Heat → Strength → Weight → Nutrition. Plan 16-04 shipped `food.md.example` (14 entries, both formats side-by-side, 0 malformed), `docs/NUTRITION.md` (394 lines), `.env.example` (`RUNOS_TARGET_KCAL` opt-in knob), and a README mention. 655 tests green (+36 from Phase 15), ruff clean. NUTR-03 / NUTR-04 / NUTR-05 all satisfied; NUTR-01 / NUTR-02 explicitly reclassified to v2 (`NUTR-CSV-01` / `NUTR-CSV-02`) — CSV importer becomes a Layer-2 follow-up once the markdown surface proves itself. Verifier PASS 3/3.
Last activity: 2026-05-28 — Phase 16 verified. v1.5 milestone closed. The recovery report now carries Heat → Strength → Weight → Nutrition as its non-running-context cluster, and `runos run-daily` automatically renders a dated nutrition report alongside the other four.

## What's Done (Phase 16: Nutrition Tracker — v1.5 milestone)

- `runos/analysis/nutrition.py` (558 LoC) — five `@dataclass(frozen=True, slots=True)` types (`FoodEntry`, `MealBlock`, `FoodContext`, `DailyNutrition`, `NutritionRollup`), three regex grammars (`_INLINE_RE` for Format A, `_BLOCK_HEADER_RE` + `_BLOCK_BULLET_RE` for Format B), the `_parse_macros` extractor (case-insensitive, unordered, `cal:` rounded via `int(round(float(...)))`, missing-required → `None`), and the public `parse_food(path)` / `daily_nutrition(entries, day)` / `nutrition_rollup(entries, today, *, target_kcal=None)` functions. Missing file → `present=False`. Both formats freely intermix in the same file. Latest-wins dedup on `(date, meal_name, food_label)`. Block with a malformed `##` header → whole block skipped (header + bullets all recorded as malformed). Never raises. Lenient throughout. (NUTR-03, NUTR-04)

- `daily_nutrition` math: sums P/C/F/kcal across all entries with `entry.date == day`; macro percentages computed AFTER summation via the kcal-share formula `(protein_g * 4) / kcal * 100` etc.; zero-kcal degenerate → all three percentages = `0.0` (no `ZeroDivisionError`). `entry_count` exposes the per-day count for transparency. (NUTR-04)

- `nutrition_rollup` math: windows are left-open right-closed `(today - N, today]` so a same-day log always counts; averages across DAYS with entries (a day without entries is NOT in the denominator) — `days_logged_7d` exposes the day-count; `avg_28d_kcal` is a single scalar int (deeper 28d trend lives in v2); `deficit_surplus_7d = avg_7d.kcal - target_kcal` when both are available, else `None`. Empty/all-future `entries` → all-None / zero-counts rollup. (NUTR-04)

- `Settings.food_path` derived property in `runos/config.py` (mirrors `weight_path` exactly): returns `content_root / "food.md"`. `Settings.target_kcal_default: int | None` with `validation_alias="RUNOS_TARGET_KCAL"`, default `None`, silently absent when unset. (NUTR-04)

- `runos/analysis/nutrition_report.py` (192 LoC) — `render_nutrition(today, rollup, today_breakdown, blocks_today, context)` builds the standalone-report markdown body: header `# Nutrition — YYYY-MM-DD` + `Data:` freshness line + 5 sections in fixed order: `## Today's totals` (P/C/F/cal + macro % or `_No entries logged for today yet._`), `## Per-meal breakdown` (subheaders per `(today, meal_name)`, omitted when today has zero entries), `## 7-day rolling average` (mean P/C/F/cal + `(D days logged of 7)` + macro-split second line, or `_No entries in the last 7 days._`), `## 28-day kcal mean` (scalar or `_Insufficient history._`), `## Goal` (only when `rollup.target_kcal is not None`; Unicode `+` / `−` (U+2212) / `±` (U+00B1) sign). Absent-file short-circuit emits a single-paragraph "Create food.md" body, no sections. (NUTR-05)

- `runos analyze nutrition` CLI (`runos/cli.py:722-754`) — new typer command mirroring the existing per-report commands. Reads `food.md` via `settings.food_path`, threads `settings.target_kcal_default`, calls `runner.generate_nutrition`, writes `reports/<YYYY-MM-DD>-nutrition.md`. `runner.generate_all` accepts both new params and emits the nutrition report when `food_path is not None`. `cli.py` passes them at both the top-level `runos analyze` and `runos analyze recovery` call sites. `runos/sync/daily.py:111-112` threads them into the daily launchd pipeline so `runos run-daily` automatically renders nutrition. (NUTR-05)

- `RecoveryAssessment` gains `nutrition: NutritionRollup | None = None` and `nutrition_present: bool = False` (defaults preserve back-compat). `assess_recovery_from_db` accepts `food_path: Path | None = None` and `target_kcal: int | None = None`, threads them through the same single-reconstruction pattern that carries heat + strength + weight. `_render_nutrition_section` enforces the 3-state rule: `nutrition_present is False` OR `nutrition is None` OR `latest_day is None` → omit; `days_since_last > 3` → `_Last food entry N days ago — log today's meals to keep the rollup live._`; current → `7d avg P:<g> · C:<g> · F:<g> · cal:<n> (D days logged of 7)`. When `target_kcal` is set AND `deficit_surplus_7d is not None`, a second line `Target N kcal/day · 7d Δ ±X kcal/day` is appended (Unicode minus / plus-minus / plain plus). Section is placed AFTER `_render_weight_section` so the non-running-context cluster reads Heat → Strength → Weight → Nutrition. (NUTR-05)

- `food.md.example` (75 lines) — committed worked example. Both formats side-by-side: 7 inline entries + 2 Format-B blocks containing 7 nested-bullet entries = 14 total entries. Parses cleanly: `present=True`, 14 entries, 2 blocks, `malformed_lines=()`. Doubles as a parser regression fixture. (NUTR-05)

- `docs/NUTRITION.md` (394 lines) — end-to-end format documentation. Sections: `## Two interchangeable formats` (Format A inline grammar + Format B block grammar + worked examples + equivalence guarantee), `## Lenient parsing` (missing-file → `present=False`, malformed lines captured by number, never logs entry contents, never raises), `## Rollup semantics` (left-open-right-closed windows, days-with-entries-only averaging, optional kcal-goal opt-in), `## Recovery report integration` (3-state rule, 3-day staleness threshold, section ordering), `## Agent-append guidance` (single-line inline appends are safe; corrections via append + latest-wins), `## Future MFP CSV importer relationship` (the inline format is the natural CSV output shape; `NUTR-CSV-01` Layer-2 follow-up). (NUTR-05)

- `.env.example:131` — `# RUNOS_TARGET_KCAL=2200   # optional, enables goal tracking in nutrition + recovery reports` documented as a commented opt-in knob.

- `README.md` (L424-431) — Tracker-files paragraph updated to include `food.md.example` / `food.md` alongside `races.md` / `heat.md` / `strength.md` / `weight.md` with a link to `docs/NUTRITION.md`.

- `tests/test_nutrition.py` (22 tests) — covers all CONTEXT-listed cases: `parse_food_missing_file_returns_absent_context`, `parse_food_inline_format_happy_path`, `parse_food_block_format_happy_path`, `parse_food_both_formats_in_same_file`, `parse_food_inline_and_block_produce_equivalent_entries` (the equivalence guarantee), `parse_food_unordered_macro_keys`, `parse_food_case_insensitive_keys`, `parse_food_tolerates_rounding_on_kcal`, `parse_food_skips_entries_missing_required_macros`, `parse_food_unknown_keys_ignored`, `parse_food_latest_wins_on_same_date_meal_food`, `parse_food_block_with_malformed_header_skipped`, `parse_food_ignores_headers_blanks_and_comments`, `daily_nutrition_sums_entries`, `daily_nutrition_macro_percentages_kcal_share`, `daily_nutrition_zero_kcal_degenerate`, `nutrition_rollup_empty_returns_all_none_with_zero_days_logged`, `nutrition_rollup_7d_window_left_open`, `nutrition_rollup_averages_across_days_with_entries_only`, `nutrition_rollup_target_deficit_surplus_when_set`, `nutrition_rollup_target_none_when_unset`, + a `_parse_macros` direct unit test.

- `tests/test_nutrition_report.py` (6 tests) — `writes_dated_file_to_reports_dir`, `today_no_entries_emits_placeholder`, `per_meal_breakdown_present_when_today_has_entries`, `omits_goal_section_when_target_unset`, `omits_all_sections_when_food_file_absent`, `included_in_generate_all_aggregate`.

- `tests/test_recovery.py` — 7+ new tests under `# ---- Nutrition section ----` divider: `omits_nutrition_section_when_absent`, `omits_nutrition_when_present_but_empty`, `emits_stale_nudge_when_last_entry_over_3d`, `emits_7d_trailing_rollup_when_current`, `appends_goal_line_when_target_set` (covering both surplus and deficit signs), `nutrition_section_follows_weight`, plus an end-to-end fixture-driven render.

- **Test totals:** 655 tests green (was 619 after Phase 15; +36 from this phase). `ruff check runos/ tests/` clean. Zero `TODO` / `FIXME` / `XXX` / `TBD` / `HACK` / `PLACEHOLDER` markers in `runos/analysis/nutrition.py` or `runos/analysis/nutrition_report.py`. The one slow Whisper test stays deselected per project convention.

- **Verifier outcome:** PASS 3/3 success criteria. See `.planning/phases/16-nutrition-tracker/16-VERIFICATION.md`.

### Conventions established this phase

- **A markdown tracker can accept multiple interchangeable formats parsed by one lenient pipeline.** Nutrition is the first tracker to ship with two equivalent grammars (inline + block) — the choice is purely ergonomic (inline for quick agent-append; block when logging a multi-item meal). Both formats produce identical `FoodEntry` records modulo `source_line` / `source_format`. The equivalence is asserted by a dedicated test. This pattern unlocks future trackers where the natural agent-typed form differs from the natural human-typed form.

- **Optional opt-in knobs stay silent when unset.** `RUNOS_TARGET_KCAL` is the first user-facing env var that activates a per-report feature (goal-delta line). When unset, the rollup's `target_kcal` and `deficit_surplus_7d` are `None` and the renderer omits the goal section entirely with no warning. Pattern: silent absence > warning noise for optional features.

- **Markdown-first, structured-table-later still applies.** Nutrition is the FIFTH tracker (races / heat / strength / weight / food) to ship as a lenient markdown-only Layer-1 surface. `NUTR-01` / `NUTR-02` (the original MFP-CSV-import requirements) are explicitly reclassified to v2 as `NUTR-CSV-01` / `NUTR-CSV-02` — the inline format was deliberately designed to be the natural output of a future CSV importer, so the Layer-2 work becomes mechanical once the markdown surface proves itself.

## What's Done (Phase 15: Weight Tracker — v1.4 milestone)

- `runos/analysis/weight.py` (303 LoC) — three `@dataclass(frozen=True, slots=True)` types (`WeightEntry`, `WeightContext`, `WeightRollup`), the `_to_kg` + `_parse_entry_line` helpers, the lenient `parse_weight(path)` reader, and the `weight_rollup(entries, today)` function. Single regex grammar `- YYYY-MM-DD: <weight> [kg|lb|lbs] [| notes: ...]`; `lbs` normalises to `lb`; missing unit defaults to `kg`. Out-of-range guard: kg-equivalent must satisfy `20 < kg < 500` (catches `7.24 kg`, `724 kg`, `1600 lb`). Latest-wins on duplicate dates via `dict[date, WeightEntry]`. Lenient throughout: missing file → `present=False`, malformed lines recorded in `malformed_lines`, never raises. (WEIGHT-01, WEIGHT-02)

- `weight_rollup` math: windows `(today - N, today]` left-open right-closed (same-day weigh-in always counts; `today - N` itself excluded); every numeric output kg-normalised via `_to_kg` (lb × 0.453592); EWMA `alpha=0.1` seeded from the FIRST entry's kg-converted weight, iterated forward; `latest_entry` preserves original unit; `unit_mixed=True` iff both `kg` and `lb` appear. Hand-computed EWMA `[70, 80, 90] → 72.9` proven by test. (WEIGHT-03)

- `Settings.weight_path` derived property in `runos/config.py` (mirrors `strength_path` exactly): returns `content_root / "weight.md"`. No new env var. (WEIGHT-01)

- `RecoveryAssessment` gains `weight: WeightRollup | None = None` and `weight_present: bool = False` (defaults preserve back-compat). `assess_recovery_from_db` accepts `weight_path: Path | None = None` and threads it through the same single-reconstruction pattern that already carries heat + strength. `_render_weight_section` enforces the 3-state rule: absent OR `latest_entry is None` → omit; `days_since_last > 14` → `_Last weigh-in N days ago — log a current reading to keep the rollup live._`; current → `{latest} kg today · 7d avg {a7} kg · 28d avg {a28} kg · trend {ewma} kg · {±X.X} kg vs 28d baseline`. `_fmt_weight_delta` uses Unicode minus (U+2212) for negatives, plus-minus (U+00B1) for near-zero, plain `+` for positives, one decimal throughout. When `unit_mixed=True`, trailing ` _(mixed kg/lb in log — normalised to kg)_` caveat is appended. Section is placed AFTER `## Strength & conditioning` so the recovery report's non-running-context cluster reads Heat → Strength → Weight. (WEIGHT-04)

- `runner.generate_recovery` + `runner.generate_all` accept `weight_path: Path | None = None` and thread it through `assess_recovery_from_db` alongside heat + strength. CLI: `runos analyze` (L613) and `runos analyze recovery` (L684) both pass `weight_path=settings.weight_path` next to `settings.strength_path`. (WEIGHT-04)

- `weight.md.example` (71 lines) — committed worked example with 14 entries spanning 2026-05-15→2026-05-28 (2 weeks). 12 × `kg`, 1 × `lb`, 1 × `lbs` (demonstrating the normalisation contract); 4 entries carry `| notes: ...` annotations; one entry's notes contain an embedded `|` pipe (proving only the FIRST `| notes:` is the split point). Numbers are synthetic. Parses cleanly through `runos.analysis.weight.parse_weight`: `present=True`, 14 entries, `malformed_lines=()`. Doubles as a parser regression fixture. (WEIGHT-05)

- `docs/WEIGHT.md` (199 lines) — end-to-end format documentation mirroring `docs/STRENGTH.md`: `## Format` (entry grammar + 4-entry CONTEXT example as fenced code), `## Lenient parsing` (missing-file degradation, malformed-line recording, out-of-range guard, never-logs-values privacy guarantee), `## Rollup semantics` (windows, kg-normalisation, EWMA alpha=0.1 + ~7-entry half-life), `## Recovery report integration` (3-state rule, placement, mixed-unit caveat), `## Agent-append guidance` (single-line append at EOF, `cat >> weight.md` is safe, latest-wins enables append-only corrections), `## What's NOT in this layer` (deferred features). (WEIGHT-05)

- `README.md` — Tracker-files paragraph (L424-428) updated to include `weight.md.example` / `weight.md` alongside `races.md` / `heat.md` / `strength.md` with a link to `docs/WEIGHT.md`. (WEIGHT-05)

- `tests/test_weight.py` (315 LoC, 19 tests) — covers `_to_kg`, `_parse_entry_line`, the full `parse_weight` lenient contract (missing file, happy path with mixed kg/lb/lbs, malformed lines, latest-wins on duplicates, optional notes with embedded `|` pipes, out-of-range rejection, header/blank-line/prose ignoring), and the rollup (empty, single-entry-today, left-open-right-closed window math, EWMA hand-computed expectation, unit-mixed kg-normalisation, days-since-last).

- `tests/test_recovery.py` — 7 new tests under `# ---- Weight section ----` divider: `test_recovery_renderer_omits_weight_section_when_absent`, `test_recovery_renderer_omits_weight_when_present_but_empty`, `test_recovery_renderer_emits_stale_nudge_when_last_weigh_in_over_14d`, `test_recovery_renderer_emits_full_rollup_when_current`, `test_recovery_renderer_appends_mixed_unit_caveat`, `test_recovery_renderer_weight_section_follows_strength`, `test_fmt_weight_delta_signs`.

- **Test totals:** 619 tests green (was 593 after Phase 14; +26 from this phase). `ruff check runos/ tests/` clean. Zero `TODO` / `FIXME` / `XXX` / `TBD` / `HACK` / `placeholder` markers in any `runos/analysis/weight.py` or `tests/test_weight.py`. The one slow Whisper test stays deselected per project convention.

- **Verifier outcome:** PASS 5/5 success criteria. See `.planning/phases/15-weight-tracker/15-VERIFICATION.md`.

### Conventions established this phase

- **Markdown trackers stay markdown until they prove themselves.** Weight is the fourth tracker (races / heat / strength / weight) to ship as a lenient markdown-only Layer-1 surface before any structured DB table. Pattern stays consistent: frozen+slots dataclasses, lenient parser, never-raises contract, `Settings.{name}_path` derived property, recovery-report integration with the 3-state degradation rule, committed `.example` file that doubles as a parser fixture, `docs/{NAME}.md` end-to-end. Next tracker (nutrition, Phase 16) will follow the same shape.

- **kg-normalisation at rollup time, unit preserved on the entry.** The rollup carries kg numerics; the latest entry preserves its original unit so the user-facing renderer can still report `72.4 kg today` faithfully when the source was kg, OR `72.6 kg today` after auto-converting from `160.0 lb`. Mixed-unit logs get a footnote caveat rather than a broken rollup — the user who logs lb on a hotel scale and kg at home shouldn't see a discontinuity.

## What's Done (Phase 14: First-Run Setup Wizard — v1.3 milestone)

- `runos/setup/env_io.py` — `read_env(path) -> dict[str, str]` (lenient: missing → `{}`, blank/comment lines skipped, duplicate keys last-wins, surrounding double-quotes stripped) and `atomic_write_env(path, updates, delete_keys)`. The write template mirrors `runos/connectors/tokens.py` exactly: `tempfile.mkstemp` in destination dir → `os.fchmod(fd, 0o600)` → write + `flush` + `fsync` → `os.replace(tmp, path)` → best-effort `fsync` on the parent dir → final `chmod 0o600`. Crash mid-write leaves either the prior complete `.env` or the new one, never a torn file. Comments + untouched key ordering preserved byte-identically; values with spaces / `$` / `#` / tab are double-quoted on write. Module never logs / echoes a value. (SETUP-03)

- `runos/setup/state.py` — `@dataclass(frozen=True, slots=True) class InstallState` with 7 bool fields (`db_initialised`, `content_dir_set`, `strava_configured`, `garmin_configured`, `telegram_configured`, `daily_scheduler_installed`, `bot_scheduler_installed`); `detect_install_state(settings)` is pure read-only over filesystem + a single read-only SQLite connection (closed in `finally`) for the schema-version check. No network, no `launchctl`. Plist presence at `~/Library/LaunchAgents/com.runos.{daily,telegram-bot}.plist` is the contract. (SETUP-02)

- `runos/setup/prompts.py` — thin `typer.prompt` / `typer.confirm` / `typer.secho` wrappers with `[set]` / `[done]` / `[fresh]` / `[skip]` coloured indicators. `prompt_secret(label)` always passes `hide_input=True, confirmation_prompt=False`. Single mockable surface for tests. (SETUP-03)

- `runos/setup/wizard.py` (~670 LOC) — the 10-step orchestrator. `STEP_IDS = ("welcome", "db", "content", "strava", "garmin", "telegram", "scheduler", "bot-scheduler", "smoke", "finish")`. One function per step; each starts with a state check and returns `[done]`+skipped when the corresponding `InstallState` bool is True. `run_wizard(settings, *, only, skip_garmin, skip_telegram, skip_scheduler, skip_bot_scheduler, skip_smoke, non_interactive)` iterates the dispatch list, re-detects state after every step (cheap), and returns the exit code (0 = ok / 1 = a non-skipped step failed terminally / 2 = `typer.Abort` from Ctrl-C or `--non-interactive` hitting a required prompt). `--skip-telegram` implies `--skip-bot-scheduler`. The bot-scheduler step is only offered if Telegram is configured (either already-state or completed-this-run). Credentials are always written to `.env` BEFORE the downstream delegated call, so a partial failure leaves creds in place for retry. (SETUP-01, SETUP-02, SETUP-05)

- **Delegation (SETUP-04, LOCKED)** — every credentialed step calls into the existing helper directly: DB → `runos.cli._init`; Strava → `runos.connectors.factory.build_strava_connector` + `connector.authorization_url` + `connector.exchange_code` (same triple `runos strava auth` makes); Garmin → `runos.connectors.factory.garmin_login(settings, prompt_mfa=…)`; daily scheduler → `runos.scheduler.install_plist(...)`; bot scheduler → `runos.scheduler.install_telegram_bot_plist(...)`; smoke → `runos.sync.pipeline.run_full_sync(conn, settings)`. Zero subprocess calls; zero duplicated handshake / plist render / MFA prompt code.

- `runos/cli.py` — `@app.command("setup")` thin wrapper that parses `--only` / `--skip-*` / `--non-interactive`, validates `--only` against `STEP_IDS - {welcome, finish}` (unknown → `typer.Exit(2)`), calls `run_wizard(settings, …)`, raises `typer.Exit(exit_code)` on non-zero return.

- `docs/SETUP.md` — end-to-end walkthrough. Two paths (one-command + manual). All 10 steps documented in the locked order with *What it does* / *Wizard prompts* / *Files written* / *Manual equivalent* / *Skip* / *Recover* subsections.

- `README.md` — "Getting Started" rewritten to lead with the 4-line `git clone / cd / uv sync / uv run runos setup` path.

- 593 tests green (+63 from Phase 13). Verifier PASS 5/5.

## What's Done (Phase 13: Strength & Conditioning Tracker — v1.2 milestone)

- `runos/analysis/strength.py` — frozen+slots `StrengthSet` / `StrengthExercise` / `StrengthSession` / `StrengthContext` / `StrengthRollup` dataclasses + `parse_strength(path)` + `strength_rollup(sessions, today)`. Lenient parser modelled directly on `runos/analysis/heat.py`: missing file → `present=False`, malformed lines skipped, unknown keys ignored, never raises. Handles weighted sets (`55x8`), bare-rep sets (`15`), timed holds (`1:00`), supersets (`[A]`/`[B]`), equipment / notes / rest metadata. (SC-01, SC-02)

- `runos/config.py` — `Settings.strength_path` returns `<content_root>/strength.md` (mirrors `heat_path`). (SC-03)

- `runos/analysis/recovery.py` + `runos/analysis/runner.py` + `runos/analysis/report.py` — recovery report gains a `## Strength & conditioning` section with the same 3-state degradation as the heat section (absent → omit / lapsed → one-line nudge / active → rollup with session count, total tonnage, last-session age). (SC-04, SC-05)

- `strength.md.example` + `docs/STRENGTH.md` — committed format reference + operational doc.

- `tests/test_strength.py` + recovery-report integration tests — 32 new tests; 530 total tests green.

## What's Done (Phase 12: lifecycle / hardening / privacy — v1.1 closing milestone)

- Plan 12-01: `runos bot install-scheduler` + launchd `com.runos.telegram-bot.plist` with `KeepAlive=true` so a crash / sleep / network blip auto-restarts the bot. `VOICE_RETENTION_DAYS` startup sweep + per-handler immediate-delete + `runos bot purge-voice` manual hatch. Agent cwd + data_dir logged at startup.

- Plan 12-02: top-level `telegram_error_handler` (logs traceback, sends a fixed "something went wrong" reply, never re-raises). `docs/PRIVACY.md` is the single-source user-facing privacy contract. README + `docs/TELEGRAM_BOT.md` updated with launchd lifecycle, voice retention, and error-handler sections.

- 498 tests green; v1.1 closed.

## What's Done (Phase 11: Claude Code agent via SDK)

- `runos/bot/agent.py` — wraps `claude-agent-sdk` (uses the user's Claude Code subscription, no `ANTHROPIC_API_KEY`). Per-chat `--resume` over a 4hr rolling window. Final assistant text → HTML reply, split at 4096 chars. Detects `AssistantMessage` by class name (the SDK 0.2.x message shapes have no `.role` / `.type` attrs). Empty assistant text → `"(agent finished without a reply)"` so Telegram doesn't reject an empty message.

- `runos/bot/sessions.py` — per-chat session-id store with a 4-hour idle window; `/new` resets.

## What's Done (Phase 10: Telegram bot worker)

- `runos/bot/app.py` — Telegram Application builder + handler registration + Whisper warmup + cwd log + voice sweep. Defensive `delete_webhook` in `post_init` to avoid 409 Conflicts.

- `runos/bot/handlers.py` — `start`, `voice`, `text`, `/new` handlers. Owner-chat-id allowlist; the bot ignores everything else silently.

- `runos/bot/transcribe.py` — `faster-whisper` singleton on CPU (no Metal/GPU on Mac). `small.en` int8 default. Eager `list(segments)` because the iterator is lazy.

## What's Done (Phase 9: Telegram + Whisper foundations)

- `pyproject.toml` deps: `python-telegram-bot`, `faster-whisper`, `claude-agent-sdk`. `WHISPER_MODEL_NAME` / `WHISPER_COMPUTE_TYPE` / `WHISPER_DEVICE` / `VOICE_RETENTION_DAYS` settings (with `validation_alias` for bare-name env keys).

- Voice cache under `<content_dir>/voice/`, gitignored. faster-whisper warmup on startup.

## What's Done (Phase 8: Modular Trackers + Heat Adaptation)

- `races.md` gains a `result:` field + auto-link from race → matching Strava activity (`runos/analysis/race_link.py`).

- New `heat.md` tracker — appendable session log; `runos/analysis/heat.py` lenient parser + 3-state rollup surfaced in recovery report.

- `plan.md` retired (training plan moved to whichever format the owner prefers; no more parser).

- `runos/analysis/context.py` deleted; per-tracker modules now own their own parse + render shape.

## What's Done (Phases 1-7: v1.0 — Strava + Garmin → SQLite → analyses → daily launchd job)

- See `.planning/phases/01-foundation/` through `.planning/phases/07-recovery-correlation/` for the full per-phase shipped list. Summary: Strava OAuth + paged resumable backfill → raw store; Garmin (isolated failure domain, no-retry-on-429) → raw store; pure-stdlib transforms → structured layer + `daily_summary` view; `runos/analysis/{load,fitness,race,recovery,correlation,noteworthy}.py` produce dated markdown reports; `runos run-daily` launchd job runs the lot at 05:30 local time. 235 → 288 → 339 → 497 tests across phases.

## Performance Metrics

**Velocity:**

- Total plans completed (this milestone): 3
- Average duration: ~ unknown (parallel waves; not tracked per-plan in this phase)
- Total execution time (this milestone): single-day session

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 15. Weight Tracker (v1.4) | 3 | — | — |
| 14. First-Run Setup Wizard (v1.3) | 3 | — | — |
| 13. Strength & Conditioning Tracker (v1.2) | 3 | — | — |
| 12. Lifecycle / hardening / privacy (v1.1 closing) | 2 | — | — |

**Recent Trend:**

- Last 3 plans: 15-01 (weight parser + rollup), 15-02 (recovery integration), 15-03 (docs + example)
- Trend: shipped same-day; 619 tests green; ruff clean.

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Strava-first milestone: prove pull → store → analyse end-to-end on the clean source before the fragile Garmin connector
- Two-layer raw → structured storage: connectors write only to `raw_response`; transforms read raw and write structured, enabling `runos rederive` with no network
- Date spine in Phase 3 (not later): CTL/ATL EWMAs and ACWR windows are silently wrong without a zero-filled spine
- Journaling early (Phase 5): correlation analysis is data-hungry, so paired subjective history must start accumulating before Garmin
- **(Phase 14, 2026-05-28)** First-run setup is orchestration-only — every credentialed step delegates in-process to the existing helper. No subprocess; no duplicated OAuth handshake, MFA prompt, or plist render. `.env` writes go through a single atomic helper modelled on `tokens.py`.
- **(Phase 15, 2026-05-28)** Markdown trackers stay markdown until they prove themselves. Weight is the fourth tracker (races / heat / strength / weight) to ship as a lenient markdown-only Layer-1 surface before any structured DB table. kg-normalisation happens at rollup time; the entry preserves its original unit. Mixed-unit logs get a footnote caveat rather than a broken rollup.

### Roadmap Evolution

- Phase 8 added: Modular Trackers + Heat Adaptation — split plan.md into focused tracker files (`races.md` w/ result + auto-link, new `heat.md`); retire `plan.md`. (2026-05-27)
- Phase 14 added + shipped: First-Run Setup Wizard (v1.3) — `runos setup` reduces clone-to-working-daily-sync from a multi-step README walkthrough to a single idempotent command. (2026-05-28)
- Phase 15 added + shipped: Weight Tracker (v1.4) — `weight.md` markdown tracker with kg/lb normalisation + EWMA trend; surfaced in the recovery report as the third tracker section (Heat → Strength → Weight). (2026-05-28)

### Pending Todos

- **Live `runos setup` smoke** against real Strava + Garmin + Telegram (the wizard is verified against mocked delegated symbols; this is a follow-up session task carried over from Phase 14).
- **Phase 16 (Nutrition Tracker, v1.5)** is the next planned phase: `food.md` markdown tracker (two interchangeable formats — inline single-line and block-per-meal), daily P/C/F/cal rollup, new `runos analyze nutrition` standalone report, recovery-report 7-day-trailing nutrition mini-section.

### Blockers/Concerns

- [Phase 2 — RESOLVED] Strava API Agreement conflict documented as accepted (README + REQUIREMENTS Known Accepted Conflicts); private self-data, never shared.
- [Phase 2 — pending user] Live Strava pull needs the user's own API app: create at https://www.strava.com/settings/api, set RUNOS_STRAVA_CLIENT_ID/SECRET in .env, run `runos strava auth`, then `runos strava backfill`. All machinery (incl. Phase-4 analysis) proven against mocks/seeded data; this is the only remaining step before live reports.
- [Phase 4 — RESOLVED] rTSS uses `avg_pace_s_km` directly (no grade-adjusted/normalised pace in v1; NGP/GAP is a documented future refinement). hrTSS fallback uses HR-reserve anchored on threshold HR. Threshold pace is a configurable pydantic setting. Insufficient days are flagged, not invented.
- [Phase 6] `garminconnect` is the single fragile dependency (garth deprecated 2026-03-27); pin version, monitor upstream, budget for a version bump
- [Phase 7] HRV baseline cold-start and multi-signal recovery weighting may need a brief planning-time research pass; first weeks of Garmin data will be low-quality and must be flagged honestly

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Setup | `runos doctor` (diagnose-only health check; separable from setup) | Deferred to follow-up phase | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | `runos setup --uninstall` reverse path (3-line manual `rm` documented in `docs/SETUP.md`) | Deferred; manual is fine | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Pi / Linux systemd-equivalent of the launchd steps | Deferred until Pi-port milestone | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Auto-detect optimal Whisper model / threshold pace / max HR / resting HR | Deferred (cross-cuts Phase 4) | 2026-05-28 (Phase 14 CONTEXT) |
| Weight | Structured `weight_entry` DB table (markdown layer proves itself first) | Deferred | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | `runos weight add --kg 72.4` CLI (symmetric with `runos journal add`) | Deferred to Layer 2 | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Body composition (body-fat %, lean mass) | Deferred / out of scope | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Withings / Fitbit / Garmin weight auto-import | Deferred (separate phase if ever) | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Standalone `runos analyze weight` trend report | Deferred (recovery section is enough) | 2026-05-28 (Phase 15 CONTEXT) |
| Weight | Goal tracking (target weight + ETA from trend) | Deferred | 2026-05-28 (Phase 15 CONTEXT) |

## Session Continuity

Last session: 2026-05-28T11:15:00.000Z
Stopped at: v1.4 SHIPPED. Phase 15 (Weight Tracker) verified PASS 5/5. New
`runos/analysis/weight.py` (lenient parser + kg/lb normalisation +
7d/28d windows + EWMA alpha=0.1 trend + unit_mixed flag);
`Settings.weight_path`; `RecoveryAssessment` gains `weight` / `weight_present`;
`_render_weight_section` enforces the 3-state degradation rule (absent /
stale >14d / current with Unicode-minus / plus-minus delta and optional
mixed-unit caveat); `runner.generate_recovery` + `generate_all` thread
`weight_path`; CLI passes `settings.weight_path` at both call sites.
`weight.md.example` (14 mixed-unit entries) + `docs/WEIGHT.md` (199 lines) +
README mention. 619 tests green (+26 from Phase 14), ruff clean. All 5
WEIGHT-* requirements satisfied. Next planned: Phase 16 (Nutrition
Tracker, v1.5).

Previous session: 2026-05-28T10:30:00.000Z. Stopped at: v1.3 SHIPPED. Phase 14
(First-Run Setup Wizard) verified PASS 5/5. New `runos setup` command walks 10
locked steps in order (welcome → db → content → strava → garmin → telegram →
scheduler → bot-scheduler → smoke → finish); every credentialed step delegates
in-process to the existing `runos` helper. Atomic `.env` writes at 0600 perms
mirror `runos/connectors/tokens.py`. 593 tests green (+63), ruff clean.

Resume file: None
