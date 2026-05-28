---
phase: 08
status: passed
verified_at: 2026-05-27
---

# Phase 8 Verification

## Verdict
**passed**: every TRACK-01..06 requirement is shipped with code + tests; the `plan.md` retirement sweep is clean; suite is 398/398 green and ruff is clean.

## Goal-backward check

- **G1 ✓** — `races.md` is one file holding both upcoming and past races. `races.md.example` has a `## Past races` section purely for human readability (`races.md.example:31-33`); the parser ignores section headers (`runos/analysis/races.py:198`). No schema split; `parse_races` produces one flat list of `Race` regardless of section.
- **G2 ✓** — `Race` dataclass carries `result: str | None` (`runos/analysis/races.py:46`) and the parser stores it verbatim from the `result:` key (`races.py:176, 186`). Rendered verbatim in race-readiness via `_link_line` (`runos/analysis/report.py:209-210`).
- **G3 ✓** — `RacesContext.completed(today)` returns races strictly before `today`, undated excluded, **most recent first** via `reverse=True` (`runos/analysis/races.py:66-77`). Mirrors `upcoming()` (lines 57-64). Test coverage in `tests/test_races.py`.
- **G4 ✓** — `link_races_to_activities(races, conn)` (`runos/analysis/race_link.py:47-92`) returns a parallel list of `RaceLink` with the four honest statuses: `linked` (1 match), `unlinked_no_match` (0), `unlinked_ambiguous` (>1), `unlinked_no_date` (race has no date). Defensive missing-`activity`-table branch returns `unlinked_no_match` for every dated race. Tested in `tests/test_race_link.py` (9 tests).
- **G5 ✓** — Race-readiness renderer threads `race_links` and emits per-race lines: `**Result**: <verbatim> (activity id: N)` when linked + result present, "Activity recorded on race day" when linked without result, "_No activity recorded for race date._" for `unlinked_no_match`, "_Multiple activities on race day; cannot auto-link._" for `unlinked_ambiguous`, nothing for `unlinked_no_date` (`runos/analysis/report.py:189-217, 304-309`). Wired in `runner.generate_race_readiness` (`runos/analysis/runner.py:274-289`).
- **G6 ✓** — `parse_heat(path)` returns `HeatContext(present=False)` when file missing (`runos/analysis/heat.py:174-176`). Unrecognised keys ignored via the `_RECOGNISED_KEYS` gate (`heat.py:94, 122`); malformed lines silently skipped (`heat.py:183-186`). Undated entries dropped to keep rollups consistent (`heat.py:138-140`). 13 tests in `tests/test_heat.py`.
- **G7 ✓** — Recovery renderer's `_render_heat_section` implements the A4 three-state rule (`runos/analysis/recovery.py:458-500`): omit entirely when `heat_present=False` or `heat is None` or `last_session_date is None` (present-but-empty); full rollup line (`last 7 days: N sessions / M min · ...`) when any of 7/14/28-day windows is non-zero; single-line lapsed nudge `_Heat protocol lapsed -- last session N days ago. (No sessions in the last 28 days.)_` when sessions exist in history but all windows are zero. Wired in `runner.generate_recovery` via `heat_path` (`runner.py:294-324`).
- **G8 ✓** — `plan.md` fully retired. Retirement sweep grep over `runos/`, `tests/`, `.env.example`, `README.md` returns only ONE hit: `tests/test_config.py:55 test_no_plan_path_attribute_on_settings` — the defensive pin verifying `settings` has no `plan_path`. `runos/analysis/context.py`, `tests/test_context.py`, `plan.md.example` all confirmed gone (`ls` returns "No such file or directory"). `runos/config.py` exposes `races_path` and `heat_path` only (lines 142, 147); `plan_path` absent. `runner.py` no longer loads plan context (only races + race_link + heat). `report.py` has no "Current focus" block.
- **G9 ✓** — Each tracker degrades gracefully: missing `races.md` -> `RacesContext(present=False)` (`races.py:192-193`); missing `heat.md` -> `HeatContext(present=False)` (`heat.py:174-176`); race-link with empty `races` list short-circuits (`race_link.py:66-67`); race-link against pre-Phase-3 DB without `activity` table treats day-map as empty (`race_link.py:69-71`); recovery report omits heat section when not present or empty (`recovery.py:474-478`); race-readiness renders cleanly when `races_ctx.present=False` (`report.py:241-249`).
- **G10 ✓** — `uv run pytest tests/ -x` -> **398 passed in 1.51s**. `uv run ruff check runos/ tests/` -> **All checks passed!**.

## Requirement coverage

- **TRACK-01 ✓** — `Race.result: str | None` + parser stores verbatim (`races.py:46, 176`). Example file shows both past and upcoming races in one file (`races.md.example:27-33`).
- **TRACK-02 ✓** — `RacesContext.completed(today)` exists, mirrors `upcoming`, most-recent-first (`races.py:66-77`); covered in `tests/test_races.py`.
- **TRACK-03 ✓** — `link_races_to_activities` handles 0/1/N honestly with four explicit statuses (`race_link.py:47-92`); 9 tests in `tests/test_race_link.py`.
- **TRACK-04 ✓** — `heat.md` parser with lenient behavior, `HeatSession` dataclass with optional fields, full test suite (`heat.py:33-186`; `tests/test_heat.py` 13 tests). Example template at `heat.md.example` shows the format with 6 sample sessions.
- **TRACK-05 ✓** — `heat_rollup` produces `HeatRollup` with last_7d/14d/28d counts + minutes + last_session_date + last_session_days_ago (`heat.py:190-245`). Surfaced in recovery report (`recovery.py:458-500`). End-to-end render covered in `tests/test_recovery.py`.
- **TRACK-06 ✓** — `plan.md` retired entirely: zero references in production code/docs except the defensive retirement pin in `tests/test_config.py:55`. `context.py`, `test_context.py`, `plan.md.example` deleted. Race-readiness report renders without plan content (no "Current focus" block in `report.py`).

## Build/test evidence

- **pytest**: `uv run pytest tests/ -x` -> `398 passed in 1.51s`. Targeted trackers run (`test_heat.py + test_race_link.py + test_races.py + test_recovery.py`) -> `57 passed in 0.48s`.
- **ruff**: `uv run ruff check runos/ tests/` -> `All checks passed!`
- **imports**: `uv run python -c "from runos.analysis import races, heat, race_link, recovery, runner, report, data, load, fitness, race; print('OK')"` -> `OK`
- **retirement sweep grep**: `grep -rn "plan_path|PlanContext|parse_plan|_PLAN_FIELD_RE|RUNOS_PLAN_PATH|runos.analysis.context" runos/ tests/ .env.example README.md` -> only `tests/test_config.py:55 test_no_plan_path_attribute_on_settings` + line 58 assertion. Zero hits in production code or docs.
- **deletions confirmed**: `ls runos/analysis/context.py tests/test_context.py plan.md.example` -> "No such file or directory" for all three.
- **example files**: `races.md.example` + `heat.md.example` present at repo root; `plan.md.example` gone.

## Issues found

None. One cosmetic note (not a defect): `runos/config.py:119` docstring on `content_root` still reads "plan, races, reports". CONTEXT.md explicitly permits stale phrasing in docstrings as long as it doesn't cause confusion. Leaving it is the locked decision — not a gap.

## Recommendation

**Proceed to ship.** Phase 8 is feature-complete, tests are green, lint is clean, the `plan.md` retirement is total, and every TRACK requirement has both code and test evidence. The single defensive retirement pin in `test_config.py` is intentional and prevents regression. No follow-up plans required.

## VERIFICATION COMPLETE
