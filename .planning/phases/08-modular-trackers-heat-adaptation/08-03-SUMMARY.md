---
phase: 08-modular-trackers-heat-adaptation
plan: 03
subsystem: analysis
tags: [analysis, race, auto-link, TRACK-03]
requires: []
provides: [RaceLink, link_races_to_activities]
affects: [runos/analysis/race_link.py (new), tests/test_race_link.py (new)]
tech_stack:
  added: []
  patterns: ["read-only composition layer", "defensive sqlite_master probe", "0/1/N classification (returns instead of raises)"]
key_files:
  created:
    - runos/analysis/race_link.py
    - tests/test_race_link.py
  modified: []
decisions:
  - "race-link returns unlinked_ambiguous instead of raising (analyses must not crash on multi-activity race days; diverges from journal-service Phase 5)"
  - "no sport filter: a race links to whatever activity is on its local date (Run/Ride/Swim/...) per CONTEXT.md"
  - "single DB scan: one SELECT loads all (day, activity_id) pairs into a dict; classification is pure Python (not N queries per race)"
  - "linker is time-of-day-blind: past-no-match and future-no-match both yield unlinked_no_match (renderer chooses phrasing)"
metrics:
  duration: "single session"
  completed: "2026-05-27"
commits:
  - {hash: "78e3ff9", subject: "feat(08-03): add race_link module for race-to-activity auto-link (TRACK-03)"}
  - {hash: "b40fdb4", subject: "test(08-03): cover 7 edge cases for race-to-activity auto-link"}
---

# Phase 8 Plan 03: Race-to-Activity Auto-Link Summary

Added the `runos/analysis/race_link.py` module: a read-only composition layer that classifies each parsed `Race` against the Strava activities on its local date (TRACK-03), backed by a 9-test edge-case suite.

## What landed

**`runos/analysis/race_link.py`** (92 lines) — exports:

- `RaceLink` (`@dataclass(frozen=True, slots=True)`): `race: Race`, `activity_id: int | None`, `link_status: str` (one of `'linked' | 'unlinked_no_match' | 'unlinked_ambiguous' | 'unlinked_no_date'`).
- `link_races_to_activities(races: list[Race], conn: sqlite3.Connection) -> list[RaceLink]`: returns a list parallel to `races` (same length, same order).

**`tests/test_race_link.py`** (241 lines) — 9 tests, all PASSED.

## Tests (all PASSED)

| # | Test | Pins |
|---|------|------|
| L1 | `test_link_race_with_single_activity_links` | 1 activity on race day -> `linked` with the right `activity_id` |
| L2 | `test_link_race_with_no_activity_unlinked_no_match` | empty activity table on race day -> `unlinked_no_match` |
| L3 | `test_link_race_with_multiple_activities_ambiguous` | 3 activities on race day -> `unlinked_ambiguous`, no guess |
| L4 | `test_link_race_with_no_date_unlinked_no_date` | `race_date=None` -> `unlinked_no_date` |
| L5 | `test_link_race_links_non_run_activities` | only a `Ride` on race day -> still `linked` (no sport filter) |
| L6 | `test_link_race_future_dated_unlinked_no_match` | `race_date = today + 365` with no activity -> `unlinked_no_match` (no special "future" status) |
| L7 | `test_link_race_no_activity_table_returns_unlinked` | bare in-memory conn with NO activity table -> `unlinked_no_match`, no crash |
| + | `test_link_empty_races_returns_empty_list` | empty input -> empty output, no DB probe |
| + | `test_link_races_performs_single_db_scan` | 5 races, exactly 1 `SELECT ... FROM activity`; parallel-ordering proven |

Verification commands run:

```
uv run pytest tests/test_race_link.py -x -v   # 9 passed
uv run pytest tests/ -x                       # 370 passed (was 361, +9 new)
uv run ruff check runos/analysis/race_link.py tests/test_race_link.py   # clean
uv run ruff format --check ...                # clean
```

## Single-DB-scan proof

The `link_races_to_activities` body has exactly **one** `conn.execute("SELECT day, activity_id FROM activity")` call, NOT inside the per-race loop (lines 76-81 of `race_link.py`):

```python
has_activity = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='activity'"
).fetchone()
day_to_activities: dict[str, list[int]] = {}
if has_activity is not None:
    rows = conn.execute("SELECT day, activity_id FROM activity").fetchall()
    for row in rows:
        day = str(row[0])
        activity_id = int(row[1])
        day_to_activities.setdefault(day, []).append(activity_id)
```

`test_link_races_performs_single_db_scan` enforces this empirically via a `CountingConn` subclass that increments a counter on every `execute(...)` whose SQL contains `FROM activity` but not `sqlite_master`. With 5 races classified in one call, the counter reads exactly 1.

## Edge-case decisions exposed

- **Future-date case (L6) collapses to `unlinked_no_match` (same as L2 past-no-match)**: the linker doesn't know "today". Plan D's renderer decides whether to phrase as "race upcoming, no run yet" vs. "race day passed, no activity recorded".
- **0/1/N divergence from journal-service**: journal-service raises `MultipleActivitiesError` on N>1; race-link returns `unlinked_ambiguous`. The reason is asymmetric: journal writes need a definite link, but analyses must never crash on a multi-activity race day.
- **No sport filter (L5)**: a race could be ridden, swum, run, or triathlon — the linker matches by date only. Per CONTEXT.md decision; renderer downstream judges fit.
- **L7 defensive table-existence probe**: mirrors `srpe_by_day` in `runos/analysis/data.py`. A fresh pre-Phase-3 DB without an `activity` table returns `unlinked_no_match` for every dated race rather than crashing with `OperationalError: no such table`.
- **Race import path**: imports `Race` from `runos.analysis.context` (current home in this worktree, pre-Plan-08-01-rename). When Plan 08-01 lands and moves `Race` to `runos.analysis.races`, the context shim re-exports keep this import path working until Plan 08-05 sweeps.

## Function signature

```python
def link_races_to_activities(
    races: list[Race], conn: sqlite3.Connection
) -> list[RaceLink]:
    """Classify each race against the activities on its local date."""
```

## File line counts

| File | Lines |
|------|-------|
| `runos/analysis/race_link.py` | 92 |
| `tests/test_race_link.py` | 241 |

## Deviations from Plan

None — plan executed as written. One bonus test (`test_link_races_performs_single_db_scan`) was added beyond the 7 mandated cases to empirically pin the single-scan contract that the plan calls out as a must-have truth; this is additive and does not change the plan's scope.

## Self-Check: PASSED

- File `runos/analysis/race_link.py` exists in worktree (verified).
- File `tests/test_race_link.py` exists in worktree (verified).
- Commit `78e3ff9` (`feat(08-03): add race_link module ...`) exists on branch (verified).
- Commit `b40fdb4` (`test(08-03): cover 7 edge cases ...`) exists on branch (verified).
- All 9 tests pass (verified).
- Full suite (370 tests) passes (verified).
- ruff check + format clean on both new files (verified).
