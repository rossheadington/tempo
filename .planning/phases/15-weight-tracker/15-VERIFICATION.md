---
phase: 15-weight-tracker
verified: 2026-05-28T11:15:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Phase 15: Weight Tracker (v1.4) — Verification Report

**Phase Goal:** New `weight.md` markdown tracker with lenient parser (latest-wins on duplicate dates, kg/lb accepted, kg-normalised rollup); surfaced in recovery report (latest, 7d avg, 28d avg, EWMA trend, delta-vs-baseline, days-since-last) with the same 3-state degradation rule (absent / stale / current) heat & strength use.

**Verified:** 2026-05-28T11:15:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

The goal-backward target — "a user with a `weight.md` in their content dir runs `runos analyze recovery` and sees a `## Weight` section with latest reading, 7d/28d averages, EWMA trend, delta-vs-28d baseline, and days-since-last; missing file silently omits; stale (>14d) shows a one-line nudge; mixed kg/lb yields kg-normalised output with a footnote caveat" — is fully achieved in the codebase.

### Requirements Coverage (WEIGHT-01..05)

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| WEIGHT-01 | `weight.md` lenient format `- YYYY-MM-DD: <weight> [kg\|lb] [\| notes: ...]`, latest-wins on dupes, missing file is not an error | PASS | `runos/analysis/weight.py::parse_weight` returns `WeightContext(present=False, ...)` for missing path (lines 168-179); `_parse_entry_line` regex (lines 90-97) honours the locked grammar; latest-wins implemented via `dict[date, WeightEntry]` (line 185); tests `test_parse_weight_missing_file_returns_absent_context`, `test_parse_weight_latest_wins_on_duplicate_date` pass |
| WEIGHT-02 | `parse_weight(path)` produces a frozen+slots `WeightContext` of deduplicated, date-sorted entries plus malformed-line numbers; never raises | PASS | All three dataclasses declared `@dataclass(frozen=True, slots=True)` (lines 36, 53, 67); `entries` sorted ascending (line 204); `malformed_lines` tuple recorded (line 211); 7 parser tests cover the lenient contract; full test suite 619 passed with zero raises |
| WEIGHT-03 | `weight_rollup(entries, today)` produces a `WeightRollup` with latest reading, 7d avg, 28d avg, EWMA (alpha=0.1), delta-vs-28d, days-since-last, all kg-normalised, plus `unit_mixed` flag | PASS | `weight_rollup` (lines 215-303) computes all 8 fields; `_to_kg` (line 104) converts lb via 0.453592; EWMA alpha=0.1 seeded from first entry's kg-converted weight (lines 282-286); windows are `(today - N, today]` left-open right-closed (lines 264-276); `unit_mixed` set when `len({e.unit for e in filtered}) > 1` (line 262); 6 rollup tests pass including hand-computed EWMA `[70, 80, 90] -> 72.9` |
| WEIGHT-04 | Recovery report renders `## Weight` section with 3-state degradation: absent → omit; stale (>14d) → one-line nudge; current → full rollup line | PASS | `_render_weight_section` (recovery.py:631-688) implements the 3-state rule verbatim; emits `## Weight\n` only when `weight_present and weight is not None and weight.latest_entry is not None`; stale branch emits `_Last weigh-in N days ago — log a current reading to keep the rollup live._`; current branch emits `{latest} kg today · 7d avg ... · 28d avg ... · trend ... · {±X.X} kg vs 28d baseline`; mixed-unit caveat `_(mixed kg/lb in log — normalised to kg)_` appended when `unit_mixed=True`; called immediately after `_render_strength_section` (recovery.py:741); 7 integration tests in `tests/test_recovery.py` cover all 4 sub-states |
| WEIGHT-05 | `weight.md.example` committed; `docs/WEIGHT.md` documents format, EWMA, agent-append guidance | PASS | `weight.md.example` (71 lines, 14 entries spanning 2026-05-15→05-28, mixed kg/lb/lbs, 4 notes-bearing entries, 1 embedded-pipe-in-notes); parses cleanly: `present=True`, 14 entries, `malformed_lines=()`, units={kg, lb}; `docs/WEIGHT.md` (199 lines) contains all required sections (`## Format`, `## Lenient parsing`, `## Rollup semantics`, `## Recovery report integration`, `## Agent-append guidance`, `## What's NOT in this layer`); README.md mentions `weight.md` at L424-428 |

**Score:** 5/5 requirements verified.

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User can `from runos.analysis.weight import parse_weight, weight_rollup, WeightEntry, WeightContext, WeightRollup` and call them | VERIFIED | Imports succeed; `runos/analysis/__init__.py` lists `runos.analysis.weight`; `runos/analysis/weight.py` exposes all five symbols |
| 2 | `Settings.weight_path` returns `<content_root>/weight.md` | VERIFIED | `runos/config.py:224` defines `weight_path` derived property mirroring `strength_path`/`heat_path` |
| 3 | Parsing `weight.md.example` yields `present=True`, 14 entries, 0 malformed, both `kg` and `lb` units present | VERIFIED | Smoke check: `parse_weight(Path('weight.md.example'))` returned `present=True entries=14 malformed=() units=['kg', 'lb']` |
| 4 | EWMA alpha=0.1 seeded from first entry; `[70, 80, 90]` produces trend `72.9` | VERIFIED | `test_weight_rollup_ewma_seeded_from_first_entry` passes; recurrence at recovery.py-equivalent lines 282-286 |
| 5 | Mixed kg/lb log produces `unit_mixed=True` with kg-normalised numerics; `latest_entry` preserves original unit | VERIFIED | `test_weight_rollup_unit_mixed_flag_normalises_to_kg` passes; rollup line 260: `latest_kg = _to_kg(latest_entry.weight, latest_entry.unit)` |
| 6 | Windows `(today - N, today]` left-open right-closed; same-day weigh-in counts; `today - N` itself excluded | VERIFIED | `test_weight_rollup_7d_28d_windows_left_open` passes; rollup lines 267-276 use `cutoff_N < e.date <= today` |
| 7 | Out-of-range guard (`20 < kg_equivalent < 500`) catches typos like `7.24 kg`, `724 kg`, `1600 lb` | VERIFIED | `test_parse_weight_rejects_out_of_range_weights` passes; weight.py:155 |
| 8 | Recovery report `## Weight` section omitted when `weight_present=False` OR `weight.latest_entry is None` | VERIFIED | `test_recovery_renderer_omits_weight_section_when_absent` + `test_recovery_renderer_omits_weight_when_present_but_empty` pass; recovery.py:650-653 |
| 9 | Stale (>14d) emits one-line nudge; no rollup markers present | VERIFIED | `test_recovery_renderer_emits_stale_nudge_when_last_weigh_in_over_14d` passes; recovery.py:657-663 |
| 10 | Current state emits full rollup line with one-decimal kg, signed Unicode-minus delta | VERIFIED | `test_recovery_renderer_emits_full_rollup_when_current` passes; smoke render produced `72.4 kg today · 7d avg 72.6 kg · 28d avg 72.9 kg · trend 72.8 kg · −0.5 kg vs 28d baseline` |
| 11 | Mixed-unit caveat appended when `unit_mixed=True` | VERIFIED | `test_recovery_renderer_appends_mixed_unit_caveat` passes; recovery.py:685-686 |
| 12 | Weight section renders AFTER Strength section (order: Heat → Strength → Weight) | VERIFIED | `test_recovery_renderer_weight_section_follows_strength` passes; `_render_weight_section` called at recovery.py:741, after `_render_strength_section` |
| 13 | `_fmt_weight_delta` renders Unicode-minus + plus-minus glyphs with one decimal | VERIFIED | `test_fmt_weight_delta_signs`: `+0.3 kg`, `−0.5 kg`, `±0.0 kg`, `+1.2 kg`; recovery.py:518-530 |
| 14 | CLI passes `settings.weight_path` to runner at both `analyze` + `analyze recovery` call sites | VERIFIED | `runos/cli.py:613` and `runos/cli.py:684` both pass `weight_path=settings.weight_path` |
| 15 | `runner.generate_recovery` + `runner.generate_all` thread `weight_path: Path \| None = None` through to `assess_recovery_from_db` | VERIFIED | `runner.py:300, 328, 388, 414` |

**Truths score:** 15/15 verified.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `runos/analysis/weight.py` | 3 dataclasses + `_to_kg` + `_parse_entry_line` + `parse_weight` + `weight_rollup` | VERIFIED | 303 LoC; all symbols present; imports succeed |
| `runos/config.py::Settings.weight_path` | Derived property `<content_root>/weight.md` | VERIFIED | Line 224, mirrors `strength_path` |
| `runos/analysis/__init__.py` | Module index mentions weight | VERIFIED | Updated alongside heat + strength entries |
| `runos/analysis/recovery.py` | `RecoveryAssessment` + `weight`/`weight_present`, `_render_weight_section`, `_fmt_weight_delta`, `assess_recovery_from_db(weight_path=...)`, render call after strength | VERIFIED | Lines 42-44 (imports), 262-263 (fields), 408+ (kwarg), 518 (`_fmt_weight_delta`), 631 (`_render_weight_section`), 741 (render call) |
| `runos/analysis/runner.py` | `generate_recovery` + `generate_all` accept + thread `weight_path` | VERIFIED | Lines 300, 328, 388, 414 |
| `runos/cli.py` | Both call sites pass `settings.weight_path` | VERIFIED | Lines 613, 684 |
| `tests/test_weight.py` | 13+ tests covering parser + helpers + rollup + EWMA + unit-mixed | VERIFIED | 19 tests (exceeds target); all pass |
| `tests/test_recovery.py` (weight additions) | 7 new tests covering 3-state + delta formatting + section ordering | VERIFIED | 7 tests under `# ---- Weight section ----` divider; all pass |
| `weight.md.example` | ≥14 entries, mixed kg/lb, ≥1 notes, ≥1 embedded-pipe-in-notes | VERIFIED | 71 lines; 14 entries; 12 kg + 1 lb + 1 lbs; 4 notes-bearing; 1 with embedded `\|` |
| `docs/WEIGHT.md` | All 5+ required sections | VERIFIED | 199 lines; all required headings present |
| `README.md` | `weight.md` mention | VERIFIED | 3 occurrences at L424-428 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `runos/cli.py::analyze_recovery` | `runner.generate_recovery` | `weight_path=settings.weight_path` | WIRED | cli.py:613, 684 |
| `runner.generate_recovery` | `recovery.assess_recovery_from_db` | `weight_path=weight_path` | WIRED | runner.py:328, 414 |
| `recovery.assess_recovery_from_db` | `weight.parse_weight` + `weight.weight_rollup` | imported as `_parse_weight` / `_weight_rollup`, called inside the single reconstruction | WIRED | recovery.py:43-44, 471-472 |
| `recovery.render_recovery` | `_render_weight_section` | direct call after `_render_strength_section` | WIRED | recovery.py:741 |
| `parse_weight` | `_parse_entry_line` | called per `- ` bullet line | WIRED | weight.py:198 |
| `weight_rollup` | `_to_kg` | called for every entry before averaging | WIRED | weight.py:260, 268, 274, 283, 285 |
| `Settings.weight_path` | `content_root` | derived property | WIRED | config.py:224-227 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `_render_weight_section` | `weight: WeightRollup \| None` | `assess_recovery_from_db` → `parse_weight(weight_path)` + `weight_rollup` | YES — reads real file, computes real numerics | FLOWING |
| `WeightRollup.avg_7d`/`avg_28d`/`ewma_trend` | filtered entries | `WeightContext.entries` populated by `_parse_entry_line` regex matches | YES — every entry from file flows through `_to_kg` into the windows | FLOWING |
| `RecoveryAssessment.weight` | `weight_rollup_obj` | `_weight_rollup(weight_ctx.entries, today_for_tracker)` | YES — `today_for_tracker` aligned to `points[-1].day` then falls back to `date.today()` | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `parse_weight` on `weight.md.example` returns 14 clean entries | `python -c "from pathlib import Path; from runos.analysis.weight import parse_weight; ctx = parse_weight(Path('weight.md.example')); print(ctx.present, len(ctx.entries), ctx.malformed_lines, sorted({e.unit for e in ctx.entries}))"` | `True 14 () ['kg', 'lb']` | PASS |
| Active rollup renders the full one-line summary with Unicode minus | `_render_weight_section(out, WeightRollup(..., delta_vs_28d=-0.5, unit_mixed=False), True)` smoke | `## Weight\n\n72.4 kg today · 7d avg 72.6 kg · 28d avg 72.9 kg · trend 72.8 kg · −0.5 kg vs 28d baseline` | PASS |
| Stale state renders single-line nudge, no rollup markers | smoke with `days_since_last=21` | `## Weight\n\n_Last weigh-in 21 days ago — log a current reading to keep the rollup live._` | PASS |
| `_fmt_weight_delta` sign glyphs | direct call | `−0.5 kg`, `±0.0 kg`, `+0.3 kg` | PASS |
| Full test suite green | `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` | `619 passed, 1 deselected` in 2.68s | PASS |
| Ruff clean | `uv run ruff check runos/ tests/` | `All checks passed!` | PASS |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| _none_ | — | — | — | No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`placeholder` markers in any Phase 15 file. Empty `return` defaults are well-justified frozen-dataclass safety fallbacks. |

### Human Verification Required

None. All goal-backward assertions are testable programmatically and confirmed via the automated test suite + smoke checks. The one out-of-scope item (a live `runos analyze recovery` run against a real `weight.md` populated by the owner) is acknowledged but not required for Phase 15 closure — the committed `weight.md.example` IS itself a regression test for the parser, and the recovery-report rendering is covered by 7 integration tests.

### Gaps Summary

No gaps. Phase 15 is complete:

- All five WEIGHT-* requirements satisfied with code, tests, and docs evidence.
- 19 new parser/rollup tests + 7 new recovery-integration tests = +26 tests vs Phase 14 baseline (593 → 619).
- Full suite 619/619 green; ruff clean; zero debt markers.
- The single deviation from Plan 15-03 — that the three deliverables (`weight.md.example`, `docs/WEIGHT.md`, `README.md`) were bundled into commit `52ca1be` (feat 15-02) rather than landing as a separate `docs(15-03):` commit — is documented in `15-03-SUMMARY.md` and visible in `git log`. The follow-up commit `05a13de docs(15-03): complete weight tracker docs + example plan` only records the SUMMARY artifact, not file content. All file contents are intact on `main`.

---

*Verified: 2026-05-28T11:15:00Z*
*Verifier: Claude (gsd-verifier)*
