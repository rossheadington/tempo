# 17-02 Summary: Units formatter module

**Wave:** 17-02
**Status:** Complete
**Date:** 2026-05-28

## Files created

- `tempo/units.py` — top-level presentation-layer helper. Exposes:
  - `KM_PER_MILE: Final[float] = 1.609344`
  - `km_to_miles(km) -> float`
  - `miles_to_km(mi) -> float`
  - `s_per_km_to_s_per_mile(s_per_km) -> float`
  - `format_distance(metres, units, *, precision=1) -> str`
  - `format_pace(s_per_km, units) -> str`
- `tests/test_units.py` — 13 tests, stdlib + pytest only.

## Units-import choice

**Option A (preferred per the plan).** The `Units` type from
`runos.analysis.preferences` (built by Wave 17-01) is imported only under
`TYPE_CHECKING` for type hints; at runtime the formatters duck-type the
value, reading only `units.distance` and `units.pace`. This keeps the wave
testable in isolation and avoids any circular-import risk between
`runos.units` (called by report renderers) and `runos.analysis.preferences`
(part of the analysis layer).

`tests/test_units.py` defines a minimal local `Units` dataclass with the
same shape (`distance` + `pace` Literal fields, `frozen=True, slots=True`)
so the test module is self-contained. The shape matches the locked spec in
`17-CONTEXT.md` exactly.

## Test count

13 tests, all passing:

1. `test_km_to_miles_roundtrip`
2. `test_s_per_km_to_s_per_mile_uses_km_per_mile`
3. `test_format_distance_km_default`
4. `test_format_distance_miles_converts`
5. `test_format_distance_precision_argument`
6. `test_format_distance_none_returns_dash`
7. `test_format_distance_negative_returns_dash`
8. `test_format_distance_nan_returns_dash`
9. `test_format_pace_km_default`
10. `test_format_pace_mile_converts_correctly_at_known_pivot` (the 4:00/km == 6:26/mi pivot)
11. `test_format_pace_seconds_rollover_handled` (covers SS == 60 rollover)
12. `test_format_pace_none_or_zero_returns_dash`
13. `test_dash_constant_is_en_dash` (locks U+2013 explicitly, not hyphen or em-dash)

CONTEXT.md § Test scope lists ~9 tests under `tests/test_units.py`. I added
four extras that the plan implicitly demanded (negative distance, NaN
distance, the `s_per_km_to_s_per_mile` scalar, and an en-dash identity
check). All cover behaviour that the plan explicitly locks down.

## Decisions beyond CONTEXT/PLAN

- **Rounding rule for `format_pace`:** the plan flagged a decision needed
  for "round to nearest second" — locked to Python's built-in `round()`
  (banker's rounding). Documented in the module docstring. Verified
  consequences:
  - 270 s/km × 1.609344 = 434.52288 s/mi → `round` → 435 → `"7:15 /mi"`.
  - 240 s/km × 1.609344 = 386.24256 s/mi → `round` → 386 → `"6:26 /mi"`
    (the pivot point lock in 17-02-PLAN.md and 17-CONTEXT.md).
- **NaN handling:** treated identically to `None` (returns en-dash). Plan
  said "NaN → en-dash" for pace; extended the same defensiveness to
  `format_distance`.
- **Type-coercion defence:** both formatters wrap the numeric `float()` cast
  in a `try / except (TypeError, ValueError)` so a stray string or other
  unexpected type degrades to the en-dash rather than raising. ENGINEERING.md
  mandates parenthesised `except` clauses; followed.
- **Sentinel is module-private:** the en-dash is held as `_DASH` rather than
  hard-coded in two places. Test asserts on `units_mod._DASH` to lock its
  value (U+2013, not hyphen-minus or em-dash).

## Anything weird

- One minor ruff fix-up: initial `tests/test_units.py` had an extra blank
  line before the first `## Local test double` comment block which `I001`
  flagged as part of the import block. `ruff --fix` collapsed it. No
  behaviour change.
- The `VIRTUAL_ENV` env var in the worktree points at the parent `.venv`,
  so `uv run` prints a warning and creates a fresh local `.venv`. Tests
  ran clean under it.

## Verification

```
uv run pytest tests/test_units.py -x       # 13 passed in 3.93s
uv run ruff check runos/units.py tests/test_units.py   # All checks passed!
```

Both green. Did NOT commit (per executor instructions); parent agent will
commit after all waves merge.
