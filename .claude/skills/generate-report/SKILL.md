---
name: generate-report
description: Use when Ross asks for a specific named report. Trigger phrases include "recovery report", "load trend", "race readiness", "correlations", "nutrition report", "today's recovery", "how's my CTL", "show me the report". Runs `runos analyze <type>` and returns the rendered markdown.
---

# generate-report

When Ross asks for a specific report, run the right `runos analyze` subcommand and show him the result.

## The five reports

| Report | Command | What it shows |
|---|---|---|
| Recovery | `runos analyze recovery` | Today's verdict (elevated risk / monitor / clear), load drivers (CTL ramp + ACWR), recovery markers (HRV / resting HR / sleep vs baseline), tracker sections (heat / strength / weight / nutrition where data exists) |
| Load trend | `runos analyze load-trend` | Daily load series, CTL / ATL / TSB / ACWR over time, ramp rate guardrail |
| Race readiness | `runos analyze race-readiness` | Goal race + projected fitness on race day, target pace feasibility (Riegel + VDOT), training-load posture |
| Correlations | `runos analyze correlations` | Pairwise Pearson correlations across HRV, sleep, resting HR, training load — what's actually moving together in Ross's data |
| Nutrition | `runos analyze nutrition` | Today's totals, per-meal breakdown, 7-day rolling P/C/F/cal, 28-day kcal mean, goal delta (if `RUNOS_TARGET_KCAL` set) |

## How to run it

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run runos analyze recovery
```

The CLI writes the report to `<reports_dir>/<YYYY-MM-DD>-<type>.md` and prints the path. Read that file with the Read tool and return its content (or the relevant section) to Ross.

For all five at once:

```bash
RUNOS_CONTENT_DIR=$(pwd)/training uv run runos analyze
```

## What to reply

**Two-layer reply:**

1. **The short version** — a 2-3 sentence verbal summary highlighting the actionable signal.
2. **The detail** — the report markdown if Ross asked for the full thing, or a relevant slice if he asked something narrower ("how's my CTL?").

Examples:

> "Recovery is showing **elevated overtraining risk**: TSB is at -22 and resting HR is 1.7 z-scores above baseline. Easy day tomorrow is the call."
>
> [full recovery report markdown]

> "CTL 78, ramp +12 over the last 28 days — that's in the aggressive zone. ACWR 1.4 is fine. Form (TSB) at -18 is deep but not extreme."

> "Nothing useful from correlations this month — too few data points in the HRV stream to be reliable. Need another two weeks."

If Ross asked for one specific number ("what's my CTL"), give just that number with one line of context: "CTL is 78 today. Up 4 from last week."

## Edge cases

- **Stale data.** If the report's freshness header shows last-sync was >24h ago, mention it: "Note: data is 2 days stale — Garmin hasn't synced since 26 May. Numbers below are slightly behind reality."
- **Insufficient data.** Reports degrade gracefully — sections with insufficient data flag themselves. Don't invent fitness numbers. If recovery report says "insufficient HRV history", tell Ross.
- **Empty trackers.** If he asks for nutrition and `food.md` has no entries, say so: "No food logged this week. Want me to add today's meals first?"
- **Ross asks "is this good or bad?"** Interpret with context. CTL trending up alone isn't good or bad — depends on the phase of training and his goal race date. Reference his races.md when interpreting.
- **He asks for "the report" with no qualifier.** Default to `recovery` — that's the highest-frequency ask. Confirm: "Running recovery — say if you wanted a different one."
- **He asks for multiple at once** ("recovery and race readiness"). Run them in sequence; return both.
