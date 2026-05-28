---
phase: 13-strength-tracker
verified: 2026-05-28T00:00:00Z
status: passed
verdict: PASS
score: 5/5 success criteria verified
---

# Phase 13: Strength & Conditioning Tracker — Verification Report

**Phase Goal:** Owner-maintained `strength.md` (in the content dir) captures S&C
sessions as an append-only markdown log, parsed leniently into structured
`StrengthSession` / `StrengthExercise` / `StrengthSet` dataclasses and surfaced
in the recovery report. Mirrors `heat.md` exactly: lenient parser, frozen+slots
dataclasses, rolling-window rollup attached to `RecoveryAssessment`, renderer
with 3-state degradation (absent / lapsed / active). Layer 1 only — DB tables,
Strong-app CSV import, and tonnage-trend report are out of scope.

**Verdict:** **PASS** — all 5 success criteria fully met; 55 tests green; ruff
clean; sanity check confirms owner's Tuesday 2026-05-26 session totals 9,835 kg.

---

## Success Criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Dataclasses (`StrengthSet`/`StrengthExercise`/`StrengthSession`/`StrengthContext`/`StrengthRollup`) + `parse_strength` + `strength_rollup`, modelled on `heat.py` | MET | `tempo/analysis/strength.py:37-103` defines all five frozen+slots dataclasses; `parse_strength` at line 228, `strength_rollup` at line 335. Shape matches `tempo/analysis/heat.py` (lenient missing-file path, dataclasses, rollup-with-windows). |
| 2 | Parser handles header (date + optional time + optional name via `—` or ` - `), `rest:`/`notes:`, exercise bullets w/ `(Equipment)` + `[GROUP]`, weighted `WxR`, timed `M:SS`, bodyweight bare ints; unknown keys, malformed sets, missing file all degrade gracefully | MET | `strength.py:106-225` (regexes + parsers); `tests/test_strength.py:57-292` covers every wrinkle including `test_parse_set_weighted_separators` (x/X/×), `test_parse_set_timed_hold`, `test_parse_set_bodyweight`, `test_parse_strength_metadata_unknown_key_ignored`, `test_parse_strength_session_with_unparseable_date_skipped`, `test_parse_strength_missing_file`. |
| 3 | `Settings.strength_path` mirrors `heat_path`; runner threads `strength_path` through `assess_recovery_from_db`; recovery report renders `## Strength & conditioning` w/ same 3-state rule (absent → omit / lapsed → nudge / active → rollup line w/ counts + tonnage + last age) | MET | `tempo/config.py:219-221` (strength_path property mirrors heat_path); `tempo/analysis/runner.py:299,381,406` (threaded through `generate_recovery` + `generate_all`); `tempo/cli.py:612,682` (settings passed at both call sites); `recovery.py:394-468` (parses + rolls + attaches); `recovery.py:542-592` `_render_strength_section` implements 3-state rule; `_render_strength_section` called at `recovery.py:644`. |
| 4 | Committed `strength.md.example` with owner's 2026-05-26 session as worked example; `docs/STRENGTH.md` documents format end-to-end | MET | `strength.md.example` present (90 lines, Tuesday session at line 50-60); `docs/STRENGTH.md` present (authoritative, status-marked); `README.md:362-366` references it. `tests/test_strength.py:439` `test_example_file_parses_cleanly` asserts the example parses without raising. |
| 5 | Tests under `tests/test_strength.py` (parser + rollup window math) + `tests/test_recovery.py` extended w/ strength integration; full pytest green; ruff clean | MET | `tests/test_strength.py` has 28 tests covering parser happy/malformed/missing-file paths and rollup window math (lines 57-446); `tests/test_recovery.py:451-580` adds 5 strength-integration render tests. **Smoke check:** `uv run pytest tests/test_strength.py tests/test_recovery.py` → **55 passed in 0.53s**. `uv run ruff check tempo/analysis/strength.py tempo/analysis/recovery.py tests/test_strength.py tests/test_recovery.py` → **All checks passed.** |

---

## Sanity Check — Owner's Tuesday Session

Ran `parse_strength` + `strength_rollup` against the owner's documented
2026-05-26 lower-body session (RDL 40×8/50×8/55×7/55×8, Hip Thrust 50×10 +
55×10×3, Seated Leg Curl 25×12 + 30×12×2, Calf Press 80×16×4, plus bodyweight
Pogos/SLGB and timed Plank):

```
tonnage_7d: 9835.0
count_7d:   1
last name:  Lower body
```

Expected 9,835.0 kg confirmed. Bodyweight + timed sets correctly contribute 0
to tonnage but DO count toward session count (matches the dataclass docstring
contract).

---

## Key Wiring Verified

| From | To | Via | Status |
|------|----|----|--------|
| `cli.analyze`/`cli.run_daily` | `runner.generate_all` | `strength_path=settings.strength_path` | WIRED (cli.py:612, 682) |
| `runner.generate_recovery` | `assess_recovery_from_db` | `strength_path=strength_path` | WIRED (runner.py:322, 406) |
| `assess_recovery_from_db` | `parse_strength` + `strength_rollup` | when `strength_path is not None` | WIRED (recovery.py:449-454) |
| `render_recovery` | `_render_strength_section` | follows `_render_heat_section` call | WIRED (recovery.py:643-644) |

---

## Anti-Pattern Scan

- No TBD / FIXME / XXX markers in any Phase 13 file.
- No stub returns (`return null` / `return []` flowing to rendering).
- 3-state degradation matches the heat pattern exactly; no "N/A" rendering.

---

## Notes

- `_fmt_tonnage` (recovery.py:485-494) renders sub-10t loads as `"9,835 kg"`
  and ≥10t as `"12.4 t"` — sensible human-readable output that wasn't
  explicitly specified but follows the report convention of degrading without
  fabricating.
- Parser correctly drops sessions with unparseable dates AND the entire block
  beneath them (skip-mode in `parse_strength`), preventing exercises from one
  session bleeding into the next.

---

_Verified: 2026-05-28_
_Verifier: Claude (gsd-verifier), goal-backward methodology against ROADMAP.md
SC-1..5._
