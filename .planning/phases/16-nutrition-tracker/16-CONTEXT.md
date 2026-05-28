# Phase 16: Nutrition Tracker — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline spec from owner (conversation 2026-05-28) — full design locked in advance, no discuss-phase needed. Phase 16 closes the v1.5 milestone and **reclassifies NUTR-01 / NUTR-02 from v2 → v1.5** (CSV import becomes a Layer-2 follow-up; the markdown tracker proves the surface first).

<domain>
## Phase Boundary

**What this phase delivers (Layer 1):**

- A new `food.md` tracker file in the content dir (default `<content_root>/food.md`, redirectable via `TEMPO_CONTENT_DIR` — the owner's working dir is `~/Projects/tempo/training/`).
- A new module `tempo/analysis/nutrition.py` defining frozen+slots `FoodEntry` / `MealBlock` / `FoodContext` / `DailyNutrition` / `NutritionRollup` dataclasses plus a lenient `parse_food(path)`, `daily_nutrition(entries, day)`, and `nutrition_rollup(entries, today)` function. Mirrors `tempo/analysis/weight.py` / `tempo/analysis/strength.py` in shape but accepts two interchangeable input formats (inline single-line and block-per-meal).
- `Settings.food_path` derived property in `tempo/config.py` (mirrors `weight_path` / `strength_path` / `heat_path`).
- A new `tempo analyze nutrition` CLI command + `tempo/analysis/nutrition_report.py` (or extension to the existing report renderer) that writes `reports/<date>-nutrition.md` with daily breakdown + 7-day rollup. Date freshness header consistent with the other reports.
- The recovery analysis (`tempo/analysis/recovery.py`) gains a nutrition mini-section attached to `RecoveryAssessment` (`nutrition: NutritionRollup | None`, `nutrition_present: bool`) and the renderer gains a `## Nutrition` section AFTER the `## Weight` section, BEFORE any future trailing section. Same 3-state degradation rule: absent (file missing or empty) → omit; stale (last logged day >3 days ago) → one-line nudge; current → 7-day P/C/F/cal trailing rollup line.
- `assess_recovery_from_db` accepts an optional `food_path: Path | None` argument; the analysis runner (`tempo/analysis/runner.py`) threads it through; the CLI passes `settings.food_path` exactly the way it passes `settings.weight_path` / `settings.strength_path` / `settings.heat_path`.
- A new `tempo analyze nutrition` CLI command (mirrors the existing per-report CLI commands) AND the top-level `tempo analyze` aggregate gains a nutrition report alongside the existing four.
- A committed `food.md.example` template in the repo root showing both formats side-by-side (inline AND block-per-meal in the same example file) as a worked example.
- A new `docs/NUTRITION.md` documenting both formats end-to-end (grammar for each, lenient-parsing contract, equivalence: a meal parsed from inline == a meal parsed from block, agent-append guidance, the relationship to MyFitnessPal exports — the parser format is designed so a future CSV importer can produce the inline form trivially).
- New tests `tests/test_nutrition.py` (parser happy/malformed/missing-file paths + both formats parse equivalently + rollup window math) + extended `tests/test_recovery.py` covering the recovery-report integration + `tests/test_nutrition_report.py` (or extended `tests/test_report.py`) covering the standalone report.

**What this phase does NOT deliver (explicitly out of scope, deferred):**

- A MyFitnessPal CSV importer (`tempo food import <csv>`). Layer 2 follow-up — reclassified as `NUTR-CSV-01` / `NUTR-CSV-02` in REQUIREMENTS.md. The format chosen here is deliberately importer-friendly so the CSV importer becomes mechanical.
- Structured DB tables (`food_entry`, `meal_block`, `daily_nutrition`). The markdown layer must prove useful in real use first. If it does, a follow-up phase adds the structured-table derivation (rederivable from the markdown source — same invariant as the rest of Tempo).
- A `tempo food add` CLI command (mirror of `tempo journal add`). Manual markdown edit only for now; the agent appends directly when asked.
- Macro / calorie GOAL setting + deficit/surplus computation. The dataclass exposes a `target_kcal: int | None` slot but the rollup leaves goal-comparison to the report renderer; setting goals is deferred to a follow-up phase.
- Per-meal-type aggregation (breakfast vs lunch vs dinner vs snacks). `MealBlock.meal_name` is captured but the rollup is daily-total only.
- Micronutrients (fibre, sugar, sodium, vitamins). Macros + calories only.
- Food database lookups / autocomplete. The owner writes what they eat with macros they've already computed (or pulled from MFP); the parser doesn't know "80g rolled oats" implies 303 kcal — the user/agent does.
- Unit conversion for food quantities. Quantity is stored verbatim as metadata, NOT as a numeric value (one `slice`, one `cup`, `80g` are all opaque strings). Macros are the source of truth.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### File layout & locations

- All tracker files live in the user's content dir, resolved via `config.content_dir`. Same pattern as `races_path` / `heat_path` / `strength_path` / `weight_path`.
- New derived path: `food_path` on `Settings` (`content_root / "food.md"`).
- Committed `.md.example` template in the repo root (`food.md.example`), mirroring `weight.md.example` / `strength.md.example`.
- The owner's content dir resolves to `~/Projects/tempo/training/` per their `.env`. The phase MUST NOT hard-code this path; it MUST go through `settings.food_path`. The real file at `training/food.md` is gitignored by the existing `training/` rule.

### `food.md` format (LOCKED) — TWO interchangeable formats

A single lenient parser accepts BOTH formats freely intermixed within the same file. The two formats are semantically equivalent; the choice is purely ergonomic (inline for quick agent-append, block when logging a multi-item meal).

**Format A — Inline (one entry per `-` bullet):**

```markdown
- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303
- 2026-05-28 lunch: chicken salad bowl | p:38 c:22 f:18 cal:404
- 2026-05-28 dinner: salmon + rice + greens | p:42 c:60 f:14 cal:558
- 2026-05-28 snack: 1 banana | p:1.3 c:27 f:0.4 cal:105
```

Each entry: `- YYYY-MM-DD <meal_name>: <food> | p:<g> c:<g> f:<g> cal:<n>`

**Format B — Block-per-meal (header + nested bullets):**

```markdown
## 2026-05-28 breakfast
- 80g rolled oats: p:13 c:54 f:6 cal:303
- 1 banana: p:1.3 c:27 f:0.4 cal:105

## 2026-05-28 lunch
- chicken (200g): p:38 c:0 f:8 cal:230
- mixed greens (150g): p:3 c:8 f:0.5 cal:45
- vinaigrette (1 tbsp): p:0 c:1 f:9 cal:84
- rice (100g cooked): p:2.4 c:28 f:0.3 cal:130
```

Header line: `## YYYY-MM-DD <meal_name>` (mandatory date + meal_name; meal_name is free-form text after the date, lowercased on parse). Nested bullets follow the inline grammar minus the `YYYY-MM-DD <meal>:` prefix: `- <food>: p:<g> c:<g> f:<g> cal:<n>`.

**Meal-name canonicalisation:** any string is allowed; the parser lowercases + strips whitespace. Common values: `breakfast`, `lunch`, `dinner`, `snack`, `pre-run`, `post-run`. If meal_name is missing or unparseable → `meal_name=None` (entry still counts toward daily totals).

**Macro key grammar** (LOCKED):
- `p:<num>` — protein grams. Required. Parseable as float.
- `c:<num>` — carbs grams. Required. Parseable as float.
- `f:<num>` — fat grams. Required. Parseable as float.
- `cal:<num>` — kcal. Required. Parseable as int (rounding tolerance on parse: `303.4` → `303`).
- Order is FREE. `cal:303 p:13 c:54 f:6` is valid.
- Keys are case-insensitive on parse (`P:13` works). Stored lowercased.
- Whitespace around `:` and between keys is tolerated.
- Any unknown key (`fibre:5`, `sodium:230`) → silently ignored (lenient).
- Missing ANY of p/c/f/cal → entry skipped (logged in malformed_lines). The rule: macros are the source of truth, so an entry without all four is unusable.

**Quantity (the bit before `:` in Format B, or between `<meal_name>:` and `|` in Format A)** is stored verbatim as a single opaque string in `FoodEntry.food_label`. The parser does NOT split `80g rolled oats` into quantity + food name. The agent / user picks whatever phrasing they like.

**Comments & noise:**
- Any line starting with `#` (other than the `##` block header) is ignored.
- Blank lines ignored.
- Bullet lines that don't parse as either format → skipped, line number captured in `malformed_lines`. NEVER raises.

**Latest-wins rule:** if the same `(date, meal_name, food_label)` triple appears twice, the LAST occurrence in file order wins (consistent with weight.md). Different `food_label`s under the same `(date, meal_name)` are kept as separate entries (same meal, multiple foods).

**Agent-append guidance** (documented in `docs/NUTRITION.md`):
- Appending a new meal is a single-line operation in Format A: `cat >> food.md` works.
- Appending a multi-item meal in Format B is a few lines but still append-only.
- The agent SHOULD NOT modify existing lines. Corrections happen by appending a fresh entry with the same `(date, meal_name, food_label)` (latest-wins).
- Both formats may coexist in the same file freely.

### Dataclasses (LOCKED)

All `@dataclass(frozen=True, slots=True)`, in `tempo/analysis/nutrition.py`:

```python
@dataclass(frozen=True, slots=True)
class FoodEntry:
    date: date                # ISO date
    meal_name: str | None     # lowercased + stripped, or None if absent/unparseable
    food_label: str           # verbatim opaque string ("80g rolled oats", "1 banana")
    protein_g: float          # required
    carbs_g: float            # required
    fat_g: float              # required
    kcal: int                 # required
    source_line: int          # 1-indexed; for Format B, the bullet line (not the header)
    source_format: str        # "inline" or "block"

@dataclass(frozen=True, slots=True)
class MealBlock:
    date: date
    meal_name: str | None
    entries: tuple[FoodEntry, ...]   # all entries from one ## YYYY-MM-DD <meal> block

@dataclass(frozen=True, slots=True)
class FoodContext:
    present: bool                          # False if file missing
    entries: tuple[FoodEntry, ...]         # dedup'd by (date, meal_name, food_label), latest-wins, sorted by (date, source_line)
    blocks: tuple[MealBlock, ...]          # only Format-B blocks (Format-A entries don't appear here); for renderer convenience
    path: Path | None
    malformed_lines: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class DailyNutrition:
    date: date
    protein_g: float
    carbs_g: float
    fat_g: float
    kcal: int
    macro_pct_protein: float    # 0.0–100.0; computed from kcal-share: (4 * protein_g) / kcal * 100
    macro_pct_carbs: float      # (4 * carbs_g) / kcal * 100
    macro_pct_fat: float        # (9 * fat_g) / kcal * 100
    entry_count: int

@dataclass(frozen=True, slots=True)
class NutritionRollup:
    today: date                              # the reference date passed in
    latest_day: DailyNutrition | None        # the most-recent day in the window with any data
    days_since_last: int | None              # today - latest_day.date in days
    avg_7d: DailyNutrition | None            # 7-day trailing window: mean P/C/F/cal across days WITH entries; macro_pct computed on the means; None if no entries in window
    days_logged_7d: int                      # count of distinct dates in (today-7, today] with entries
    avg_28d_kcal: int | None                 # 28-day trailing mean kcal (single scalar, since deeper trend is the 28d's job in v2)
    target_kcal: int | None                  # optional, sourced from Settings.target_kcal_default if set; otherwise None
    deficit_surplus_7d: int | None           # avg_7d.kcal - target_kcal; None if either is None
```

### Rollup semantics (LOCKED)

- `today` is the reference date passed in (the CLI / runner passes `date.today()`).
- All averages use `(today - N, today]` left-open right-closed windows so a same-day log always counts.
- `daily_nutrition(entries, day)` sums all `FoodEntry`s with `entry.date == day`. Macro percentages computed AFTER summation, from kcal-share: `(protein_g * 4) / total_kcal * 100`. If `kcal == 0` (degenerate) → all three percentages = 0.0.
- `avg_7d` averages across DAYS with entries, not raw entries. A day without entries is not counted in the denominator (so a partial log doesn't drag the average down). `days_logged_7d` is the count for transparency.
- `target_kcal_default: int | None = None` on `Settings`. Optional; the user can set `TEMPO_TARGET_KCAL` in `.env` to enable goal tracking. If unset, `deficit_surplus_7d` is `None` and the report renderer omits the goal line.

### Standalone `tempo analyze nutrition` report (LOCKED)

- New CLI: `tempo analyze nutrition` writes `reports/<YYYY-MM-DD>-nutrition.md` for today (or `--date YYYY-MM-DD` for a back-date).
- Report content (matches the existing report convention from `tempo/analysis/report.py`):
  - Header banner with date + data-freshness line: `Data: food.md present (N entries across D days, last 2026-05-27)`.
  - `## Today's totals` — single line: `P:38g · C:54g · F:6g · cal:303 (28/52/18 P/C/F %)`. If today has no entries: `_No entries logged for today yet._`.
  - `## Per-meal breakdown` — one subheader per `(date=today, meal_name)`, listing the entries. Omitted if no entries today.
  - `## 7-day rolling average` — single line: `P:122g · C:312g · F:64g · cal:2310 (D days logged of 7)` and the macro-pct line. If days_logged_7d == 0 → `_No entries in the last 7 days._`.
  - `## 28-day kcal mean` — `2235 kcal/day` or `_Insufficient history._`.
  - `## Goal` (only if `target_kcal_default` is set) — `Target 2200 kcal/day · 7d delta +110 kcal/day` (with sign).
- Top-level `tempo analyze` (the aggregator) gains nutrition alongside the existing four reports.

### Recovery-report integration (LOCKED)

- `RecoveryAssessment` gains two fields: `nutrition: NutritionRollup | None`, `nutrition_present: bool`.
- The recovery-report renderer adds a `## Nutrition` section AFTER `## Weight`, BEFORE any future trailing section.
- 3-state rule (mirrors weight / strength / heat):
  - **Absent** — `nutrition_present is False` (file missing) OR `entries` empty → section omitted entirely.
  - **Stale** — file exists with entries but `days_since_last > 3` → one-line nudge: `## Nutrition\n_Last food entry N days ago — log today's meals to keep the rollup live._`.
  - **Current** — `days_since_last <= 3` → 7-day trailing line:
    ```
    ## Nutrition
    7d avg P:122g · C:312g · F:64g · cal:2310 (D days logged of 7)
    ```
    If `target_kcal_default` is set and `deficit_surplus_7d` is not None, append a second line: `Target 2200 kcal/day · 7d Δ +110 kcal/day` (sign Unicode `+` / `−` / `±`).

### Lenient-parsing contract (LOCKED)

- Missing file → `FoodContext(present=False, entries=(), blocks=(), path=None, malformed_lines=())`. Never raises.
- Malformed line → skipped, line number captured. NEVER raises.
- Missing required macro key → entry skipped, line captured as malformed.
- Float-parse failure on any of p/c/f → skipped.
- Int-parse failure on `cal:` → skipped (but tolerates rounding, e.g. `cal:303.4` → 303).
- Date parse failures → skipped.
- Block headers with malformed dates → the whole block (header + bullets) skipped, each line captured as malformed.
- Unicode-safe; tolerates trailing whitespace; tolerates BOM; tolerates mixed line endings.

### Test scope (LOCKED)

- `tests/test_nutrition.py`:
  - `test_parse_food_missing_file_returns_absent_context`
  - `test_parse_food_inline_format_happy_path`
  - `test_parse_food_block_format_happy_path`
  - `test_parse_food_both_formats_in_same_file`
  - `test_parse_food_inline_and_block_produce_equivalent_entries` (the equivalence guarantee — same entry inline and block parse to identical `FoodEntry` modulo source_line/source_format)
  - `test_parse_food_unordered_macro_keys`
  - `test_parse_food_case_insensitive_keys`
  - `test_parse_food_tolerates_rounding_on_kcal`
  - `test_parse_food_skips_entries_missing_required_macros`
  - `test_parse_food_unknown_keys_ignored`
  - `test_parse_food_latest_wins_on_same_date_meal_food`
  - `test_parse_food_block_with_malformed_header_skipped`
  - `test_parse_food_ignores_headers_blanks_and_comments`
  - `test_daily_nutrition_sums_entries`
  - `test_daily_nutrition_macro_percentages_kcal_share`
  - `test_daily_nutrition_zero_kcal_degenerate`
  - `test_nutrition_rollup_empty_returns_all_none_with_zero_days_logged`
  - `test_nutrition_rollup_7d_window_left_open`
  - `test_nutrition_rollup_averages_across_days_with_entries_only`
  - `test_nutrition_rollup_target_deficit_surplus_when_set`
  - `test_nutrition_rollup_target_none_when_unset`
- `tests/test_nutrition_report.py` (or extension of test_report.py):
  - `test_nutrition_report_writes_dated_file_to_reports_dir`
  - `test_nutrition_report_today_no_entries_emits_placeholder`
  - `test_nutrition_report_per_meal_breakdown_present_when_today_has_entries`
  - `test_nutrition_report_omits_goal_section_when_target_unset`
- `tests/test_recovery.py` additions:
  - `test_recovery_renderer_omits_nutrition_section_when_absent`
  - `test_recovery_renderer_emits_stale_nudge_when_last_entry_over_3d`
  - `test_recovery_renderer_emits_7d_trailing_rollup_when_current`
  - `test_recovery_renderer_appends_goal_line_when_target_set`
- Stdlib + pytest's `tmp_path` only. No new test deps.

### Out-of-band safety items

- Food intake is sensitive data. The `food.md` file MUST live in the gitignored content dir; the repo MUST NOT contain real entries — only `food.md.example` with anonymised numbers.
- The parser MUST NOT log entry contents. If a malformed-line warning is emitted, it logs the line number only, not the food label or macros.
- `Settings.target_kcal_default` is OPTIONAL and silently absent when `TEMPO_TARGET_KCAL` is unset (no warning).

### Code organisation conventions

- New module: `tempo/analysis/nutrition.py`. Mirror `tempo/analysis/weight.py` structure.
- New module (or extension): `tempo/analysis/nutrition_report.py` for the standalone report rendering. Reuses helpers from `tempo/analysis/report.py` (the header/freshness banner pattern).
- Tests: `tests/test_nutrition.py` (parser + rollup + daily). `tests/test_nutrition_report.py` (or test_report.py extension) for the standalone report. Recovery integration in `tests/test_recovery.py`.
- `Settings.food_path` and `Settings.target_kcal_default` in `tempo/config.py` next to `weight_path`.
- `NutritionRollup` import in `tempo/analysis/recovery.py` next to `WeightRollup`.
- New `.env.example` key: `# TEMPO_TARGET_KCAL=2200   # optional, enables goal tracking in nutrition report`. Add with a comment.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Direct-mirror references (Phases 13, 15 shipped the patterns)

- `tempo/analysis/weight.py` — primary template for `nutrition.py` (parser shape, dataclass shape, rollup shape, lenient-parsing contract). Adapted for two-format input + per-day aggregation.
- `tempo/analysis/strength.py` — secondary template; multi-line-per-session is the closer analogue to block-per-meal.
- `tempo/config.py::Settings.weight_path` — exact pattern for `food_path` derived property.
- `tempo/analysis/recovery.py` — find the weight integration (added in Phase 15-02). The nutrition integration goes immediately after, structurally identical.
- `tempo/analysis/runner.py::generate_recovery` + `generate_all` — find the `weight_path` parameter. Add `food_path` next to it with the same threading.
- `tempo/cli.py` — find the two `analyze recovery` / `analyze all` call sites that pass `settings.weight_path`. Add `settings.food_path` next to them. ALSO add a new `analyze nutrition` command (mirror the existing per-report commands).
- `tempo/analysis/report.py` — the existing standalone-report renderer; `nutrition_report.py` reuses its header/freshness banner helpers.
- `tests/test_weight.py` + `tests/test_recovery.py` (Phase 15-02 additions) — the test patterns. Mirror them.
- `weight.md.example` — the template shape. `food.md.example` follows the same conventions plus shows both formats.
- `docs/WEIGHT.md` — the doc shape. `docs/NUTRITION.md` mirrors it (longer; two formats need separate sections).

### Settings / config

- `tempo/config.py:1-227` — `Settings` class. `food_path` joins `weight_path`, `strength_path`, `heat_path` as derived properties off `content_root`. `target_kcal_default: int | None` joins as a new optional field with `validation_alias="TEMPO_TARGET_KCAL"`.

### Standalone report convention

- `tempo/analysis/report.py` — find an existing report's structure (e.g. recovery report or load-trend report) for the freshness-header / `## sections` pattern.
- `tempo/cli.py::analyze_recovery` / `analyze_correlation` / etc. — existing per-report CLI commands. The new `analyze nutrition` command mirrors them.

</canonical_refs>

<specifics>
## Specific Ideas

- The owner's working dir is `~/Projects/tempo/training/`. The Phase-14 wizard's content-dir step writes `TEMPO_CONTENT_DIR` to `.env`; this phase relies on that already being set.
- The two-format parser is deliberately permissive at the meal-name level — `breakfast`, `Breakfast`, `pre-run`, `post-run`, `late-snack`, `4am-snack` all work. Canonicalisation is lowercase + trim only.
- `cal:` is the only int field; floats with rounding round to nearest int. Everything else stays as a float (P/C/F to one decimal is fine for `1.3 g` banana protein).
- The `(today-7, today]` window for `avg_7d` means: today's data counts, but exactly 7 days ago does NOT. This matches weight.md's window semantics for consistency.
- The `days_since_last > 3` staleness threshold (vs weight's 14) reflects that food is logged daily not weekly; a 3-day gap is enough to call the rollup unreliable.

</specifics>

<deferred>
## Deferred Ideas

- **MFP CSV importer** (`tempo food import <csv>`) — reclassified as v2 `NUTR-CSV-01`. The format chosen here was designed to be the natural output of a CSV importer.
- **Structured DB tables** (`food_entry`, `meal_block`) — derived from the markdown source, rederivable. Layer 2.
- **Per-meal-type rollups** (avg breakfast kcal, etc.) — Layer 2 once the daily rollup proves itself.
- **Micronutrients** (fibre, sugar, sodium) — add columns to `FoodEntry`. Out of scope.
- **Food-database lookups** — out of scope; user/agent computes macros at write time.
- **Goal-tracking deeper** — current setup exposes `target_kcal_default + deficit_surplus_7d`; richer goal tracking (weekly deficit, projected weight loss) is Layer 2.
- **Hydration tracking** — separate metric, separate file. Out of scope.
- **Alcohol tracking** — treated as any other food (p:0 c:X f:0 cal:Y); no special-casing.

</deferred>

---

*Phase: 16-nutrition-tracker*
*Context written from owner's inline spec: 2026-05-28*
