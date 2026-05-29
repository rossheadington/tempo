# COROS.md

How the Coros connector works, how to set it up, and how it sits alongside the
Garmin connector during the v1.7 switch-over. Added in Phase 18.

## 1. What it does

The Coros connector pulls daily wellness (HRV, resting HR, sleep stages,
stress, steps) and EvoLab analytics (VO2max, stamina level, training load,
threshold HR + pace) from the unofficial Coros Training Hub API. The raw JSON
is written into `raw_response` keyed by ISO date; pure transforms project it
onto the structured `wellness_day` table (shared with Garmin) and the new
`coros_evolab_day` table. The recovery report grows a `## Coros (EvoLab)`
section after the existing tracker cluster.

Activities continue to flow from Strava — Coros syncs to Strava already, so
de-duplicating against Strava is intentionally out of scope for v1.7 (deferred
to a later phase if useful).

## 2. Setup

Three steps:

1. **Edit `.env`.** Add your Coros email + password:

   ```
   RUNOS_COROS_EMAIL=you@example.com
   RUNOS_COROS_PASSWORD=your-coros-password
   ```

   Then `chmod 600 .env` so other local users cannot read the file. If you
   prefer not to put the password in `.env`, leave it blank — the next step
   will prompt for it.

2. **Run the one-time login.**

   ```
   runos coros login
   ```

   This performs the email + MD5(password) handshake, prints
   `Coros authorised.`, and persists `{access_token, user_id}` atomically into
   `~/.runos/tokens/coros/token` (mode 0600, directory 0700). You should not
   need to run this command again unless you change your Coros password or
   delete the token bundle.

3. **Done.** The hourly sync (`runos sync`, scheduled via launchd) now pulls
   recent wellness + EvoLab automatically alongside Strava and Garmin.

For an on-demand pull:

```
runos coros sync
```

## 3. Fragility profile

Coros is an **unofficial-API** source like Garmin, but in practice it's been
quieter:

| | Garmin | Coros |
|---|---|---|
| Auth | SSO + MFA, fragile `garminconnect` lib | email + MD5(password), thin client |
| Rate limit / 429 | known per-account lockout (48h+ if you retry) | none documented |
| 2FA / CAPTCHA | yes | no |
| Web session side-effect | none | API login invalidates `training.coros.com` browser session (mobile app unaffected) |

Both connectors are wrapped in `runos/sync/pipeline.py` with the same
symmetric isolation contract: any failure surfaces as a degraded
`SourceResult(ok=False, ...)`, never propagates, and never blocks the other
sources or the analysis layer.

**On a 401**: the connector silently re-authenticates ONCE using credentials
from `.env`, persists the new token, retries the original call. A second 401
becomes `CorosAuthError` and the pipeline isolates it. The connector
**never busy-loops on auth failure**.

## 4. Wellness priority resolver (Coros wins, Garmin fills gaps)

`wellness_day` is a single row per day with no `source` column — intentionally
flat. The transform layer reconciles overlapping Coros + Garmin data via a
per-metric resolver:

```
for each metric in (resting_hr, hrv_last_night, sleep_seconds, deep_s, ...):
    wellness_day[metric] = coros_value if coros_value is not None else garmin_value
```

Execution order is enforced by `runos/transforms/runner.py`: the **Garmin
wellness transform runs FIRST** (populates the day), the **Coros wellness
transform runs SECOND** (overwrites any metric where Coros has a value).

**Why this design**: during the Garmin → Coros switch-over the owner may wear
both watches (or only one on a given day). The resolver means the report
keeps working in every combination — Coros-only, Garmin-only, or both.

**During transition**: leave both connectors active. Coros covers the new days
cleanly; Garmin remains the safety net if Coros's API ever breaks.

## 5. EvoLab field reference

The `coros_evolab_day` table (schema migration `0006_coros_evolab.sql`,
introduced in Phase 18) stores one row per local calendar day:

| Column | Units | Meaning |
|---|---|---|
| `day` | `TEXT` (ISO `YYYY-MM-DD`) | Primary key; the local calendar date the value was computed for. |
| `vo2max` | ml/kg/min | Coros's VO2max estimate (running). |
| `stamina_level` | integer 0–100 | Coros's base-fitness score. Higher = more fitness reserve. |
| `training_load` | integer | Coros's load score for the day (units per Coros's dashboard). |
| `lthr` | bpm | Lactate-threshold HR — cross-check vs `preferences.md` `threshold_hr`. |
| `ltsp_s_per_km` | seconds per km | Lactate-threshold speed/pace, normalised to s/km. Cross-check vs `preferences.md` `threshold_pace`. |
| `fetched_at` | ISO 8601 UTC | When this row was written from raw. |

All metric columns are nullable — Coros does not always populate every field.
The recovery report's `## Coros (EvoLab)` section renders whatever is present.

## 6. Threshold pace from EvoLab is info-only

Coros reports a threshold pace via EvoLab and we surface it in the recovery
report, but **we deliberately do NOT auto-overwrite `preferences.md`**.

Your `preferences.md` is hand-edited and reflects your felt threshold — the
pace you can hold for ~60 minutes on a good day, validated against actual race
performances. Coros's number is an algorithmic estimate from your recent
workouts. The two often agree within a few seconds; when they diverge, it's a
conversation, not a silent overwrite.

If Coros's pace consistently matches what you feel, edit `preferences.md`
manually — the source of truth stays in the file you own.

## 7. Troubleshooting

| Symptom | Likely cause + fix |
|---|---|
| `runos coros sync` reports `not authenticated: ...` | The persisted token has expired and the refresh failed (credentials wrong or missing). Re-run `runos coros login`. |
| `runos coros sync` reports `skipped: ...` with a 5xx detail | Transient upstream API failure. The hourly sync will retry next hour automatically; no action needed. |
| `runos coros login` fails with `Coros login rejected (result='0007')` or similar | Wrong password or wrong email. Double-check `RUNOS_COROS_EMAIL` / `RUNOS_COROS_PASSWORD`. |
| Web UI at `training.coros.com` is logged out after `runos coros login` | Documented side effect of the API login. Re-log in to the web UI; the mobile app is unaffected. |
| Recovery report shows the `## Coros (EvoLab)` section but no values | Coros payload was empty for that day (no data synced from the watch yet). Wear the watch and let it sync; the next hourly run will pick the data up. |
| Recovery report omits the `## Coros (EvoLab)` section entirely | No EvoLab rows exist yet. Run `runos coros sync` and `runos transform`, then re-render. |

If Coros is genuinely down, **Garmin's already-stored raw + the priority
resolver mean the wellness report keeps working on Garmin data alone**. The
connector isolation contract is the safety net.

## 8. Removing Garmin entirely

Once Coros has covered every day reliably for a few weeks and you no longer
want to maintain a Garmin login, retire the Garmin connector by removing its
call from `run_full_sync` in `runos/sync/pipeline.py`:

```python
# Delete the Garmin block in run_full_sync(...):
# try:
#     garmin = build_garmin_connector(settings)
# except Exception as exc:
#     ...
# else:
#     results.append(run_garmin_sync(conn, garmin))
```

The Garmin connector code, transforms, and historical raw rows can stay in
place — they're inert if never invoked. If you ever want to come back, just
re-add the call.

This is a one-line change with no schema impact. Recommended only after at
least two weeks of clean Coros sync history.
