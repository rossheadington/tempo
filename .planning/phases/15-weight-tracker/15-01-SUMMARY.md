---
phase: 15-weight-tracker
plan: 01
subsystem: analysis/weight-tracker
tags: [weight, parser, ewma, rollup, lenient]
requires: []
provides:
  - runos.analysis.weight.parse_weight
  - runos.analysis.weight.weight_rollup
  - runos.analysis.weight.WeightEntry
  - runos.analysis.weight.WeightContext
  - runos.analysis.weight.WeightRollup
  - runos.analysis.weight._to_kg
  - runos.analysis.weight._parse_entry_line
  - runos.config.Settings.weight_path
affects:
  - runos/analysis/__init__.py (module index updated)
requirements:
  - WEIGHT-01
  - WEIGHT-02
  - WEIGHT-03
key-files:
  created:
    - runos/analysis/weight.py
    - tests/test_weight.py
  modified:
    - runos/config.py
    - runos/analysis/__init__.py
decisions:
  - "EWMA seeded from first entry's kg-converted weight, alpha=0.1, iterated forward through all filtered entries"
  - "Out-of-range sanity check uses kg-equivalent (20 < kg < 500) so the typo guard catches lb-overflow as well as kg-overflow"
  - "Latest-wins on duplicate dates implemented via dict[date, WeightEntry] -- later assignments overwrite earlier"
  - "Windows are left-open right-closed (today - N, today]; today - N itself is excluded"
metrics:
  duration_seconds: 220
  completed: 2026-05-28
  task_count: 3
  test_count_delta: 19
---

# Phase 15 Plan 15-01: Weight tracker parser + EWMA rollup — Summary

Lenient `weight.md` parser, frozen+slots dataclasses, and a rolling-window rollup with EWMA trend — the Layer-1 foundation for the weight tracker, structurally identical to `runos/analysis/strength.py` and `runos/analysis/heat.py`. Recovery-report integration is deferred to 15-02; `weight.md.example` + `docs/WEIGHT.md` are deferred to 15-03.

## What shipped

- **`runos/analysis/weight.py`** (303 LoC) — three `@dataclass(frozen=True, slots=True)` types (`WeightEntry`, `WeightContext`, `WeightRollup`), the `_to_kg` + `_parse_entry_line` helpers, the lenient `parse_weight(path)` reader, and the `weight_rollup(entries, today)` function. Stdlib only — no pandas/numpy. Pure-Python EWMA recurrence.
- **`Settings.weight_path`** in `runos/config.py` — mirrors `strength_path` exactly: derived property returning `content_root / "weight.md"`. No new env var.
- **`runos/analysis/__init__.py`** — module index now lists `runos.analysis.weight` alongside `strength` and `heat`.
- **`tests/test_weight.py`** (315 LoC, 19 tests) — covers `_to_kg`, `_parse_entry_line`, the full `parse_weight` lenient contract (missing file, happy path with mixed kg/lb/lbs, malformed lines, latest-wins on duplicates, optional notes with embedded `|` pipes, out-of-range rejection, header/blank-line/prose ignoring), and the rollup (empty, single-entry-today, left-open-right-closed window math, EWMA hand-computed expectation, unit-mixed kg-normalisation, days-since-last).

## Test count delta

Baseline: 593 tests (excluding the slow Whisper fixture). After this plan: 612 tests. **Delta: +19 tests, all green.**

## Goal-backward anchors confirmed

- `test_weight_rollup_ewma_seeded_from_first_entry` passes: weights `[70, 80, 90]` produce trend `72.9` (hand-computed: `0.1*90 + 0.9*(0.1*80 + 0.9*70) = 72.9`).
- `test_weight_rollup_unit_mixed_flag_normalises_to_kg` passes: a mixed `72.0 kg` + `160.0 lb` log yields `unit_mixed=True`, `avg_28d ≈ (72.0 + 72.5747)/2`, `latest_kg ≈ 72.5747`, and `latest_entry.unit` preserves `"lb"`.

## Deviations from `strength.py` shape

None substantive. Two cosmetic differences worth noting:

1. **Single regex up front** for the entry grammar (`_ENTRY_RE`) rather than a multi-step partition-then-regex pipeline like `_parse_exercise_bullet`. The weight format is one-line-per-day with a flat grammar, so one regex with named groups is cleaner than the multi-line session block of `strength.md`.
2. **`WeightContext.path` is `Path | None`** (not `source_path: str | None` like `StrengthContext` / `HeatContext`). Plan-locked: the CONTEXT specifies `path: Path | None`. Caller (Plan 15-02 recovery integration) gets the `Path` directly.

## Self-Check: PASSED

- `runos/analysis/weight.py` exists (303 LoC) ✓
- `tests/test_weight.py` exists (315 LoC, 19 tests) ✓
- `Settings.weight_path` resolves to `<content_root>/weight.md` ✓
- Commit `3e11b74` exists on `main` ✓
- Full test suite: 612 passed, 1 deselected ✓
- `uv run ruff check runos/ tests/` clean ✓

## Commit

`3e11b74` — `feat(15-01): add runos/analysis/weight.py parser + EWMA rollup + Settings.weight_path`
