# Wave 18-02: Coros wellness transform — Summary

**Status:** Complete
**Files created:** 2
**Files modified:** 1
**Tests added:** 6 (all passing)
**ruff:** clean

## Delivered

| File | Lines | Role |
|------|-------|------|
| `runos/transforms/coros_wellness.py` | 346 | Pure transform; COALESCE-based per-(day,metric) resolver; defensive parsing; idempotent |
| `tests/test_coros_wellness_transform.py` | 299 | 6 tests (all 6 from CONTEXT.md § Test scope) |
| `runos/transforms/runner.py` | 132→159 | Sequences Garmin wellness FIRST, Coros SECOND (priority resolver works deterministically) |

## Priority resolver mechanism

For each row, the UPDATE applies `column = COALESCE(?, column)` — Coros's non-NULL value overrides, NULL preserves whatever Garmin wrote. Per-column. Encoded once in `COROS_WELLNESS_COLUMNS` tuple that drives the UPSERT, the COALESCE clause, the dataclass slots, and the `has_any_value` check.

## `wellness_day` columns Coros fills (in v1.7)

Coros's current endpoint surface only populates two of the thirteen wellness columns:

- `resting_hr` ← `rhr` from `/analyse/dayDetail/query`
- `hrv_last_night` ← `avgSleepHrv` from `/dashboard/query` (preferred) or `/analyse/dayDetail/query` (fallback)

All other columns (`hrv_status`, `sleep_score`, `sleep_seconds`, `deep_s`, `rem_s`, `light_s`, `awake_s`, `body_battery_high`, `body_battery_low`, `stress_avg`, `steps`) are NOT in the v1.7 endpoint surface and remain Garmin-fed via the COALESCE preservation. Sleep stages live behind the mobile `/coros/data/statistic/daily` endpoint — explicitly out of v1.7 scope.

**This is fine for the switchover.** Garmin keeps providing those metrics until the user retires it; the priority resolver hands authority to Coros for the two metrics it provides AND lets Garmin survive elsewhere.

## Decisions worth knowing

1. **HRV source precedence** when both `/dashboard/query` and `/analyse/dayDetail/query` carry `avgSleepHrv`: dashboard wins (dedicated endpoint). DayDetail is fallback.
2. **All-NULL Coros rows skipped at write time** via `has_any_value` — no churn on `updated_at` when Coros has nothing useful for a day.
3. **`_rebuild_wellness_if_present` extended** in `transforms/runner.py` to call both transforms and sum row counts. No new scaffolding needed.

## Verification

```
uv run python -m pytest tests/test_coros_wellness_transform.py -x  → 6 passed
uv run python -m pytest tests/test_wellness_transform.py -x        → 7 passed (no Garmin regression)
uv run ruff check runos/transforms/coros_wellness.py tests/test_coros_wellness_transform.py → clean
```
