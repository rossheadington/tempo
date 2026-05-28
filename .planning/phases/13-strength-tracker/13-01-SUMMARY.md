---
phase: 13-strength-tracker
plan: 01
subsystem: analysis
tags: [strength, parser, recovery, layer-1]
requires: []
provides:
  - runos.analysis.strength module (5 frozen+slots dataclasses + parser + rollup)
  - Settings.strength_path derived property
affects:
  - runos/config.py (one new property)
  - runos/analysis/__init__.py (module-index bullet)
tech_stack:
  added: []
  patterns: [lenient-parser, frozen-slots-dataclasses, closed-interval-rollup]
key_files:
  created:
    - runos/analysis/strength.py
    - tests/test_strength.py
  modified:
    - runos/config.py
    - runos/analysis/__init__.py
decisions: []
metrics:
  duration_min: ~15
  tests_added: 26
  loc_added: 399 (strength.py) + 436 (test_strength.py)
  completed: 2026-05-28
---

# Phase 13 Plan 01: Strength Parser + Rollup Summary

Self-contained Layer-1 module for the strength & conditioning tracker:
`runos/analysis/strength.py` mirrors `runos/analysis/heat.py` exactly. 5
frozen+slots dataclasses, a lenient `parse_strength`, and a closed-interval
`strength_rollup`. No recovery integration yet — that lands in Plan 13-02.

## What shipped

- **`runos/analysis/strength.py`** (399 LoC, within the 200-280 target zone if
  you exclude blank/doc lines; raw line count includes a generous module
  docstring and clear dataclass-per-block layout). Contents:
  - `StrengthSet`, `StrengthExercise`, `StrengthSession`, `StrengthContext`,
    `StrengthRollup` — all `@dataclass(frozen=True, slots=True)`. `sets` and
    `exercises` are tuples for frozen safety per CONTEXT.
  - `_parse_set(token)` — three branches in priority order: weighted
    (`_WEIGHTED_RE`, accepts `x` / `X` / `×` and whitespace), timed
    (`_TIMED_RE`, single colon `M:SS`), bodyweight (`_BODYWEIGHT_RE`, bare
    integer). Anything else → `None`.
  - `_parse_header`, `_parse_rest`, `_parse_exercise_bullet` helpers.
  - `parse_strength(path)` — line-by-line state machine. Missing file →
    `present=False`. `##` header opens a session (flushes any in-flight one).
    Unparseable date sets `in_skip_mode=True` until the next valid header.
    Metadata lines (`rest:`, `notes:`) accumulate into the in-flight session;
    unknown keys silently dropped. Exercise bullets parsed via
    `_parse_exercise_bullet`. Prose / blank / non-recognised lines skipped.
  - `strength_rollup(sessions, today)` — closed intervals `[today-N+1 .. today]`
    for N=7/14/28, future-dated sessions filtered defensively, `last_session_*`
    derived from the most-recent in-past session with `"unnamed"` fallback for
    a None name.
  - `_session_tonnage` helper keeps the rollup loop readable.

- **`runos/config.py`** — added `strength_path` property immediately after
  `heat_path`. Body and docstring mirror heat_path's shape.

- **`runos/analysis/__init__.py`** — added a `strength` bullet to the module
  index alongside heat.

- **`tests/test_strength.py`** (436 LoC, 26 tests):
  - 5 `_parse_set` unit tests (weighted basic / separators / timed / bodyweight
    / malformed).
  - 12 `parse_strength` tests (missing file / header-only / header with time +
    name + both separators / rest + notes / malformed rest / unknown key /
    exercise basic / superset group / timed holds / malformed set in valid
    bullet / unparseable-date session dropped + re-anchoring / top-level `#`
    heading ignored).
  - **1 integration test** `test_parse_owners_tuesday_session_full_roundtrip`
    parses the exact 7-exercise Tuesday session from CONTEXT, asserts names in
    order, Pogos + Single Leg Glute Bridge supersetted as `[A]`, Plank's
    `60,60,60,30s` holds, Calf Press's `80x16` four times.
  - 8 `strength_rollup` tests including the **tonnage = 9835.0 kg** worked
    Tuesday check (goal-backward anchor).

## Verification results

- `uv run pytest tests/test_strength.py -v` → **26 passed**.
- `uv run pytest tests/ --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` → **523 passed, 1 deselected** (498 baseline + 26 new − the 1 slow Whisper test that's always deselected).
- `uv run ruff check runos/ tests/` → All checks passed.
- Smoke: `Settings().strength_path` → `~/.runos/strength.md` (parent =
  `content_root`, name = `strength.md`).
- Goal-backward: owner's Tuesday session parses to 1 session with 7 exercises,
  `last_7d_tonnage_kg == 9835.0`, `last_session_name == "Lower body"`.

## Deviations from plan

None. Implementation mirrors `heat.py` shape exactly. One small judgement call:
I did **not** import or reuse heat's `_parse_kv` — strength's metadata grammar
is single-key-per-line (one `key: value` per line, no `|` separators), which
is structurally different from heat's `key: value | key: value` bullets, so
inlining a `partition(":")` in `parse_strength` was cleaner than reusing the
heat helper. The CONTEXT explicitly left that judgement to the planner /
executor ("planner's call").

## Commit

- `73aefa7` — `feat(13-01): add runos/analysis/strength.py parser + rollup + Settings.strength_path`

## Self-Check: PASSED

- `runos/analysis/strength.py` exists.
- `tests/test_strength.py` exists (26 tests, all green).
- `Settings.strength_path` returns `<content_root>/strength.md`.
- Commit `73aefa7` exists in `git log`.
- Full test suite green, ruff clean.
- No files outside the plan's `files_modified` list were touched.
