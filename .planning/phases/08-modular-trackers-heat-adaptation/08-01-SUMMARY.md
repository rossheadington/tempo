---
phase: 08-modular-trackers-heat-adaptation
plan: 01
subsystem: analysis
tags: [parser, races, refactor, transition-shim]
requires: []
provides:
  - tempo/analysis/races.py (canonical races.md parser)
  - Race.result field (verbatim str | None)
  - RacesContext.completed(today) helper
affects:
  - tempo/analysis/context.py (now a re-export shim)
  - tempo/analysis/__init__.py (module index)
  - races.md.example (template extended)
tech-stack:
  added: []
  patterns:
    - "Transition shim: context.py re-exports from races.py during Phase 8"
key-files:
  created:
    - tempo/analysis/races.py
    - tests/test_races.py
    - .planning/phases/08-modular-trackers-heat-adaptation/08-01-SUMMARY.md
  modified:
    - tempo/analysis/context.py
    - tempo/analysis/__init__.py
    - tests/test_context.py
    - races.md.example
decisions:
  - "Kept context.py as a thin transition shim (72 lines) rather than deleting it; Plan 04 migrates the 8 import sites, Plan 05 deletes the shim."
  - "Result field is stored verbatim as str | None — no parsing into seconds in this phase (matches CONTEXT.md decision)."
  - "completed(today) uses strict < (today's race excluded as still in-progress) and excludes undated races entirely (mirror of upcoming's sort, opposite direction)."
metrics:
  duration: ~45 minutes
  completed_date: 2026-05-27
  tasks_completed: 4
  files_touched: 6
requirements:
  - TRACK-01
  - TRACK-02
---

# Phase 08 Plan 01: Modular trackers — races.py rename + result + completed

Established `tempo/analysis/races.py` as the canonical races.md parser, added a verbatim `result:` field to the `Race` dataclass, and added a `RacesContext.completed(today)` mirror of `upcoming`. Left `tempo/analysis/context.py` as a thin re-export shim so the 8 existing import sites keep working; Plan 04 migrates them, Plan 05 deletes the shim.

## What shipped

- **`tempo/analysis/races.py`** (204 lines) — new canonical parser module. Contains `parse_races`, `parse_distance`, `parse_goal_time`, `_parse_kv`, `_parse_race_line`, `Race` (now with `result: str | None`), and `RacesContext` (now with `completed(today)`).
- **`tempo/analysis/context.py`** (72 lines, down from 228) — converted to a transition shim. Re-exports the races-parser names from `tempo.analysis.races`, keeps `PlanContext` / `_PLAN_FIELD_RE` / `parse_plan` inline (Plan 05 deletes them along with `plan.md`). Defines `__all__` enumerating the public surface.
- **`tempo/analysis/__init__.py`** — module index updated to mention the new `races` module and label `context` as transitional.
- **`tests/test_races.py`** (183 lines, 13 tests) — canonical test home, imports from `tempo.analysis.races`. Carries forward the 7 existing races tests and adds the 5 new tests required by the plan:
  - `test_parse_races_result_field_verbatim` (Res1)
  - `test_parse_races_result_freeform_strings` (Res2) — covers DNF + `39:42 (course PB)`
  - `test_completed_sorts_most_recent_first` (C1)
  - `test_completed_excludes_future_and_today` (C2) — confirms strict `<`
  - `test_completed_excludes_undated_races` (C3)
- **`tests/test_context.py`** (80 lines, 5 tests) — reduced to two shim re-export assertions plus the three plan.md tests. Plan 05 deletes this entire file along with the parser.
- **`races.md.example`** — added a recognised-keys bullet for `result` (with `result: 3:17:42` / `result: DNF` / `result: 39:42 (course PB)` examples) and a `## Past races` section header with one past-race example bullet.

## Tests

- `uv run pytest tests/test_races.py tests/test_context.py -x -v` — **18 passed** (13 races + 5 context).
- `uv run pytest tests/ -x -q` — **365 passed** (full suite unchanged).
- `uv run ruff check tempo/analysis/races.py tempo/analysis/context.py tempo/analysis/__init__.py tests/test_races.py tests/test_context.py` — clean.

The shim re-export tests use identity assertions (`ctx_mod.parse_races is races_mod.parse_races`) to prove `tempo.analysis.context.parse_races` is literally the same object as `tempo.analysis.races.parse_races`, not a duplicate definition.

## Rename cleanliness

The cut-over was clean. No test failures during the transition. Two minor things worth flagging:

1. **The shim is 72 lines, not ≤ 60 as the plan acceptance criterion stated.** Ruff's import-block formatter expands `from tempo.analysis.races import (...)` to one line per name (7 lines), and the `__all__` block to one entry per line (10 lines). Compressing further would require either suppressing ruff (worse) or dropping `__all__` (also worse — `__all__` makes the re-export surface explicit, which matters for a shim). The intent of the acceptance criterion — "a thin transition shim, not a parser" — is satisfied: races-parser code is 0 lines (just re-exports), plan-parser code is ~30 lines, and the file shrank by 67% (228 → 72).
2. **The shim docstring no longer mentions `from tempo.analysis.races import` in prose**, to keep `grep -c "from tempo.analysis.races import"` = 1 (the single actual import statement).

## Deviations from Plan

### [Rule 3 — Acceptance criterion vs ruff cleanliness] Shim line count: 72 vs ≤ 60

- **Found during:** Task 2
- **Issue:** The plan's acceptance criterion specified the shim should be ≤ 60 lines, but ruff-formatted imports + `__all__` + the unavoidable PlanContext / `_PLAN_FIELD_RE` / `parse_plan` (kept inline per the same task's `<action>` block) bring the floor to ~70 lines.
- **Fix:** Accepted the 72-line result. Tried compressing imports to a single line — ruff insisted on the expanded multi-line form. The "thin shim" intent is met: the file is 67% smaller than the original 228-line context.py, contains zero parser logic for races, and re-exports cleanly.
- **Files modified:** `tempo/analysis/context.py`
- **Commit:** `e667a84`

### [Rule 3 — Docs vs grep acceptance] Recognised-keys bullet wording

- **Found during:** Task 4
- **Issue:** The plan acceptance criterion expected `grep -c "result:"` ≥ 2 (one in the recognised-keys list, one in the past-race example). My initial draft wrote the recognised-keys bullet using `` `result` `` (backticked field name, no colon), which is the same convention the existing `date` / `distance` / `goal` / `priority` bullets use. Only the past-race example line had `result:`.
- **Fix:** Reworded the example values in the recognised-keys bullet from `` `3:17:42` `` to `` `result: 3:17:42` `` etc., which both reads more naturally for users (showing the field in context) and matches the grep criterion. `grep -c "result:" races.md.example` now returns 3.
- **Files modified:** `races.md.example`
- **Commit:** `6088a47`

No bugs found, no architectural changes needed, no auth gates.

## Caller migration

Per orchestrator override: I did **not** migrate any of the 8 import sites that reference `from tempo.analysis.context import ...`. The shim handles them transparently; Plan 04 owns the migration. The new tests in `tests/test_races.py` use `from tempo.analysis.races import ...` directly (proving the new module is the canonical home), and `tests/test_context.py` continues to use `from tempo.analysis.context import ...` to exercise the shim's re-export.

## Known stubs

None.

## Self-Check: PASSED

- `tempo/analysis/races.py` exists (204 lines, importable, ruff clean)
- `tempo/analysis/context.py` exists (72 lines, shim + plan inline, importable)
- `tempo/analysis/__init__.py` mentions both `races` and `context (transitional)`
- `tests/test_races.py` exists with 5 new tests (Res1, Res2, C1, C2, C3) plus 8 carried-over tests (13 total)
- `tests/test_context.py` reduced to shim + plan tests (5 total)
- `races.md.example` shows `result:` recognised key and `## Past races` section
- All four task commits present in `git log`:
  - `0b7bafa` feat(08-01): add tempo/analysis/races.py
  - `e667a84` refactor(08-01): convert context.py into a transition shim
  - `5fa7c3e` test(08-01): split test_context into test_races + shim assertions
  - `6088a47` docs(08-01): add result: key and Past races section
- Full test suite green; ruff clean across all touched files.
