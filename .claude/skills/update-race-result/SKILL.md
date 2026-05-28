---
name: update-race-result
description: Use when Ross is adding a new planned race OR logging the result of a completed one. Trigger phrases include "race", "racing", "next race", "ran the [event]", "did the half", "finished in", "PB", "race result", or any combination of race name + date + time. Edits `training/races.md` directly — adding new race entries or filling in `result:` fields.
---

# update-race-result

When Ross is talking about a race — planned or completed — edit `training/races.md`.

## The format

Each race is a `-` bullet with key/value pairs separated by `|`:

```
- 2026-09-14 - name: Edinburgh Half | distance: 13.1mi | priority: A | target: 1:24 | result: 1:23:47 | strava: 12345678
- 2026-11-23 - name: NYC Marathon | distance: 26.2mi | priority: A | target: 2:55
- 2026-07-12 - name: 5K parkrun TT | distance: 5km | priority: C | target: 17:30
```

### Keys

| Key | Required | Notes |
|---|---|---|
| Date (first token after `-`) | yes | ISO YYYY-MM-DD |
| `name:` | yes | Free-form race name |
| `distance:` | optional | `13.1mi`, `5km`, `42.2km`, `marathon`, `half`, `10K`, `5K` — parser tolerates many forms |
| `priority:` | optional | Single letter `A` / `B` / `C` (A = goal race, C = supporting) |
| `target:` | optional | Target finish time `H:MM:SS` or `MM:SS` |
| `result:` | optional | Actual finish time after the race |
| `strava:` | optional | Strava activity id — auto-filled by `race_link.py` post-race if not provided |
| `notes:` | optional | Free-form |

Unrecognised keys are silently ignored (lenient parser).

## Three operations

### 1. Adding a new planned race

Append a new `-` bullet under the `## Races` (or equivalent) header.

```
- 2026-09-14 - name: Edinburgh Half | distance: 13.1mi | priority: A | target: 1:24
```

Reply: "Added. Edinburgh Half on 14 Sep, A-priority, target 1:24."

### 2. Logging the result of a completed race

**Read** `training/races.md` to find the existing entry. **Append `| result: H:MM:SS`** to that line.

Crucially: **don't rewrite the whole line.** Find the existing bullet, add the `result:` field at the end (or update an existing `result:` if Ross is correcting it).

```bash
# Read the file first to find the race
grep -n "Edinburgh Half" training/races.md
# Then use Edit tool to append the result field
```

If Ross gives a Strava activity id, also append `| strava: <id>`. Otherwise the post-race link sweep (`runos/analysis/race_link.py`) will auto-link based on date + distance match.

Reply: "Logged result. Edinburgh Half: 1:23:47 (target 1:24). 13 seconds under."

### 3. Updating an existing race (date change, target tweak, etc.)

Use the Edit tool to modify the existing line. Race entries are NOT append-only (unlike the other trackers) because future-dated races have no "history" to preserve. Editing in place is correct.

If Ross is significantly changing the race (different distance, different city, postponed by months) — that's a NEW race entry, not an edit. Add a new line and ask if he wants the old one removed.

## How to verify

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from runos.analysis.races import parse_races
ctx = parse_races(Path('training/races.md'))
for r in ctx.races:
    result_str = f' RESULT={r.result}' if r.result else ''
    print(f'{r.date} | {r.name} | priority={r.priority}{result_str}')
"
```

## What to reply

Be brief, include the relevant detail.

Examples:

- "Added. NYC Marathon 23 Nov, A-priority, target 2:55."
- "Logged result: 1:23:47 vs 1:24 target. 13s under — solid execution."
- "Updated target to 1:22 for Edinburgh Half."
- "Date changed to 21 Sep. The original 14 Sep entry is gone."

## Edge cases

- **Race in the past with no entry.** Ross might tell you about a result for a race that was never planned (he just signed up and ran). Add the full entry as a completed race: date + name + distance + result, no `target:` or `priority:`.
- **Multiple races same day** (parkrun + something else). Just two entries on the same date. Distance + name disambiguate.
- **Distance in unusual units.** `13.1mi`, `13.1 miles`, `21.1km` all parse. Stick close to what Ross said.
- **PB / best-time context.** If Ross asks "is this a PB?", read all his completed races at that distance and answer with the previous best. You can query the SQLite `activity` table for Strava-logged race-shaped activities too.
- **He hasn't given a Strava id.** Leave it off — `race_link.py` auto-links post-race based on date + distance proximity.
