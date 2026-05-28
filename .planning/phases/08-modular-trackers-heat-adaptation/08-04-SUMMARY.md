---
phase: 08-modular-trackers-heat-adaptation
plan: 04
subsystem: analysis
tags:
  - heat-adaptation
  - race-link
  - recovery-report
  - race-readiness-report
  - wave-2-integration
requires:
  - runos.analysis.heat (Plan 08-02)
  - runos.analysis.races (Plan 08-01)
  - runos.analysis.race_link (Plan 08-03)
provides:
  - "recovery.RecoveryAssessment.heat / heat_present fields"
  - "recovery._render_heat_section with A4 3-state degradation"
  - "recovery.assess_recovery_from_db(..., heat_path=...)"
  - "report.render_race_readiness(..., race_links=...)"
  - "runner.generate_recovery(..., heat_path=...)"
  - "runner.generate_all(..., heat_path=...)"
  - "runner.generate_race_readiness now calls link_races_to_activities"
affects:
  - runos/cli.py (analyze + analyze recovery pass settings.heat_path)
  - runos/sync/daily.py (run_daily passes settings.heat_path to generate_all)
tech-stack:
  added: []
  patterns:
    - "frozen dataclass with optional fields for back-compat extension"
    - "renderer-side state machine: 3-way degradation (absent / lapsed / live)"
    - "identity-match between parallel race[] and race_links[]"
key-files:
  created: []
  modified:
    - runos/analysis/recovery.py  # +heat field + parser-thread + _render_heat_section
    - runos/analysis/report.py    # +_link_line + race_links param + per-race wiring
    - runos/analysis/runner.py    # heat_path param + link_races_to_activities call
    - runos/cli.py                # settings.heat_path threaded to two analyze sites
    - runos/sync/daily.py         # settings.heat_path threaded to generate_all
    - tests/test_recovery.py      # +5 heat-section tests
    - tests/test_analysis_reports.py  # +4 link-line tests +3 end-to-end tests
    - tests/test_analyze_cli.py   # seeded_cli fixture writes heat.md +1 assertion
decisions:
  - "Heat parsing lives in recovery.assess_recovery_from_db, not runner.generate_recovery (delegation keeps runner thin)"
  - "Heat windows anchor to the latest fitness-point day, not date.today(), so counts align with the verdict's 'as of' day"
  - "render_race_readiness identity-matches race_links by 'lk.race is race' (parallel-list contract)"
  - "_link_line returns None for unlinked_no_date (heading already shows the missing date)"
metrics:
  duration_minutes: 30
  test_count_before: 390
  test_count_after: 402
  test_count_delta: 12
  completed_date: 2026-05-27
---

# Phase 8 Plan 04: Wire heat + race-link integrations Summary

Wave-2 integration plan: the three Wave-1 capabilities (races.result + completed,
heat.md parser+rollup, race_link auto-link) are now visible to the user in the
`runos analyze recovery` and `runos analyze race-readiness` reports, with the
CLI and daily-scheduler callers updated to pass `settings.heat_path` through.

## Tasks completed

| Task | Name                                                         | Commit  | Tests added |
| ---- | ------------------------------------------------------------ | ------- | ----------- |
| 1    | recovery.py: HeatRollup field + _render_heat_section (A4)    | b91ba31 | 5           |
| 2    | report.py: per-race auto-link surfacing (4 phrasings)        | 162ff09 | 4           |
| 3    | runner / CLI / daily wiring + end-to-end tests               | e266d13 | 3           |

Total: 12 new tests; suite at **402 tests, all green** (was 390).

## A4 override: 3-state heat degradation -- proof points

The orchestrator override on RESEARCH.md A4 (silent-default-on-stale becomes a
one-line lapsed-nudge instead) is pinned by these tests:

| State                                | Test                                                        |
| ------------------------------------ | ----------------------------------------------------------- |
| heat.md absent / not threaded        | `test_render_recovery_omits_heat_when_no_heat_file`         |
| heat.md present but no sessions      | `test_render_recovery_omits_heat_when_present_but_empty`    |
| recent sessions in 7/14/28-day window | `test_render_recovery_renders_heat_when_recent_sessions`    |
| sessions exist but all >28 days old  | `test_render_recovery_renders_heat_lapsed_nudge` (A4 nudge) |
| session today -> "last session: today" | `test_render_recovery_heat_today_phrase`                   |

Plus end-to-end coverage:
- `test_recovery_renders_heat_section_end_to_end` proves a seeded heat.md flows
  through `generate_recovery` and surfaces `## Heat adaptation` in the written
  markdown file.
- `test_recovery_omits_heat_section_when_no_heat_file` proves a missing heat.md
  does not crash and the section is absent.
- `test_analyze_recovery_subcommand` (in `test_analyze_cli.py`) seeds heat.md
  in the CLI's data dir and asserts `## Heat adaptation` is in the CLI-rendered
  report -- end-to-end through the actual `runos analyze recovery` invocation.

## Race-link surfacing (TRACK-03) -- 4 phrasings pinned

The renderer's `_link_line` helper maps the 4 RaceLink.link_status values to
4 line phrasings; each is pinned by a unit test in `test_analysis_reports.py`:

| link_status         | Rendered line                                                  | Test                                                          |
| ------------------- | -------------------------------------------------------------- | ------------------------------------------------------------- |
| `linked` + result   | `- **Result**: 1:31:48 (activity id: 987654321)`               | `test_race_readiness_renders_result_when_linked`              |
| `linked` no result  | `- Activity recorded on race day (id: 9999).`                  | `test_race_readiness_links_activities_end_to_end` (E2E)       |
| `unlinked_no_match` | `- _No activity recorded for race date._`                      | `test_race_readiness_renders_no_activity_when_unlinked_no_match` |
| `unlinked_ambiguous`| `- _Multiple activities on race day; cannot auto-link._`        | `test_race_readiness_renders_ambiguous_when_multiple`         |
| `unlinked_no_date`  | (no line emitted)                                              | `test_race_readiness_renders_nothing_when_unlinked_no_date`   |

## Public signature changes (for downstream callers / Plan 08-05)

| Function                                 | Change                                                              |
| ---------------------------------------- | ------------------------------------------------------------------- |
| `recovery.RecoveryAssessment`            | +`heat: HeatRollup | None = None`, +`heat_present: bool = False`    |
| `recovery.assess_recovery_from_db`       | +optional `heat_path: Path | None = None` keyword                   |
| `report.render_race_readiness`           | +optional `race_links: list[RaceLink] | None = None` keyword        |
| `runner.generate_recovery`               | +required `heat_path: Path` keyword                                 |
| `runner.generate_all`                    | +required `heat_path: Path` keyword                                 |
| `runner.generate_race_readiness`         | (no signature change; now internally calls `link_races_to_activities`) |

`plan_path` and `plan_ctx` are left intact in all signatures. Plan 08-05
(Wave 3) owns their retirement.

## Deviations from plan

### [Rule design choice] Heat parsing centralised in recovery.assess_recovery_from_db

The plan's action sketch had `runner.generate_recovery` directly call `parse_heat`
+ `heat_rollup` and pass the rollup down to the renderer. I instead extended
`recovery.assess_recovery_from_db` to accept `heat_path` and own the parse +
rollup; the runner just passes the path through.

**Rationale:** keeps the runner a thin orchestrator (consistent with how it
delegates baseline reads to `baselines.latest_baselines`), and lets the heat
windows be anchored to the same "as of" day the recovery verdict uses (the
latest fitness-point day, not the wall clock). The runner has no business
constructing dates; the analysis layer does.

**Consequence vs the plan's acceptance grep** (`from runos.analysis.heat import`
in `runner.py`): the strict grep returns 0 instead of >=1, because the runner
now relies on the heat module transitively through `recovery_mod`. Functionally
equivalent (heat IS used by the runner pipeline), so this is a deviation in
*where* the integration lives, not whether it exists. All other Task-3
acceptance grep results pass (`heat_path: Path` in runner = 2;
`link_races_to_activities` in runner = 2; `heat_path=settings.heat_path` in
cli = 2; same in daily = 1).

### [Rule 3 - blocking] Wave-1 deliverables not on this worktree branch at start

The execution context stated Wave 1 was already on main, but the worktree
branch was forked from a Phase-7 commit and the 08-01/02/03 work lived on
main but not on this branch. Fast-forwarded the worktree from main (clean
merge, no conflicts) before starting Task 1. Documented here for the verifier.

## Self-Check

- All 3 task commits exist: b91ba31, 162ff09, e266d13 -- verified via `git log`.
- Files claimed modified all exist + diff-statted in their commit.
- 402 tests pass via `uv run pytest tests/ -x` (+12 from 390 baseline).
- `uv run ruff check runos/ tests/` clean.
- `uv run ruff format --check runos/ tests/` clean.
- Smoke-tested actual rendered output for both the full-rollup and lapsed-nudge
  heat sections via inline `uv run python -c ...` against a seeded throwaway DB.

## Self-Check: PASSED
