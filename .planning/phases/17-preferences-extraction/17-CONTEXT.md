# Phase 17: Preferences Extraction — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline owner spec from conversation 2026-05-28. Locked design — no discuss-phase needed. This is an **architectural refactor**, not a new user-facing feature. Net behaviour change for the user: their threshold pace / max HR / resting HR / kcal target move OUT of `.env` and INTO a new human-edited markdown file. Plus a new units preference (miles vs km) and a free-form weekly-training-shape prose section the coach can reference.

<domain>
## Phase Boundary

**What this phase delivers (Layer 1):**

- A new `preferences.md` tracker file in the user's content dir (`<content_root>/preferences.md`). UNLIKE other trackers (food.md, weight.md, races.md, etc.) which are append-only logs, this is an **edit-in-place profile**. The coach reads it as prose; the analysis layer reads typed sections as structured config.
- A new module `runos/analysis/preferences.py` defining frozen+slots `Physiology` / `Units` / `Nutrition` / `PreferencesContext` dataclasses plus a lenient `parse_preferences(path)` function. Mirrors `runos/analysis/races.py` / `weight.py` in shape (lenient, never raises, `present=False` on missing file).
- A new module `tempo/units.py` providing `format_distance(metres, units)` / `format_pace(s_per_km, units)` — the single source of truth for unit conversion at the display boundary. Storage stays SI everywhere; conversion happens only when rendering.
- Migration of four currently-in-`.env` knobs OUT of `Settings` and INTO `preferences.md`:
  - `RUNOS_THRESHOLD_PACE_S_PER_KM` → `Physiology.threshold_pace_s_per_km`
  - `RUNOS_MAX_HR` → `Physiology.max_hr`
  - `RUNOS_RESTING_HR` → `Physiology.resting_hr`
  - `RUNOS_THRESHOLD_HR` → `Physiology.threshold_hr`
  - `RUNOS_TARGET_KCAL` → `Nutrition.target_kcal`
- A new `Settings.preferences_path` derived property (mirrors `food_path` / `weight_path`).
- Plumbing: `runos/analysis/runner.py` reads `parse_preferences(settings.preferences_path)` ONCE per CLI invocation, then threads `Physiology` / `Nutrition` into the existing analysis modules (`load.py` already takes a `LoadConfig` — feed it from `Physiology`; `recovery.py` and `runner.py::generate_*nutrition*` take a `target_kcal: int | None` — feed it from `Nutrition.target_kcal`).
- `runos/cli.py` updated at every `analyze` entry point so `settings.target_kcal_default` reads become `prefs.nutrition.target_kcal`. Same for the four physiology knobs.
- `runos/sync/daily.py` updated identically.
- Report renderers updated to use the Units formatter for the user-facing distance column (`runos/analysis/report.py::render_load_trend` and the noteworthy / race-readiness / recovery renderers wherever km appears). DB columns + math stay in SI (metres, m/s, s/km).
- `.env.example` updated — the four migrated knobs deleted, replaced by a short comment pointing to `preferences.md.example`.
- `docs/PREFERENCES.md` documenting the file format end-to-end (typed sections grammar, lenient-parsing contract, prose section conventions, agent-edit-in-place guidance).
- `preferences.md.example` committed at repo root (mirrors `weight.md.example` / `food.md.example`).
- Tests: `tests/test_preferences.py` (parser happy/malformed/missing-file paths, threshold-pace format variants, units enum parsing), `tests/test_units.py` (formatter conversion correctness, edge cases), plus targeted edits to `tests/test_load.py` / `tests/test_recovery.py` / `tests/test_nutrition.py` / `tests/test_cli.py` / `tests/test_runner.py` (any test that currently sets `RUNOS_THRESHOLD_PACE_S_PER_KM` etc. now builds a `Physiology` directly).

**What this phase does NOT deliver (explicitly out of scope, deferred):**

- **Auto-loading `preferences.md` into the bot's session bootstrap** so the coach always has it in context without an explicit Read. Considered for inclusion but defer: the coach can `Read training/preferences.md` reactively when planning conversations come up; auto-injection is a Phase 18 polish.
- **A `tempo preferences edit` CLI** (mirror of `runos journal add`). The file is meant to be hand-edited; no CLI surface for now.
- **Backward-compat fallback** that reads the old `RUNOS_THRESHOLD_PACE_S_PER_KM` etc. from `.env` if `preferences.md` is missing the section. Solo-user project, single cut-over. The old env vars are GONE from `Settings` after this phase. `.env.example` documents the move with a comment.
- **Units beyond miles/km and min/km / min/mile.** No support for nautical miles, no metric/imperial weight mixing (weight tracker already supports kg/lb normalisation at parse time — orthogonal). Pace formatter handles only the two main options.
- **Restructuring weight tracker's kg/lb handling** to also flow through `Units`. Weight is normalised at the parser boundary, not at display. Leave it alone.
- **A structured DB table** for `preferences`. The markdown is the source of truth; rederivable means the file is the artifact.
- **Per-block training-template structured parsing.** The "Training week" section in `preferences.md` is FREE-FORM PROSE — the coach reads it, the analysis layer ignores it. No parser for "Monday: easy doubles".

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### File layout & locations

- `preferences.md` lives in `config.content_root` (so the user's `~/Projects/RunOS/training/preferences.md` resolves naturally via `RUNOS_CONTENT_DIR`).
- `preferences.md.example` committed at repo root (sibling of `food.md.example` / `weight.md.example`).
- New module file: `runos/analysis/preferences.py`.
- New module file: `tempo/units.py` (top-level, NOT under `analysis/` — it's a presentation-layer helper, not an analysis).

### `preferences.md` format (LOCKED)

A single Markdown file with NAMED H2 sections. Sections are independently optional — a section missing means the typed fields default to `None`. Free-form prose between sections is tolerated and ignored by the parser (but visible to the coach when it reads the file).

```markdown
# Preferences

## Physiology
threshold_pace: 4:00/mi          # accepted forms: M:SS/mi, M:SS/km, NNN s/km, NNN
max_hr: 188
resting_hr: 40
threshold_hr: 172                # optional; if absent the load.py ~0.92*max_hr fallback kicks in

## Units
distance: miles                  # km | miles
pace: min_per_mile               # min_per_km | min_per_mile

## Nutrition
target_kcal: 2800                # optional; enables goal tracking in nutrition + recovery reports

## Training week
<free-form prose — coach reads, parser ignores>
Monday: easy doubles, ~12 mi
Tuesday: threshold day (sometimes double threshold, sometimes threshold + easy)
Wednesday: medium-long, 14-16 mi steady
Thursday: single easy
Friday: marathon-pace work OR easy (inverse of Sunday)
Saturday: easy
Sunday: long with marathon-pace work OR pure easy (inverse of Friday)
Note: don't typically do MP work on both Fri AND Sun in the same week.

## Goals / current focus
- Serpent 100k 4 Jul (A-race)
- Berlin Marathon 27 Sep (A-race)
<free-form>
```

### Section grammar (LOCKED)

- **H2 headers** (`## <Name>`) delimit sections. Header text is case-insensitive and stripped (`## physiology`, `## Physiology`, `##   PHYSIOLOGY   ` all map to the same section).
- Inside a typed section (`Physiology`, `Units`, `Nutrition`), each non-blank, non-comment line is parsed as `key: value`. Whitespace tolerated. Trailing `# comment` stripped.
- Unknown keys inside a typed section → ignored silently (lenient).
- Unparseable values inside a typed section → that field stays `None`, line number captured in `malformed_lines`. NEVER raises.
- Free-form sections (`Training week`, `Goals / current focus`, anything else) → captured as raw text in `PreferencesContext.prose_sections: dict[str, str]` keyed by lowercased header. The parser does NOT interpret them.
- Lines BEFORE the first H2 header → ignored (covers the `# Preferences` H1 + any preamble).

### Threshold-pace parsing (LOCKED)

Accept these forms, normalise to `int` seconds-per-km internally:

| Input         | Parsed (s/km) | Notes |
|---------------|---------------|-------|
| `4:00/mi`     | 149           | `mi` → convert from min/mi to s/km (1 mile = 1.609344 km) |
| `6:26/mi`     | 240           | M:SS/mi |
| `4:00/km`     | 240           | M:SS/km direct |
| `240 s/km`    | 240           | bare seconds with unit |
| `240`         | 240           | bare integer assumed s/km |
| `4 min/mi`    | malformed     | malformed; needs colon for seconds component |
| `garbage`     | malformed     | caught, field stays None |

Single helper function `_parse_threshold_pace(value: str) -> int | None` lives in `runos/analysis/preferences.py`. Returns `None` for unparseable input; the caller records the malformed line.

### Units parsing (LOCKED)

`distance` accepts: `km`, `kilometres`, `kilometers`, `mi`, `mile`, `miles`. Normalises to `"km"` or `"miles"`.
`pace` accepts: `min_per_km`, `min/km`, `mpk`, `min_per_mile`, `min/mi`, `mpm`. Normalises to `"min_per_km"` or `"min_per_mile"`.

Defaults when section missing or fields absent: `distance="km"`, `pace="min_per_km"` (matches current behaviour).

### Dataclasses (LOCKED)

All `@dataclass(frozen=True, slots=True)`, in `runos/analysis/preferences.py`:

```python
@dataclass(frozen=True, slots=True)
class Physiology:
    threshold_pace_s_per_km: float | None = None
    max_hr: int | None = None
    resting_hr: int | None = None
    threshold_hr: int | None = None

@dataclass(frozen=True, slots=True)
class Units:
    distance: Literal["km", "miles"] = "km"
    pace: Literal["min_per_km", "min_per_mile"] = "min_per_km"

@dataclass(frozen=True, slots=True)
class Nutrition:
    target_kcal: int | None = None

@dataclass(frozen=True, slots=True)
class PreferencesContext:
    present: bool                            # False if file missing
    physiology: Physiology
    units: Units
    nutrition: Nutrition
    prose_sections: dict[str, str]           # lowercased-header -> raw section body
    path: Path | None
    malformed_lines: tuple[int, ...]
```

`parse_preferences(path: Path) -> PreferencesContext` — lenient, never raises. Missing file → `PreferencesContext(present=False, physiology=Physiology(), units=Units(), nutrition=Nutrition(), prose_sections={}, path=None, malformed_lines=())`.

### Units formatter (LOCKED)

`tempo/units.py` exposes pure functions (no class needed — operates on a `Units` value):

```python
def format_distance(metres: float, units: Units, *, precision: int = 1) -> str:
    """Return a display string: '12.9 km' or '8.0 mi'."""

def format_pace(s_per_km: float, units: Units) -> str:
    """Return a display string: '4:30 /km' or '7:14 /mi'."""

def km_to_miles(km: float) -> float: ...
def miles_to_km(mi: float) -> float: ...
def s_per_km_to_s_per_mile(s_per_km: float) -> float: ...
```

Conversion constant: `KM_PER_MILE = 1.609344` (canonical, NIST).
- `format_pace`: divides s/km by 60 → MM:SS, no decimal. Handles seconds rollover (`4:90` becomes `5:30`). Returns `"–"` (en-dash) for `s_per_km is None`, `<= 0`, or NaN.
- `format_distance`: rounds to `precision` decimals. Returns `"–"` for None / negative.

Tests cover: round-trip km↔miles, pace 4:00/km → 6:26/mi, edge cases.

### Wire-up plan (LOCKED)

| File | Change |
|------|--------|
| `runos/config.py` | DELETE `target_kcal_default`, `threshold_pace_s_per_km`, `max_hr`, `resting_hr`, `threshold_hr` Field declarations. ADD `preferences_path` derived property pointing at `content_root / "preferences.md"`. |
| `runos/analysis/runner.py` (~line 217) | Replace `settings.threshold_pace_s_per_km` / `settings.max_hr` / etc. with `prefs.physiology.*`. Each entry point (`generate_load_trend`, `generate_recovery`, `generate_race_readiness`, `generate_correlation`, `generate_nutrition`, `generate_all`) takes an optional `prefs: PreferencesContext` argument; if not provided, calls `parse_preferences(settings.preferences_path)` lazily. |
| `runos/cli.py` (lines ~726, 799, 854 and around the `analyze` command group) | Same change — load `prefs` at the top of each analyze command, pass through. |
| `runos/sync/daily.py` (lines ~66, 112) | Same. |
| `runos/analysis/report.py` (lines 153, 157 — "Distance (km)" column) | Accept `units: Units` parameter on the render functions; column header label switches based on `units.distance` (`"Distance (km)"` vs `"Distance (mi)"`); cell value uses `format_distance(distance_m, units)`. |
| `.env.example` | Delete the four moved knobs. Add a short comment block (10 lines) explaining they moved to `preferences.md` and pointing at `preferences.md.example`. |
| `.env` (live, owner's file) | Owner-managed; handled in the final migration task, not by an executor. |

### Lenient-parsing contract (LOCKED)

- Missing file → `PreferencesContext(present=False, ...)`. Never raises.
- Malformed value in typed section → field stays default (`None` for physiology/nutrition, default unit for units), line number captured in `malformed_lines`.
- Unknown key in typed section → silently ignored.
- Unrecognised section header → captured as a free-form prose section.
- Unicode-safe; tolerates trailing whitespace; tolerates BOM; tolerates mixed line endings; tolerates Windows CRLF.

### Test scope (LOCKED)

`tests/test_preferences.py`:
- `test_parse_preferences_missing_file_returns_absent_context`
- `test_parse_preferences_empty_file_returns_defaults`
- `test_parse_preferences_physiology_section_happy_path`
- `test_parse_preferences_threshold_pace_format_mi`
- `test_parse_preferences_threshold_pace_format_km`
- `test_parse_preferences_threshold_pace_bare_seconds`
- `test_parse_preferences_threshold_pace_malformed_captured`
- `test_parse_preferences_units_section_happy_path`
- `test_parse_preferences_units_aliases_normalised`
- `test_parse_preferences_units_default_when_section_absent`
- `test_parse_preferences_nutrition_section_target_kcal`
- `test_parse_preferences_unknown_keys_in_typed_section_ignored`
- `test_parse_preferences_unknown_section_captured_as_prose`
- `test_parse_preferences_inline_comments_stripped`
- `test_parse_preferences_case_insensitive_section_headers`
- `test_parse_preferences_lines_before_first_h2_ignored`
- `test_parse_preferences_prose_section_preserved_verbatim`

`tests/test_units.py`:
- `test_format_distance_km_default`
- `test_format_distance_miles_converts`
- `test_format_distance_precision_argument`
- `test_format_distance_none_returns_dash`
- `test_format_pace_km_default`
- `test_format_pace_mile_converts_correctly_at_known_pivot`  (4:00/km == 6:26/mi)
- `test_format_pace_seconds_rollover_handled`
- `test_format_pace_none_or_zero_returns_dash`
- `test_km_to_miles_roundtrip`

Affected existing tests (`test_load.py`, `test_recovery.py`, `test_nutrition.py`, `test_nutrition_report.py`, `test_correlation.py`, `test_cli.py`, `test_runner.py`): any test that currently sets `RUNOS_THRESHOLD_PACE_S_PER_KM` / `RUNOS_MAX_HR` / `RUNOS_RESTING_HR` / `RUNOS_THRESHOLD_HR` / `RUNOS_TARGET_KCAL` via `monkeypatch.setenv` either (a) constructs a `Physiology(...)` / `Nutrition(...)` directly and passes it through, or (b) writes a `preferences.md` to a tmp content dir. Choice per test — direct construction is simpler when not exercising the parser.

Stdlib + pytest's `tmp_path` only. No new test deps.

### Out-of-band safety items

- `preferences.md` may contain personal physiology numbers. It MUST live in the gitignored content dir. `preferences.md.example` in the repo MUST use placeholder values (`threshold_pace: 4:00/km`, `max_hr: 190`, `resting_hr: 50` — generic textbook numbers, not the owner's actual values).
- The parser MUST NOT log the parsed VALUES on malformed-line warnings. Log the LINE NUMBER only.
- DB columns + storage stay SI. Units conversion happens ONLY at the renderer boundary. No "stored in miles" anywhere.

### Code organisation conventions

- New module: `runos/analysis/preferences.py`. Mirror `runos/analysis/races.py` shape (lenient parser + frozen dataclasses).
- New module: `tempo/units.py` (top-level, presentation-layer).
- Tests: `tests/test_preferences.py`, `tests/test_units.py`. Mirror modules.
- `Settings.preferences_path` next to `food_path` in `runos/config.py`.
- `.env.example` keeps a brief comment trail documenting the move (`# Moved to preferences.md (Phase 17): RUNOS_THRESHOLD_PACE_S_PER_KM, RUNOS_MAX_HR, RUNOS_RESTING_HR, RUNOS_THRESHOLD_HR, RUNOS_TARGET_KCAL — see preferences.md.example`).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Direct-mirror references

- `runos/analysis/races.py` — primary template for `preferences.py` parser shape (lenient, never raises, `present=False` on missing, malformed-line capture).
- `runos/analysis/weight.py` — secondary template; same lenient contract.
- `runos/analysis/nutrition.py` — tertiary template; recent (Phase 16), shows the modern lenient-parser shape including `malformed_lines: tuple[int, ...]`.
- `runos/config.py:131-184` — current home of the five migrated knobs. DELETE these fields. ADD `preferences_path` next to `food_path` (line 244).
- `runos/analysis/runner.py:200-260` — current `LoadConfig` construction from `settings.threshold_pace_s_per_km` etc. This is the central refactor point. Replace with `prefs.physiology` reads.
- `runos/analysis/load.py:61-72` — `LoadConfig` dataclass (already takes the four physiology fields). NO CHANGE to this dataclass — just change the construction site in `runner.py` to read from `Physiology` not `Settings`.
- `runos/analysis/recovery.py:415, 500, 790` — `target_kcal` flows. Source becomes `prefs.nutrition.target_kcal` instead of `settings.target_kcal_default`.
- `runos/cli.py:726, 799, 854` — three sites passing `settings.target_kcal_default`. Replace all with `prefs.nutrition.target_kcal`.
- `runos/sync/daily.py:66-69, 112` — symmetric replacement to runner.py/cli.py.
- `runos/analysis/report.py:37, 153-157` — distance display points; thread `units` through and use `format_distance`.

### Format / docs templates

- `docs/NUTRITION.md` — most recent docs template (Phase 16). `docs/PREFERENCES.md` follows its shape.
- `food.md.example` / `weight.md.example` — committed-template shape.
- `.env.example` (Load & analysis settings section, lines ~95-115) — what gets deleted, and the comment-only stub that replaces it.

</canonical_refs>

<specifics>
## Specific Ideas

- The owner's content dir resolves to `~/Projects/RunOS/training/`. The live `preferences.md` lands there. The wizard's content-dir step (Phase 14) already set `RUNOS_CONTENT_DIR`; this phase relies on that.
- Threshold-pace parsing accepts both `/mi` and `/km` because the owner thinks in miles but the storage layer is SI. The parser does the conversion at parse time — downstream code only ever sees `int` seconds-per-km.
- Free-form prose sections are captured but UNINTERPRETED. The coach reads them via `Read training/preferences.md` in conversation; the analysis layer never touches them.
- This is an architectural refactor, NOT a feature add. User-visible change: one fewer concept in `.env`, one new markdown file, distance/pace render in miles+min/mile if they set the Units section. That's it.
- The migration of the LIVE `preferences.md` (with the owner's actual values + the dictated weekly-training-shape prose) is a manual one-step task, NOT something an executor agent does. The executor agents only touch code, `.env.example`, and `preferences.md.example`.

</specifics>

<deferred>
## Deferred Ideas

- **Auto-load `preferences.md` into bot session bootstrap** — Phase 18 polish. The coach can `Read` it reactively for now.
- **`tempo preferences edit` CLI** — out of scope; manual edit.
- **`tempo preferences show`** that pretty-prints the parsed `PreferencesContext` — nice debug surface, defer.
- **Structured prose-section parsing** (weekly template as YAML / TOML inside the markdown) — speculative; defer until needed.
- **Settings backward-compat fallback** to the old `.env` keys — solo project, single cut-over, no need.
- **Per-section presence tracking** (`physiology_present: bool`) — `Physiology()` defaults are all `None` so callers can already detect emptiness; no separate flag.
- **Re-flowing kg/lb weight handling through `Units`** — orthogonal; weight parser handles its own normalisation at parse time.
- **Generalising the prose-section idea to other trackers** (a `## Notes` section in `food.md`?) — speculative; defer.

</deferred>

---

*Phase: 17-preferences-extraction*
*Context written from owner's inline spec: 2026-05-28*
