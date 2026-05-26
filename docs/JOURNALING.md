# Journaling via Claude: the capture interface

**Status:** Authoritative for Phase 5 (JRNL-01/02/03).

Tempo has no journaling UI by design. The user "tells Claude" how a session felt,
and **Claude captures it by calling the validated `tempo journal add` command** —
never by writing SQL. This document is the contract Claude follows.

---

## The one rule

> **Claude writes structured journal rows ONLY through `tempo journal add`.**
> It never runs `INSERT`/`UPDATE` against the database, and never edits the
> SQLite file directly.

`tempo journal add` is a thin wrapper over the validated service
`tempo.journal.service.add_entry`, which is the single boundary that:

- validates RPE (an integer 1–10; rejects 0, 11, fractional, non-numeric);
- resolves which activity the entry links to, by **local date + sport**;
- computes **sRPE = RPE × duration_minutes** (a subjective load track);
- inserts via parameterised SQL inside a transaction.

This keeps the store trustworthy (Tempo's core value): bad RPE values, orphaned
rows, and accidental writes are impossible through this path. See
`.planning/research/ARCHITECTURE.md` Pattern 5 / Anti-Pattern 4.

---

## How Claude captures an entry

When the user describes a session ("today's tempo felt brutal, like an 8, legs
were dead"), Claude maps the free speech to the structured fields and runs:

```
tempo journal add --rpe 8 --feel "legs dead" --notes "tempo felt brutal" \
    --day 2026-05-25 --sport Run
```

### Arguments

| Flag | Required | Meaning |
|------|----------|---------|
| `--rpe` | **yes** | Session RPE, integer 1–10 (the only required field). |
| `--feel` | no | Short "how it felt" tag (e.g. `strong`, `flat`, `sore`). |
| `--notes` | no | Free-text reflection. |
| `--day` | no | Local date `YYYY-MM-DD` the entry is for. Defaults to **today**. |
| `--sport` | no | Sport used to resolve the activity (e.g. `Run`, `TrailRun`, `Ride`). |
| `--activity-id` | no | Explicitly link to this activity id (disambiguates). |
| `--duration-min` | no | Minutes for sRPE when no activity is linked, or to override a linked activity's duration. |

---

## Activity resolution (date + sport)

`add_entry` looks at the structured `activity` rows on `--day`, optionally
filtered by `--sport` (case-insensitive, matched against Strava's `sport_type`,
e.g. `Run` / `TrailRun`):

- **0 matches** → no link. The entry is recorded as a **rest-day / non-activity
  reflection** (`activity_id` is null). This is valid and expected — rest days
  get journaled too.
- **exactly 1 match** → linked automatically.
- **many matches** (e.g. a double-run day) → **ambiguous**. The command errors
  and lists the candidate activity ids. Claude must re-run with
  `--activity-id <id>` to pick the right one. Tempo never guesses, because a
  wrong link would corrupt later correlation analysis.

An explicit `--activity-id` always wins and skips resolution (it must reference a
real activity).

---

## sRPE: the subjective load track (JRNL-03)

`sRPE = RPE × duration_in_minutes`. Duration is taken, in priority order:

1. an explicit `--duration-min` (always wins — e.g. cross-training that Strava
   never recorded, or a correction);
2. the **linked activity's** moving time (else elapsed time), in minutes.

If neither yields a positive duration, the entry is still saved but `srpe` is
left null (Tempo never invents a load).

**Why it matters:** sRPE is used by the analysis layer as a *fallback* daily
load when the pace/HR-based load (rTSS/hrTSS) is **insufficient** for that day —
for example a treadmill/trail run with no usable pace or HR, or a strength/bike
session with no Strava activity at all. On such a day the daily load series picks
up the sRPE value, flagged with method `sRPE` so reports show the load came from
subjective input. Objective load (rTSS/hrTSS) always wins when available; sRPE
never overrides it.

---

## Examples

A normal run that links automatically and gets sRPE from the activity:

```
tempo journal add --rpe 6 --feel ok --day 2026-05-25 --sport Run
# -> linked to the day's Run; sRPE = 6 × (moving minutes)
```

A rest-day reflection (no activity):

```
tempo journal add --rpe 3 --notes "full rest, legs recovering" --day 2026-05-26
# -> no activity linked; sRPE null
```

Cross-training Strava never saw, with an explicit duration:

```
tempo journal add --rpe 5 --sport Strength --duration-min 45 \
    --notes "gym lower body" --day 2026-05-26
# -> no activity linked; sRPE = 5 × 45 = 225
```

A double-run day (disambiguate):

```
tempo journal add --rpe 8 --day 2026-05-25 --sport Run
# error: 2 activities match ... pass an explicit --activity-id to disambiguate.
tempo journal add --rpe 8 --day 2026-05-25 --activity-id 987654321
# -> linked to activity 987654321
```

Review what's been captured:

```
tempo journal list --limit 10
```

---

## Where the data lives (privacy)

Journal content is **personal data**. It only ever lives in the gitignored
`~/.tempo/tempo.db`. It is never committed to the public repo, never written to
`reports/` in raw form, and never leaves the local machine. This is
non-negotiable (see `.planning/PROJECT.md` privacy constraint).
