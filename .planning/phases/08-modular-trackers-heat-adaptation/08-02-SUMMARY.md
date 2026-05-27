---
phase: 08-modular-trackers-heat-adaptation
plan: 02
subsystem: analysis/heat
tags: [tracker, heat-adaptation, parser, rollup, recovery, TRACK-04, TRACK-05]
requires: [tempo/analysis/context.py (pattern source), tempo/config.py.content_root]
provides:
  - tempo.analysis.heat.parse_heat
  - tempo.analysis.heat.heat_rollup
  - tempo.analysis.heat.HeatSession
  - tempo.analysis.heat.HeatContext
  - tempo.analysis.heat.HeatRollup
  - Settings.heat_path
affects: [Plan 08-04 will import HeatRollup into recovery.py]
tech_stack_added: []
patterns: [lenient-parser, frozen+slots-dataclass, closed-interval-rollup, derived-path-property]
key_files_created:
  - tempo/analysis/heat.py
  - tests/test_heat.py
  - heat.md.example
key_files_modified:
  - tempo/config.py
  - tests/test_config.py
  - tempo/analysis/__init__.py
key_decisions:
  - "Copy _parse_kv inline into heat.py rather than import from context.py -- keeps heat.py standalone and survives the deferred context.py -> races.py rename."
  - "HeatSession.date is non-Optional: entries without a parseable date are dropped at parse time (inverse of races.md, since the rollup is date-keyed)."
  - "Closed-interval windows: 7d = [today-6 .. today] (7 dates total). Future-dated sessions are defensively filtered from all windows."
  - "Sessions with duration_min=None count toward *_count but contribute 0.0 to *_minutes -- a real session with missing duration data."
  - "heat is not re-exported at the tempo.analysis package level (import-on-demand convention; only journal symbols are re-exported)."
duration_min: 7
completed: 2026-05-27
---

# Phase 08 Plan 02: Heat Tracker Infrastructure Summary

Stood up the heat-adaptation tracking infrastructure end-to-end -- `heat.md` parser, three frozen+slotted dataclasses, the rolling-window `heat_rollup`, the committed `heat.md.example` template, and `settings.heat_path`. Pure additions: no existing module rewired (Plan 08-04 will wire `HeatRollup` into `recovery.py`).

## What shipped

| File | Lines | Role |
| --- | --- | --- |
| `tempo/analysis/heat.py` | 245 | new module: parser + HeatSession + HeatContext + heat_rollup + HeatRollup |
| `tests/test_heat.py` | 243 | 13 stdlib-only tests (7 parser + 6 rollup) -- all pass |
| `heat.md.example` | 35 | committed user-facing template at repo root |
| `tempo/config.py` | +5 | new `heat_path` property mirroring `races_path` |
| `tests/test_config.py` | +2 | parallel `heat_path` assertions inside the two existing content-dir tests |
| `tempo/analysis/__init__.py` | +1 | module-index bullet for `heat` |

Smoke test against `heat.md.example`: **6 sessions parsed cleanly**, including the bullet with the unrecognised `hr_max:` key (silently ignored per the lenient-parser contract). Against reference date `2026-06-01`, the 28-day rollup contains all 6 sessions.

## Commits

| Hash | Type | Summary |
| --- | --- | --- |
| `f5ac964` | feat(08-02) | add heat.md parser + HeatSession/Context/Rollup dataclasses |
| `16ac060` | test(08-02) | cover heat parser + heat_rollup with 13 stdlib-only tests |
| `8a06882` | feat(08-02) | add settings.heat_path derived property |
| `2fcf2f2` | docs(08-02) | add heat.md.example committed template at repo root |
| `c522beb` | docs(08-02) | add heat module to tempo.analysis package index |

## Trap coverage

All 7 subtle correctness traps from RESEARCH.md section 1 are pinned by a named test:

| Trap (RESEARCH.md §1) | Test |
| --- | --- |
| #1 Inclusive both ends (7d = 7 dates) | `test_heat_rollup_window_edges_inclusive` (R2) |
| #2 Today yields `days_ago = 0`, not None | `test_heat_rollup_last_session_today_is_zero_days_ago` (R4) |
| #3 None duration counts but contributes 0 min | `test_heat_rollup_minutes_sum_skips_unparseable_duration` (R3) |
| #4 Multiple sessions on same day each counted | `test_heat_rollup_multiple_sessions_same_day_each_counted` (R6) |
| #5 Future-dated sessions filtered defensively | `test_heat_rollup_filters_future_dated_sessions` (R5) |
| #6 No spans-midnight risk (safe by construction) | n/a -- guaranteed by HeatSession.date being a single date |
| #7 Empty sessions → zero rollup, None last_session | `test_heat_rollup_empty_sessions` (R1) |

The inverse-of-races date-required rule is additionally pinned by `test_parse_heat_bad_date_drops_session` (H6), and the leading-date prefix fallback by `test_parse_heat_leading_date_prefix_used` (H7).

## Verification results

- `uv run pytest tests/test_heat.py -x -v` → **13 passed**.
- `uv run pytest tests/test_config.py -x -v` → **9 passed** (existing 7 + parallel `heat_path` assertions in 2 existing tests).
- `uv run pytest tests/ -x` → **374 passed** (no regression in the full suite).
- `uv run ruff check tempo/analysis/heat.py tempo/config.py tempo/analysis/__init__.py tests/test_heat.py tests/test_config.py` → clean.
- `uv run ruff format --check` on the same set → clean.
- Smoke: `parse_heat('heat.md.example')` → 6 sessions; `heat_rollup(sessions, 2026-06-01).last_28d_count` → 6.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] heat module bullet exceeded 100-char line limit on first draft**
- **Found during:** Task 5 verification (`uv run ruff check tempo/analysis/__init__.py`)
- **Issue:** The proposed bullet `* :mod:tempo.analysis.heat -- parse heat.md + heat-adaptation session rollups for the recovery report (TRACK-04, TRACK-05).` rendered at 132 columns; ruff `E501` blocked the commit.
- **Fix:** Tightened the bullet to `parse heat.md + heat-session rollups (TRACK-04/05).` -- preserves the module name, mention of the file it parses, the rollup concept, and both requirement IDs in <= 100 columns.
- **Files modified:** `tempo/analysis/__init__.py`
- **Commit:** `c522beb` (no separate commit; fixed before the Task-5 commit landed)

**2. [Rule 1 - Bug] Initial test data used a comma inside `notes:` value**
- **Found during:** Task 2 first run of `uv run pytest tests/test_heat.py`
- **Issue:** `_parse_kv` splits on both `|` and `,` (verbatim copy from context.py); the test fixture `notes: post-run, felt easier` parsed `notes` as `"post-run"` and dropped `" felt easier"`. The test asserted the full string and failed -- but this is the parser's documented behaviour (the same constraint applies to `races.md` notes).
- **Fix:** Rewrote the test fixture to use `notes: post-run felt easier` (no internal comma). The constraint is a known limitation of the kv-splitter and is documented in `heat.md.example` (users have `notes` available for free-text but should avoid commas/pipes inside it -- same rule as races).
- **Files modified:** `tests/test_heat.py`
- **Commit:** Folded into `16ac060` (test commit) -- no extra commit needed.

## CLAUDE.md compliance

- Python 3.14 / uv / no new deps. ✓
- Frozen + slots on every new dataclass (3/3). ✓
- Lenient parser, never raises. ✓
- Pure stdlib, no network imports (`grep -c "import socket|import urllib|import requests|import httpx" tempo/analysis/heat.py` = 0). ✓
- Local-first, no I/O outside the parse_heat file read. ✓
- GSD-workflow entry: this work was executed under `/gsd-execute-phase` per CLAUDE.md's GSD enforcement section. ✓

## Self-Check: PASSED

- `tempo/analysis/heat.py` exists ✓
- `tests/test_heat.py` exists ✓
- `heat.md.example` exists ✓
- `tempo/config.py` contains `heat_path` ✓
- `tempo/analysis/__init__.py` contains `heat` bullet ✓
- All 5 commits (`f5ac964`, `16ac060`, `8a06882`, `2fcf2f2`, `c522beb`) found in `git log` ✓
- All 13 named heat tests collected and pass ✓
