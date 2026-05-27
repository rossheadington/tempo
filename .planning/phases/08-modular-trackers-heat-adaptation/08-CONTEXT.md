# Phase 8: Modular Trackers + Heat Adaptation — Context

**Gathered:** 2026-05-27
**Status:** Ready for planning
**Source:** Inline discussion (conversation 2026-05-27)

<domain>
## Phase Boundary

**What this phase delivers:**

- `races.md` gains a free-form optional `result:` field per race; the parsed `RacesContext` exposes a new `completed(today)` helper (mirror of existing `upcoming(today)`).
- A new auto-link layer joins each `races.md` entry to the Strava `activity` on its date (when one exists) — the race-readiness report can then surface "Berlin Marathon: 3:17:42 (2:42 over goal)" or "no activity recorded for race date".
- A new `heat.md` tracker file lives alongside `races.md` in the content dir. Append-only list of heat-adaptation sessions. Parsed leniently into `HeatContext` (mirror of `RacesContext`).
- Heat-session context is surfaced in analyses — at minimum a rolling-window count + total minutes (last 7 / 14 / 28 days) appears in the **recovery report** context so Claude knows current heat-adaptation status.
- `plan.md` is **retired entirely**: parser, `PlanContext` dataclass, config field (`plan_path`), `plan.md.example`, references in `tempo/analysis/report.py`, CLAUDE.md tech-stack section mentions (`plan.md.example` in README), and `tests/test_context.py` plan tests all removed.
- Race-readiness report continues to render cleanly without plan.md (the "current focus" section is just dropped — analyses already degrade when `present=False`).

**What this phase does NOT deliver (out of scope):**

- A "modular tracker registry" abstraction. We have two trackers (races, heat) after this phase; a registry is premature. Each tracker is wired directly into `analysis/context.py` like the current pattern. The registry idea is documented in [[deferred]] and revisited when a third tracker is added.
- A `runs.md` or `cross-training.md` file. Strava captures every run/ride/swim/cross-training activity; the journal already captures subjective per-session reflection. Both files would duplicate existing data.
- Forward-looking plan tracking of any kind. The user explicitly decided they don't want to track upcoming weekly plans right now.
- A nutrition tracker or strength-and-conditioning tracker. May come later; explicitly deferred.
- Backwards-compatibility shim for `plan.md`. The retirement is a hard cut; existing users (just the project owner) will see plan.md content stop being read on upgrade. No migration tool, no deprecation warning — just gone.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### File layout & locations

- All tracker files live in the user's content dir, resolved via `config.content_dir` (or `data_dir` fallback). Same pattern as today's `races_path` / `plan_path` derived properties.
- New derived path: `heat_path` (`content_dir / "heat.md"`).
- Remove derived path: `plan_path`.
- Each tracker has a corresponding committed `.md.example` template in the repo root (mirrors `races.md.example`). Delete `plan.md.example`. Add `heat.md.example`.

### `races.md` changes

- **New optional `result:` key.** Free-form string. Examples: `result: 3:17:42`, `result: 1:32:11`, `result: DNF`, `result: 39:42 (course PB)`. Parser stores it verbatim on the `Race` dataclass as `result: str | None`.
- **No structured result-time parsing in this phase.** Don't try to parse `3:17:42` into seconds. The string is reported verbatim. Comparison vs. goal is the next phase's job if at all.
- **`completed(today)` helper on `RacesContext`.** Returns past-dated races (date < today), sorted **most recent first** (opposite of `upcoming` which is soonest-first). Undated races are excluded from `completed` (they cannot be known-past).
- **Existing `upcoming(today)` unchanged.** Still includes undated races at the end.
- **All existing races.md format conventions preserved** — lenient parsing, `key: value | key: value` separators, recognised keys still ignored if unknown, etc. The `result:` field is added to the set of recognised keys but is purely additive.

### Race ↔ Strava activity auto-link

- New function in `analysis/data.py` (or a new `analysis/race_link.py` — planner's call): `link_races_to_activities(races: list[Race], conn) -> list[RaceLink]`.
  - Returns a parallel list of `RaceLink(race=race, activity_id=int | None, link_status: 'linked' | 'unlinked_no_match' | 'unlinked_ambiguous' | 'unlinked_no_date')`.
  - Lookup is by **local date** (`activity.day`) only — not by sport. (A race could be ridden, swum, or run; one activity per race day is the overwhelmingly common case.)
  - **0 matches** → `unlinked_no_match`, `activity_id=None`. Don't error.
  - **Exactly 1 match** → `linked`, `activity_id=<id>`.
  - **>1 match** → `unlinked_ambiguous`, `activity_id=None`. The user did multiple things on race day; we refuse to guess (mirrors journal-service convention from Phase 5).
  - **Race has no date** → `unlinked_no_date`.
- Reads from the existing `activity` table; uses the read-only data layer (`analysis/data.py`).
- Called from `analysis/runner.py` during race-readiness rendering.
- **No write-back to races.md.** The link is purely runtime context for analyses.

### `heat.md` shape

- **Append-only markdown list**, same syntactic family as `races.md` for consistency.
- Each entry is one bullet: `- YYYY-MM-DD - type: sauna | duration_min: 25 | temp_c: 85 | hr_avg: 105 | notes: post-run, felt easier than last week`
- **Recognised keys (all lenient, all optional except `type` and `duration_min` are practically needed for rollups):**
  - `date` — ISO `YYYY-MM-DD`. If omitted, the bullet's leading date (before the first ` - `) is used. (Mirrors `races.md` "name first, then kv" convention.)
  - `type` — free-form string (`sauna`, `hot-bath`, `hot-run`, `steam-room`, etc.). Stored verbatim; no enum.
  - `duration_min` — integer or float minutes.
  - `temp_c` — optional ambient temperature, Celsius.
  - `hr_avg` — optional average HR during the session.
  - `notes` — optional free text.
- **Lenient parser** — unrecognised keys ignored, malformed lines skipped, missing file → `HeatContext(present=False)` (mirrors `parse_races` / `parse_plan` behavior).
- **HeatSession dataclass** in `tempo/analysis/heat.py` (new module): `date, type, duration_min, temp_c, hr_avg, notes` (all optional except date — entries without a parseable date are dropped, not stored, since they break rollups).

### Heat rollup surfacing

- New function in `tempo/analysis/heat.py`: `heat_rollup(sessions: list[HeatSession], today: date) -> HeatRollup`.
  - Returns `HeatRollup(last_7d_count, last_7d_minutes, last_14d_count, last_14d_minutes, last_28d_count, last_28d_minutes, last_session_date, last_session_days_ago)`.
- Wired into the **recovery report** (`tempo/analysis/recovery.py` + `report.py`). Heat status appears as a "Heat adaptation" section that says: `last 7 days: 3 sessions / 78 min · last 14 days: 6 sessions / 154 min · last session: 2 days ago`. When `HeatContext(present=False)` or rollups are zero, the section is omitted entirely (degrade gracefully).
- **Not yet surfaced in race-readiness or load-trend** — recovery is the natural home (heat is a recovery-domain intervention). Other reports can pick it up later if useful.

### `plan.md` retirement

- Delete: `parse_plan`, `PlanContext`, `_PLAN_FIELD_RE` from `tempo/analysis/context.py`.
- Remove `plan_path` derived property + any usage in `tempo/config.py` and `.env.example`.
- Delete: `plan.md.example` from repo root.
- Update: `tempo/analysis/runner.py` and `tempo/analysis/report.py` — remove the plan-context loading + the "Current focus" / `Phase`/`Week`/`Focus`/etc. rendering block from the race-readiness report. Report continues to render without it.
- Update: any tests in `tests/test_context.py`, `tests/test_analysis_reports.py`, `tests/test_analyze_cli.py` that exercise plan.md — delete plan-specific tests, leave races tests intact.
- Update: README mentions of plan.md → remove.
- Update: CLAUDE.md tech-stack section — drop reference to `plan.md` if any. (Spot-check: the bulk of CLAUDE.md is research output, not code reference; user is fine with leaving stale phrasing if it doesn't cause confusion.)

### `races.md.example` updates

- Add `result:` example to the template (one past race shown with result).
- Add a section header `## Past races` separating past from upcoming, purely for human readability (parser doesn't care about section headers — it already skips lines beginning with `#`).
- Example past race entry: `- Local Half - date: 2026-04-12 | distance: half | goal: 1:32:00 | priority: B | result: 1:31:48`.

### `heat.md.example`

- New committed template file in repo root, gitignored real file lives in content dir.
- Documents the format, recognised keys, and shows 4-6 sample sessions of varying shape (sauna, hot-bath, hot-run; with and without temp/HR).

### Code-organisation conventions

- New module: `tempo/analysis/heat.py` (parser + dataclasses + rollup function).
  - Mirrors the structure of `tempo/analysis/context.py` (which currently holds both races and plan parsers). Don't merge heat into `context.py` — `context.py` will become races-only after plan retirement, and a separate `heat.py` is cleaner for future tracker additions (we're already mentally treating each tracker as its own thing, even without the registry).
- `tempo/analysis/context.py` after this phase: races parsing only. Consider whether to rename to `tempo/analysis/races.py` — **deferred** to avoid churn unless the planner sees a clean reason. (Symmetry with `heat.py` would argue for the rename; the cost is import churn across other modules. Leave to planner's judgement.)
- Auto-link function lives in `analysis/data.py` (extends the existing read-only data layer) OR a new `analysis/race_link.py`. Planner picks based on what reads cleanest.

### Honesty / failure modes (mirror v1 conventions)

- Missing `heat.md` → `HeatContext(present=False)`, rollups all `None` or `0`, recovery report section omitted.
- Missing `races.md` → already handled (returns `RacesContext(present=False)`); no change.
- Race ↔ activity ambiguous → race appears in race-readiness without a linked time; no guess.
- Parser never raises on malformed lines — skip the line, continue.

### Migration / one-time user actions

- After upgrading, the user's existing `plan.md` content (if any) is silently ignored. The user is informed (commit message + brief note in PR) that plan.md is retired; they can manually carry forward anything they care about.
- The user's existing `races.md` is forward-compatible — `result:` is optional, no migration needed. If they want to record past race results, they edit the file by hand.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing parser pattern (the model to follow)
- `tempo/analysis/context.py` — the current `parse_races` / `parse_plan` / `_parse_race_line` / `_parse_kv` pattern. Both lenient parsers + dataclasses live here today. New heat parser mirrors this style.

### Existing config pattern
- `tempo/config.py:46-150` — `content_dir`, `_content_root`, `races_path`, `plan_path` derived properties; pydantic-settings validators. New `heat_path` follows the `races_path` pattern; `plan_path` is removed.

### Existing report renderer (where heat rollup plugs in)
- `tempo/analysis/recovery.py` — the recovery analysis builder. Heat rollup is added as another piece of context here.
- `tempo/analysis/report.py` — markdown rendering. Heat-adaptation section added to recovery report; "current focus" / plan block removed from race-readiness report.

### Existing read-only data layer
- `tempo/analysis/data.py` — read-only queries against the structured tables. Race ↔ activity lookup lives here (or a new file in the same layer).

### Existing analysis runner
- `tempo/analysis/runner.py` — orchestrates inputs → series → reports. Wire in heat parsing + heat rollup + race-link resolution. Remove plan-context loading.

### Existing example file convention
- `races.md.example` and `plan.md.example` (root of repo, committed). Update races, delete plan, add `heat.md.example`.

### Tests-mirror-modules convention
- `tests/test_context.py` — current parser tests. Split: races tests stay here (or move to `tests/test_races.py`), plan tests deleted, new `tests/test_heat.py` for the new parser + rollup.
- `tests/test_analysis_reports.py`, `tests/test_analyze_cli.py` — end-to-end render tests; update to drop plan assertions and add heat-section assertions.

### Race + activity link target table
- `tempo/migrations/0002_structured.sql` — `activity` table, has `day` column keyed on the date_spine. The auto-link queries `activity.day = race.date`.

</canonical_refs>

<specifics>
## Specific Ideas

- The user uses **Strong** (iOS workout tracker) for strength-and-conditioning work today. The maintainer reverse-engineering effort exists but the official path is CSV export. **Not in scope for Phase 8** — surfaced in conversation as a possible future tracker; explicitly deferred.
- The user mentioned "saunaing I put in the other day" — there is no existing sauna-tracking code; they likely typed a free-form note into `plan.md` which is currently read as prose. After Phase 8, those notes belong in `heat.md`.
- The journal system already exists (`tempo/journal/service.py`) and captures per-workout RPE / feel / notes linked to Strava activities — this is the right home for post-run / post-race "how it felt" content. No need to duplicate it in any markdown file.
- Race auto-link **could** join through to a journal entry on the same date in a follow-up phase, so a race shows: goal, result, RPE, what you said about it. **Phase 8 stops at race → activity link.** Journal join is a small follow-up.

</specifics>

<deferred>
## Deferred Ideas

- **Modular tracker registry** (`tempo/analysis/trackers/` discovery layer with a `Tracker` protocol). Worth introducing when a third tracker is added. Until then, direct wiring is simpler and clearer.
- **Strength tracker** consuming Strong CSV exports → SQLite `strength_sets` table. Out of scope; surfaced in discussion.
- **Nutrition tracker** (NUTR-01 / NUTR-02 already in REQUIREMENTS.md v2). Out of scope.
- **Race result time → seconds parsing** + automated "X seconds over/under goal" rendering. Phase 8 stores `result:` verbatim; structured comparison is a follow-up.
- **Race → journal → activity join** so race-readiness can quote the post-race journal note. Small follow-up after Phase 8 lands the race → activity link.
- **Rename `tempo/analysis/context.py` → `tempo/analysis/races.py`** for symmetry with `heat.py`. Planner's call — if low churn, do it; if it cascades through many imports, leave for a future cleanup.

</deferred>

---

*Phase: 08-modular-trackers-heat-adaptation*
*Context gathered: 2026-05-27 via inline discussion*
