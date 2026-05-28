# Weight log (`weight.md`)

**Status:** Authoritative for Phase 15 (WEIGHT-01 through WEIGHT-05).

RunOS has no weight-tracking UI by design. The owner maintains a hand-edited
`weight.md` markdown file in the content dir (default `<content_root>/weight.md`,
redirect with `RUNOS_CONTENT_DIR`), and RunOS reads it for body-weight context in
the recovery report. The markdown layer comes first by intent: the format has to
prove itself in real use before a structured DB table earns its place (see
"What's NOT in this layer" at the bottom). The Telegram bot may append entries
on request via the same single-line append shape the user uses by hand.

The committed `weight.md.example` at the repo root is both the documentation of
the format and a parser fixture exercised by `tests/test_weight.py`.

---

## Format

One entry per ` - ` bullet line. Lenient throughout: malformed lines are
skipped, never raise.

### Entry shape

```
- <YYYY-MM-DD>: <weight> [<unit>] [| notes: <free text>]
```

- **`<YYYY-MM-DD>`** -- required, ISO date. Malformed date → line skipped.
- **`<weight>`** -- required, parseable as float (`72.4`, `72`, `160.6`).
  Malformed → line skipped.
- **`<unit>`** -- optional. Defaults to `kg` when absent. Accepted forms:
  `kg`, `lb`, `lbs`. `lbs` is normalised to `lb` on the parsed `WeightEntry`.
- **`| notes: <text>`** -- optional. Everything after the FIRST `| notes:`
  on the line is stored verbatim as `notes`. Subsequent `|` pipes stay
  inside the notes (the parser only splits on the first `| notes:`).

Header lines (any line starting with `#`) and blank lines are silently
ignored. Non-bullet prose lines are silently ignored (NOT recorded as
malformed).

### The owner's recent week as a worked example

```markdown
# Weight log

- 2026-05-28: 72.4 kg | notes: post-run, pre-breakfast
- 2026-05-27: 72.8 kg
- 2026-05-26: 73.1 kg | notes: post-strength session
- 2026-05-25: 72.9 kg
```

Parses to four `WeightEntry` rows, sorted ascending by date, all unit=`kg`,
two with `notes`. The committed `weight.md.example` carries 14+ entries over
two weeks with mixed `kg` / `lb` / `lbs` units and a notes line whose body
contains an embedded `|` pipe -- that file doubles as a parser fixture.

### Latest-wins on duplicate dates

If the same date appears more than once, the **LAST occurrence in file order
wins**. This is intentional: the file is treated as append-only by
convention, so a correction is a single new line at the bottom with the same
date -- no need to edit (and risk damaging) the earlier line.

---

## Lenient parsing

The parser is built to **never break** on user-edited markdown. The
guarantees:

- **Missing file** → `WeightContext(present=False, entries=(), path=None, malformed_lines=())`.
  Analyses still run; the recovery report omits the `## Weight` section
  entirely.
- **Malformed line** (bad date, bad float, unknown unit, or out-of-range
  weight) → the 1-indexed line number lands in
  `WeightContext.malformed_lines`. The line is skipped; parsing continues.
- **Out-of-range sanity check** -- a weight whose kg-equivalent is **≤ 20 or
  ≥ 500** is rejected as a typo. This catches `7.24 kg` (decimal slip),
  `724 kg` (unit slip), and `1600 lb` (≈ 726 kg, also too high).
- **Unknown bullet formats** (anything that doesn't match
  `- YYYY-MM-DD: <float> ...`) → recorded as malformed.
- **Non-bullet prose** (paragraphs, list items that don't begin `- `) →
  silently ignored, NOT recorded.
- **BOM + trailing whitespace** → tolerated transparently.

The parser **NEVER raises**. The only failure mode is "this line didn't
make it into the parsed result"; downstream analyses see whatever did
parse.

For privacy, the parser **never logs weight values** -- only line numbers
are surfaced in `malformed_lines`.

---

## Rollup semantics

`weight_rollup(entries, today)` returns a `WeightRollup` summarising the
parsed entries against a reference date (the CLI / runner passes
`date.today()`). Every numeric output is kg-normalised so a hotel-scale lb
reading mixes cleanly with home-scale kg readings.

### Windows

- All averages use **left-open right-closed** windows: `(today - N, today]`.
  A same-day weigh-in counts for every window. `today - N` itself is
  EXCLUDED.
- **`avg_7d`** -- mean of entries with `today - 7 < e.date <= today`,
  kg-normalised. `None` if no entries fall in the window.
- **`avg_28d`** -- same shape, with `today - 28 < e.date <= today`.
- **`delta_vs_28d`** -- `latest_kg - avg_28d`, in kg. `None` if either side
  is `None`.
- **`days_since_last`** -- `(today - latest_entry.date).days` (≥ 0).
- Future-dated entries (typo'd year) are defensively filtered out before
  any window arithmetic.

### EWMA trend

- `ewma_trend` is an **exponentially-weighted moving average** with
  **`alpha = 0.1`**, iterated forward through ALL entries up to `today`,
  in date order.
- The trend is **seeded from the FIRST entry's** kg-normalised weight.
- The half-life is **~7 entries** -- so ~7 days if logged daily, ~14 days
  if every other day. Slow trend by design: it ignores single-day swings
  (post-meal, post-long-run dehydration) and surfaces the underlying
  direction over a couple of weeks.

### Unit normalisation

- Every weight is converted to kg before averaging (`lb * 0.453592`). The
  rollup's numeric fields (`latest_kg`, `avg_7d`, `avg_28d`, `ewma_trend`,
  `delta_vs_28d`) are all kg.
- `latest_entry` preserves the **original unit** so the recovery report
  can render the user's most recent reading in the unit they actually
  logged.
- `unit_mixed` is `True` iff both `kg` and `lb` appear in the entries.
  The flag is purely informational -- the rollup still computes
  kg-normalised numbers cleanly.

---

## Recovery report integration

Parsed entries surface in the recovery report as a `## Weight` section,
placed **after `## Strength & conditioning`** so the report's
non-running-context cluster reads `Heat → Strength → Weight`.

The renderer follows the 3-state degradation rule mirroring `heat.md` and
`strength.md`:

- **Absent** -- file missing (`present=False`) OR zero entries ever → the
  `## Weight` section is omitted entirely.
- **Stale** -- entries exist but `days_since_last > 14` → a one-line nudge:
  `_Last weigh-in N days ago — log a current reading to keep the rollup live._`
- **Current** -- `days_since_last <= 14` → full rollup line:
  `{latest} kg today · 7d avg {a7} kg · 28d avg {a28} kg · trend {ewma} kg · {±X.X} kg vs 28d baseline`.

When `unit_mixed=True`, a trailing caveat is appended:
`_(mixed kg/lb in log — normalised to kg)_`. This is the
"hotel scale in lb during travel" case -- the user sees the kg-normalised
number with an honest footnote, not a broken rollup.

---

## Agent-append guidance

Appending a new entry is a **single-line operation**. Write
`- YYYY-MM-DD: 72.4 kg | notes: ...\n` at the end of the file. There is no
parse-then-rewrite step -- a plain shell `cat >> weight.md` works because
the parser is line-based and order within the file doesn't matter (the
deduper + sorter normalises that at parse time).

To **correct an earlier entry**, append a new line with the same date.
Latest-wins resolves it -- the earlier line stays in the file as a record,
the corrected number is what surfaces in the rollup.

The agent **SHOULD NOT modify existing lines**. Append-only is the
convention; it keeps `weight.md` safe to edit by hand from any editor,
and a `cat >> ...` correction from the Telegram bot can't accidentally
clobber unrelated history.

---

## What's NOT in this layer

Deferred to later phases (per Phase 15 CONTEXT):

- **Structured DB tables** (`weight_entry`) -- the markdown layer must prove
  useful in real use first.
- **`runos weight add --kg 72.4` CLI** -- symmetric with `runos journal add`;
  deferred until the markdown layer proves itself.
- **Body composition** (body-fat %, lean mass, hydration) -- out of scope; a
  single weight metric is the deliberate starting point.
- **Withings / Fitbit / Garmin Connect auto-import** -- separate phase if it
  ever happens.
- **Standalone `runos analyze weight` trend report** -- the recovery-report
  section is enough surface for now.
- **Goal tracking** -- target weight + ETA from EWMA trend; deferred until
  the rollup proves itself.
