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
2. **Resolve macros via the waterfall below** (stop at the first hit). Note the source in your reply so Ross can audit.
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

## Macro resolution waterfall

When Ross doesn't give macros explicitly, resolve them in this order. **Stop at the first hit.** Always tell Ross which source you used — silent matching is the failure mode we're trying to avoid.

1. **Ross gave the macros.** Use them verbatim. Don't second-guess. No source note needed.
2. **Recent food.md history (last ~30 days).** Skim the file for a prior entry of the same food. Match leniently — "Biscoff crunchy spread (50g)" matches "Biscoff" / "biscoff spread" / "biscoff crunchy". If you find one:
   - Reuse those macros, scaled to today's quantity. e.g. log has `bagel (118g): p:8.3 c:40 f:1.9 cal:214`; today he's eating one bagel → use as-is. If he says 60g of something the log has at 100g, scale by 0.6.
   - Reply: `Logged. Matched against <date> entry (<original qty>) → scaled to <today qty>. Day total ...`
3. **Standard, generic foods you know well from training.** Plain banana, raw broccoli, 100g cooked basmati rice, skinless chicken breast, USDA-shape stuff. Use your built-in knowledge.
   - Reply: `Logged. Used standard reference for <food>. Day total ...`
4. **Brand-specific, unfamiliar, or you're not confident.** Use WebSearch to look up the actual product macros. Cite the source briefly.
   - Reply: `Logged. Looked up <product> macros via web (source: <domain>). Day total ...`

**The bias is toward (2) — reuse what we've already logged.** Ross eats the same things repeatedly, and consistent macros make the 7d averages more trustworthy. Only fall to (3) or (4) when there's no prior log for the item.

If you're between (2) and (3) — e.g. the food.md entry is months old and the brand might've changed — prefer (2) but note "macros from older log; double-check if the product changed."

## What to reply

One or two lines. Always include the day's running total after the addition AND the source note from the waterfall (which step you used).

Examples:

- "Logged. Day total now P:38 C:140 F:18 cal:854." (step 1)
- "Logged. Matched MyProtein whey from 2026-05-29 entry (62g → scaled to 59g). Day total now P:60 C:380 F:28 cal:1993." (step 2)
- "Logged. Used standard reference for 1 banana. Day total now P:8 C:94 F:13 cal:526." (step 3)
- "Logged. Looked up SIS Beta Fuel via scienceinsport.com (40g carbs / 158 cal per gel). Day total now P:12 C:314 F:24 cal:1520." (step 4)

## Edge cases

- **Macro source = waterfall above.** See "Macro resolution waterfall" — that's the canonical decision tree.
- **Quantity in opaque terms** ("a handful of nuts", "a small piece"): pick a sensible default (e.g. handful of almonds = ~30g) and note it. Don't refuse to log.
- **Same food, different quantity than the log has.** Scale linearly. Log: `Biscoff (100g): cal:571`. Today: 50g. Use cal:286. State the scaling in your reply.
- **Ambiguous match across multiple recent log entries** (e.g. "chicken" matches three different chicken dishes): pick the closest by name + context (today's meal, recent date), and tell Ross which one you matched. He can correct.
- **Correcting a previous entry.** Append a new line with the same `(date, meal_name, food_label)` triple — the parser's latest-wins kicks in. Tell Ross which line you superseded.
- **Block-after-inline parser caveat.** The parser handles inline-after-block correctly (post-fix 2026-05-28), but prefers cleanly-separated blocks. If today is split across both formats, that's fine — just be consistent within a meal.
- **The food.md.example file is committed as a reference** — read it if format details are ambiguous.
- **`RUNOS_TARGET_KCAL` in .env** enables goal-tracking. If it's set, the recovery report shows a 7d delta vs target. You can mention this if Ross is curious.
