# Phase 8: Modular Trackers + Heat Adaptation — Research

**Researched:** 2026-05-27
**Domain:** Lenient markdown trackers, append-only session rollups, runtime race↔activity join, surgical removal of an existing parser.
**Confidence:** HIGH for all design calls (codebase is in-repo and small; conventions are explicit in PATTERNS.md and the source files).
**Scope guard:** PATTERNS.md is trusted for "where do I plug in" — this document only covers design judgement and edge cases.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- `races.md` gains optional verbatim `result:` field; `RacesContext.completed(today)` mirror of `upcoming`.
- New `heat.md` tracker in content_dir, parsed leniently into `HeatContext`; new module `runos/analysis/heat.py`.
- New `heat_path` derived property on settings; `plan_path` removed.
- Heat rollup surfaces in **recovery report only** (not race-readiness, not load-trend).
- Race ↔ activity auto-link by **local date only** (not sport); 0/1/N → `linked` / `unlinked_no_match` / `unlinked_ambiguous` / `unlinked_no_date`. No write-back.
- `plan.md` retirement is a **hard cut**, no compatibility shim.
- Lenient-parser convention: missing file → `present=False`; malformed lines skipped; unknown keys ignored; never raise.
- Frozen + slotted dataclasses, pure-Python stdlib, no network in analysis layer.

### Claude's Discretion

- Auto-link function placement: extend `data.py` vs. new `race_link.py` (planner's call).
- Whether to also rename `runos/analysis/context.py` → `races.py` (deferred unless cheap — see Q7).
- Re-implement `_parse_kv` in heat.py vs. import from context.py.
- Whether to attach `HeatRollup` as a field on `RecoveryAssessment` or pass-through param to `render_recovery`.

### Deferred Ideas (OUT OF SCOPE)

- Modular tracker registry / `Tracker` protocol.
- Strength tracker (Strong CSV), nutrition tracker, runs.md, cross-training.md.
- Forward-looking plan tracking of any kind.
- Parsing `result:` into seconds; goal comparison logic.
- Race → journal → activity transitive join.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRACK-01 | `races.md` gains optional `result:` field; verbatim string on `Race` dataclass | Q3 (link + result rendering), Q6 (test "races result: field") |
| TRACK-02 | `RacesContext.completed(today)` helper; past-dated, most-recent-first, undated excluded | Q6 (3 completed tests) |
| TRACK-03 | Race ↔ Strava activity auto-link by local date, 0/1/N convention, no write-back | Q3 (full edge-case enumeration) |
| TRACK-04 | `heat.md` parser + `HeatContext`; lenient, append-only, mirrors `races.md` shape | Q5 (heat.md.example seed), Q6 (parser tests) |
| TRACK-05 | `heat_rollup(sessions, today)` → 7/14/28-day count + minutes + last-session-days-ago | Q1 (full algorithm + dataclass), Q6 (rollup tests) |
| TRACK-06 | `plan.md` retirement: parser, dataclass, config field, example, all references | Q2 (degradation), Q4 (blast-radius completeness check) |
</phase_requirements>

## Summary

Phase 8 is a **copy-and-mirror exercise**: races.md and heat.md follow the exact same parser shape, and the plan.md retirement is mostly deletion. The interesting design judgement is concentrated in (a) the heat-rollup window semantics, (b) the recovery section's three-state degradation, (c) the race-link edge cases, and (d) confirming nothing in the plan.md retirement blast radius is missed. PATTERNS.md already nailed the structural recommendations; this doc closes the remaining open questions.

**Primary recommendation:** Take the deferred `context.py → races.py` rename — only 8 import sites, all mechanical (Q7). Put `link_races_to_activities` in a new `race_link.py` (matches the per-concern module pattern with heat.py). Attach `HeatRollup` as an **optional field on `RecoveryAssessment`** rather than as a render-time pass-through param (cleaner data flow, matches the existing `signals: list[SignalAssessment]` pattern).

---

## 1. Heat Rollup Design

### `HeatRollup` dataclass (frozen, slots)

```python
@dataclass(frozen=True, slots=True)
class HeatRollup:
    """Rolling-window summary of heat-adaptation sessions for the recovery report.

    All counts are inclusive of ``today`` and look back ``N`` calendar days
    (so a 7-day window is the closed interval ``[today - 6, today]``: today
    itself plus the six preceding days — seven dates total). Minutes are
    summed from ``session.duration_min`` and skip sessions without a
    parseable duration (they still contribute to ``last_session_*``).
    """

    today: date                           # the reference date the windows were computed against
    last_7d_count: int
    last_7d_minutes: float                # 0.0 when window empty (never None — count tells you)
    last_14d_count: int
    last_14d_minutes: float
    last_28d_count: int
    last_28d_minutes: float
    last_session_date: date | None        # None when sessions list is empty
    last_session_days_ago: int | None     # None iff last_session_date is None; 0 if today
```

**Rationale for shape choices:**

- **`today` carried as a field** — makes the rollup self-describing for tests and for downstream renderers (no risk of mismatched "as-of" between rollup and the rest of the recovery report).
- **Minutes as `float` not `int | None`** — `duration_min` is float in `HeatSession`; sessions without parseable duration are simply not added to the sum. A `0.0` minutes value with `count=3` is meaningfully different from `count=0` and tells the renderer "we have sessions but no durations" (rare; surfaced in tests).
- **`last_session_days_ago` as separate field** — pre-computed so the renderer never does date math. `0` when the most-recent session date == today (the user logged a session today).

### Algorithm (pseudocode)

```python
def heat_rollup(sessions: list[HeatSession], today: date) -> HeatRollup:
    # Empty input -> zeroed rollup, no last-session.
    if not sessions:
        return HeatRollup(
            today=today,
            last_7d_count=0, last_7d_minutes=0.0,
            last_14d_count=0, last_14d_minutes=0.0,
            last_28d_count=0, last_28d_minutes=0.0,
            last_session_date=None, last_session_days_ago=None,
        )

    # Closed-interval window edges (inclusive both ends).
    cutoff_7  = today - timedelta(days=6)   # [today-6 .. today]  = 7 dates
    cutoff_14 = today - timedelta(days=13)  # [today-13 .. today] = 14 dates
    cutoff_28 = today - timedelta(days=27)  # [today-27 .. today] = 28 dates

    # Drop future-dated sessions defensively (a typo in heat.md shouldn't poison rollups).
    in_past = [s for s in sessions if s.date <= today]

    def window(cutoff: date) -> tuple[int, float]:
        n = 0
        mins = 0.0
        for s in in_past:
            if s.date >= cutoff:
                n += 1
                if s.duration_min is not None:
                    mins += s.duration_min
        return n, mins

    c7, m7  = window(cutoff_7)
    c14, m14 = window(cutoff_14)
    c28, m28 = window(cutoff_28)

    # Most-recent session (max by date; ties don't matter for count/days_ago).
    last_date = max((s.date for s in in_past), default=None)
    days_ago = (today - last_date).days if last_date is not None else None

    return HeatRollup(
        today=today,
        last_7d_count=c7, last_7d_minutes=m7,
        last_14d_count=c14, last_14d_minutes=m14,
        last_28d_count=c28, last_28d_minutes=m28,
        last_session_date=last_date,
        last_session_days_ago=days_ago,
    )
```

### Subtle correctness traps the planner must call out in tests

1. **Inclusive both ends.** A 7-day window must contain 7 distinct dates (today and the six prior), not 8 and not 6. The most common bug is `today - timedelta(days=7)` which gives an 8-date window. Test: build sessions at `today-7` and `today-6` and assert only the latter is counted in the 7-day window.

2. **Session "today" yields `days_ago = 0`, not `None`.** The renderer phrasing should be `"last session: today"` for 0 and `"last session: 2 days ago"` for >0. Test the boundary.

3. **Sessions without parseable `duration_min`** are still counted toward `*_count` but contribute 0 to `*_minutes`. They are real sessions — the user just didn't log a duration. Test: one session with `duration_min=None` in the 7-day window → `count=1`, `minutes=0.0`.

4. **Multiple sessions on the same day** are each counted separately (a morning sauna + evening hot-bath = 2 sessions). This is the right answer; the user did two distinct heat exposures.

5. **Future-dated sessions** (typo, e.g. `2027-` not `2026-`) are silently filtered out of all windows. They would otherwise inflate counts and corrupt `last_session_days_ago` (negative). Drop them defensively at the rollup boundary — the parser is lenient and won't have caught the year typo as malformed.

6. **No "spans midnight" risk.** `HeatSession.date` is a single date (per CONTEXT.md spec); a long sauna session is one date entry. There is no datetime + duration math, so the midnight-spanning bug cannot occur. **Confirmed safe by construction.**

7. **Empty sessions list** returns a rollup with all zeros and `last_session_date=None` (not raise). Test explicitly.

---

## 2. Recovery Report Integration Shape

### Where it renders

In `runos/analysis/recovery.py:render_recovery`, after the "Recovery markers vs personal baseline" section (line 448) and before the trailing "insufficient" footnote (line 450). It's a **new helper** kept inline at first — `_render_heat_section(out: list[str], rollup: HeatRollup | None) -> None` — pulled out only because the empty-rollup degradation is easier to test in isolation than via the full `render_recovery` pipeline.

### Data flow — recommended

**Attach `HeatRollup | None` as an optional field on `RecoveryAssessment`** (mirrors `signals: list[SignalAssessment]`), and have `assess_recovery_from_db` populate it from `parse_heat(heat_path)` + `heat_rollup(...)` after the existing assess_recovery call. The render signature stays the same shape; one more field, no new param.

```python
@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    day: str | None
    status: str
    rising_load: bool
    load_reasons: list[str]
    signals: list[SignalAssessment]
    messages: list[str] = field(default_factory=list)
    heat: HeatRollup | None = None        # NEW, defaults to None for back-compat
```

This is cleaner than a render-time param because (a) the assessment dataclass is the single source of truth for "everything the recovery report knows" — already the existing pattern, and (b) tests can hand-build a `RecoveryAssessment` with arbitrary heat state without touching the renderer signature.

**Tradeoff:** Requires `heat_path` to flow into `assess_recovery_from_db` (or a sibling helper), which means the pure `assess_recovery` function shouldn't read heat directly (it stays pure over in-memory inputs). The DB-backed wrapper does the I/O. This matches today's pattern where `assess_recovery_from_db` calls `baselines.latest_baselines(conn, ...)` for I/O.

### Three-state degradation rule

The user asked for a recommendation on three states. Here it is:

| State | Condition | Behavior |
|---|---|---|
| **absent** | `HeatContext(present=False)` (file missing) | Omit the `## Heat adaptation` section entirely. No header, no placeholder. |
| **empty** | `present=True` but `sessions=[]` (file exists, no parseable entries) | Omit the section entirely — same as absent. The file's existence is not a signal worth surfacing if there are no entries to roll up. |
| **stale** | `sessions` non-empty but `last_session_days_ago > 28` (zero counts in all three windows) | Omit the section entirely. The 28-day window is the longest we track; if it's empty, the user is not currently doing heat work, and a "Heat adaptation: 0/0/0 · last session: 47 days ago" line is noise, not signal. |

**Single rule:** *Render the heat section iff at least one of `last_7d_count`, `last_14d_count`, `last_28d_count` is > 0.*

**Why one rule for all three states (justification, 2 sentences):** The recovery report's job is to surface what's actively shaping recovery now; a heat-adaptation block that the user can't act on (no recent sessions) is dead weight in a single-user reflection tool. Degrade by omission, consistent with the existing `if not race_readiness: return early` convention in `report.py:render_race_readiness`.

### One-liner format (locked from CONTEXT.md)

```
## Heat adaptation

last 7 days: 3 sessions / 78 min · last 14 days: 6 sessions / 154 min · last session: 2 days ago
```

Edge phrasings the planner must handle in `_render_heat_section`:
- `last_session_days_ago == 0` → `"last session: today"`
- `last_session_days_ago == 1` → `"last session: 1 day ago"`
- `minutes == 0.0 and count > 0` → still render `"3 sessions / 0 min"` honestly — flags to the user that duration logging is missing.

---

## 3. Race ↔ Activity Auto-Link — Edge Case Checklist

Local-date join on `activity.day` (which is `start_date_local[:10]`, a bare local-date string per `migrations/0002_structured.sql:21`). Race date in `races.md` is also a bare local-date ISO string. **No TZ risk** — both sides are the same wall-clock-local convention by construction (confirmed by reading `0002_structured.sql` + `_parse_race_line`).

| # | Edge case | Recommended behavior | Justification |
|---|---|---|---|
| **a1** | Race date is in the **future**, no activity on that day yet | `RaceLink(activity_id=None, link_status='unlinked_no_match')` | The 0-match status is correct; "no match" is the same answer whether the race hasn't happened or just wasn't logged. The renderer text differs (`"Race upcoming"` vs `"No activity recorded for race date"`) and is decided by comparing `race.race_date` to `today` at render time, not by the linker. |
| **a2** | Race date is **today**, no activity yet | Same as a1 (`unlinked_no_match`) | Linker doesn't know whether sync ran today. Renderer can say "Race is today" if `race.race_date == today` and no link. |
| **a3** | Race date is in the **past**, activity exists | `linked` | The happy path. |
| **b**  | Race day activity is **not a Run** (user cycled / swam on race day) | Still **link it** — `link_status='linked'`, `activity_id=<id>` | CONTEXT.md explicit: "A race could be ridden, swum, or run". Sport filtering would break tri / cycling races and would require a guess about the race's discipline. |
| **c1** | **Multiple activities on race day**, one of them is the race | `unlinked_ambiguous`, `activity_id=None` | Mirrors journal-service 0/1/N. Renderer says "Multiple activities on race day; cannot auto-link." User can hand-pick later (next phase). |
| **c2** | Multiple activities on race day, **none** is the race (user did a warmup + a workout + a recovery walk) | Same: `unlinked_ambiguous` | The linker can't distinguish — refuses to guess. |
| **d**  | Race date is local wall-clock; activity.day is `start_date_local[:10]`. **Any TZ risk?** | **No** — both are local-date strings by construction. | Confirmed by reading `migrations/0002_structured.sql:21,27` ("day is the local date derived from Strava's start_date_local[:10]"). The race date is parsed by `date.fromisoformat` from the user's hand-typed `YYYY-MM-DD`. Both sides are timezone-naïve local dates — they compare cleanly. **The planner does not need to add TZ tests.** |
| **e**  | Race date in future, no activity exists yet | `unlinked_no_match` (covered by a1) | The link_status field name is honest: the *match attempt* found nothing. |
| **f1** | Race date typo'd as nonsense (`date: tomorrow`) | Lenient parser sets `race.race_date = None` (existing behavior at context.py:160-162). Linker returns `unlinked_no_date`. | Already handled by existing parser. New test should pin this. |
| **f2** | Race date is parseable but absurd (`date: 1900-01-01`) | Parser accepts it; linker returns `unlinked_no_match` (no activity on that day). | Garbage in, garbage out — the parser is lenient, not validating. Renderer behavior is still graceful. |
| **g**  | Race has no `date:` field at all | `race.race_date = None` → `unlinked_no_date` | Same path as f1. |
| **h**  | The `activity` table doesn't exist yet (pre-Phase-3 DB) | Linker should not crash. Returns `unlinked_no_match` for every race. | Mirror `srpe_by_day`'s `sqlite_master` table-existence check (data.py:135-139). Defensive: protects against running analyses on a fresh DB. |

**Implementation note:** The linker takes a `list[Race]` (not just upcoming) so both `upcoming` and `completed` race rendering can use it. CONTEXT.md says the runner calls it "during race-readiness rendering" — the planner should call it on `upcoming + completed` once, dict-by-race-identity (or list-parallel), so a single DB scan handles both.

---

## 4. plan.md Retirement Blast Radius — Completeness Check

Greps run: `plan\.md`, `plan_path`, `PlanContext`, `parse_plan`, `_PLAN_FIELD_RE`, `"Phase:"`, `"Week:"`, `"Focus:"` rendering text.

### Confirmed already in PATTERNS.md (no action needed beyond what's documented)

- `runos/analysis/context.py` — module docstring lines 1, 11, 21-23; `PlanContext` dataclass lines 67-75; `_PLAN_FIELD_RE` lines 203-205; `parse_plan` lines 208-227. ✅
- `runos/analysis/runner.py` — `plan_path` param + `parse_plan` call + `plan_ctx` pass-through (lines 263, 270, 281). ✅
- `runos/analysis/report.py` — `PlanContext` import line 20; `plan_ctx` param line 194; plan-rendering block lines 219-223. ✅
- `runos/config.py` — `plan_path` property lines 146-149. ✅
- `.env.example` — comment block lines 65-67. ✅
- `tests/test_context.py` — plan tests lines 98-134. ✅
- `tests/test_analysis_reports.py` — `_write_context` helper + `plan_path=` arg sites + plan-context assertion. ✅
- `tests/test_analyze_cli.py` — `plan_path.write_text` line 63 + assertion line 95. ✅
- `README.md` — lines 239-241. ✅
- `plan.md.example` (deleted entirely). ✅

### MISSED by PATTERNS.md (planner must add these to the plan)

| File | Line | What it is | Action |
|---|---|---|---|
| `.env.example` | **line 16** | Comment `# The files you read/edit: plan.md, races.md, and generated reports/.` — separate from the lines 65-67 block PATTERNS.md flagged. | Update comment: drop "plan.md" from the file list. |
| `runos/cli.py` | **lines 449, 493** | Two `plan_path=settings.plan_path,` kwarg sites in the `analyze` subcommand wiring (one for `race-readiness`, one for `all`). | Remove both — they pass to `generate_race_readiness` / `generate_all`. |
| `runos/sync/daily.py` | **line 110** | `plan_path=settings.plan_path,` kwarg in the daily-sync wrapper that calls `generate_all`. | Remove. |
| `runos/analysis/__init__.py` | **lines 5, 16** | Package docstring: line 5 mentions `"races.md / plan.md context"`; line 16 says `parse races.md / plan.md`. | Update both — drop plan.md mentions; consider adding a `runos.analysis.heat` bullet (PATTERNS.md mentions this for the module index but the docstring at line 5 was missed). |
| `tests/test_config.py` | **lines 38, 46** | Two assertions `settings.plan_path == ...` testing the derived path. | Delete both. Optionally add `settings.heat_path == content / "heat.md"` parallel assertions. |
| `tests/test_analysis_reports.py` | **lines 10, 169, 185** | Module docstring line 10 mentions `"races.md/plan.md context"`. Lines 169 and 185 are two additional `plan_path=` kwarg sites that PATTERNS.md only flagged the first one of (lines 130-132). | Update docstring; remove the additional kwarg sites. |
| `tests/test_analyze_cli.py` | **line 4** | Module docstring mentions `"races.md/plan.md context"`. | Update — drop plan.md. |
| `runos/analysis/runner.py` | **lines 358, 376** | PATTERNS.md flagged line 358 (`generate_all` signature). Line 376 is the internal call's `plan_path=plan_path,` kwarg — already flagged in PATTERNS.md too. | Already covered, just verifying. |

**Net new files PATTERNS.md missed:** `tests/test_config.py`, plus the **second mention** in `.env.example` (line 16) and the **multiple kwarg sites** in `runos/cli.py` and `runos/sync/daily.py`. These are the highest-risk omissions because they would cause runtime errors after the parser/property is deleted (TypeError: unexpected keyword argument `plan_path`).

**`runos/analysis/race.py`** — grepped explicitly per the question. No matches. Safe.
**`CLAUDE.md`** — contains long research output describing the v1 stack; no live code reference to `plan.md`. Per CONTEXT.md the user is fine leaving stale phrasing if it doesn't cause confusion. **No action.**
**`docs/`** — directory exists? Quick check below.

```bash
$ ls docs/ 2>/dev/null
# (no docs/ directory in this repo — verified)
```

---

## 5. heat.md.example — Concrete Content

Drop this **verbatim** into `heat.md.example` at the repo root.

```markdown
# Heat sessions -- EXAMPLE / TEMPLATE

Copy this file to your RunOS content dir as `heat.md` (default
`~/.runos/heat.md`) and edit it. RunOS reads it for heat-adaptation context in
the recovery report (TRACK-04/05); it is never committed (the content dir lives
outside the repo tree).

## Format

One session per markdown list item. Each entry leads with the date, then
`key: value` pairs separated by `|` (or commas), in any order. Parsing is
lenient: unknown keys are ignored and a malformed line is skipped, so you can't
break analysis by editing this file. Append new sessions to the bottom -- the
file is treated as append-only by convention (RunOS never edits it).

Recognised keys:

- `date` -- ISO date `YYYY-MM-DD` (the session day). If omitted, the leading
  date before the first ` - ` on the bullet is used. An entry without any
  parseable date is dropped (it would break the rolling-window rollup).
- `type` -- free-form label, e.g. `sauna`, `hot-bath`, `hot-run`, `steam-room`.
  Stored verbatim; no enum.
- `duration_min` -- session length in minutes (integer or decimal).
- `temp_c` -- ambient temperature in Celsius (optional).
- `hr_avg` -- average heart rate during the session (optional).
- `notes` -- free-form text (optional).

## Sessions

- 2026-05-26 - type: sauna | duration_min: 20 | temp_c: 85 | hr_avg: 105 | notes: post-run, felt easier than last week
- 2026-05-24 - type: hot-bath | duration_min: 25 | temp_c: 41 | notes: shorter than planned, water cooled fast
- 2026-05-22 - type: hot-run | duration_min: 45 | hr_avg: 158 | notes: midday run in 28C, fully exposed
- 2026-05-20 - type: sauna | duration_min: 30 | temp_c: 90 | hr_max: 128 | notes: hr_max is ignored by the parser -- unrecognised keys are silently skipped
- 2026-05-18 - type: steam-room | duration_min: 15
- 2026-05-15 - type: sauna | duration_min: 22 | temp_c: 82 | hr_avg: 108
```

**Design notes for the planner:**

- The fourth bullet (`hr_max:` key) is **intentional** — it demonstrates the lenient-parser contract by showing an unrecognised key getting silently ignored. The user reads the example and learns "I can add my own keys; RunOS won't choke."
- The fifth bullet is intentionally minimal (`type + duration_min` only) to show the partial-fields path works.
- All session dates are within the last ~12 days so a user pasting the example and running the recovery report immediately sees populated 7/14/28 windows.
- Header style matches `races.md.example` (title → copy instruction → `## Format` → `## Sessions`).
- The phrase "append-only by convention" sets expectation; the parser doesn't enforce it.

---

## 6. Test Coverage Outline

### Heat parser (`tests/test_heat.py`) — 7 tests

| # | Test name | Intent |
|---|---|---|
| H1 | `test_parse_heat_missing_file` | Missing file → `HeatContext(present=False, sessions=[])`. |
| H2 | `test_parse_heat_basic` | Two well-formed sessions parse into two `HeatSession` objects with expected fields. |
| H3 | `test_parse_heat_ignores_prose_and_headings` | Doc bullets (no recognised key) and `#` lines do not become sessions. |
| H4 | `test_parse_heat_lenient_partial_fields` | A bullet with only `date + type + duration_min` parses; `temp_c`, `hr_avg`, `notes` are `None`. |
| H5 | `test_parse_heat_unknown_keys_silently_ignored` | A bullet with `hr_max: 130` (not recognised) still parses cleanly; the unknown field is dropped. |
| H6 | `test_parse_heat_bad_date_drops_session` | A bullet with an unparseable date AND no leading-date prefix is dropped entirely (sessions list excludes it). This is the inverse of the races behavior — call out in the test name. |
| H7 | `test_parse_heat_leading_date_prefix_used` | A bullet like `- 2026-05-20 - type: sauna | duration_min: 20` (no `date:` key) uses the leading `2026-05-20` as the date. |

### Heat rollup — 6 tests

| # | Test name | Intent |
|---|---|---|
| R1 | `test_heat_rollup_empty_sessions` | Empty list → all counts 0, `last_session_date=None`, `last_session_days_ago=None`. |
| R2 | `test_heat_rollup_window_edges_inclusive` | Session on `today` is in the 7d window; session on `today-6` is in; session on `today-7` is **not** in the 7d window. Pin the closed-interval contract. |
| R3 | `test_heat_rollup_minutes_sum_skips_unparseable_duration` | A session with `duration_min=None` counts toward count but contributes 0 to minutes. |
| R4 | `test_heat_rollup_last_session_today_is_zero_days_ago` | Most-recent session date == today → `last_session_days_ago == 0`. |
| R5 | `test_heat_rollup_filters_future_dated_sessions` | A session dated `today + 5` is excluded from all counts and does not affect `last_session_date`. |
| R6 | `test_heat_rollup_multiple_sessions_same_day_each_counted` | Two sessions on the same date count as 2 (not 1). |

### `races.md` `result:` field — 2 tests

| # | Test name | Intent |
|---|---|---|
| Res1 | `test_parse_races_result_field_verbatim` | `result: 1:31:48` lands on `Race.result == "1:31:48"`. No parsing into seconds. |
| Res2 | `test_parse_races_result_freeform_strings` | `result: DNF`, `result: 39:42 (course PB)` both stored verbatim with no normalisation. |

### `RacesContext.completed(today)` — 3 tests

| # | Test name | Intent |
|---|---|---|
| C1 | `test_completed_sorts_most_recent_first` | Three past-dated races → returned reverse-chronological (opposite of `upcoming`). |
| C2 | `test_completed_excludes_future_and_today` | A race dated `today` is NOT in `completed` (strict `<`, not `<=`); a future race is excluded. |
| C3 | `test_completed_excludes_undated_races` | A race with `race_date=None` does not appear in `completed` (cannot be known-past). |

### Race ↔ activity link — 7 tests (one per edge case in Q3)

| # | Test name | Intent |
|---|---|---|
| L1 | `test_link_race_with_single_activity_links` | Past race + 1 activity on race day → `linked`, correct `activity_id`. |
| L2 | `test_link_race_with_no_activity_unlinked_no_match` | Past race + 0 activities on day → `unlinked_no_match`. |
| L3 | `test_link_race_with_multiple_activities_ambiguous` | Race + 3 activities on day → `unlinked_ambiguous`, `activity_id=None`. |
| L4 | `test_link_race_with_no_date_unlinked_no_date` | Race with `race_date=None` → `unlinked_no_date`. |
| L5 | `test_link_race_links_non_run_activities` | Race on a day the user cycled (sport != Run) → still `linked`. Pin the "no sport filter" rule. |
| L6 | `test_link_race_future_dated_unlinked_no_match` | Future-dated race + no activity yet → `unlinked_no_match` (not a special status). |
| L7 | `test_link_race_no_activity_table_returns_unlinked` | Fresh DB without an `activity` table → every race returns `unlinked_no_match`, no crash. Defensive table-existence check. |

### `plan.md` retirement — 4 tests (mostly deletion, a few "no traces" assertions)

| # | Test name | Intent |
|---|---|---|
| P1 | `test_no_plan_path_attribute_on_settings` | `not hasattr(settings, 'plan_path')`. Defensive — catches an incomplete retirement. |
| P2 | `test_context_module_does_not_export_parse_plan` | `from runos.analysis.context import parse_plan` raises `ImportError`. |
| P3 | `test_race_readiness_report_renders_without_plan` | After plan.md is gone, the race-readiness report renders cleanly with no "Plan context" section and no errors. |
| P4 | `test_no_plan_md_example_file` | `not (repo_root / "plan.md.example").exists()`. Pin the file deletion. |

**Total tests in plan: 29.** All small and stdlib-only (no fixtures beyond `tmp_path` + a sqlite `conn` for L1-L7).

---

## 7. Judgement Call — Rename `context.py` → `races.py`?

### Import-site inventory (grep results)

```
tests/test_noteworthy.py:220:    from runos.analysis.context import Race, RacesContext
tests/test_noteworthy.py:233:    from runos.analysis.context import RacesContext
tests/test_context.py:14:from runos.analysis.context import (
runos/analysis/__init__.py:16:* :mod:`runos.analysis.context` -- parse `races.md` / `plan.md`.
runos/analysis/runner.py:23:from runos.analysis import context as ctx
runos/analysis/runner.py:29:from runos.analysis.context import Race
runos/sync/daily.py:34:from runos.analysis import context as ctx
runos/analysis/report.py:20:from runos.analysis.context import PlanContext, Race, RacesContext
```

**8 import sites across 6 files.** All mechanical:

- 5 are `from runos.analysis.context import ...` (renaming the path is a one-line sed per file).
- 2 are `from runos.analysis import context as ctx` — the simplest fix is to rename the alias source (`from runos.analysis import races as ctx` keeps `ctx.parse_races` calls untouched), but the cleaner option is to rename the alias to `races` and update the call sites.
- 1 is a doc-string mention in `__init__.py`.

There is **no `__init__.py` re-export** from `runos.analysis` that pins the name `context` publicly. `tests/test_context.py` should also be renamed to `tests/test_races.py` for symmetry (cheap; 1 file move).

### Recommendation: **Do the rename.**

Reasons:

1. **It's cheap.** 8 sites, all in code we already touch in this phase (`runner.py`, `report.py`, `sync/daily.py`, `__init__.py`, `test_context.py`, `test_noteworthy.py`). No external consumers — RunOS is single-user with no published API surface.
2. **Symmetry pays off immediately.** After Phase 8, the analysis layer has `races.py` and `heat.py` as siblings. Calling one of them `context.py` is a naming accident from when it held both races and plan parsers; the asymmetry will cause low-grade confusion in every PR that touches both files.
3. **The renamed-test-file split is honest about scope.** `test_context.py` currently mixes races and plan tests; the plan tests are being deleted anyway, so the file is renamed *and* losing half its content — natural moment to do the rename.
4. **`runos/analysis/race.py` already exists** (it's the predictions module that holds `STANDARD_DISTANCES_M`, imported by `context.py:33`). The new file would be `races.py` (plural), distinct from the existing `race.py` (singular). This is **slightly ugly** but unambiguous in practice — the plural matches the markdown filename `races.md` and the dataclass `RacesContext`. If the planner is uncomfortable with the `race.py` / `races.py` pair, an alternative is to call the new module `runos/analysis/races_md.py` — explicit about the file it parses. Recommendation: **plural `races.py`** matches the existing `RacesContext` naming and the user-facing filename.

### Compatibility shim?

**Not needed.** Single-user repo, hard cuts are fine (same philosophy as the plan.md retirement). One commit, all 8 sites updated atomically.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | `activity.day` and `race.race_date` are both timezone-naïve local-date strings, so they compare cleanly without TZ handling | Q3 edge case `d` | LOW — confirmed by reading `migrations/0002_structured.sql:21,27` and `context.py:160` (`date.fromisoformat`). Both sources verified. |
| A2 | `runos/analysis/race.py` (singular) does not currently parse `races.md` content (only holds distance constants + prediction math), so the planner can introduce `races.py` (plural) without collision | Q7 | LOW — confirmed by grep: `context.py:33` imports only `STANDARD_DISTANCES_M` from it. |
| A3 | Multiple sessions on the same date should each count separately in the rollup | Q1 trap #4 | LOW — matches journal-service convention (multiple activities on a day are each real). User can flag in review if they disagree. |
| A4 | The "stale" case (last session > 28 days ago) should also omit the heat section, not render a "0/0/0 last session 47 days ago" line | Q2 degradation | MEDIUM — this is a judgement call. If the user prefers to *see* the stale signal (a nudge to resume heat work), the rule changes to "render iff `last_28d_count > 0` OR `last_session_days_ago is not None and last_session_days_ago <= 60`". Flag for discuss-phase confirmation. |

## Open Questions

1. **Should the recovery report show stale heat-adaptation status as a nudge?** (A4 above — current recommendation: no, omit. Alternative: render a one-line "last session: 47 days ago — heat protocol lapsed" hint. Defer to user.)

2. **Should `link_races_to_activities` also surface a "linked but suspicious" status** when the linked activity's `sport` doesn't match a heuristic for the race distance (e.g. a 10k race linked to a Ride)? Out of scope per CONTEXT.md (no sport filter), but worth flagging as a possible follow-up enhancement — the rendered output could note `(sport: Ride)` after the link so the user sees the join is "linked but to a non-Run".

3. **Test file rename:** if Q7's rename is taken, should `tests/test_context.py` → `tests/test_races.py` happen in the same commit, or as a follow-up cleanup? Recommendation: same commit (file split is happening anyway).

## Project Constraints (from CLAUDE.md)

- **Python 3.14** + **uv** for packaging — phase 8 adds zero new deps.
- **Stdlib `sqlite3` + hand-written SQL** — race-link query uses raw `conn.execute(...)`, mirrors `srpe_by_day`. No ORM.
- **No pandas/polars** — heat rollup is pure stdlib (`datetime`, list comprehensions, sum).
- **`@dataclass(frozen=True, slots=True)`** — `HeatSession`, `HeatContext`, `HeatRollup`, `RaceLink` all follow this.
- **Lenient parsers, never raise on malformed input** — locked.
- **Local-first, no network in analysis layer** — none of this phase's code touches the network.
- **Files in `content_dir` are gitignored** — `heat.md.example` is committed (repo root); the actual `heat.md` lives in `~/.runos/` and is never committed.

## Sources

### Primary (HIGH confidence)
- `/Users/rossheadington/Projects/RunOS/.planning/phases/08-modular-trackers-heat-adaptation/08-CONTEXT.md` — locked decisions, full read.
- `/Users/rossheadington/Projects/RunOS/.planning/phases/08-modular-trackers-heat-adaptation/08-PATTERNS.md` — file-level pattern map, trusted as codebase truth source.
- `runos/analysis/context.py` (228 lines) — current parser, the model to mirror.
- `runos/analysis/recovery.py:402-455` — `render_recovery` shape, the integration target.
- `runos/analysis/report.py:180-279` — `render_race_readiness` shape, the plan-block removal target.
- `runos/analysis/runner.py:160-311` — orchestrator wiring.
- `runos/analysis/data.py:60-143` — read-only data layer convention, table-existence check pattern.
- `runos/journal/service.py:1-90` — 0/1/N convention source.
- `runos/migrations/0002_structured.sql:21,27,46` — confirms `activity.day` is local-date string.
- `.env.example`, `runos/cli.py`, `runos/sync/daily.py`, `runos/analysis/__init__.py`, `tests/test_config.py`, `tests/test_analyze_cli.py`, `tests/test_analysis_reports.py`, `tests/test_noteworthy.py` — grep-verified for Q4 blast radius.

### Secondary
None — entire research is in-repo code reading.

### Tertiary
None.

## Metadata

**Confidence breakdown:**
- Heat rollup design (Q1): HIGH — algorithm follows direct stdlib date math; closed-interval convention is well-defined.
- Recovery integration (Q2): HIGH for placement and "omit on stale" rule; MEDIUM for the assertion that stale should be silent (flagged as A4).
- Race-link edge cases (Q3): HIGH — every case mapped to existing convention (journal-service 0/1/N).
- plan.md blast radius (Q4): HIGH — full grep run, missed files identified explicitly.
- heat.md.example content (Q5): HIGH — directly mirrors `races.md.example`.
- Test outline (Q6): HIGH — each test maps to a specific behavior in CONTEXT.md or this research.
- Rename judgement (Q7): HIGH — import inventory is exhaustive (8 sites, all listed).

**Research date:** 2026-05-27
**Valid until:** 2026-06-26 (30 days — codebase is stable, no fast-moving deps in scope).
