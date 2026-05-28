# Phase 8: Modular Trackers + Heat Adaptation — Pattern Map

**Mapped:** 2026-05-27
**Files analyzed:** 16 (3 new, 12 modified, 1 deleted)
**Analogs found:** 16 / 16 (every file has a strong in-repo analog — this phase is intentionally symmetric with existing tracker plumbing)

## File Classification

| New / Modified / Deleted | File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|---|
| NEW | `runos/analysis/heat.py` | parser + dataclasses + pure rollup | file-in → typed dataclass | `runos/analysis/context.py` (parse_races, RacesContext, Race, _parse_kv, _parse_race_line) | exact (same shape, same lenient idioms) |
| NEW | `heat.md.example` | committed user-facing template | docs | `races.md.example` | exact |
| NEW | `tests/test_heat.py` | parser + rollup unit tests | tmp_path file → assert | `tests/test_context.py` (races section, lines 21-95) | exact |
| MOD | `runos/analysis/context.py` | parser + dataclasses (races-only after retirement) | file-in → typed dataclass | self (surgical edit) | n/a — self |
| MOD | `runos/analysis/data.py` (or new `runos/analysis/race_link.py`) | read-only SQLite query + linker | DB read + zip with list | `runos/analysis/data.py:srpe_by_day` + `runos/journal/service.py:resolve_activity` (0/1/N pattern) | role-match + convention-match |
| MOD | `runos/analysis/recovery.py` | analysis renderer consumer | dataclass-in → render lines | self (extend render_recovery) + `runos/analysis/noteworthy.py:next_race_within_days` (consume RacesContext) | self + role-match |
| MOD | `runos/analysis/report.py` | markdown renderer | dataclass-in → str | self (surgical edit — drop plan block, recovery rendering lives in recovery.py) | n/a — self |
| MOD | `runos/analysis/runner.py` | orchestrator | wire-in parsers/series | self (mirror `parse_races` call at line 269; remove `parse_plan` at line 270) | n/a — self |
| MOD | `runos/config.py` | settings + derived paths | env → typed paths | self (mirror `races_path` property at lines 142-144) | n/a — self |
| MOD | `.env.example` | user-facing config docs | docs | self (the comment block at lines 65-67) | n/a — self |
| MOD | `races.md.example` | committed user-facing template | docs | self (add result: example + ## Past races header) | n/a — self |
| MOD | `tests/test_context.py` | unit tests | tmp_path file → assert | self (lines 85-95 `test_upcoming_sorts_and_filters` is the model for `completed`) | n/a — self |
| MOD | `tests/test_analysis_reports.py` | end-to-end report tests | DB seed + render → assert | self (lines 124-143 `test_race_readiness_report_written_with_predictions`) | n/a — self |
| MOD | `tests/test_analyze_cli.py` | CLI smoke tests | CLI invoke → file assert | self (lines 87-95 `test_analyze_race_readiness_subcommand`) | n/a — self |
| MOD | `README.md` | project docs | docs | self (lines 239-241 plan/race mention) | n/a — self |
| DEL | `plan.md.example` | (removed) | — | — | — |

---

## Pattern Assignments

### NEW: `runos/analysis/heat.py` (parser + dataclasses + rollup)

**Analog:** `runos/analysis/context.py` (entire file is the model)

**Module docstring pattern** (context.py:1-24): a tidy paragraph naming the file the parser reads, calling out leniency, and including a one-line documented format example. Heat docstring mirrors this and shows the bullet shape from CONTEXT.md.

**Frozen, slotted dataclass for one record** (context.py:36-46):
```python
@dataclass(frozen=True, slots=True)
class Race:
    """One upcoming (or past) race parsed from ``races.md``."""

    name: str
    race_date: date | None = None
    distance_m: float | None = None
    distance_label: str | None = None
    goal: str | None = None
    goal_time_s: float | None = None
    priority: str | None = None
```
**Apply to:** `HeatSession` — `date: date`, `type: str | None`, `duration_min: float | None`, `temp_c: float | None`, `hr_avg: float | None`, `notes: str | None`. Per CONTEXT.md, entries without a parseable date are dropped (not stored) because rollups break on them — so `date` is non-Optional on the dataclass.

**Frozen, slotted context wrapper with `present` flag** (context.py:49-65):
```python
@dataclass(frozen=True, slots=True)
class RacesContext:
    """The parsed ``races.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    races: list[Race] = field(default_factory=list)
    source_path: str | None = None

    def upcoming(self, today: date) -> list[Race]:
        ...
```
**Apply to:** `HeatContext(present, sessions, source_path)`. Mirror the `present` + `source_path` fields exactly.

**Lenient kv splitter** (context.py:116-127) — copy verbatim into heat.py:
```python
def _parse_kv(segment: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in re.split(r"[|,]", segment):
        if ":" not in part:
            continue
        key, _, val = part.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key and val:
            fields[key] = val
    return fields
```
(Reuse vs. re-implement: planner's call — exporting `_parse_kv` from context.py keeps DRY; copying keeps heat.py independent of context.py module-load order. Copy is simpler given context.py may be renamed later per the deferred rename.)

**Bullet-stripping + recognised-keys guard** (context.py:130-156) — direct model for `_parse_heat_line`. The "must carry at least one recognised key" pattern protects against documentation prose bullets being treated as data:
```python
recognised = {"date", "distance", "goal", "priority", "name"}
if not (recognised & fields.keys()):
    return None
```
**Apply to:** heat recognised set = `{"date", "type", "duration_min", "temp_c", "hr_avg", "notes"}`. A line must hit at least one to be a session.

**Date parsing fallback** (context.py:157-162) — bad date → drop the field, not the line:
```python
race_date: date | None = None
if "date" in fields:
    try:
        race_date = date.fromisoformat(fields["date"][:10])
    except ValueError:
        race_date = None
```
**Apply to:** heat — but per CONTEXT.md the date is **required for a session to be stored**, so the inverse: if `date.fromisoformat` raises AND no leading-date prefix in the bullet, return `None` from `_parse_heat_line`. Also: CONTEXT.md says "If omitted, the bullet's leading date (before the first ` - `) is used" — that mirrors how `name` is taken from the leading text in `_parse_race_line` at line 144.

**Top-level parse function** (context.py:186-200) — the master pattern for `parse_heat`:
```python
def parse_races(path: Path) -> RacesContext:
    """Parse ``races.md`` at ``path``; missing file -> empty, ``present=False``."""
    if not path.exists():
        return RacesContext(present=False, source_path=str(path))
    text = path.read_text(encoding="utf-8")
    races: list[Race] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "-*+":
            race = _parse_race_line(line)
            if race is not None:
                races.append(race)
    return RacesContext(present=True, races=races, source_path=str(path))
```
**Apply to:** `parse_heat(path) -> HeatContext` — same skeleton, swap `_parse_race_line` for `_parse_heat_line`.

**NEW pure function (no analog inside context.py — model after `runos/analysis/recovery.py:assess_recovery`):** `heat_rollup(sessions, today)`:
- Returns a frozen+slotted `HeatRollup` dataclass.
- Pure over already-parsed sessions + a `today: date` (mirror the "pure over already-read inputs" convention in recovery.py:20-22).
- Field naming convention from `RecoveryAssessment` (recovery.py:228-247) — short, snake_case, `_count` / `_minutes` / `_days_ago` suffixes per the CONTEXT.md spec.

**Seam markers (heat.py is greenfield):**
- Module sits next to `context.py` and `recovery.py` in `runos/analysis/`.
- Add to `runos/analysis/__init__.py` module index (lines 11-19) — append a bullet for `heat`.

---

### NEW: `heat.md.example` (committed template)

**Analog:** `races.md.example` (lines 1-27, the whole file)

**Header / intent block** (races.md.example:1-13):
```markdown
# Races — EXAMPLE / TEMPLATE

Copy this file to your RunOS data dir as `races.md` (default
`~/.runos/races.md`) and edit it. RunOS reads it for race-readiness context
(PLAN-01); it is never committed (the data dir lives outside the repo tree).

## Format

One race per markdown list item. After the race name put `key: value` pairs
separated by `|` (or commas), in any order. Parsing is lenient: unknown keys are
ignored and a malformed line is skipped, so you can't break analysis by editing
this file.
```
**Apply to:** heat.md.example — same shape: title, copy-target instruction, gitignore note, `## Format` section.

**Recognised-keys list** (races.md.example:14-21):
```markdown
Recognised keys:

- `date` — ISO date `YYYY-MM-DD` (the race day)
- `distance` — `marathon`, `half`, `10k`, `5k`, `50k`, or a number with a unit
  like `42.195km`, `21100m`, `13.1mi`
- `goal` — a target finish time `H:MM:SS` (or `MM:SS`), or free text
- `priority` — `A` (key race), `B`, or `C`
```
**Apply to:** heat's six recognised keys (date, type, duration_min, temp_c, hr_avg, notes).

**Examples block** (races.md.example:22-26):
```markdown
## Races

- Spring Half - date: 2026-03-15 | distance: half | goal: 1:32:00 | priority: B
- Berlin Marathon - date: 2026-09-27 | distance: marathon | goal: 3:15:00 | priority: A
- Club 10k - date: 2026-06-07 | distance: 10k | goal: 39:30 | priority: C
```
**Apply to:** heat.md.example — `## Sessions` header (or `## Heat sessions`) + 4-6 sample bullets covering sauna, hot-bath, hot-run, with/without `temp_c` / `hr_avg`, per CONTEXT.md spec. Place the file at the repo root next to `races.md.example`.

---

### NEW: `tests/test_heat.py` (parser + rollup unit tests)

**Analog:** `tests/test_context.py` lines 1-95 (races portion only — drop the plan portion from the template)

**Module header + ruler comments** (test_context.py:1-19):
```python
"""Parsing of races.md / plan.md context files, including the missing-file path.

Covers PLAN-01/02. Parsing is lenient: unknown lines ignored, malformed fields
skipped, missing file -> empty result with ``present=False`` (analyses degrade).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from runos.analysis.context import (
    parse_distance,
    parse_goal_time,
    parse_plan,
    parse_races,
)

# ---- races.md -------------------------------------------------------------
```
**Apply to:** test_heat.py imports `parse_heat`, `heat_rollup`, `HeatSession`, `HeatContext`, `HeatRollup` from `runos.analysis.heat`.

**Missing-file test** (test_context.py:24-27):
```python
def test_parse_races_missing_file(tmp_path: Path) -> None:
    ctx = parse_races(tmp_path / "nope.md")
    assert ctx.present is False
    assert ctx.races == []
```
**Apply to:** `test_parse_heat_missing_file` — assert `ctx.present is False`, `ctx.sessions == []`.

**Basic parse test** (test_context.py:30-46) — model for `test_parse_heat_basic`. Write a tmp file with two sauna entries (one with temp_c, one without), assert two `HeatSession` objects with the right field values.

**Prose-bullets-are-ignored test** (test_context.py:49-63):
```python
def test_parse_races_ignores_prose_and_headings(tmp_path: Path) -> None:
    # Documentation-style bullets (no recognised race fields) must not become races.
    p = tmp_path / "races.md"
    p.write_text(
        "# Format\n\n"
        "Recognised keys:\n\n"
        "- `date` - ISO date YYYY-MM-DD\n"
        ...
```
**Apply to:** heat — a doc bullet `- type description here` without any `key:value` pair must not become a session.

**Lenient partial-fields test** (test_context.py:66-74) — apply: a heat bullet with only `date + type + duration_min` should parse cleanly with `temp_c=None`, `hr_avg=None`.

**Bad-date test** (test_context.py:77-82) — apply: a heat bullet whose date is unparseable AND has no leading-date prefix is dropped entirely (sessions list is empty). This is the inverse of the races behavior — call it out in the test name.

**Sort/filter helper test** (test_context.py:85-95) — model for `test_heat_rollup_windows`. Build 5-10 sessions across the last 30 days, call `heat_rollup(sessions, today=date(2026, 5, 27))`, assert counts in 7/14/28 windows and `last_session_days_ago`.

**Seam markers:**
- New file at `tests/test_heat.py`.
- conftest.py provides `tmp_path` (pytest stdlib fixture) — no extra fixtures needed.

---

### MOD: `runos/analysis/context.py` (surgical edits)

**Self-analog. Exact line targets:**

**REMOVE block 1 — PlanContext dataclass** (lines 67-75):
```python
@dataclass(frozen=True, slots=True)
class PlanContext:
    """The parsed ``plan.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    text: str = ""
    headings: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None
```

**REMOVE block 2 — `_PLAN_FIELD_RE`** (lines 203-205):
```python
_PLAN_FIELD_RE = re.compile(
    r"^\s*(phase|week|focus|mileage|target|block)\s*:\s*(.+)$", re.IGNORECASE
)
```

**REMOVE block 3 — `parse_plan`** (lines 208-227): entire function body.

**REMOVE block 4 — references in module docstring** (lines 1, 11, 21-23): the leading docstring talks about both races.md and plan.md; trim to races.md only.

**ADD to `Race` dataclass** (after line 46, before closing):
```python
    result: str | None = None
```
Verbatim string, no parsing — per CONTEXT.md: "Free-form string ... Parser stores it verbatim on the `Race` dataclass as `result: str | None`."

**ADD `result` to recognised-keys set in `_parse_race_line`** (line 153):
```python
recognised = {"date", "distance", "goal", "priority", "name", "result"}
```

**ADD result extraction in `_parse_race_line`** (after line 173, before the `return Race(...)`):
```python
result_str = fields.get("result")
```
Then pass `result=result_str` into the `Race(...)` constructor call (lines 175-183).

**ADD `completed(today)` to `RacesContext`** (after line 64, mirror of `upcoming`):
```python
def completed(self, today: date) -> list[Race]:
    """Past-dated races (date < today), most recent first. Undated races excluded."""
    return sorted(
        (r for r in self.races if r.race_date is not None and r.race_date < today),
        key=lambda r: r.race_date,  # type: ignore[arg-type,return-value]
        reverse=True,
    )
```
Per CONTEXT.md: "Returns past-dated races (date < today), sorted **most recent first** (opposite of `upcoming` which is soonest-first). Undated races are excluded."

**Seam markers:**
- `from dataclasses import dataclass, field` — if `field` is no longer used after removing PlanContext, drop the `field` import.
- `parse_distance`, `parse_goal_time`, `_parse_kv`, `_parse_race_line`, `parse_races` all stay.

---

### MOD: `runos/analysis/data.py` (race↔activity auto-link) — OR — NEW `runos/analysis/race_link.py`

**Analog 1 (data-layer query convention):** `runos/analysis/data.py:srpe_by_day` (lines 127-143):
```python
def srpe_by_day(conn: sqlite3.Connection) -> dict[str, float]:
    """Return ``{day: total_srpe}`` summed across journal entries (JRNL-03).

    Days with no journal sRPE are simply absent from the map. ...
    """
    has_journal = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='journal'"
    ).fetchone()
    if has_journal is None:
        return {}
    rows = conn.execute(
        "SELECT day, SUM(srpe) AS total FROM journal WHERE srpe IS NOT NULL GROUP BY day"
    ).fetchall()
    return {str(r["day"]): float(r["total"]) for r in rows if r["total"] is not None}
```
**Apply to:** new helper `activities_by_local_date(conn) -> dict[str, list[int]]` — group `activity.activity_id` by `activity.day`. Use `activities_by_day` (lines 73-78) as the direct shape model:
```python
def activities_by_day(conn: sqlite3.Connection) -> dict[str, list[ActivityRecord]]:
    """Group activities by their local day."""
    grouped: dict[str, list[ActivityRecord]] = {}
    for rec in read_activities(conn):
        grouped.setdefault(rec.day, []).append(rec)
    return grouped
```
For race-link purposes a lighter `dict[str, list[int]]` (day → list of activity_id) is sufficient — counts decide ambiguity, the id is the link.

**Analog 2 (0/1/N convention):** `runos/journal/service.py:resolve_activity` docstring + behavior (service.py:8-25):
```
* **0 matches** -> no link. The entry is a rest-day / non-activity reflection;
  ``activity_id`` is ``None``. (Valid: rest days get journaled too.)
* **exactly 1 match** -> link to it automatically.
* **many matches** -> ambiguous. The caller must disambiguate ... rather
  than silently guessing -- guessing would undermine "trustworthy structured
  signal".
```
**Apply to:** `link_races_to_activities(races, conn) -> list[RaceLink]` — same 0/1/N taxonomy, but instead of raising on N>1 (which would crash analyses), return a `RaceLink` with `link_status='unlinked_ambiguous'`. Per CONTEXT.md: "**>1 match** → `unlinked_ambiguous`, `activity_id=None`. The user did multiple things on race day; we refuse to guess (mirrors journal-service convention from Phase 5)."

**Dataclass shape** — model after `runos/analysis/data.py:SourceFreshness` (lines 34-41):
```python
@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """Per-source sync recency, for the report freshness header (ANL-05)."""

    source: str
    last_sync_at: str | None
    last_entity_ts: str | None
    days_stale: int | None  # full days since last successful sync (None if never)
```
**Apply to:** `RaceLink(race, activity_id, link_status)` — frozen, slotted, single docstring line. `link_status` is a `Literal["linked", "unlinked_no_match", "unlinked_ambiguous", "unlinked_no_date"]` (or plain `str` to mirror existing convention which uses bare str for status fields, see `RecoveryAssessment.status` and `SignalAssessment.status` in recovery.py:65 and 244).

**File-placement decision (planner's call per CONTEXT.md line 107):**
- **Option A — extend data.py:** Add `RaceLink` dataclass + `link_races_to_activities` here. Pro: single read-only data layer; con: data.py grows another concern.
- **Option B — new race_link.py:** Pro: dedicated module symmetric with `analysis/heat.py`. Con: another import. Recommend B if the planner takes the deferred `context.py → races.py` rename in CONTEXT.md (consistent module-per-concern). Otherwise A.

**Seam markers:**
- Add the function name to `runos/analysis/__init__.py` module index after `data` bullet.
- Called from `runos/analysis/runner.py:generate_race_readiness` after `parse_races` (today's line 269).

---

### MOD: `runos/analysis/recovery.py` (add heat-adaptation section)

**Self-analog. Wiring touchpoints:**

**ADD param to `render_recovery` signature** (recovery.py:402-408):
```python
def render_recovery(
    *,
    generated_on: date,
    freshness: list[dataread.SourceFreshness],
    data_range: tuple[str, str] | None,
    assessment: RecoveryAssessment,
) -> str:
```
**Apply to:** add `heat_rollup: HeatRollup | None = None` (default None so callers that haven't been updated still work, and the section is omitted when None or when all counts are zero — degrade gracefully per CONTEXT.md).

**ADD heat-adaptation section** — model after the "Recovery markers vs personal baseline" section (recovery.py:434-448):
```python
out.append("## Recovery markers vs personal baseline\n")
out.append(
    "_HRV is judged in **both** directions: ..._\n"
)
for s in a.signals:
    icon = {...}.get(s.status, "")
    out.append(f"- {icon} {s.message}")
out.append("")
```
**Apply to:** new section `## Heat adaptation` rendered with the one-line summary from CONTEXT.md spec: `last 7 days: 3 sessions / 78 min · last 14 days: 6 sessions / 154 min · last session: 2 days ago`. Omit the whole section if `heat_rollup is None` or all counts are 0.

**Optional: extend `RecoveryAssessment`** — the cleanest place to attach heat rollup is as an optional field on the assessment dataclass (recovery.py:228-247), so `assess_recovery` (or a new `assess_recovery_with_heat`) carries it through to render. Mirror the `signals: list[SignalAssessment]` field pattern at line 246. Planner picks: pass-through param (simpler, no dataclass change) vs. field-on-assessment (more honest about the data flow).

**Seam markers:**
- Import: `from runos.analysis.heat import HeatRollup` near the top with the other in-package imports (recovery.py:31-34).
- Caller in runner.py:289-311 (`generate_recovery`) gets the rollup before calling `render_recovery`.

---

### MOD: `runos/analysis/report.py` (surgical removal of plan block)

**Self-analog. Exact removals:**

**REMOVE import of PlanContext** (report.py:20):
```python
from runos.analysis.context import PlanContext, Race, RacesContext
```
→
```python
from runos.analysis.context import Race, RacesContext
```

**REMOVE plan param + plan block** (report.py:188-223 — the `render_race_readiness` signature + body):
- Remove `plan_ctx: PlanContext,` from the function signature (line 194).
- Remove the plan-context rendering block (lines 219-223):
```python
if plan_ctx.present and plan_ctx.fields:
    out.append("## Plan context\n")
    for key, val in plan_ctx.fields.items():
        out.append(f"- **{key}**: {val}")
    out.append("")
```

**ADD race-link surfacing (NEW)** — model after the per-race rendering inside `render_race_readiness` (report.py:243-278). After the existing per-race fields, append a line driven by the new `RaceLink` (passed in alongside the existing `RaceReadiness` findings, or attached as a field on `RaceReadiness`). Two phrasing forms from CONTEXT.md:
- `linked` → `"Result: {race.result} (activity id: {activity_id})"` if `race.result` is set, else just `"Activity recorded on race day (id: {activity_id})"`.
- `unlinked_no_match` → `"No activity recorded for race date."`
- `unlinked_ambiguous` → `"Multiple activities on race day; cannot auto-link."`
- `unlinked_no_date` → render nothing (race had no date to link against; this is already obvious from the missing "-- date" in the heading).

This insertion point is after line 277 (`out.append(f"- **Form check**: {r.form_note}")`) and before line 278 (the trailing `out.append("")`).

**Seam markers:**
- `render_recovery` is **not** in this file — it lives in `recovery.py`. The heat section is added there, not here. report.py is touched only for the race-readiness changes.

---

### MOD: `runos/analysis/runner.py` (orchestrator wiring)

**Self-analog. Exact line targets:**

**REMOVE plan loading** (runner.py:270):
```python
plan_ctx = ctx.parse_plan(plan_path)
```

**REMOVE plan param** from `generate_race_readiness` signature (runner.py:258-266):
```python
def generate_race_readiness(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    races_path: Path,
    plan_path: Path,    # <-- remove
    reports_dir: Path,
    generated_on: date,
) -> Path:
```

**REMOVE plan param** from `generate_all` signature (runner.py:353-361):
```python
def generate_all(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    races_path: Path,
    plan_path: Path,    # <-- remove
    reports_dir: Path,
    generated_on: date,
) -> AnalyzeResult:
```

**REMOVE `plan_path=` from internal call** (runner.py:377):
```python
race_readiness=generate_race_readiness(
    conn,
    cfg=cfg,
    races_path=races_path,
    plan_path=plan_path,   # <-- remove
    ...
)
```

**REMOVE plan_ctx pass to renderer** (runner.py:276-285):
```python
text = report_mod.render_race_readiness(
    ...
    plan_ctx=plan_ctx,    # <-- remove
    ...
)
```

**ADD heat loading + rollup** to `generate_recovery` — model after races loading at line 269. Insertion is around line 297-301:
```python
def generate_recovery(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    heat_path: Path,            # <-- NEW
    reports_dir: Path,
    generated_on: date,
) -> Path:
    series = build_load_series(conn, cfg)
    guardrail = fitness.evaluate_guardrail(series.points)
    heat_ctx = heat_mod.parse_heat(heat_path)          # <-- NEW
    heat_roll = heat_mod.heat_rollup(heat_ctx.sessions, generated_on)  # <-- NEW
    assessment = recovery_mod.assess_recovery_from_db(...)
    ...
    text = recovery_mod.render_recovery(
        ...
        heat_rollup=heat_roll,    # <-- NEW
    )
```
**Import:** add `from runos.analysis import heat as heat_mod` near the other `from runos.analysis import ...` imports (runner.py:23-28).

**ADD heat_path to `generate_all` signature** (mirror what was just removed for plan_path).

**ADD race-link resolution in `build_race_readiness`** (runner.py:170-206). After computing the `upcoming` list at line 182 (or paralleling it on `completed`), call `race_link.link_races_to_activities(upcoming_races, conn)` and zip the results into the per-race findings. Per CONTEXT.md: "Called from `analysis/runner.py` during race-readiness rendering."

**Seam markers:**
- `from runos.analysis import context as ctx` (line 23) stays — `parse_races` is still here.
- `from runos.analysis.context import Race` (line 29) stays.
- Callers of `generate_race_readiness` / `generate_all` need updating: `runos/cli.py:445-457`, `runos/cli.py:487-499`, `runos/sync/daily.py:106-113`.

---

### MOD: `runos/config.py` (paths)

**Self-analog. Exact line targets:**

**REMOVE `plan_path` property** (config.py:146-149):
```python
@property
def plan_path(self) -> Path:
    """Path to the user-maintained training-plan markdown (read for context)."""
    return self.content_root / "plan.md"
```

**ADD `heat_path` property** — directly model on `races_path` (config.py:141-144):
```python
@property
def races_path(self) -> Path:
    """Path to the user-maintained races markdown (read for analysis context)."""
    return self.content_root / "races.md"
```
**Apply to:**
```python
@property
def heat_path(self) -> Path:
    """Path to the user-maintained heat-adaptation log (read for recovery context)."""
    return self.content_root / "heat.md"
```
Insert immediately after `races_path` (between current lines 144 and 146) to keep tracker paths grouped.

**Seam markers:**
- No new env var per CONTEXT.md: "no new env var needed for heat — it's content-dir resolved".
- The `content_root` derived property (config.py:117-124) is the resolution mechanism — heat_path inherits this for free.
- `ensure_dirs` (config.py:151-160) does **not** create races.md / plan.md — it creates dirs only. No change needed.

---

### MOD: `.env.example` (docs)

**Self-analog. Exact line targets:**

**REMOVE plan mention** in the trailing comment block (lines 65-67):
```
# Race-readiness context (PLAN-01/02): RunOS reads `races.md` and `plan.md` from
# the data dir (default ~/.runos/). Copy the committed races.md.example /
# plan.md.example there and edit them. These files are never committed.
```
**Apply to:** rewrite as something like:
```
# Tracker files (read by analyses for context):
#   races.md  -- race-readiness context (PLAN-01)
#   heat.md   -- heat-adaptation sessions (recovery context)
# RunOS reads them from RUNOS_CONTENT_DIR (default ~/.runos/). Copy the committed
# *.md.example templates there and edit them. These files are never committed.
```

No new `RUNOS_*` vars (per CONTEXT.md).

---

### MOD: `races.md.example` (template additions)

**Self-analog. Exact line targets:**

**ADD `result` to recognised keys block** (between lines 20 and 21):
```markdown
- `result` — free-form finish-time string for a past race, e.g. `3:17:42`,
  `1:32:11`, `DNF`, `39:42 (course PB)`. Stored verbatim; no comparison logic.
```

**ADD `## Past races` section header** (after current line 26, before next section) — per CONTEXT.md: "purely for human readability (parser doesn't care about section headers — it already skips lines beginning with `#`)".

**ADD past-race example bullet** under the new section (per CONTEXT.md spec):
```markdown
## Past races

- Local Half - date: 2026-04-12 | distance: half | goal: 1:32:00 | priority: B | result: 1:31:48
```

---

### MOD: `tests/test_context.py` (split + extend)

**Self-analog. Exact line targets:**

**REMOVE** `parse_plan` from imports (lines 14-19) — keep `parse_distance`, `parse_goal_time`, `parse_races`.

**REMOVE the plan section entirely** — lines 98-134:
- `test_parse_plan_missing_file` (lines 101-105)
- `test_parse_plan_captures_headings_and_fields` (lines 108-126)
- `test_parse_plan_first_field_wins` (lines 129-133)
- The `# ---- plan.md ----` ruler comment (line 98)

**REMOVE plan mentions from module docstring** (lines 1-5):
```python
"""Parsing of races.md / plan.md context files, including the missing-file path.

Covers PLAN-01/02. Parsing is lenient: ...
"""
```
→ races-only docstring.

**EXTEND with `result:` parsing test** — model after `test_parse_races_basic` (lines 30-46):
```python
def test_parse_races_basic(tmp_path: Path) -> None:
    p = tmp_path / "races.md"
    p.write_text(
        "# My races\n\n"
        "- Berlin Marathon - date: 2026-09-27 | distance: marathon | goal: 3:15:00 | priority: A\n"
        ...
    )
    ctx = parse_races(p)
    ...
    assert berlin.priority == "A"
```
**Add:** `test_parse_races_result_field` — write a bullet with `result: 1:31:48`, assert the parsed `Race.result == "1:31:48"` (verbatim, no comparison).

**EXTEND with `completed(today)` test** — model after `test_upcoming_sorts_and_filters` (lines 85-95):
```python
def test_upcoming_sorts_and_filters(tmp_path: Path) -> None:
    p = tmp_path / "races.md"
    p.write_text(
        "- Past - date: 2026-01-01 | distance: 5k\n"
        "- Later - date: 2026-12-01 | distance: marathon\n"
        "- Soon - date: 2026-06-01 | distance: 10k\n",
        encoding="utf-8",
    )
    ctx = parse_races(p)
    upcoming = ctx.upcoming(date(2026, 5, 1))
    assert [r.name for r in upcoming] == ["Soon", "Later"]
```
**Apply to:** `test_completed_sorts_and_filters` — same fixture, call `ctx.completed(date(2026, 5, 1))`, assert `[r.name for r in completed] == ["Past"]` (Later is in the future; undated excluded; sort order would be reverse-chronological if multiple).

**Optional: rename the file** — CONTEXT.md notes the deferred `context.py → races.py` rename; if the planner doesn't rename, this test file can stay. If it does, also rename `tests/test_context.py → tests/test_races.py`.

---

### MOD: `tests/test_analysis_reports.py` (drop plan assertions, add heat)

**Self-analog. Exact line targets:**

**REMOVE plan write in `_write_context`** (lines 58-67):
```python
def _write_context(tmp_path: Path) -> tuple[Path, Path]:
    races = tmp_path / "races.md"
    plan = tmp_path / "plan.md"
    races.write_text(...)
    plan.write_text("Phase: Base\nFocus: build aerobic base\n", encoding="utf-8")
    return races, plan
```
→ return just `races` (Path), drop plan; helper signature changes.

**REMOVE `plan_path=` from all `generate_race_readiness` calls** — lines 130-132, 166-172, 182-188, 205-207. Each looks like:
```python
runner.generate_race_readiness(
    conn, cfg=CFG, races_path=races, plan_path=plan, reports_dir=reports, generated_on=GEN_ON
)
```

**REMOVE plan-context assertion** (line 142):
```python
assert "**phase**: Base" in text  # plan.md context
```

**ADD heat section assertion** to `test_recovery` (need new test, no existing analog — but model after `test_race_readiness_report_written_with_predictions` lines 124-143 for structure):
- Write a `heat.md` with 2-3 recent sauna sessions in the same tmp_path as races.
- Call `generate_recovery(conn, cfg=CFG, heat_path=heat, ...)`.
- Assert `"## Heat adaptation"` in the rendered text and the rolling-window numbers appear.
- Add a complementary test that with no heat.md the section is omitted (the degrade-gracefully path from CONTEXT.md).

---

### MOD: `tests/test_analyze_cli.py` (CLI smoke)

**Self-analog. Exact line targets:**

**REMOVE plan_path write in `seeded_cli` fixture** (line 63):
```python
settings.plan_path.write_text("Phase: Base\nFocus: aerobic\n", encoding="utf-8")
```

**REMOVE plan-context CLI assertion** (line 95 in `test_analyze_race_readiness_subcommand`):
```python
assert "**phase**: Base" in text
```

**OPTIONAL: extend `test_analyze_recovery_subcommand`** (lines 98-107) — model:
```python
def test_analyze_recovery_subcommand(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze", "recovery"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-recovery.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# Recovery & Overtraining" in text
    ...
```
Drop heat.md content into `settings.heat_path` in the fixture and assert `"## Heat adaptation"` appears in the rendered recovery report.

---

### MOD: `README.md` (docs)

**Self-analog. Exact line targets:**

**EDIT lines 239-241:**
```markdown
**Plan & race context**: copy `races.md.example` / `plan.md.example` into your
data dir as `races.md` / `plan.md` (default `~/.runos/`) and edit them. RunOS
reads them for race-readiness context; they are never committed.
```
→
```markdown
**Tracker files**: copy `races.md.example` and `heat.md.example` into your
content dir as `races.md` / `heat.md` (default `~/.runos/`) and edit them.
RunOS reads `races.md` for race-readiness context and `heat.md` for heat-adaptation
context in the recovery report; both are never committed.
```

(No other plan.md mentions in README — verified by spot-check of the file.)

---

### DELETED: `plan.md.example`

Delete the file at repo root. No analog needed — pure removal.

---

## Shared Patterns

### Lenient parser convention
**Source:** `runos/analysis/context.py:186-200` (`parse_races`)
**Apply to:** `runos/analysis/heat.py:parse_heat` (NEW)
- Missing file → `(present=False, …=[], source_path=str(path))`.
- Skip empty + `#` lines + non-bullet lines.
- Per-line parser returns `None` on failure; caller filters Nones.
- Never raise on malformed lines — silently drop.

### Frozen+slotted dataclass with `present` flag
**Source:** `runos/analysis/context.py:49-65` (`RacesContext`)
**Apply to:** `HeatContext` (new), and any new `RaceLink` collection wrapper if added.
- `@dataclass(frozen=True, slots=True)`.
- `present: bool` first field.
- `source_path: str | None = None` last field.
- Lists default to `field(default_factory=list)`.

### 0/1/N matching convention
**Source:** `runos/journal/service.py:8-25` (resolve_activity docstring) + behavior
**Apply to:** `link_races_to_activities` (NEW)
- 0 matches → "no link", `activity_id=None`, status: `unlinked_no_match`.
- 1 match → auto-link, `activity_id=<id>`, status: `linked`.
- N>1 matches → refuse to guess. Journal raises; race-link returns `unlinked_ambiguous` (analyses must not crash on multi-activity race days).

### Pure renderer takes already-computed dataclasses
**Source:** `runos/analysis/recovery.py:402-455` (`render_recovery`) and `runos/analysis/report.py:188-279` (`render_race_readiness`)
**Apply to:** heat rendering inside recovery.py.
- Renderer is a pure string builder; it does no DB I/O.
- Inputs are already-computed dataclasses + the freshness header.
- Output is `"\n".join(out)` over a list-of-strings.
- Sections degrade by being **omitted** when their inputs are empty/None (not by emitting "no data" text inside an already-rendered section header).

### Frozen-slot status fields are bare `str`
**Source:** `runos/analysis/recovery.py:65` (`SignalAssessment.status`), recovery.py:244 (`RecoveryAssessment.status`)
**Apply to:** `RaceLink.link_status: str` (with the four documented values in CONTEXT.md). Convention is bare `str` not `Literal[...]` — keeps the dataclasses simple and tests can assert string equality.

### Derived path properties on Settings
**Source:** `runos/config.py:141-149` (`races_path`, `plan_path`)
**Apply to:** new `heat_path` property — same shape, `content_root / "heat.md"`.
- All paths derived from `content_root` (not `data_dir`) so they follow `RUNOS_CONTENT_DIR` overrides.
- No `ensure_dirs` change — these are files, not directories.

---

## No Analog Found

None. Every file in this phase has a clear in-repo analog (either self-analog for surgical edits, or `context.py` / `races.md.example` / `test_context.py` / data.py for the new modules). Phase 8 is intentionally a copy-and-mirror exercise — the architecture is already in place.

---

## Metadata

**Analog search scope:**
- `runos/analysis/` (full directory — read context.py, data.py, recovery.py, report.py, runner.py, noteworthy.py headers)
- `runos/config.py` (full)
- `runos/cli.py` (race-readiness section)
- `runos/sync/daily.py` (race-readiness section)
- `runos/journal/service.py` (0/1/N convention)
- `runos/migrations/0002_structured.sql` (activity.day column shape)
- `tests/test_context.py`, `tests/test_analysis_reports.py`, `tests/test_analyze_cli.py`, `tests/test_config.py`, `tests/test_recovery.py` (header for shape)
- `races.md.example`, `plan.md.example`, `.env.example`, `README.md`

**Files scanned:** ~16
**Pattern extraction date:** 2026-05-27
