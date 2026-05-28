---
name: coach-readout
description: Use when Ross asks an open coaching question — "how am I doing", "give me the picture", "where am I at", "what should I do this week", "how's training", "am I on track". This is the synthesise-everything skill. Pulls together recovery, load, races, journal, weight, nutrition into a verbal coach's read of the situation. Not a report dump — a judgement.
---

# coach-readout

When Ross asks an open coaching question, do the synthesis a real coach would. This is NOT "run the recovery report and paste it" — this is "read the data, form a view, give him the read."

## What to actually do

1. **Pull the current state from multiple sources:**
   - Recent activities (last 7-14 days) — `runos analyze load-trend` OR query `activity` table directly
   - Recovery markers — `runos analyze recovery`
   - Strength/heat/weight/nutrition rollups — read the trackers
   - Upcoming races + their priority — read `training/races.md`
   - Recent journal entries (RPE + feel) — `runos journal list --limit 14`

2. **Form a view**, considering:
   - What's the training phase? (Base, build, peak, taper, recovery)
   - What's the next goal race? How far away?
   - Are load + recovery aligned, or diverging?
   - Anything anomalous in the data (a sudden HRV drop, a 5-day no-log gap, weight trending the wrong way)?
   - What's Ross subjectively saying in his recent journal entries?

3. **Reply with a coach's read.** Brief, opinion-bearing, action-oriented.

## Reply shape

Three parts, ~4-8 sentences total:

```
[Headline judgement — one sentence]

[The "why" — 2-3 sentences citing actual numbers]

[The "so what" — what to do this week, or next 24-48h]
```

## Examples

> **Headline:** You're handling this block, but only just.
>
> **Why:** CTL is 78 with a +12 ramp over the last 4 weeks (aggressive but not red-line). TSB at -18 is deep — your last 6 journal entries average RPE 7.2 vs your usual 6.0 baseline. Resting HR is 1.4 z above baseline.
>
> **So what:** Drop tomorrow's tempo to easy, swap Thursday's intervals for a fartlek with shorter reps. Edinburgh Half is 16 days out and the freshness has to come back before then.

> **Headline:** Solid week. Nothing to fix.
>
> **Why:** Load held at CTL 65, ramp +3 — sustainable territory. HRV is on baseline, sleep averaging 7.4h. Two strength sessions logged, weight steady at 72.4 kg.
>
> **So what:** Plan stays. If you want to push, this is the week to add a quality session.

> **Headline:** Recovery is lagging your effort.
>
> **Why:** You've done 4 hard sessions in 6 days, but every journal note from those days says "tired" or "flat". Resting HR has crept up 4 bpm in a week. Nutrition log is empty since Tuesday.
>
> **So what:** Two days easy, log meals so we can see if you're under-fuelling, then reassess Wednesday.

## What this skill is NOT

- **Not a numbers dump.** Don't paste the full recovery report. That's `generate-report`.
- **Not a cheerleader.** "You're crushing it!" is noise. So is "be sure to rest!". Say what you actually see.
- **Not hedged.** "Maybe consider possibly resting if you feel like it" is useless. Make a call.
- **Not generic.** "Listen to your body" / "trust the process" are bot-shaped. Use his actual numbers.

## When to push back

Sometimes Ross will ask for confirmation he's right ("I'm thinking of doing intervals tomorrow, sound good?"). Look at the data first. If load + recovery say it's a bad idea, say so:

> "I'd hold off. Your last interval session was 4 days ago, RPE 9, and HRV has been below baseline since. One easy day, then intervals Thursday is the better call."

## Useful queries

```bash
# Recent journal entries with feel
RUNOS_CONTENT_DIR=$(pwd)/training uv run runos journal list --limit 14

# Recent activities with load
RUNOS_CONTENT_DIR=$(pwd)/training uv run python -c "
import sqlite3
conn = sqlite3.connect('$HOME/.runos/runos.db')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT day, sport, distance_m/1000.0 as km, avg_hr, name FROM activity ORDER BY day DESC LIMIT 14'):
    print(f'{r[\"day\"]} | {r[\"sport\"]} | {r[\"km\"]:.1f} km | hr={r[\"avg_hr\"]} | {r[\"name\"]}')
"

# Latest recovery + load + race-readiness reports
RUNOS_CONTENT_DIR=$(pwd)/training uv run runos analyze
```

Synthesise — don't just dump.
