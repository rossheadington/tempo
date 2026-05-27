---
phase: 08-modular-trackers-heat-adaptation
plan: 05
subsystem: analysis-trackers
tags: [retirement, plan.md, refactor, requirements-pin]
requires:
  - 08-01 (races.py canonical home + context shim)
  - 08-02 (heat tracker infrastructure)
  - 08-03 (race-to-activity auto-link)
  - 08-04 (heat + race-link wired through runner/CLI/report)
provides:
  - "tempo.analysis.context module deleted; tempo.analysis.races is now the sole home for race parsing"
  - "Settings.plan_path attribute removed; pinned by test_no_plan_path_attribute_on_settings"
  - "render_race_readiness no longer accepts plan_ctx; report no longer renders a '## Plan context' section"
  - "plan.md.example deleted; .env.example + README updated to the races + heat landscape"
affects:
  - tempo/analysis/runner.py (plan_path param + parse_plan call removed from generate_race_readiness + generate_all)
  - tempo/analysis/report.py (plan_ctx param + plan-rendering block removed from render_race_readiness)
  - tempo/analysis/race_link.py (docstring class ref now points at tempo.analysis.races)
  - tempo/cli.py (two plan_path kwarg sites removed from analyze + analyze race-readiness)
  - tempo/sync/daily.py (one plan_path kwarg site removed from run_daily)
  - tempo/config.py (Settings.plan_path property removed)
  - tempo/analysis/__init__.py (module docstring scrubbed of plan.md / context shim mentions)
  - tests/test_config.py (defensive test_no_plan_path_attribute_on_settings added)
  - tests/test_analysis_reports.py (_write_context now returns races only; plan_path/plan_ctx scrubbed from all sites)
  - tests/test_analyze_cli.py (seeded_cli no longer writes plan_path; **phase**: Base assertion removed)
  - tests/test_noteworthy.py + tests/test_race_link.py (imports rewired to tempo.analysis.races)
tech-stack:
  added: []
  patterns:
    - "Defensive hasattr() retirement pin (RESEARCH.md section 6 P1) — locks plan_path removal at the type level."
    - "Atomic rename + retirement: shim (Plan 08-01) + import sweep (Task 1) + signature scrub (Task 4) + file deletion (Task 2) + doc scrub (Task 5)."
key-files:
  created:
    - tests/test_config.py::test_no_plan_path_attribute_on_settings
  modified:
    - tempo/analysis/runner.py
    - tempo/analysis/report.py
    - tempo/analysis/race_link.py
    - tempo/analysis/__init__.py
    - tempo/cli.py
    - tempo/sync/daily.py
    - tempo/config.py
    - tests/test_config.py
    - tests/test_analysis_reports.py
    - tests/test_analyze_cli.py
    - tests/test_noteworthy.py
    - tests/test_race_link.py
    - .env.example
    - README.md
  deleted:
    - plan.md.example
    - tempo/analysis/context.py
    - tests/test_context.py
decisions:
  - "Reordered execution: ran Task 4 before Task 2 because Task 2's deletion of context.py would have broken the still-live `from tempo.analysis.context import PlanContext, RacesContext` reference inside tests/test_analysis_reports.py::_render_with_links. Task 4 removed plan_ctx from render_race_readiness (and from that helper) first, then Task 2 deleted the shim. Final state is identical to the plan's stated end state; only the intermediate order changed."
  - "Scope expansion (Rule 3 — blocking issue): two additional `from tempo.analysis.context import Race` import sites surfaced beyond RESEARCH.md section 7's enumerated 8 — tempo/analysis/race_link.py:35 and tests/test_race_link.py:21. Migrated both in Task 1's commit."
  - "Scope expansion (Rule 1 — bug): tests/test_analyze_cli.py::test_analyze_race_readiness_subcommand asserted `**phase**: Base` in the rendered report. With the '## Plan context' section gone, this assertion no longer holds; dropped it as part of Task 4."
  - "Defensive retirement test name `test_no_plan_path_attribute_on_settings` keeps the string `plan_path` alive in the codebase by design (RESEARCH.md section 6 P1) — this is the canonical sentinel that proves the retirement is in force."
  - "Marked TRACK-01, TRACK-02, TRACK-04 complete in REQUIREMENTS.md (and TRACK-06 per this plan). Verified TRACK-01 (Race.result field at races.py:46), TRACK-02 (RacesContext.completed at races.py:66), TRACK-04 (tempo/analysis/heat.py + heat.md.example) are all satisfied by earlier Phase 8 plans but were not previously checked off."
metrics:
  duration: "9 min"
  completed: "2026-05-27"
---

# Phase 8 Plan 05: plan.md retirement + analysis.context deletion — Summary

## One-liner

`plan.md` is fully retired — the parser, `PlanContext`, `parse_plan`, `_PLAN_FIELD_RE`, `Settings.plan_path`, `plan.md.example`, the `render_race_readiness(plan_ctx=...)` parameter, the `## Plan context` report section, and every README / `.env.example` / docstring mention are gone; `tempo.analysis.races` is now the canonical home for race parsing with the transition shim from Plan 08-01 deleted; full test suite (398 tests) and ruff stay green.

## What changed

- **Import sweep (Task 1)** — 10 import sites total were rewired from `tempo.analysis.context` to `tempo.analysis.races`: the 8 enumerated in RESEARCH.md section 7 (`tempo/analysis/runner.py:23,29`, `tempo/analysis/report.py:20`, `tempo/sync/daily.py:34`, `tempo/analysis/__init__.py:16`, `tests/test_noteworthy.py:220,233`, and the test_context.py site which became moot when the file was deleted) plus 2 that PATTERNS.md and RESEARCH.md section 7 missed (`tempo/analysis/race_link.py:35`, `tests/test_race_link.py:21`). All migrated to `from tempo.analysis.races import Race`/`RacesContext`/`as ctx` patterns.

- **Config layer (Task 3)** — `Settings.plan_path` property deleted. Two `settings.plan_path == ...` assertions removed from `test_content_dir_defaults_to_data_dir` and `test_content_dir_redirects_content_only`. Added `test_no_plan_path_attribute_on_settings` (P1 retirement pin from RESEARCH.md section 6) — `assert not hasattr(Settings(_env_file=None), 'plan_path')`.

- **Caller scrub (Task 4)** — Removed `plan_path: Path` keyword param from `runner.generate_race_readiness` + `runner.generate_all`; removed the `plan_ctx = ctx.parse_plan(plan_path)` body line; removed `plan_ctx=plan_ctx` kwarg from the inner `render_race_readiness` call. In `report.render_race_readiness`, dropped the `plan_ctx: PlanContext` parameter and the entire `if plan_ctx.present and plan_ctx.fields: ...` block (the `## Plan context` section). In `cli.py`, dropped `plan_path=settings.plan_path,` from both `analyze` and `analyze race-readiness` invocations. In `sync/daily.py`, dropped `plan_path=settings.plan_path,` from `run_daily`'s `generate_all` call.

  Test scrub: `tests/test_analysis_reports.py`'s `_write_context` helper now returns just `races` (was `(races, plan)`); 5 separate test sites had `plan_path=` kwargs removed; the `## Plan context` assertion (`**phase**: Base`) removed from `test_race_readiness_report_written_with_predictions`; `_render_with_links` no longer constructs or passes a `PlanContext`. `tests/test_analyze_cli.py`'s `seeded_cli` fixture no longer writes `settings.plan_path`; the `**phase**: Base` assertion in `test_analyze_race_readiness_subcommand` was dropped.

- **Shim deletion (Task 2, reordered to run AFTER Task 4)** — `tempo/analysis/context.py` (72 lines: `PlanContext`, `parse_plan`, `_PLAN_FIELD_RE`, the races-name re-exports) deleted. `tests/test_context.py` (80 lines: shim-assertion tests + plan-parser tests) deleted. Lingering docstring class reference `:class:~tempo.analysis.context.Race` in `race_link.py` rewritten to point at `:class:~tempo.analysis.races.Race`.

- **Docs + content scrub (Task 5)** — `plan.md.example` deleted from repo root. `.env.example` "Content location" header reworded ("plan, races, reports" → "races, heat, reports"; "plan.md, races.md" → "races.md, heat.md") and the trailing Race-readiness comment block rewritten as a generic "Tracker files" block listing `races.md` + `heat.md`. `README.md` "Plan & race context" block rewritten to "Tracker files" listing the same. `tempo/analysis/__init__.py` module docstring scrubbed of the plan.md / context-shim bullet (the heat bullet from Plan 08-02 was preserved). `tempo/analysis/runner.py` module docstring updated to reference `races`/`heat` parsers instead of the deleted `context` module. `CLAUDE.md` was deliberately left untouched per 08-CONTEXT.md (out of scope).

- **REQUIREMENTS.md** — Marked TRACK-01, TRACK-02, TRACK-04, TRACK-06 complete (TRACK-03 + TRACK-05 already checked by 08-03 and 08-02 respectively).

## File deletions confirmed

```
$ ls plan.md.example tempo/analysis/context.py tests/test_context.py
ls: plan.md.example: No such file or directory
ls: tempo/analysis/context.py: No such file or directory
ls: tests/test_context.py: No such file or directory
```

## Final retirement sweep

```
$ grep -rn "plan_path\|PlanContext\|parse_plan\|_PLAN_FIELD_RE\|TEMPO_PLAN_PATH\|tempo.analysis.context" \
    tempo/ tests/ .env.example README.md
tests/test_config.py:55:def test_no_plan_path_attribute_on_settings() -> None:
tests/test_config.py:58:    assert not hasattr(settings, "plan_path")
```

The only remaining live references are the defensive retirement pin (P1 from RESEARCH.md section 6), which exists specifically to detect any accidental resurrection. Everything else — the property, the parser, the dataclass, the regex, the env-var, the shim module, the example file, the report section, the docs — is gone.

## Metrics

- **Plan duration:** ~9 minutes (executor wall-clock from spawn to SUMMARY commit)
- **Tasks:** 5 / 5 complete (executed in order 1 → 3 → 4 → 2 → 5; see deviations)
- **Files deleted:** 3 (`plan.md.example`, `tempo/analysis/context.py`, `tests/test_context.py`)
- **Files modified:** 14 (5 in tempo/, 6 in tests/, 2 docs, 1 config)
- **Lines net change across the 5 commits of 08-05:** **+42 / −253** = net **−211 lines** removed
- **Import sites migrated:** 10 (8 from RESEARCH.md + 2 missed sites surfaced during execution)
- **Test count:** **398** (was 402 at start of 08-05; net −4 — test_context.py's 6 tests deleted, 1 defensive test added, plus a handful of plan-related assertions dropped from existing tests). All 398 passing.
- **Ruff:** Clean (`ruff check` + `ruff format --check`)

## Self-Check: PASSED

- File deletions: `plan.md.example`, `tempo/analysis/context.py`, `tests/test_context.py` all confirmed absent.
- All 5 commits in `git log` (`7ffbdd6`, `219cda4`, `5ec6e59`, `f129f57`, `e92d70c`).
- Final-state grep returns only the 2 lines of the defensive retirement pin.
- `uv run pytest tests/` → 398 passed.
- `uv run ruff check tempo/ tests/` + `ruff format --check` → clean.
- `uv run python -c "from tempo.analysis import races, heat, race_link, recovery, runner, report, data, load, fitness, race; print('all imports work')"` → exits 0.

## Deviations from Plan

### Reordered task execution: 1 → 3 → 4 → 2 → 5 (instead of plan's 1 → 2 → 3 → 4 → 5)

**Why:** Task 2's deletion of `tempo/analysis/context.py` would have broken `tests/test_analysis_reports.py::_render_with_links`, which still imported `from tempo.analysis.context import PlanContext, RacesContext` at the time. Task 4 was the one that removed `plan_ctx` from `render_race_readiness` (and from that helper), so it had to run BEFORE Task 2. Running Task 3 (config) first was order-independent and let me commit progress.

**Outcome:** End state matches the plan's success criteria exactly. The intermediate ordering doesn't change any output artifact.

### Auto-fixed Issues

**1. [Rule 3 — blocking issue] Two additional `from tempo.analysis.context import Race` import sites beyond RESEARCH.md section 7**
- **Found during:** Task 1 (final grep verification surfaced them)
- **Issue:** `tempo/analysis/race_link.py:35` and `tests/test_race_link.py:21` both imported `Race` from the shim. They were not in RESEARCH.md section 7's enumerated list of 8.
- **Fix:** Migrated both to `from tempo.analysis.races import Race`.
- **Commit:** `7ffbdd6` (Task 1)

**2. [Rule 1 — bug] Stale `**phase**: Base` assertion in `tests/test_analyze_cli.py::test_analyze_race_readiness_subcommand`**
- **Found during:** Task 4's full-suite run
- **Issue:** After dropping the `## Plan context` section from `render_race_readiness`, this assertion failed because the rendered report no longer contains plan-field key/value pairs.
- **Fix:** Removed the assertion. (Plan listed only the seeded_cli fixture's `plan_path.write_text(...)` as a Task 5 site — missed this assertion.)
- **Commit:** `5ec6e59` (Task 4)

**3. [Rule 3 — blocking issue] Lingering `:class:~tempo.analysis.context.Race` docstring reference in `race_link.py:5` after Task 2's deletion of context.py**
- **Found during:** Task 2's post-delete grep
- **Issue:** The module docstring still referred to the deleted `tempo.analysis.context.Race` class.
- **Fix:** Rewrote as `:class:~tempo.analysis.races.Race`.
- **Commit:** `f129f57` (Task 2)

**4. [Rule 2 — missing required scope]: TRACK-01, TRACK-02, TRACK-04 were complete on disk but unchecked in REQUIREMENTS.md**
- **Found during:** Final state-update step
- **Issue:** The plan's `requirements: [TRACK-06]` only listed TRACK-06, but the verification context said to mark TRACK-01, TRACK-02, TRACK-04, TRACK-06. TRACK-01 (Race.result field), TRACK-02 (RacesContext.completed helper), TRACK-04 (heat.md infrastructure) were all delivered by earlier Phase 8 plans (08-01, 08-02, 08-04) but the REQUIREMENTS.md checkboxes were never updated.
- **Fix:** Marked all four ([x]).
- **Commit:** included in final metadata commit

### Auth gates / blockers
- None encountered.

## Threat surface scan

This plan is pure deletion and rename refactoring — no new endpoints, no new auth surface, no new file access patterns, no schema changes. The deleted code (`parse_plan`, `PlanContext`, `_PLAN_FIELD_RE`) was a stdlib regex over a user-controlled local markdown file inside `~/.tempo/` (no network surface). Net threat surface change: reduced (one fewer file-read code path).

No threat flags.

## Known Stubs

None. The plan's purpose was to remove stubs (the plan_path infrastructure that no longer fed any output).

## Retirement surprises (callers that surfaced during execution that weren't in RESEARCH.md section 4)

1. **tempo/analysis/race_link.py:35** — `from tempo.analysis.context import Race`. Real production code; not enumerated in RESEARCH.md section 7.
2. **tests/test_race_link.py:21** — `from tempo.analysis.context import Race`. Test code; not enumerated.
3. **tempo/analysis/race_link.py:5** — docstring class reference `:class:~tempo.analysis.context.Race`. Only surfaces after the file is deleted (grep then matches the docstring text).
4. **tempo/analysis/runner.py:1-5** — module docstring referenced `:mod:context`. Updated in Task 4 to point at `races`/`heat`.
5. **tests/test_analyze_cli.py:106** — `assert "**phase**: Base" in text`. Test-level surprise; assertion no longer holds after the `## Plan context` section is gone.

These are typical "PATTERNS.md / RESEARCH.md missed it" findings for a refactor of this scope. The full-suite test runs after Tasks 4 and 5 caught them all.
