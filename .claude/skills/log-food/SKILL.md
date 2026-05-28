---
name: log-food
description: Use when Ross describes a meal, snack, or specific food with macros. Trigger phrases include "had breakfast", "for lunch", "ate", "just had", "snack", any food description, or any macro values (protein/carbs/fat/calories). Appends to `training/food.md` in either inline or block format.
---

# log-food

When Ross logs food, append to `training/food.md`. The parser accepts two formats — pick based on the shape of what he's logging.

## Two formats — when to use which

### Format A — inline (single food item)

```
- YYYY-MM-DD <meal>: <food> | p:<g> c:<g> f:<g> cal:<n>
```

Use for: single-item meals, snacks, drinks.

```
- 2026-05-28 snack: 500ml Monster Energy original | p:0 c:60 f:0 cal:237
- 2026-05-28 snack: 1 banana | p:1.3 c:27 f:0.4 cal:105
- 2026-05-28 breakfast: 2 eggs on toast | p:18 c:28 f:14 cal:310
```

### Format B — block (multi-item meal)

```
## YYYY-MM-DD <meal>
- <food>: p:<g> c:<g> f:<g> cal:<n>
- <food>: p:<g> c:<g> f:<g> cal:<n>
```

Use for: meals with multiple components broken down by item.

```
## 2026-05-28 breakfast
- bagel (toasted, halved): p:8.3 c:40 f:1.9 cal:214
- salted butter (~1 tbsp, both halves): p:0.1 c:0 f:11.5 cal:102

## 2026-05-28 lunch
- bagels (2, toasted): p:16.6 c:80 f:3.8 cal:428
- honey roasted ham (39.4g): p:8.2 c:1.3 f:1.1 cal:48
- iceberg lettuce (57g): p:0.5 c:1.7 f:0.1 cal:8
- cucumber (47g): p:0.3 c:2.0 f:0.1 cal:7
```

**Important:** if a `## block` for `(date, meal)` already exists today, append the new item INSIDE that block. Don't create a duplicate block header — keep meals consolidated.

## Macro key grammar

- `p:`, `c:`, `f:`, `cal:` — required. Order is free (`cal:303 p:13 c:54 f:6` works). Case-insensitive.
- Floats accepted for p/c/f. `cal:` is int (rounds: `cal:303.4` → 303).
- Missing any one = entry skipped as malformed. All four required.

## How to log it

1. **Read `training/food.md`** to see today's entries and pick the right format.
2. **Estimate macros where Ross doesn't provide them.** Use a reasonable database value (USDA or common labels) and FLAG the assumption in `notes:` or in your reply.
3. **Append** to `training/food.md`. Block-format: insert under the existing `## YYYY-MM-DD <meal>` header if one exists; create a new block otherwise.
4. **Verify:**

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from datetime import date
from runos.analysis.nutrition import parse_food, daily_nutrition
ctx = parse_food(Path('training/food.md'))
d = daily_nutrition(ctx.entries, date.today())
if d:
    print(f'today total: P:{d.protein_g}g C:{d.carbs_g}g F:{d.fat_g}g cal:{d.kcal}')
    print(f'macro split: P:{d.macro_pct_protein:.0f}% / C:{d.macro_pct_carbs:.0f}% / F:{d.macro_pct_fat:.0f}%')
"
```

## What to reply

One or two lines. Always include the day's running total after the addition.

Examples:

- "Logged. Day total now P:38 C:140 F:18 cal:854."
- "Logged butter at ~1 tbsp (102 cal) — flagged my assumption. Adjust if it was lighter."
- "Logged Monster (210 cal). Day total now P:8 C:94 F:13 cal:526."

## Edge cases

- **He gives macros explicitly** ("214 cal, 40 carbs, 8.3 protein, 1.9 fat"): use them verbatim. Don't second-guess.
- **He doesn't give macros** ("had a bagel with butter"): use reasonable defaults from common food databases, flag the assumptions explicitly. Better to be transparent than precise.
- **Quantity in opaque terms** ("a handful of nuts", "a small piece"): pick a sensible default (e.g. handful of almonds = ~30g) and note it. Don't refuse to log.
- **Correcting a previous entry.** Append a new line with the same `(date, meal_name, food_label)` triple — the parser's latest-wins kicks in. Tell Ross which line you superseded.
- **Block-after-inline parser caveat.** The parser handles inline-after-block correctly (post-fix 2026-05-28), but prefers cleanly-separated blocks. If today is split across both formats, that's fine — just be consistent within a meal.
- **The food.md.example file is committed as a reference** — read it if format details are ambiguous.
- **`RUNOS_TARGET_KCAL` in .env** enables goal-tracking. If it's set, the recovery report shows a 7d delta vs target. You can mention this if Ross is curious.
