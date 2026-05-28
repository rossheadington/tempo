---
name: log-heat-session
description: Use when Ross mentions a sauna, hot tub, steam room, or any heat exposure with a duration. Trigger phrases include "sauna", "hot tub", "heat session", "steam", "20 minutes hot", or any combo of heat modality + duration. Appends a single line to `training/heat.md`.
---

# log-heat-session

When Ross logs a heat session, append one line to `training/heat.md`.

## The format

```
- YYYY-MM-DD - type: <type> | duration_min: <int> [| notes: <free text>]
```

- `type:` — usually `sauna`. Other values: `hot-tub`, `steam`, `bath`.
- `duration_min:` — integer minutes.
- `notes:` — optional free-form.

Concrete examples:

```
- 2026-05-28 - type: sauna | duration_min: 20
- 2026-05-27 - type: sauna | duration_min: 25 | notes: post-run, deep heat
- 2026-05-26 - type: hot-tub | duration_min: 15
```

## How to log it

1. **Read the existing `training/heat.md`** to see the current format (it has a `# Heat-adaptation sessions` header and a `## Sessions` section).
2. **Append the new bullet under the `## Sessions` header.** Use the Write tool or `cat >> training/heat.md`.
3. **Verify with `parse_heat`:**

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from datetime import date
from runos.analysis.heat import parse_heat, heat_rollup
ctx = parse_heat(Path('training/heat.md'))
r = heat_rollup(ctx.sessions, date.today())
print(f'sessions_7d={r.sessions_7d} ({r.minutes_7d} min), last={r.days_since_last} days ago')
"
```

## What to reply

One line.

Examples:

- "Logged. 20 min sauna. 3 sessions / 65 min in the last 7 days."
- "Logged. 25 min sauna, post-run. Last 7 days: 4 sessions / 90 min."

## Edge cases

- **Duration in seconds.** If he says "30 seconds in the cold plunge", that's not a heat session — politely note "heat tracker is for heat exposure; cold is separate". Don't log it.
- **No duration.** Ask once. Don't guess.
- **Cold exposure / contrast.** Heat tracker is heat-only. Cold gets logged in journal notes if at all (no dedicated tracker yet).
- **Multiple sessions same day.** Just append both lines — the rollup counts each separately.
- **Backdated.** Use the date he names; don't backfill more than ~7 days without confirming.
