---
name: log-strength-session
description: Use when Ross describes a lift / strength / conditioning session, OR pastes content from his Strong app, OR mentions specific exercises with weights and reps. Triggers on phrases like "did legs", "deadlifts", "bench", "squats", "pasted from Strong", "lower body session", "upper body", "push day", "pull day". Appends a properly-formatted block to `training/strength.md` — never destructively rewrites.
---

# log-strength-session

When Ross logs a strength session, append it to `training/strength.md` in the locked block format.

## The format (REQUIRED — the lenient parser depends on this)

```
## YYYY-MM-DD [HH:MM] [— Session name]
rest: M:SS                                  (optional)
notes: free-form text                       (optional)

- Exercise Name (Equipment) [Superset-group]: 40x8, 50x8, 55x7, 55x8
- Bodyweight Exercise [A]: 15, 15, 15        (bare integers = bare reps)
- Plank: 1:00, 1:00, 0:30                    (M:SS = timed hold)
```

Concrete example:

```
## 2026-05-28 18:19 — Lower body
rest: 1:30
notes: pogos + SLGB supersetted

- Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8
- Hip Thrust (Barbell): 50x10, 55x10, 55x10, 55x10
- Seated Leg Curl (Machine): 25x12, 30x12, 30x12
- Calf Press (Leg Press): 80x16, 80x16, 80x16, 80x16
- Pogos [A]: 15, 15, 15
- Single Leg Glute Bridge [A]: 8, 8, 8
- Plank: 1:00, 1:00, 1:00, 0:30
```

### Set grammar

Three flavours, parsed in this order:

| Flavour | Looks like | Meaning |
|---|---|---|
| Weighted | `40x8` | 40 kg × 8 reps |
| Timed hold | `1:00` | 1 min 00 sec hold |
| Bodyweight | `15` | 15 reps, no weight |

### Header rules

- ISO date `YYYY-MM-DD` is required.
- Time `HH:MM` (24-hour, local) is optional.
- Session name is optional. The separator (`—` em-dash or `-` hyphen) is also optional now (post-fix 2026-05-28). `## 2026-05-26 Lower body` works.

## How to log it

1. **Read the existing `training/strength.md`** so you see what's already there. The content_dir is at `~/Projects/RunOS/training/` (or wherever `RUNOS_CONTENT_DIR` points).

2. **Append the new block at the end of the file.** Never edit existing blocks — corrections happen by appending a new block with the same date (latest-wins).

3. Use the Write tool or `cat >> path/to/strength.md` via Bash. A single `cat >>` is fine because blocks are self-contained.

4. **Verify by running** `parse_strength` after the write:

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from runos.analysis.strength import parse_strength
ctx = parse_strength(Path('training/strength.md'))
last = ctx.sessions[-1]
print(f'{last.date} {last.start_local or \"\"} — {last.name or \"\"}: {len(last.exercises)} exercises, {sum(len(e.sets) for e in last.exercises)} sets')
"
```

## How to handle Strong-app pastes

The user sometimes pastes raw output from the Strong app. Strong format looks roughly like:

```
Lower body
26 May 2026 6:19 PM
Romanian Deadlift (Barbell)
1: 40 kg × 8
2: 50 kg × 8
...
```

Convert that to the RunOS format. The exercise name (with equipment in parens) stays the same; reduce each set to `WxR` and join with `, `. Drop the leading `1:`, `2:` set numbers.

## What to reply

Short and concrete. Include the exercise count + total tonnage if non-trivial.

Examples:

- "Logged. Lower body / 7 exercises / 9,835 kg tonnage."
- "Logged. Upper body / 5 exercises / 4,200 kg tonnage."
- "Logged. Pogos + SLGB session, bodyweight only."

You can compute total tonnage from the rollup:

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
from pathlib import Path
from datetime import date
from runos.analysis.strength import parse_strength, strength_rollup
ctx = parse_strength(Path('training/strength.md'))
r = strength_rollup(ctx.sessions, date.today())
print(f'tonnage_7d={r.tonnage_kg_7d}, sessions_7d={r.sessions_7d}, last_session={r.last_session_name}')
"
```

## Edge cases

- **Superset labels in `[brackets]`.** Two exercises in the same session sharing a label are understood to be supersetted. Ross labels them explicitly with `[A]` / `[B]` / etc. Preserve his labels verbatim.
- **Equipment in `(parens)`.** `(Barbell)`, `(Machine)`, `(Cable)`, `(Dumbbell)`, `(Leg Press)`. Stored verbatim. Bodyweight movements typically omit equipment.
- **Mixed units.** RunOS stores weights verbatim — assumed kg. If Ross says lb, store the number as-is but flag in the notes (`notes: weights in lb`). Don't convert.
- **Rest period.** `rest: M:SS` is per-session metadata. If he doesn't mention it, omit the line.
- **Header without separator.** `## 2026-05-26 Lower body` works after the fix. If you're not sure if the version is fixed, use the em-dash anyway: `## 2026-05-26 — Lower body`.
- **A name-only exercise like `Plank: 1:00, 0:30`** with timed holds: those are M:SS strings, parsed as timed holds. Don't confuse with sets named "1" and "00".
