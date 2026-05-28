# Phase 15: Weight Tracker — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline spec from owner (conversation 2026-05-28) — full design locked in advance, no discuss-phase needed. Mirrors Phase 13 (S&C tracker) shape exactly.

<domain>
## Phase Boundary

**What this phase delivers (Layer 1 only):**

- A new `weight.md` tracker file in the content dir (default `<content_root>/weight.md`, redirectable via `TEMPO_CONTENT_DIR` — the owner's working dir is `~/Projects/tempo/training/`).
- A new module `tempo/analysis/weight.py` defining frozen+slots `WeightEntry` / `WeightContext` / `WeightRollup` dataclasses plus a lenient `parse_weight(path)` and a `weight_rollup(entries, today)` function. Mirrors `tempo/analysis/strength.py` and `tempo/analysis/heat.py` exactly in shape.
- `Settings.weight_path` derived property in `tempo/config.py` (mirrors `strength_path` / `heat_path`).
- The recovery analysis (`tempo/analysis/recovery.py`) gains a weight rollup attached to `RecoveryAssessment` (`weight: WeightRollup | None`, `weight_present: bool`) and the renderer gains a `## Weight` section that follows the same 3-state degradation rule heat/strength use (absent → omit / lapsed-but-history-exists → one-line nudge / current → full rollup line).
- `assess_recovery_from_db` accepts an optional `weight_path: Path | None` argument; the analysis runner (`tempo/analysis/runner.py`) threads it through; the CLI (`tempo/cli.py`) passes `settings.weight_path` exactly the way it passes `settings.strength_path` / `settings.heat_path`.
- A committed `weight.md.example` template in the repo root showing 2+ weeks of entries as a worked example.
- A new `docs/WEIGHT.md` documenting the format end-to-end (keys, lenient-parsing contract, agent-append guidance, the 7d/28d/EWMA rollup semantics).
- New tests `tests/test_weight.py` (parser happy/malformed/missing-file paths + rollup window math) + extended `tests/test_recovery.py` covering the recovery-report integration.

**What this phase does NOT deliver (explicitly out of scope, deferred):**

- Structured DB tables for weight entries. The markdown layer must prove useful in real use first.
- Body-fat % / lean-mass / body-composition tracking. Single weight metric only.
- Unit conversion. Weight is stored verbatim with unit annotation (default `kg`); parser accepts `kg` or `lb` but does NOT cross-convert. Rollup arithmetic uses the most recent unit seen and refuses to mix units in the same window (logs a warning + returns `None` for the rollup if mixed).
- A `tempo weight add` CLI command (mirror of `tempo journal add`). Manual markdown edit only; the agent appends directly when asked.
- Auto-import from Withings / Fitbit / Garmin Connect weight scales. Phase 6 Garmin connector does NOT pull body-composition; if it did, that would be a separate phase that derives weight rows into a structured table.
- A weight-trend report (`tempo analyze weight`). The recovery-report section is enough surface; a dedicated report is Layer 2.
- Goal tracking (target weight, weekly delta vs goal). Out of scope. The rollup surfaces the delta vs 28-day baseline; goal-driven analysis is deferred.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### File layout & locations

- All tracker files live in the user's content dir, resolved via `config.content_dir` (or `data_dir` fallback). Same pattern as `races_path` / `heat_path` / `strength_path`.
- New derived path: `weight_path` on `Settings` (`content_root / "weight.md"`).
- Committed `.md.example` template in the repo root (`weight.md.example`), mirroring `strength.md.example`.
- The owner's content dir resolves to `~/Projects/tempo/training/` per their `.env`. The phase MUST NOT hard-code this path; it MUST go through `settings.weight_path`. The real file at `training/weight.md` is gitignored by the existing `training/` rule (already covered).

### `weight.md` format (LOCKED)

One entry per ` - ` bullet line. Lenient throughout: malformed lines are skipped, never raise; missing file → `WeightContext(present=False)`.

```markdown
# Weight log

- 2026-05-28: 72.4 kg | notes: post-run, pre-breakfast
- 2026-05-27: 72.8 kg
- 2026-05-26: 73.1 kg | notes: post-strength session
- 2026-05-25: 72.9 kg
```

**Entry grammar** (` - ` list items):
- `- <YYYY-MM-DD>: <weight> [<unit>] [| notes: <free text>]`
- `<YYYY-MM-DD>` — required, ISO date. Malformed date → line skipped.
- `<weight>` — required, parseable as float. Malformed → line skipped.
- `<unit>` — optional, defaults to `kg`. Accepted: `kg`, `lb`, `lbs`. Stored verbatim as `unit: str` (lowercased, `lbs` → `lb`).
- `| notes: <text>` — optional, everything after `| notes:` is stored verbatim as `notes: str | None`. Multiple `|` pipes after the notes pipe are part of the notes (we only split on the FIRST `| notes:`).

**Header lines** (any line starting with `#`) — silently ignored. Blank lines ignored.

**Latest-wins rule:** if the same date appears more than once, the LAST occurrence in file order wins. Rationale: agents append at the bottom; a manual edit can update an earlier entry by appending a new line with the same date.

**Agent-append guidance** (documented in `docs/WEIGHT.md`):
- Appending a new entry is a single-line operation: write `- YYYY-MM-DD: 72.4 kg | notes: ...\n` at the end of the file. No parse-then-rewrite needed.
- The agent SHOULD NOT modify existing lines. Corrections happen by appending a new line with the same date (latest-wins).
- The format is designed so a `cat >> weight.md` from a shell would work; no atomic rewrite required for appends.

### Dataclasses (LOCKED)

All `@dataclass(frozen=True, slots=True)`, in `tempo/analysis/weight.py`:

```python
@dataclass(frozen=True, slots=True)
class WeightEntry:
    date: date              # ISO date (datetime.date)
    weight: float           # numeric value, verbatim
    unit: str               # "kg" or "lb"
    notes: str | None       # verbatim free-text after "| notes:" or None
    source_line: int        # 1-indexed line number for malformed-line reporting

@dataclass(frozen=True, slots=True)
class WeightContext:
    present: bool                       # False if file missing
    entries: tuple[WeightEntry, ...]    # dedup'd by date, latest-wins, sorted ascending
    path: Path | None                   # the path read (None if present=False)
    malformed_lines: tuple[int, ...]    # 1-indexed line numbers that failed to parse

@dataclass(frozen=True, slots=True)
class WeightRollup:
    latest_entry: WeightEntry | None    # most recent entry (any age)
    latest_kg: float | None             # latest entry's weight converted to kg if needed (lb*0.453592); None if unit mixed-or-unknown
    days_since_last: int | None         # today - latest_entry.date in days; None if no entries
    avg_7d: float | None                # mean of entries in (today-7, today], kg-normalised; None if no entries in window
    avg_28d: float | None               # mean of entries in (today-28, today], kg-normalised; None if no entries
    ewma_trend: float | None            # EWMA with alpha=0.1 over all entries up to today, kg-normalised; None if no entries
    delta_vs_28d: float | None          # latest_kg - avg_28d; None if either is None
    unit_mixed: bool                    # True if entries used both kg and lb (rollup degrades gracefully)
```

### Rollup semantics (LOCKED)

- `today` is the reference date passed in (the CLI / runner passes `date.today()`).
- All averages use `(today - N, today]` left-open right-closed windows so a same-day weigh-in always counts.
- `ewma_trend` is computed over ALL entries up to and including today, in date order, with `alpha = 0.1` (slow trend, ~28-day half-life). Seed = first entry's weight.
- Unit normalisation: every weight is converted to kg before averaging (`lb * 0.453592`). The rollup exposes the kg-normalised numbers in `WeightRollup`. The latest entry's original unit is preserved on `latest_entry`.
- `unit_mixed` flag: True iff the entries used both `kg` and `lb`. The rollup still computes (kg-normalised); the flag is surfaced in the recovery report so the user knows.

### Recovery-report integration (LOCKED)

- `RecoveryAssessment` gains two fields: `weight: WeightRollup | None`, `weight_present: bool`.
- The recovery-report renderer adds a `## Weight` section AFTER the existing `## Strength & conditioning` section, BEFORE any nutrition section (which lands in Phase 16).
- 3-state rule (mirrors heat / strength):
  - **Absent** — `weight_present is False` (file missing) OR `entries` is empty → section omitted entirely.
  - **Stale** — file exists with entries but `days_since_last > 14` → one-line nudge: `## Weight\n_Last weigh-in N days ago — log a current reading to keep the rollup live._`.
  - **Current** — `days_since_last <= 14` → full rollup line:
    ```
    ## Weight
    72.4 kg today · 7d avg 72.6 kg · 28d avg 72.9 kg · trend 72.8 kg · −0.5 kg vs 28d baseline
    ```
    When `unit_mixed=True`, append ` _(mixed kg/lb in log — normalised to kg)_`.
- Same wiring shape Phase 13 used: `analysis/recovery.py::assess_recovery_from_db(weight_path=...)` reads the file, builds the rollup, attaches it; `analysis/runner.py::generate_recovery` accepts + forwards; `analysis/runner.py::generate_all` accepts + forwards; CLI call sites (`analyze recovery` + `analyze all`) pass `settings.weight_path`.

### Lenient-parsing contract (LOCKED)

- Missing file → `WeightContext(present=False, entries=(), path=None, malformed_lines=())`. Never raises.
- Malformed line → skipped, line number captured in `malformed_lines`. NEVER raises.
- Unknown bullet format (anything that doesn't match the entry grammar) → skipped.
- Date parse failures → skipped.
- Float parse failures → skipped.
- Negative / zero / absurdly-large weights (>500 kg or <20 kg) → skipped (sanity check; logged as malformed).
- Unicode-safe; tolerates trailing whitespace; tolerates BOM.

### Test scope (LOCKED)

- `tests/test_weight.py`:
  - `test_parse_weight_missing_file_returns_absent_context`
  - `test_parse_weight_happy_path_kg_and_lb`
  - `test_parse_weight_skips_malformed_dates_and_floats`
  - `test_parse_weight_latest_wins_on_duplicate_date`
  - `test_parse_weight_handles_optional_notes`
  - `test_parse_weight_rejects_out_of_range_weights`
  - `test_parse_weight_ignores_headers_and_blanks`
  - `test_weight_rollup_empty_returns_all_none`
  - `test_weight_rollup_single_entry_today`
  - `test_weight_rollup_7d_28d_windows_left_open`
  - `test_weight_rollup_ewma_seeded_from_first_entry`
  - `test_weight_rollup_unit_mixed_flag_normalises_to_kg`
  - `test_weight_rollup_days_since_last_computed`
- `tests/test_recovery.py` additions:
  - `test_recovery_renderer_omits_weight_section_when_absent`
  - `test_recovery_renderer_emits_stale_nudge_when_last_weigh_in_over_14d`
  - `test_recovery_renderer_emits_full_rollup_when_current`
  - `test_recovery_renderer_appends_mixed_unit_caveat`
- Stdlib + pytest's `tmp_path` only. No new test deps.

### Out-of-band safety items

- Weight is sensitive health data. The `weight.md` file MUST live in the gitignored content dir; the repo MUST NOT contain real entries — only `weight.md.example` with anonymised numbers.
- The parser MUST NOT log weight values. If a malformed-line warning is emitted, it logs the line number only, not the content.

### Code organisation conventions

- New module: `tempo/analysis/weight.py`. Mirror `tempo/analysis/strength.py` and `tempo/analysis/heat.py` structure.
- Tests: `tests/test_weight.py` (parser + rollup). Recovery integration tests go in `tests/test_recovery.py` (extend, don't duplicate).
- `Settings.weight_path` in `tempo/config.py` next to `strength_path`.
- `WeightRollup` import in `tempo/analysis/recovery.py` next to `StrengthRollup`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Direct-mirror references (Phase 13 shipped the exact pattern)

- `tempo/analysis/strength.py` — full template for `weight.py`. Parser shape, dataclass shape, rollup shape, lenient-parsing contract — all mirror this.
- `tempo/analysis/heat.py` — second template. Same lenient contract; older, slightly different rollup.
- `tempo/config.py::Settings.strength_path` — exact pattern for `weight_path` derived property.
- `tempo/analysis/recovery.py` — find the strength integration (added in Phase 13-02). The weight integration goes immediately after, structurally identical.
- `tempo/analysis/runner.py::generate_recovery` + `generate_all` — find the `strength_path` parameter. Add `weight_path` next to it with the same threading.
- `tempo/cli.py` — find the two `analyze recovery` / `analyze all` call sites that pass `settings.strength_path`. Add `settings.weight_path` next to them.
- `tests/test_strength.py` + `tests/test_recovery.py` (the Phase 13-02 additions) — the exact test pattern. Mirror it.
- `strength.md.example` — the template shape. `weight.md.example` follows the same conventions.
- `docs/STRENGTH.md` — the doc shape. `docs/WEIGHT.md` mirrors it (shorter; the format is simpler).

### Settings / config

- `tempo/config.py:1-227` — `Settings` class. `weight_path` joins `strength_path`, `heat_path`, `races_path` as derived properties off `content_root`.

</canonical_refs>

<specifics>
## Specific Ideas

- The owner's working dir is `~/Projects/tempo/training/`. The wizard's content-dir step (Phase 14) writes `TEMPO_CONTENT_DIR` to `.env`; this phase relies on that already being set.
- EWMA alpha=0.1 chosen to match the "slow trend" expectation in the user's spec; the half-life is ~7 entries (so ~7 days if logged daily, ~14 if every other day). Documented in `docs/WEIGHT.md`.
- The unit-mixed caveat in the recovery report is intentionally subtle: a user who logs lb on a hotel scale during travel and kg at home shouldn't see a broken rollup; they should see the normalised number with a "mixed" footnote.
- The `out-of-range` sanity check (20 kg < w < 500 kg) catches typos like `7.24 kg` (decimal slip) and `724 kg` (unit slip). Tight enough to catch real mistakes, wide enough to handle every realistic human weight.

</specifics>

<deferred>
## Deferred Ideas

- `tempo weight add --kg 72.4` CLI — symmetric with `tempo journal add`. Useful if the agent or the user wants a non-markdown path. Layer 2.
- A `tempo analyze weight` standalone report (trend chart, weekly delta, projected weight at goal date). Layer 2.
- Body-composition (body-fat %, lean mass, hydration) — additive columns on `WeightEntry`. Out of scope until needed.
- Withings / Fitbit / Garmin auto-import. Separate phase. Would derive into a structured `weight_entry` table; the markdown rollup would then read from the table instead of the file.
- Goal tracking — target weight + ETA from trend. Useful for cut/recomp planning; deferred until the rollup proves itself.

</deferred>

---

*Phase: 15-weight-tracker*
*Context written from owner's inline spec: 2026-05-28*
