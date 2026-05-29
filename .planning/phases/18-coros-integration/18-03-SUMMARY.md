# Wave 18-03: EvoLab schema + transform + analysis reader — Summary

**Status:** Complete
**Files created:** 5
**Files modified:** 3
**Tests added:** 9 (all passing)
**Schema:** version bumped 5 → 6
**ruff:** clean

## Delivered

| File | Lines | Role |
|------|-------|------|
| `runos/migrations/0006_coros_evolab.sql` | 56 | New `coros_evolab_day` table + index |
| `runos/transforms/coros_evolab.py` | 221 | Pure transform: raw `evolab_dashboard` → `coros_evolab_day` |
| `runos/analysis/coros_evolab.py` | 131 | `EvoLabDay` + `EvoLabContext` frozen+slots + `read_evolab(conn)` |
| `tests/test_coros_evolab_transform.py` | 201 | 6 tests (4 required + 2 defensive-parsing bonus) |
| `tests/test_coros_evolab_analysis.py` | 115 | 3 tests |
| `runos/db.py` | +1 | `SCHEMA_VERSION = 5` → `6`; added `COROS_EVOLAB_TABLES` marker |
| `runos/transforms/runner.py` | +20 | Sequences EvoLab transform; `coros_evolab_days` added to `TransformResult` |
| `tests/test_db.py` | +28 | New tests for migration + table columns |

## Schema

```sql
CREATE TABLE coros_evolab_day (
    day                       TEXT PRIMARY KEY,        -- ISO YYYY-MM-DD
    vo2max                    REAL,                    -- ml/kg/min
    stamina_level             INTEGER,                 -- 0-100; Coros's base fitness equivalent
    training_load             INTEGER,
    lthr                      INTEGER,                 -- lactate-threshold HR (bpm)
    ltsp_s_per_km             INTEGER,                 -- lactate-threshold pace (s/km)
    fetched_at                TEXT NOT NULL,
    FOREIGN KEY (day) REFERENCES date_spine(day)
);
CREATE INDEX ix_coros_evolab_day_fetched_at ON coros_evolab_day (fetched_at);
```

## `ltsp` unit decision

**No conversion applied** — pass-through as int via `_opt_int`.

The cygnusb/coros-mcp reference (`DailyRecord.ltsp` field comment: `"lactate threshold pace (s/km)"`) confirms Coros reports `ltsp` already in seconds-per-kilometre. The plan's defensive heuristic (assumes m/s or km/h) turned out unnecessary. Real-world threshold paces (3:00–7:30/km → 180–450 s/km) are far outside both alternative bands, so the heuristic wouldn't have triggered anyway. Column name `ltsp_s_per_km` makes the unit explicit at the schema level.

## Decisions worth knowing

1. **`COROS_EVOLAB_TABLES` constant** added to `db.py` matching the per-phase table-marker pattern (`FOUNDATION_TABLES`, `STRUCTURED_TABLES`, etc.).
2. **`EvoLabContext.present = False` when every row is all-NULL** — not just when the table is empty. The recovery report (18-04) doesn't render hollow blocks.
3. **`EP_EVOLAB` duplicated** in `runos/transforms/coros_evolab.py` rather than imported from `runos.connectors.coros`, preserving the transforms-must-stay-network-free invariant.
4. **`transform_evolab_entry` returns `None`** (skipped, logged) for missing/unparseable `happenDay` — verified by `test_evolab_skips_entries_without_happen_day`.
5. **Spine sequencing**: `rebuild_evolab` calls `spine.ensure_days` for referenced days before upserting (same pattern as `rebuild_activities`/`rebuild_wellness`), so the FK to `date_spine(day)` always resolves.

## Verification

```
uv run python -m pytest tests/test_coros_evolab_transform.py tests/test_coros_evolab_analysis.py -x → 9 passed
uv run python -m pytest tests/ → 739 passed (no regression)
uv run ruff check runos/ tests/ → clean
RUNOS_DATA_DIR=/tmp/runos-evolab-verify uv run runos init → PRAGMA user_version=6, table present
```
