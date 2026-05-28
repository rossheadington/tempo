---
phase: 15-weight-tracker
plan: 03
subsystem: docs
tags: [docs, weight, tracker, markdown]
requires: [15-01]
provides: [WEIGHT-05]
affects: []
tech_stack:
  added: []
  patterns: [committed-example-as-parser-fixture, mirror-strength-md-example, three-state-recovery-degradation-docs]
key_files:
  created:
    - weight.md.example
    - docs/WEIGHT.md
  modified:
    - README.md
decisions:
  - "Single atomic commit (final SHA captured below) carries all three artifacts. Parallel 15-02 executor swept all unstaged files into its own commit during the same wave; deliverables are intact on main but commit attribution merged with 15-02 rather than landing as a separate docs(15-03) commit."
  - "Format-section examples in weight.md.example shown inline with backticks (not fenced code blocks) because the parser does NOT recognise fence boundaries — it scans every line for the `- ` bullet prefix. A fenced `- 2026-...` would be parsed as a real entry."
  - "What-NOT-to-do examples use a leading `~` instead of `-` (with a one-line note explaining the visual substitution) so they cannot be mistaken for real entries by the parser."
metrics:
  duration_minutes: 8
  completed_date: 2026-05-28
---

# Phase 15 Plan 03: Weight tracker docs + example Summary

One-liner: ship the user-facing artifacts for Phase 15 — committed
`weight.md.example` with 14 mixed-unit entries (doubles as a parser fixture),
end-to-end `docs/WEIGHT.md`, and a one-paragraph README tracker-list update.

## What was built

### `weight.md.example` (71 lines)

- 14 entries spanning 2 weeks (2026-05-15 → 2026-05-28).
- Mixed units: 12 × `kg`, 1 × `lb` (bare), 1 × `lbs` (which normalises to `lb`).
- 4 entries carry `| notes: ...` annotations.
- Day 22 entry (`72.4 kg | notes: hotel scale | feels close to home scale`)
  exercises the embedded-pipe-in-notes contract: only the FIRST `| notes:` is
  the split point; subsequent `|` pipes stay verbatim in the notes.
- Parses cleanly through `tempo.analysis.weight.parse_weight`:
  `present=True`, `entries=14`, `malformed_lines=()`.
- Format-section sample lines are shown inline with backticks, NOT in fenced
  code blocks — the parser scans every line for the `- ` bullet prefix and
  doesn't recognise fence boundaries.
- "What NOT to do" examples use a leading `~` (with a one-line note
  explaining the visual substitution) so they don't get parsed as real
  entries. Documents the out-of-range guard, unknown-unit rejection, and
  unparseable-float rejection.
- Latest-wins demo + closing pointer to `docs/WEIGHT.md`.

### `docs/WEIGHT.md` (199 lines)

Mirrors `docs/STRENGTH.md` shape and tone. Sections in order:

1. Title + status + intro (3 paragraphs).
2. **`## Format`** — entry shape, field-by-field grammar (date / weight /
   unit / notes / latest-wins), and the 4-entry CONTEXT example verbatim
   in a fenced code block.
3. **`## Lenient parsing`** — missing-file degradation, malformed-line
   recording, out-of-range sanity check (`20 < kg < 500`), unknown-bullet
   handling, BOM tolerance, the "parser never raises" + "never logs weight
   values" guarantees.
4. **`## Rollup semantics`** — windows (`(today - N, today]` left-open
   right-closed), `avg_7d` / `avg_28d` / `delta_vs_28d` / `days_since_last`
   definitions, EWMA with `alpha = 0.1` seeded from first entry with
   ~7-entry half-life, unit-normalisation rules (kg-normalised numerics,
   `lb * 0.453592`, `latest_entry` preserves original unit, `unit_mixed`
   flag).
5. **`## Recovery report integration`** — 3-state degradation rule (Absent
   → omit / Stale `>14d` → one-line nudge / Current → full rollup line),
   placement (after `## Strength & conditioning`), mixed-unit caveat
   format.
6. **`## Agent-append guidance`** — single-line append at EOF works (no
   parse-then-rewrite), `cat >> weight.md` is safe, latest-wins enables
   append-only corrections, agent SHOULD NOT modify existing lines.
7. **`## What's NOT in this layer`** — deferred features (structured DB
   tables, `tempo weight add` CLI, body composition, scale auto-import,
   `tempo analyze weight` standalone report, goal tracking).

### `README.md` (1 paragraph rewritten, net +1 line)

The "Tracker files" paragraph at line 423 updated to include
`weight.md.example` / `weight.md` alongside the existing
`races.md` / `heat.md` / `strength.md` mentions, with a one-line description
("body-weight context with a 7d/28d/EWMA rollup") and a link to
`docs/WEIGHT.md`. Diff: 7 lines removed, 8 lines added — a targeted prose
update to the same single paragraph (sentence-extension path the plan
explicitly permits in Task 3 step 3). Surrounding sections untouched.

## Verification

- `parse_weight(Path('weight.md.example'))` → `present=True`, 14 entries,
  0 malformed, units = `{kg, lb}`, 4 entries with notes, 1 entry with
  embedded `|` in notes — all goal-backward checks pass.
- `grep -c "^- 2026-" weight.md.example` → 14.
- `grep -c " lbs" weight.md.example` → 1. `grep -cE " lb$| lb " weight.md.example` → 1.
- `grep -c "weight.md" README.md` → 3.
- All required sections present in `docs/WEIGHT.md`: `## Format`,
  `## Lenient parsing`, `## Rollup semantics`,
  `## Recovery report integration`, `## Agent-append guidance`. `alpha` +
  `0.1` both present. `present=False`, `latest-wins`, `kg-normalised`
  vocabulary all present.
- `wc -l docs/WEIGHT.md` → 199 (within 60-200 sanity bound).
- `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`
  → **619 passed, 1 deselected**. No production code or test files touched
  in this plan (all test changes belong to 15-02).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Format-section bullets parsed as entries]**

- **Found during:** Task 1 verification.
- **Issue:** Initial draft of `weight.md.example` had the Format-section
  grammar shown as `-` bullets and the "what NOT to do" examples shown in
  fenced code blocks. The parser's `startswith("- ")` check ignores fence
  boundaries; the example lines (`- <YYYY-MM-DD>: <weight>...`,
  `- 2026-05-14: 7.24 kg`, etc.) were treated as real entries and either
  matched the regex (producing extra parsed rows) or fell into
  `malformed_lines`. First parse: 11 malformed lines.
- **Fix:** Rewrote the Format section to show the entry shape inline with
  backticks (no leading `-`); rewrote the "what NOT to do" section to use
  a visual `~` prefix instead of `-` (with a one-line note explaining the
  substitution); rewrote the latest-wins demo as prose pointing at an
  inline-backticked example. Net effect: `malformed_lines == ()` on clean
  parse, all 14 real entries surface correctly.
- **Files modified:** `weight.md.example`.

### Authentication gates

None.

### Concurrency / commit ordering

The parallel 15-02 executor (running in the same wave per the plan
preflight notes) committed before I could run my own `git commit`. It
captured my staged 15-03 files into its `feat(15-02):` commit (likely via
`git add -A` or `git commit -a`). The three 15-03 deliverables are intact
on main and verify cleanly; only the commit attribution merged. Surfacing
here so the verifier knows there is no separate `docs(15-03):` commit to
look for.

## Files

- **Created:** `weight.md.example` (71 lines), `docs/WEIGHT.md` (199 lines).
- **Modified:** `README.md` (1 paragraph rewritten).

## Commits

- `52ca1be` (bundled with 15-02): `feat(15-02): surface weight rollup in
  the recovery report` — also carries all 15-03 deliverables
  (`weight.md.example`, `docs/WEIGHT.md`, `README.md`).

## Self-Check: PASSED

- weight.md.example: FOUND
- docs/WEIGHT.md: FOUND
- README.md "weight.md" mention: FOUND (3 occurrences)
- 52ca1be: FOUND (carries all three files per `git show --stat`)
