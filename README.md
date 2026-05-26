# Tempo

Personal training & health data pipeline. Pulls running and wellness data from
multiple sources, stores it in a structured, queryable form, and runs scheduled
Claude analyses on top of it.

> **Privacy:** this repo holds *code only*. All credentials, tokens, and actual
> health data are gitignored and stay local. See `.gitignore`.

## Data sources

| Source | Method | Status |
|---|---|---|
| **Strava** | Official REST API (OAuth2) — activities + streams (HR, pace, GPS, power, cadence) | Done |
| **Garmin** | Unofficial `garminconnect` — sleep, HRV, body battery, resting HR, stress, steps | Done (isolated) |
| **MyFitnessPal** | No official API — deferred (CSV-drop ingest later) | Deferred |

## Approach

- **Local-first.** No servers. Data lives in a local SQLite database.
- **Two-layer storage.** Raw responses are kept verbatim, then normalised into
  structured tables — so new metrics can be derived later without re-fetching.
- **Shared date spine.** Sources join on date into a unified daily summary, which
  the analysis skills read.
- **Scheduled analysis.** Claude skills run on a schedule (nightly pull, weekly
  training review).

## Strava setup (one-time)

Tempo pulls your Strava data through the official OAuth2 API. You provide your
own API application credentials; nothing is shared and no secret ever enters
this repo (tokens live under `~/.tempo/tokens/`, mode 0600, gitignored).

1. **Create a Strava API application** at
   <https://www.strava.com/settings/api>. Set the *Authorization Callback
   Domain* to `localhost`. Note the **Client ID** and **Client Secret**.
2. **Configure Tempo.** Copy `.env.example` to `.env` and fill in:
   ```
   TEMPO_STRAVA_CLIENT_ID=<your client id>
   TEMPO_STRAVA_CLIENT_SECRET=<your client secret>
   ```
3. **Authorise (one time).** Run `tempo strava auth`, open the printed URL in a
   browser, approve access, then copy the `code` query parameter from the
   redirected `localhost` URL and run:
   ```
   tempo strava auth --code <CODE>
   ```
   Tokens are stored locally and atomically. Strava rotates refresh tokens on
   every refresh; Tempo persists the new one durably so you never have to repeat
   this step.

### Pulling data

```
tempo strava backfill            # resumable all-time activity history
tempo strava backfill --page-budget 5   # spread a large history across runs/days
tempo strava streams --limit 20  # lazily fetch HR/pace/GPS/power/cadence streams
tempo sync                       # daily incremental: only activities since the watermark
```

The backfill is checkpointed: if it hits Strava's rate limit (200 req/15 min,
2000/day) or is interrupted, just run it again — it resumes from a
`backfill_cursor` and never re-fetches what's already stored. Connectors write
**only** verbatim responses to the raw store; structured tables are derived later
(Phase 3) and can be rebuilt from raw without re-fetching.

> **API terms.** Strava's API Agreement includes a 7-day cache limit and a
> restriction on feeding data to AI models. Tempo's use is private, single-user,
> self-data that is never shared; this is an accepted, documented stance (see
> `.planning/REQUIREMENTS.md` → Known Accepted Conflicts), not an oversight.

## Garmin setup (one-time)

Tempo pulls your Garmin **wellness** data (sleep, HRV, resting HR, body battery,
stress, steps) through the unofficial `garminconnect` library. Garmin has no
official personal API, so this is the most fragile source — Tempo treats it as an
**isolated failure domain**: if Garmin breaks (a site change, an account 429,
expired session), the daily run logs it and skips, and your Strava sync + all
analysis still complete on existing data. No Garmin data is ever lost (it is
re-derivable from the raw store).

> **Privacy & safety:** your Garmin password lives only in the gitignored `.env`.
> Garmin session tokens are stored under `~/.tempo/tokens/garmin/` (mode 0600),
> outside the repo tree, and are gitignored. Nothing Garmin-related is ever
> committed.

1. **Configure credentials.** In your `.env`:
   ```
   TEMPO_GARMIN_EMAIL=<your Garmin Connect email>
   TEMPO_GARMIN_PASSWORD=<your Garmin Connect password>
   ```
2. **Log in once (interactive).** Run:
   ```
   tempo garmin login
   ```
   This is the **only** command that submits your credentials. If Garmin asks for
   a multi-factor (MFA) code, Tempo prompts for it. On success, session tokens are
   persisted and **reused** — every later sync loads those tokens and **never logs
   in again**. This matters: Garmin aggressively rate-limits logins *per account*,
   and repeated logins can lock you out (of the app too) for 48h+. The scheduled
   sync therefore never triggers a fresh login.

   > **If you ever see repeated `429 Too Many Requests`: STOP.** Do not retry —
   > retries compound an account-level lockout. Wait a few hours. Tempo itself
   > never retries a Garmin 429 (it fails-logs-skips immediately) for exactly this
   > reason.

### Pulling wellness data

```
tempo garmin backfill --days 60   # one-time: trailing 60 days of wellness history
tempo garmin sync                 # incremental: recent days (reuses tokens)
tempo sync                        # daily: runs Strava, THEN attempts Garmin (isolated)
```

`tempo sync` reports per-source status, e.g.:
```
Sync complete (per-source status):
  strava: ok (1234 raw rows)
  garmin: skipped -- not authenticated: ...   # never blocks Strava
```

Raw Garmin responses are stored verbatim, then transformed (`tempo transform` /
`tempo rederive`, zero network) into a `wellness_day` table — **one row per local
calendar day**, keyed by Garmin's `calendarDate` (the wake-up day it assigns to
overnight sleep/HRV, which removes the cross-midnight ambiguity). The
`daily_summary` view left-joins wellness so every day carries its activity,
wellness, and journal context in one row (rest days with only sleep included).

### Personal baselines

Raw HRV / resting HR / sleep numbers are meaningless without a personal norm, so
Tempo computes **rolling personal baselines** (trailing-window mean + SD with a
z-score, plus an EWMA) per metric from `wellness_day`. A reading is compared only
to the user's own recent history; with too little history a baseline honestly
reports "insufficient data" rather than inventing a norm. These feed the recovery
analysis in Phase 7.

> **Library fragility.** `garminconnect` is unofficial and can break when Garmin
> changes its auth/site (e.g. the `garth` foundation was deprecated in March
> 2026). Tempo isolates it behind the connector seam so a breakage degrades
> Garmin only. If it ever stops working, bump the library when upstream patches
> it; in the meantime Strava + analysis keep running, and you can fall back to
> Garmin's manual FIT/CSV export.

## Analysis & reports

Once activities are synced and transformed, Tempo turns them into per-activity
**load** (rTSS pace-based, with an hrTSS fallback), fitness/fatigue/form
(**CTL/ATL/TSB**), an **ACWR / ramp-rate** guardrail, and **race predictions**
(Riegel/VDOT), written as dated markdown reports.

```
tempo analyze                 # both reports (load-trend + race-readiness)
tempo analyze load-trend      # CTL/ATL/TSB, ACWR/ramp, weekly volume
tempo analyze race-readiness  # Riegel/VDOT vs goal + CTL/TSB form check
```

Reports land in the gitignored reports dir (`~/.tempo/reports/` by default) as
`YYYY-MM-DD-load-trend.md` / `YYYY-MM-DD-race-readiness.md`. Every report opens
with a **per-source data-freshness header** (last successful sync + staleness
flag) so a stale dataset is never trusted silently; thin data degrades to an
explicit "insufficient data" note rather than an invented number.

**Load config** (`.env`): set `TEMPO_THRESHOLD_PACE_S_PER_KM` (required for
rTSS) and optionally `TEMPO_MAX_HR` / `TEMPO_RESTING_HR` / `TEMPO_THRESHOLD_HR`
(the hrTSS fallback). See `.env.example`.

**Plan & race context**: copy `races.md.example` / `plan.md.example` into your
data dir as `races.md` / `plan.md` (default `~/.tempo/`) and edit them. Tempo
reads them for race-readiness context; they are never committed.

## Status

Phases 1–6 complete. **Phase 4 was the Strava end-to-end milestone** (pull →
store → transform → analyze → report on real Strava data). **Phase 6** adds Garmin
wellness as an isolated source: login-once token reuse, no-retry-on-429
fail-log-skip, a `calendarDate`-keyed `wellness_day` table joined into
`daily_summary`, and personal rolling baselines. See `.planning/` for the roadmap
and requirement traceability.

## Stack

Python 3.14 · SQLite · uv · stravalib · garminconnect · tenacity · pydantic-settings
