---
name: log-weight
description: Use when Ross gives a weight reading. Trigger phrases include "weighed", "weight", "<NN> kg", "<NN> lb", "weighed myself", "scales", "weigh-in". Appends a single line to `training/weight.md` — latest-wins on same-day duplicates.
---

# log-weight

When Ross gives a weight reading, append one line to `training/weight.md`.

## The format

```
- YYYY-MM-DD: <weight> [kg|lb] [| notes: <free text>]
```

- Date is ISO YYYY-MM-DD.
- Weight is a float (one decimal usually).
- Unit defaults to `kg` if omitted. Accept `kg`, `lb`, `lbs` (normalised to `lb`).
- Notes are optional.

Concrete examples:

```
- 2026-05-28: 72.4 kg | notes: morning, fasted
- 2026-05-27: 72.6 kg
- 2026-05-26: 73.1 kg | notes: post-strength, hydrated
- 2026-05-20: 160.2 lb | notes: travelling, hotel scale
```

## How to log it

1. **Read `training/weight.md`** first to see the existing entries.
2. **Append the new line at the end.** Latest-wins on same-date — if today already has an entry and he gives a new value, just append; the parser keeps the last.
3. **Verify:**

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from datetime import date
from runos.analysis.weight import parse_weight, weight_rollup
ctx = parse_weight(Path('training/weight.md'))
r = weight_rollup(ctx.entries, date.today())
if r.latest_entry:
    print(f'latest: {r.latest_entry.weight} {r.latest_entry.unit}')
    print(f'7d avg: {r.avg_7d:.1f} kg, 28d avg: {r.avg_28d:.1f} kg, trend: {r.ewma_trend:.1f} kg')
    if r.delta_vs_28d is not None:
        print(f'delta vs 28d: {r.delta_vs_28d:+.1f} kg')
"
```

## What to reply

One line, with the trend if meaningful.

Examples:

- "Logged. 72.4 kg. Trend down 0.5 kg vs 28d baseline."
- "Logged. 72.6 kg. Steady (within 0.1 kg of trend)."
- "Logged. 160.2 lb (≈72.7 kg). First weigh-in on hotel scale — added a notes line."

If there's a meaningful trend signal (gain or loss > 0.5 kg vs 28d), mention it briefly. Don't moralise.

## Edge cases

- **Time of day matters less than consistency.** Ross usually weighs in the morning, fasted. If he weighs at a different time, log the `notes:` so the trend isn't muddled.
- **Mixed units in the log.** The parser normalises to kg internally and flags `unit_mixed=True` if both kg and lb appear. The recovery report's `## Weight` section shows the kg-normalised number with a footnote. Don't try to convert — store verbatim.
- **Implausible value.** Parser sanity-checks at 20 < kg < 500. If he gives `7.24` (decimal slip), confirm — don't log.
- **Multiple readings same day.** Latest-wins. If he reweighs, just append; the parser drops the earlier one.
- **He says "no change" or "same as yesterday".** Don't log a duplicate — ask for the actual number or skip.
