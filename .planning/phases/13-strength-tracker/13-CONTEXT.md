# Phase 13: Strength & Conditioning Tracker — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline discussion (conversation 2026-05-28) — owner pasted a Strong-app Tuesday 2026-05-26 session and asked for a "modular" way to track S&C; layered design proposed (markdown tracker → CSV importer → structured tables → analyses); owner approved Layer 1 only.

<domain>
## Phase Boundary

**What this phase delivers (Layer 1 only):**

- A new `strength.md` tracker file in the content dir (default `<content_root>/strength.md`, redirectable via `TEMPO_CONTENT_DIR` — the owner's working dir is `~/Projects/tempo/training/` per existing setup).
- A new module `tempo/analysis/strength.py` defining frozen+slots `StrengthSet` / `StrengthExercise` / `StrengthSession` / `StrengthContext` / `StrengthRollup` dataclasses plus a lenient `parse_strength(path)` and a `strength_rollup(sessions, today)` function. Mirrors `tempo/analysis/heat.py` exactly in shape.
- `Settings.strength_path` derived property in `tempo/config.py` (mirrors `heat_path`).
- The recovery analysis (`tempo/analysis/recovery.py`) gains a strength rollup attached to `RecoveryAssessment` (`strength: StrengthRollup | None`, `strength_present: bool`) and the renderer gains a `## Strength & conditioning` section that follows the same 3-state degradation rule heat already uses (absent → omit / lapsed-but-history-exists → one-line nudge / active → full rollup line).
- `assess_recovery_from_db` accepts an optional `strength_path: Path | None` argument (mirrors `heat_path`); the analysis runner (`tempo/analysis/runner.py`) threads it through; the CLI (`tempo/cli.py`) passes `settings.strength_path` exactly the way it passes `settings.heat_path`.
- A committed `strength.md.example` template in the repo root (alongside `races.md.example` / `heat.md.example`) showing the owner's Tuesday session as the worked example.
- A new `docs/STRENGTH.md` documenting the format end-to-end (keys, sets grammar, superset labels, equipment annotation, lenient-parsing contract, the relationship to Strong-app pasted sessions).
- New tests `tests/test_strength.py` (parser happy/malformed/missing-file paths + rollup window math) + extended `tests/test_recovery.py` covering the recovery-report integration.

**What this phase does NOT deliver (explicitly out of scope, deferred):**

- Structured DB tables for strength sessions / exercises / sets. The markdown layer must prove useful in real use first. If it does, a follow-up phase adds `strength_session` / `strength_exercise` / `strength_set` tables + a transform that rebuilds them from the parsed markdown (rederivable from raw — same invariant as the rest of Tempo).
- A Strong-app CSV importer. Strong exports CSV; a `tempo strength import <csv>` connector is the natural Layer 2 once Layer 1 is exercised in anger. Out of scope for now — the owner is happy hand-maintaining the markdown.
- A `tempo strength add` CLI command (mirror of `tempo journal add`). Maybe later; not needed for the first pass.
- A separate "S&C load" report (tonnage trend over time, set-volume by movement pattern, etc.). Layer 4 of the design. Out of scope.
- 1RM estimation, PR tracking, body-part categories. Strong already does this and Tempo doesn't need to duplicate it.
- Any integration with Strava `WeightTraining` activities (Strong sessions are not in Strava in this owner's setup; this is an additive log).
- Auto-detection of supersets from set timing (`rest: 1:30` is captured as metadata but not used to infer grouping; the owner labels superset groups explicitly with `[A]` / `[B]`).
- A migration tool from any prior shape. There is no prior strength data in Tempo; this is greenfield.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### File layout & locations

- All tracker files live in the user's content dir, resolved via `config.content_dir` (or `data_dir` fallback). Same pattern as `races_path` / `heat_path`.
- New derived path: `strength_path` on `Settings` (`content_root / "strength.md"`).
- Committed `.md.example` template in the repo root (`strength.md.example`), mirroring `races.md.example` / `heat.md.example`.
- The owner's content dir resolves to `~/Projects/tempo/training/` per their `.env` (`TEMPO_CONTENT_DIR=...`). The phase MUST NOT hard-code this path; it MUST go through `settings.strength_path`. The real file at `training/strength.md` is gitignored by the existing `training/` rule (already covered).

### `strength.md` format (LOCKED)

One session per `##` header block. Header line is the only mandatory thing for a session to be parsed; everything else is optional. Lenient throughout: malformed lines/sets are skipped, never raise; missing file → `StrengthContext(present=False)`.

```markdown
# Strength sessions

## 2026-05-26 18:19 — Lower body
rest: 1:30
notes: pogos + SLGB supersetted

- Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8
- Hip Thrust (Barbell): 50x10, 55x10, 55x10, 55x10
- Seated Leg Curl (Machine): 25x12, 30x12, 30x12
- Calf Press (Leg Press): 80x16, 80x16, 80x16, 80x16
- Pogos [A]: 15, 15, 15
- Single Leg Glute Bridge [A]: 8, 8, 8
- Plank: 1:00, 1:00, 1:00, 0:30
```

**Header grammar** (`## ` line):
- Required: leading ISO date `YYYY-MM-DD`.
- Optional: ` HH:MM` (24-hour wall-clock start, stored verbatim in `start_local`; for downstream sorting only, not timezone-projected).
- Optional: ` — Name` (free-form session name; everything after the first em-dash `—` OR ` - ` is the name). Trim whitespace. If absent, name is `None`.

**Metadata lines** (between header and the first ` - ` exercise bullet, any order):
- `rest: M:SS` → stored as `rest_s: int | None` on `StrengthSession` (seconds, e.g. `1:30` → 90). Malformed → `None`.
- `notes: <free text>` → `notes: str | None`. Verbatim.
- Any other `key: value` line → silently ignored (lenient).

**Exercise bullet grammar** (` - ` list items):
- `- <Exercise Name> [(Equipment)] [[GROUP]]: <set>[, <set>]...`
- `<Exercise Name>` — required, free-form text up to the first `(`, `[`, or `:` that introduces an annotation. Trim trailing whitespace.
- `(Equipment)` — optional parenthesised annotation immediately after the name (`Barbell`, `Machine`, `Dumbbell`, `Cable`, `Leg Press`, etc.). Stored verbatim as `equipment: str | None`. If absent → `None`. Bodyweight movements typically have no equipment annotation.
- `[GROUP]` — optional bracketed superset group label, single letter or short token (`A`, `B`, `C1`, etc.). Stored verbatim as `superset_group: str | None`. Two exercises with the same group label in the same session are understood (by the renderer / downstream consumers) to be supersetted; the parser does not enforce this. If absent → `None`.
- `: ` — separator. Required to introduce sets.
- `<set>` — one of three flavours, parsed in this order:
  - **Weighted** `WxR` (e.g. `55x8`, `52.5x10`) — `weight_kg: float, reps: int, duration_s: None`. Lowercase `x` only is fine; uppercase `X` also accepted; `×` (unicode) also accepted. Whitespace around `x` permitted (`55 x 8`).
  - **Timed hold** `M:SS` (e.g. `1:00`, `0:30`, `2:15`) — `weight_kg: None, reps: None, duration_s: int`. Single colon only.
  - **Bodyweight reps** bare integer (e.g. `15`, `8`) — `weight_kg: None, reps: int, duration_s: None`.
- Anything that doesn't match those three is silently skipped (lenient).
- Sets are stored on `StrengthExercise.sets` in source order (ordinal is the list index).

**Section headers / blank lines / prose:**
- A line beginning with `#` that is NOT a session-header `## YYYY-MM-DD ...` is treated as a top-level page header and skipped (e.g. `# Strength sessions`).
- Blank lines are skipped.
- Any non-bullet, non-metadata, non-header line between exercise bullets is treated as prose and skipped (lenient).

**The owner's Tuesday session as a worked parse target:**
- 1 session, header `## 2026-05-26 18:19 — Lower body`, `start_local="18:19"`, `name="Lower body"`, `rest_s=90`, `notes="pogos + SLGB supersetted"`.
- 7 exercises in source order: Romanian Deadlift, Hip Thrust, Seated Leg Curl, Calf Press, Pogos `[A]`, Single Leg Glute Bridge `[A]`, Plank.
- Pogos + Single Leg Glute Bridge both carry `superset_group="A"`.
- Plank's four sets parse as `duration_s=60, 60, 60, 30`.
- Calf Press's four sets parse as `weight_kg=80.0, reps=16` four times.
- Total tonnage (weighted-set sum of weight × reps): 40·8 + 50·8 + 55·7 + 55·8 + 50·10 + 55·10·3 + 25·12 + 30·12·2 + 80·16·4 = 320 + 400 + 385 + 440 + 500 + 1650 + 300 + 720 + 5120 = **9835 kg**. The renderer will round to 4 sig figs ("9.8 t" or "9,835 kg").

### Dataclasses (LOCKED)

All frozen+slots, mirroring `tempo/analysis/heat.py`:

```python
@dataclass(frozen=True, slots=True)
class StrengthSet:
    weight_kg: float | None = None       # None for bodyweight + timed holds
    reps: int | None = None              # None for timed holds
    duration_s: int | None = None        # None for rep-based sets

@dataclass(frozen=True, slots=True)
class StrengthExercise:
    name: str
    equipment: str | None = None
    superset_group: str | None = None
    sets: tuple[StrengthSet, ...] = ()   # tuple for frozen safety

@dataclass(frozen=True, slots=True)
class StrengthSession:
    date: date                            # required; entries without a parseable date are dropped
    start_local: str | None = None        # verbatim wall-clock "HH:MM"
    name: str | None = None
    rest_s: int | None = None
    notes: str | None = None
    exercises: tuple[StrengthExercise, ...] = ()

@dataclass(frozen=True, slots=True)
class StrengthContext:
    present: bool
    sessions: list[StrengthSession] = field(default_factory=list)
    source_path: str | None = None

@dataclass(frozen=True, slots=True)
class StrengthRollup:
    today: date
    last_7d_count: int
    last_7d_tonnage_kg: float           # sum of weight × reps over weighted sets in window
    last_14d_count: int
    last_14d_tonnage_kg: float
    last_28d_count: int
    last_28d_tonnage_kg: float
    last_session_date: date | None
    last_session_days_ago: int | None
    last_session_name: str | None       # for the "last lifted: Lower body (1 day ago)" line
```

### Tonnage rollup semantics

- Tonnage = sum of `weight_kg × reps` over every weighted set (`weight_kg is not None and reps is not None`) inside sessions in the window. Bodyweight + timed sets contribute 0 to tonnage but DO count toward `*_count`.
- Sessions with no weighted sets at all (pure conditioning days) still count toward `*_count`. Last-session-name is the most recent session's `name` (or `"unnamed"` if `None`).
- Windows are inclusive closed intervals, mirroring heat: 7-day window = `[today-6 .. today]`. Future-dated sessions (typo'd year) are defensively filtered.

### Recovery report surfacing

- `RecoveryAssessment` gains two new fields, exactly mirroring the heat additions in Phase 8: `strength: StrengthRollup | None = None`, `strength_present: bool = False`.
- `assess_recovery_from_db` gains a `strength_path: Path | None = None` parameter. When provided, parse + rollup happens after the heat block; result is attached to a re-constructed (frozen) `RecoveryAssessment` (same pattern as the existing heat re-construction at the bottom of `assess_recovery_from_db`). Today-for-rollup MUST align with the recovery report's "as of" day, exactly the way heat does it (use `points[-1].day` if points, else `date.today()`).
- New renderer helper `_render_strength_section(out, strength, strength_present)` in `tempo/analysis/recovery.py`. Mirrors `_render_heat_section`. 3-state rule:
  - `not strength_present` OR `strength is None` → omit entirely.
  - `strength_present` but `last_session_date is None` (file parsed but zero sessions ever) → omit.
  - `strength_present`, `last_session_date is not None`, ALL of `last_7d_count` / `last_14d_count` / `last_28d_count` are 0 → render header + one-line lapsed nudge: `_S&C protocol lapsed — last session N days ago. (No sessions in the last 28 days.)_`
  - Active state (any non-zero count) → render header + active rollup line:
    ```
    ## Strength & conditioning

    - last 7 days: N sessions / X kg tonnage · last 14 days: N sessions / X kg tonnage · last 28 days: N sessions / X kg tonnage · last session: Name (M days ago)
    ```
- Section is placed **after** the heat section in the report so both load-context tracker sections cluster together.
- Tonnage formatting: integer with comma separators for kg under 10,000; tonnes with 1 decimal for ≥ 10,000 (e.g. `9,835 kg`, `12.4 t`). Helper `_fmt_tonnage(kg: float) -> str` in `recovery.py`.

### Wiring through the runner + CLI

- `tempo/analysis/runner.py`: every site that already threads `heat_path` MUST thread `strength_path` next to it (same signature pattern). At time of writing this is `compute_recovery_assessment` (line ~298) and `analyze_all` (line ~370).
- `tempo/cli.py`: every site that already passes `settings.heat_path` MUST pass `settings.strength_path` next to it (lines ~611 and ~680).
- `tempo/config.py`: add `strength_path` derived property mirroring `heat_path` (after `heat_path`, line ~216).

### Lenient-parsing contract (honesty / failure modes — mirror Phase 8)

- Missing `strength.md` → `StrengthContext(present=False)`; recovery report omits the section; analyses still run.
- Malformed sets within a valid exercise bullet → skipped; the exercise still parses with the sets that did parse.
- Exercise bullet with zero parseable sets → exercise is still kept (sets=()) so the user sees a "logged but empty" exercise; the rollup just contributes 0 to tonnage and 1 to count.
- Session header with an unparseable date → the entire session block (everything up to the next `## YYYY-MM-DD` header) is skipped.
- Unknown metadata keys → silently ignored.
- Parser never raises on malformed input.

### Code-organisation conventions

- New module `tempo/analysis/strength.py` — parser + dataclasses + rollup. Mirror `tempo/analysis/heat.py` structure exactly. Keep the same `_parse_kv` helper style (or import from heat if it's identical and re-export is cleaner — planner's call).
- Tests under `tests/test_strength.py`. Mirror `tests/test_heat.py` (`TODAY` fixture, `_session()` builder, parse-then-rollup integration test).
- Recovery integration tests added to `tests/test_recovery.py`. Mirror the heat integration tests already there (search for "heat" to find them).
- Set-string parser MUST be its own function (`_parse_set(token: str) -> StrengthSet | None`) so it can be unit-tested in isolation against every flavour.

### Migration / one-time user actions

- The owner's existing Tuesday session content (pasted in conversation) is the seed for the committed `strength.md.example` template. The owner will create their real `training/strength.md` by copying the example and appending sessions over time. There is no migration tool.
- The owner's existing `.env` already sets `TEMPO_CONTENT_DIR=~/Projects/tempo/training` (or similar — confirmed by the fact that `training/races.md` is the live file). No `.env.example` change required (no new env var; `strength_path` is derived).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The pattern to follow exactly

- `tempo/analysis/heat.py` (lines 1-246) — full reference. The new `tempo/analysis/strength.py` mirrors this module's shape: lenient parser, frozen+slots dataclasses, rolling-window rollup with closed intervals.
- `tests/test_heat.py` (full file) — the test shape to follow. Fixed `TODAY` for deterministic windows; happy-path / malformed / missing-file / rollup integration tests.

### Config wiring

- `tempo/config.py:208-216` — `races_path` and `heat_path` derived properties. Add `strength_path` right after `heat_path`.

### Recovery integration (where strength plugs in)

- `tempo/analysis/recovery.py:232-260` — `RecoveryAssessment` dataclass with the existing `heat` / `heat_present` fields. Add `strength` / `strength_present` the same way.
- `tempo/analysis/recovery.py:389-441` — `assess_recovery_from_db` with the heat re-construction at the bottom. Strength block goes after the heat block; the function returns a single re-constructed assessment with both attached.
- `tempo/analysis/recovery.py:458-500` — `_render_heat_section`. Mirror as `_render_strength_section` below it; call it from `render_recovery` after the heat section call (~line 551).

### Runner + CLI wiring

- `tempo/analysis/runner.py:263-313` — `compute_recovery_assessment` signature with `heat_path: Path`. Add `strength_path: Path | None = None` (default None so existing callers don't break during transition; the CLI WILL pass it though).
- `tempo/analysis/runner.py:370-395` — `analyze_all` signature. Same shape.
- `tempo/cli.py:611, 680` — the two call sites that pass `heat_path=settings.heat_path`. Add `strength_path=settings.strength_path` next to each.

### Existing committed examples

- `races.md.example` and `heat.md.example` (repo root, committed) — pattern for `strength.md.example`. Keep the same tone: intro paragraph + Format section listing keys + Sessions section with worked examples. Show the owner's Tuesday session as the lead example.

### Existing docs convention

- `docs/JOURNALING.md` and `docs/DATE_BUCKETING.md` — the docs/ tone. `docs/STRENGTH.md` follows the same shape: format spec, parsing contract, integration touchpoints (which report consumes it).

### REQUIREMENTS / ROADMAP entries to satisfy

- `.planning/REQUIREMENTS.md` — SC-01 through SC-05 (already added in this phase's setup commit).
- `.planning/ROADMAP.md` — Phase 13 entry with 5 success criteria (already added).

</canonical_refs>

<specifics>
## Specific Ideas

- The owner trains S&C 1-2x per week on average (heat.md is sparse for comparable reason). The rollup windows (7/14/28d) match the heat rollup so the recovery report has visual consistency between the two adjacent sections.
- The Tuesday 2026-05-26 session includes a `tests: all 1:30` annotation from the user's paste — that maps to `rest: 1:30` metadata. The example template should make this mapping obvious.
- The user said "the pogos and the single leg glute bridges were superset" — this is the `[A]` group label convention. The example needs to make this explicit because it's the one non-obvious bit of the format.
- Strong-app pastes have a recognisable shape (`Set 1: 40 kg × 8`). The owner's CONTEXT explicitly approves a non-Strong-mirroring compact format (`WxR`) because the markdown is hand-maintained and the compact form fits one bullet per exercise. The Strong paste is the source material, not the storage format.
- Tonnage is an imperfect proxy for S&C load (a 4×8 squat at 100 kg has the same tonnage as a 4×8 RDL at 100 kg, despite very different stress patterns). It is still useful as a "did I lift, and roughly how much" signal alongside count + last-session age. Don't oversell it in the renderer copy — keep the line factual.

</specifics>

<deferred>
## Deferred Ideas (Layer 2+ of the modular design)

- **Layer 2 — Structured DB tables.** `strength_session` / `strength_exercise` / `strength_set` tables with a `migrations/0006_strength.sql` migration and a `tempo/transforms/strength.py` pure transform that rebuilds them from the parsed markdown. Rederivable from raw (the markdown becomes the "raw" source for this domain). Adds queryability (`SELECT * FROM strength_session WHERE day BETWEEN ...`) and unlocks Layer 4.
- **Layer 2b — Strong-app CSV importer.** `tempo strength import <csv>` connector following the `Connector` protocol shape. Writes to `raw_response` (`source='strong'`); a transform turns it into `strength_session` rows. Useful if hand-maintaining `strength.md` becomes painful.
- **Layer 3 — Validated CLI boundary.** `tempo strength add-session ...` mirror of `tempo journal add`. Lets the bot capture lifts via the Claude Code agent loop (`Hey Claude, log my lift: 4x8 RDL at 55, 4x10 hip thrust at 55, ...`).
- **Layer 4 — Standalone S&C report.** Weekly tonnage trend, set-volume by movement pattern (push / pull / hinge / squat / carry / core), PR detection, RDL/squat/hinge stress balance. Pure stdlib like the existing analyses. Only sensible after Layer 2 lands.
- **Layer 4b — S&C ↔ run-feel correlations.** Extend `tempo/analysis/correlation.py` with strength-day lookups: "next-day HRV after a heavy leg day", "RPE on runs within 24h of a hinge-dominant lift". Honest correlation (insufficient-data fallback) like the existing one.
- **1RM estimation / PR tracking / movement-pattern categorisation.** Strong already does this. Tempo doesn't need to compete.
- **Strava `WeightTraining` activity ↔ strength session auto-link** (mirroring the Phase 8 race ↔ activity link). Only useful if the owner starts logging lifts to Strava too. Currently they don't.
- **Heat + strength combined "non-running stress" rollup.** Single number that combines heat exposure minutes and strength tonnage as a generic "non-running load" proxy. Probably misleading — leave it.
- **Equipment / exercise canonicalisation.** "Romanian Deadlift" vs "RDL" vs "Romanian DL" should all map to the same canonical exercise. Painful + not useful until Layer 4 wants to group by exercise. Out of scope.

</deferred>

---

*Phase: 13-strength-tracker*
*Context gathered: 2026-05-28 via inline discussion*
