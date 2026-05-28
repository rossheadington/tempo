# Nutrition log (`food.md`)

**Status:** Authoritative for Phase 16 (NUTR-03 through NUTR-05).

Tempo has no nutrition-tracking UI by design. The owner maintains a
hand-edited `food.md` markdown file in the content dir (default
`<content_root>/food.md`, redirect with `TEMPO_CONTENT_DIR`), and Tempo
reads it for the standalone `tempo analyze nutrition` report and a
`## Nutrition` section in the recovery report. The markdown layer comes
first by intent: the format has to prove itself in real use before a
structured DB table earns its place (the MyFitnessPal CSV importer is
reclassified to v2 `NUTR-CSV-01`, and the chosen markdown format is
deliberately importer-friendly so the future CSV importer becomes
mechanical). The Telegram bot may append entries on request via the same
shapes the user uses by hand.

The committed `food.md.example` at the repo root is both documentation of
the format and a parser fixture exercised by `tests/test_nutrition.py`.

---

## Two interchangeable formats

`food.md` accepts BOTH formats freely intermixed within a single file:

- **Format A — Inline.** One entry per `-` bullet, date and meal on every
  line. Best for quick single-item logging via `cat >> food.md`.
- **Format B — Block-per-meal.** A `## YYYY-MM-DD <meal>` header followed
  by nested `- <food>: <macros>` bullets. Best for multi-item meals.

The choice is **purely ergonomic** — the parser produces identical
`FoodEntry` records from either form (same `date`, `meal_name`,
`food_label`, `protein_g`, `carbs_g`, `fat_g`, `kcal`; differing only on
`source_format` and `source_line`). This is the **equivalence guarantee**
— the downstream rollup, standalone report, and recovery-report
integration all read combined `FoodContext.entries` and don't distinguish.

---

## Format A — Inline

Entry shape:

```
- <YYYY-MM-DD> <meal>: <food> | p:<g> c:<g> f:<g> cal:<n>
```

- **`<YYYY-MM-DD>`** — required, ISO date. Malformed → line skipped.
- **`<meal>`** — required, free-form meal name up to the first `:`,
  lowercased + stripped on parse. Common values: `breakfast`, `lunch`,
  `dinner`, `snack`, `pre-run`, `post-run`. There is no enum.
- **`<food>`** — the opaque verbatim food label (`80g rolled oats`,
  `1 banana`). The parser does NOT split quantity from food name.
- **`|`** — separator. The food label runs from the meal-`:` to the
  **first `|`**; macros run from there to end of line. An embedded `|` in
  the food label is captured into the macros segment (the lenient macro
  scanner ignores non-macro text and just finds `p:` / `c:` / `f:` /
  `cal:` tokens), so `- 2026-05-26 snack: handful of almonds | with
  raisins | p:6 c:11 f:14 cal:180` parses with
  `food_label='handful of almonds'`.
- **Macros** — see `## Macro key grammar` below.

Worked example:

```markdown
- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303
- 2026-05-28 lunch: chicken salad bowl | p:38 c:22 f:18 cal:404
- 2026-05-28 dinner: salmon + rice + greens | p:42 c:60 f:14 cal:558
- 2026-05-28 snack: 1 banana | p:1.3 c:27 f:0.4 cal:105
```

Four `FoodEntry`s, all on `2026-05-28`, `source_format="inline"`.

---

## Format B — Block-per-meal

Header line:

```
## <YYYY-MM-DD> [<meal>]
```

- **`<YYYY-MM-DD>`** — required, ISO date. **If the header date is
  malformed, the WHOLE block (header + every nested bullet up to the next
  valid `## YYYY-MM-DD`) is skipped**, and each affected line is recorded
  in `FoodContext.malformed_lines`.
- **`<meal>`** — optional, free-form, lowercased + stripped on parse.
  Missing → `meal_name=None`.

Nested bullet:

```
- <food>: p:<g> c:<g> f:<g> cal:<n>
```

Same grammar as Format A but with no leading date/meal prefix (the
enclosing `##` header supplies them). A new `## `-header (or end of file)
closes the previous block. Blank lines within a block are tolerated. Use
single-`#` headers (`# Section name`) for prose section dividers in
`food.md` — those are silently ignored; only `## YYYY-MM-DD` is parsed
as a block header.

Worked example:

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

Six `FoodEntry`s + two `MealBlock`s on `FoodContext.blocks`. All entries
have `source_format="block"`.

---

## The equivalence guarantee

This Format A line:

```markdown
- 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303
```

and this Format B block:

```markdown
## 2026-05-28 breakfast
- 80g rolled oats: p:13 c:54 f:6 cal:303
```

parse to `FoodEntry`s with the same `date=2026-05-28`,
`meal_name='breakfast'`, `food_label='80g rolled oats'`,
`protein_g=13.0`, `carbs_g=54.0`, `fat_g=6.0`, `kcal=303`. Only
`source_format` and `source_line` differ. The downstream rollup,
standalone report, and recovery integration all treat both forms
identically. This is why the agent can pick inline-vs-block per turn
based purely on ergonomics — without ever altering the semantic data
stream.

---

## Macro key grammar

Shared by both formats; appears after the `|` in Format A or after `:` on
a nested bullet in Format B.

- **Four required keys:** `p:<float>` (protein g), `c:<float>` (carbs g),
  `f:<float>` (fat g), `cal:<int>` (kcal — rounding-tolerant; `cal:303.4`
  → `303`).
- **Key order is FREE.** `cal:303 p:13 c:54 f:6` parses identically to
  `p:13 c:54 f:6 cal:303`.
- **Keys are case-insensitive.** `P:13 C:54 F:6 CAL:303` works; stored
  lowercased.
- **Whitespace around `:` and between keys is tolerated.**
- **Unknown keys are silently ignored.** `p:13 c:54 f:6 cal:303 fibre:5
  sodium:230` parses without error — `fibre` / `sodium` are dropped.
  Forward-compat by design: future micronutrient extensions add columns
  without breaking existing logs.
- **Missing ANY required key → entry skipped** (line captured in
  `malformed_lines`). Macros are the source of truth, so an entry
  without all four is unusable.
- **Float-parse failure on p/c/f → skipped.** Int-parse on `cal:`
  tolerates a decimal and rounds (`cal:303.7` → `304`).

---

## Lenient parsing

The parser is built to **never break** on user-edited markdown:

- **Missing file** → `FoodContext(present=False, entries=(), blocks=(),
  path=None, malformed_lines=())`. Analyses still run; the recovery
  report's `## Nutrition` section is omitted entirely; the standalone
  report renders a placeholder.
- **Malformed line** (bad date, missing required macro key, unparseable
  float) → the 1-indexed line number lands in
  `FoodContext.malformed_lines`. Skipped; parsing continues.
- **Block header with a bad date** → the whole block (header + every
  nested bullet up to the next valid `## YYYY-MM-DD`) is skipped, each
  affected line recorded as malformed.
- **Comments + noise.** Single-`#` headers are silently ignored; only
  `## ` (double-hash) is parsed as a Format B header. Blank lines and
  non-bullet prose are silently ignored, NOT recorded.
- **BOM + trailing whitespace + mixed line endings** → tolerated
  transparently.
- **The parser NEVER raises.**

For privacy, the parser **never logs entry contents** — only line numbers
are surfaced in `malformed_lines`.

---

## Latest-wins on duplicates

If the same `(date, meal_name, food_label)` triple appears more than once,
the **LAST occurrence in file order wins**. Different `food_label`s under
the same `(date, meal_name)` are kept as **separate entries** (a multi-
item meal — chicken + rice + greens are three rows under
`(2026-05-28, lunch)`, not one).

To correct an earlier entry, append a new line/block with the same
triple. Append-only is the convention; the agent SHOULD NOT modify
existing lines.

---

## Daily aggregation

`daily_nutrition(entries, day)` returns a `DailyNutrition` summarising one
date. It sums P/C/F (floats) and kcal (int) across every `FoodEntry` with
`entry.date == day`. Macro percentages are computed from **kcal-share**
AFTER summation, using the standard Atwater 4-4-9 coefficients:

- `macro_pct_protein = (protein_g * 4) / kcal * 100`
- `macro_pct_carbs   = (carbs_g   * 4) / kcal * 100`
- `macro_pct_fat     = (fat_g     * 9) / kcal * 100`

Computed AFTER summation (not averaged from per-entry percentages) so a
big-kcal entry weighs proportionally more. When `kcal == 0` (degenerate
sum) all three percentages return `0.0` rather than raising.

---

## Rollup semantics

`nutrition_rollup(entries, today, target_kcal=None)` produces a
`NutritionRollup` over a reference date (the CLI / runner passes
`date.today()`).

- All averages use **left-open right-closed** windows `(today - N,
  today]`. A same-day log counts; `today - N` itself is EXCLUDED.
- **`latest_day`** — the `DailyNutrition` for the most recent date with
  entries (`None` if `entries` is empty).
- **`days_since_last`** — `(today - latest_day.date).days` (≥ 0).
- **`avg_7d`** — averages across **days WITH entries** in the 7-day
  window, NOT across all 7 calendar days. A day with zero entries is NOT
  in the denominator. The averaged `DailyNutrition` has `date=today` and
  `macro_pct_*` recomputed from the averaged grams.
- **`days_logged_7d`** — count of distinct dates in `(today - 7, today]`
  with ≥ 1 entry. Surfaced so the report line can pair the average with
  "(D days logged of 7)".
- **`avg_28d_kcal`** — scalar 28-day trailing mean kcal (int) in
  `(today - 28, today]`. Deeper 28-day trend is deferred to v2.
- **`target_kcal`** — optional; sourced from
  `Settings.target_kcal_default` (which reads `TEMPO_TARGET_KCAL` via a
  `validation_alias`). `None` when unset.
- **`deficit_surplus_7d`** — `avg_7d.kcal - target_kcal` (positive =
  surplus, negative = deficit). `None` when either side is `None`.

---

## Standalone report (`tempo analyze nutrition`)

`tempo analyze nutrition` writes `reports/<YYYY-MM-DD>-nutrition.md` for
today (or `--date YYYY-MM-DD` for a back-date). The top-level
`tempo analyze` aggregator also runs the nutrition report alongside the
existing four. Sections in order:

- **Header banner** — date + data-freshness line: `Data: food.md present
  (N entries across D days, last 2026-05-27)`.
- **`## Today's totals`** — `P:38g · C:54g · F:6g · cal:303 (28/52/18
  P/C/F %)`. If today has zero entries: `_No entries logged for today
  yet._`.
- **`## Per-meal breakdown`** — one subheader per `(date=today,
  meal_name)`. **Omitted** when today has zero entries.
- **`## 7-day rolling average`** — `P:122g · C:312g · F:64g · cal:2310 (D
  days logged of 7)` plus a macro-pct line. If `days_logged_7d == 0`:
  `_No entries in the last 7 days._`.
- **`## 28-day kcal mean`** — `2235 kcal/day` or `_Insufficient
  history._`.
- **`## Goal`** — `Target 2200 kcal/day · 7d delta +110 kcal/day`
  (signed). **Omitted** when `target_kcal_default` is unset.

---

## Recovery report integration

Parsed entries surface in the recovery report as a `## Nutrition` section,
placed **after `## Weight`** so the report's non-running-context cluster
reads `Heat → Strength → Weight → Nutrition`.

The renderer follows the standard **3-state degradation rule**:

- **Absent** — file missing (`present=False`) OR zero entries ever →
  section omitted entirely.
- **Stale** — entries exist but `days_since_last > 3` → one-line nudge:
  `_Last food entry N days ago — log today's meals to keep the rollup
  live._`. **The 3-day staleness threshold is intentional** — food is
  logged daily (unlike weight, where the threshold is 14 days), so a
  3-day gap is enough to call the rollup unreliable.
- **Current** — `days_since_last <= 3` → 7-day-trailing line:

  ```
  ## Nutrition
  7d avg P:122g · C:312g · F:64g · cal:2310 (D days logged of 7)
  ```

  When `target_kcal` is set, a second line is appended with a signed
  delta: `Target 2200 kcal/day · 7d Δ +110 kcal/day` (Unicode `+` / `−`
  / `±`).

---

## Optional goal tracking (`TEMPO_TARGET_KCAL`)

Set `TEMPO_TARGET_KCAL=2200` in `.env` to enable goal tracking. The
`Settings.target_kcal_default` field reads it via a `validation_alias` on
the bare env-var name. When set, both the standalone report's `## Goal`
section and the recovery-report `## Nutrition` goal-suffix line light up.
When unset (the default), both surfaces silently omit the goal display
— no warning, no error. The variable is optional by design.

---

## Agent-append guidance

Two append shapes, both append-only:

- **Inline (Format A)** — single-line:

  ```
  echo "- 2026-05-28 lunch: chicken salad | p:38 c:22 f:18 cal:404" >> food.md
  ```

  A plain `cat >> food.md` works because the parser is line-based and
  order doesn't matter (the rollup re-sorts at parse time).

- **Block (Format B)** — multi-line but still append-only; the agent
  writes the `## YYYY-MM-DD <meal>` header and all nested bullets in one
  shot:

  ```
  cat <<EOF >> food.md
  ## 2026-05-28 dinner
  - salmon (180g): p:36 c:0 f:14 cal:280
  - sweet potato (200g): p:3 c:42 f:0.2 cal:185
  - broccoli (150g): p:4 c:11 f:0.6 cal:55
  EOF
  ```

To correct an earlier entry, append a new line/block with the same
`(date, meal_name, food_label)` triple — latest-wins resolves it. The
agent **SHOULD NOT modify existing lines**. Both formats may coexist in
the same file freely.

---

## Relationship to MyFitnessPal exports

MyFitnessPal does not offer a public personal-data API, but its CSV
export is well-known. The `food.md` format is **deliberately importer-
friendly** — a future `tempo food import <csv>` connector (reclassified
to v2 `NUTR-CSV-01`) becomes mechanical: each MFP row maps to one Format
A inline line. The four required macro keys (`p` / `c` / `f` / `cal`) are
exactly the four columns every MFP export carries, in any order, so the
mapping is a direct rename.

A future importer is out of scope for Phase 16; the markdown layer must
prove itself in real use first.

---

## What's NOT in this layer

Deferred (per Phase 16 CONTEXT):

- **MyFitnessPal CSV importer** (`tempo food import <csv>`) —
  reclassified to v2 `NUTR-CSV-01`.
- **Structured DB tables** (`food_entry`, `meal_block`,
  `daily_nutrition`) — rederivable from the markdown source once the
  markdown proves itself in real use.
- **`tempo food add` CLI command** (mirror of `tempo journal add`) —
  manual edit or agent-append for now.
- **Per-meal-type rollups** (avg breakfast kcal across the week) —
  Layer 2 once the daily rollup proves itself.
- **Micronutrients** (fibre, sugar, sodium, vitamins) — macros + calories
  only. Unknown macro keys are silently ignored today, so a future
  extension can add columns without breaking existing logs.
- **Food-database lookups / autocomplete** — out of scope; the user /
  agent computes macros at write time.
- **Unit conversion for food quantities** — quantity is an opaque string;
  macros are the source of truth.
- **Richer goal tracking** (weekly deficit, projected weight loss,
  per-macro targets) — `target_kcal_default` + `7d delta` is the
  starting point.
- **Hydration / alcohol tracking** — hydration is separate (out of
  scope); alcohol is treated as any other food (`p:0 c:X f:0 cal:Y`).
