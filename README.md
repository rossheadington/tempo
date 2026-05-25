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

## Status

Bootstrapping. Architecture and roadmap are being built out with GSD
(see `.planning/` once initialised).

## Stack

Python 3.14 · SQLite · uv
