# Tempo

Personal training & health data pipeline. Pulls running and wellness data from
multiple sources, stores it in a structured, queryable form, and runs scheduled
Claude analyses on top of it.

> **Privacy:** this repo holds *code only*. All credentials, tokens, and actual
> health data are gitignored and stay local. See `.gitignore`.

## Data sources

| Source | Method | Status |
|---|---|---|
| **Strava** | Official REST API (OAuth2) — activities + streams (HR, pace, GPS, power, cadence) | Planned (first) |
| **Garmin** | Unofficial `garminconnect` — sleep, HRV, body battery, resting HR, stress, steps | Planned |
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

## Status

Phase 1 (Foundation) and Phase 2 (Strava ingestion) complete. See `.planning/`
for the roadmap and requirement traceability.

## Stack

Python 3.14 · SQLite · uv · stravalib · tenacity
