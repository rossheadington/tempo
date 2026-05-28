---
name: log-run-journal
description: Use when Ross is describing how a recent run/ride/workout felt. Trigger phrases include "RPE", "felt", "tough", "easy", "comfortable", "smashed", "garbage run", "legs were", "could've pushed harder", or any combination of effort + sensation + a session he just did. Writes a validated journal entry via `runos journal add` so it lands in the journal table and auto-links to the matching Strava activity.
---

# log-run-journal

When Ross tells you about a session he just did, log it as a journal entry.

## What to extract from his message

| Field | How to derive |
|---|---|
| `--day` | Default to today's date in YYYY-MM-DD. If he says "that run yesterday" or "Monday's", adjust accordingly. |
| `--sport` | Almost always `Run`. Other values: `Ride`, `Swim`, `WeightTraining` (rare — use `log-strength-session` instead), `Hike`, `Walk`. Default `Run` if ambiguous. |
| `--rpe` | Integer 1-10. He'll often say "felt about a 6" or "RPE 7". If he just says "easy", that's 3-4; "comfortable" / "steady" = 5-6; "tough" / "threshold" = 7-8; "all-out" / "smashed" = 9-10. If he gives no clear effort signal, ASK rather than guess — `runos journal add` will reject without --rpe. |
| `--feel` | Free-form short tag. Common values: `strong`, `easy`, `tired`, `flat`, `wired`, `heavy-legs`, `pop`. Use one or two words max. If unclear from his message, omit. |
| `--notes` | Verbatim quote of what he said about the session, trimmed of fillers. Keep it under ~200 chars; don't paraphrase his words. |
| `--duration-min` | Optional. If he gave a duration, pass it. Otherwise omit — the post-transform orphan-link sweep will fill it in from the Strava activity once that's synced. |
| `--activity-id` | Optional. Only pass if you've already looked it up and there's exactly one match. The CLI's 0/1/many resolution handles auto-link otherwise. |

## How to run it

```bash
runos journal add \
  --day 2026-05-28 \
  --sport Run \
  --rpe 6 \
  --feel strong \
  --notes "easy 10K, legs had pop after sauna yesterday, pace felt comfortable"
```

Use the Bash tool. Don't invoke via subprocess from inside the agent loop — `runos journal add` writes to SQLite via the validated boundary, which is the only legal write path to the `journal` table.

## What to reply

One line. Examples:

- "Logged. RPE 6 / strong / Run / 28 May."
- "Got it — RPE 8 threshold, tired. Logged."
- "Logged, but no Strava activity to link yet — the next sync will pick it up."

If `runos journal add` reports "Linked to activity NNNN", include that in your one-liner: "Logged, linked to activity 123456789."

## Edge cases

- **Multiple Strava activities on the same day + sport.** The CLI returns "ambiguous; pass --activity-id". Tell Ross there are N options and list them briefly (date + name + distance from `runos strava sync` output or DB query); he picks.
- **He retroactively logs a session from a few days ago.** Use the date he names. Don't backfill across more than ~7 days without confirming the date.
- **He describes a session that hasn't been synced from Strava yet.** Still log the journal entry. The orphan-link hook (`runos/transforms/runner.py`) runs after every `runos transform` and links it then.
- **He gives no RPE signal at all** (just "did a run, was fine"). Ask once: "Quick — RPE for that?" Don't invent a value.
- **He says "wasn't a real session, just a shake-out".** Still log it at RPE 2-3 if he wants. If he says "don't bother logging", don't log.
