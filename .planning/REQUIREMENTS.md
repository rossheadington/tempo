# Requirements: Tempo

**Defined:** 2026-05-26
**Core Value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.

## v1 Requirements

Requirements for the initial release. Each maps to a roadmap phase. v1 spans both
the Strava end-to-end milestone and the Garmin/analysis milestone.

### Foundation

- [x] **FND-01**: SQLite database initialises with `raw_response`, `date_spine`, and `sync_state` tables (WAL mode on)
- [x] **FND-02**: Database, tokens, `.env`, and `reports/` live outside the committed tree / are gitignored so no secret or health data can reach the public repo
- [x] **FND-03**: A pre-commit secret scan (e.g. gitleaks) blocks accidental credential commits
- [x] **FND-04**: Typed config and secrets load from a gitignored `.env` (with a committed `.env.example`)
- [x] **FND-05**: A `tempo` CLI entrypoint exists with subcommands wired (sync, transform/rederive, analyze, journal)
- [x] **FND-06**: A documented date-bucketing rule (local-date attribution) is defined before any data is ingested

### Strava Ingestion

- [x] **STRV-01**: User completes a one-time Strava OAuth handshake and the tokens are stored locally
- [x] **STRV-02**: Rotating refresh tokens are persisted atomically on every refresh so re-auth is never silently required
- [x] **STRV-03**: User can run a resumable, checkpointed all-time backfill of Strava activities that survives rate limits and restarts
- [x] **STRV-04**: Activity streams (HR, pace, GPS, power, cadence, elevation) are fetchable without blowing the rate limit (lazy / paged)
- [x] **STRV-05**: A daily incremental sync pulls only new activities since the last watermark
- [x] **STRV-06**: All Strava API responses are stored verbatim in `raw_response` (connectors write only to raw)

### Storage & Modelling

- [ ] **STORE-01**: Pure transforms derive structured `activity` and `activity_stream` rows from raw responses
- [ ] **STORE-02**: `tempo rederive` rebuilds all structured tables from stored raw data with no network calls
- [ ] **STORE-03**: A zero-filled `date_spine` gives every calendar day a row (rest days included)
- [ ] **STORE-04**: A `daily_summary` view left-joins activities, wellness, and journal onto the date spine (one row per day)
- [ ] **STORE-05**: Local-date bucketing is correct and tested for edge cases (late-night activity, timezone travel, DST, Garmin overnight sleep)

### Load Metrics & Analysis

- [ ] **LOAD-01**: Per-activity training load is computed as rTSS (pace-based, configurable threshold) with an hrTSS fallback, flagging which method was used
- [ ] **LOAD-02**: CTL / ATL / TSB (fitness / fatigue / form) daily series are computed from the daily load series
- [ ] **LOAD-03**: An ACWR / ramp-rate guardrail flags load spikes outside the safe range
- [ ] **ANL-01**: A training-load & trend report (weekly volume, intensity mix, CTL/ramp) is generated as dated markdown
- [ ] **ANL-02**: A race-readiness analysis estimates progress toward goal races (Riegel/VDOT + CTL/TSB form check)
- [ ] **ANL-03**: A recovery / overtraining analysis combines rising load with HRV / sleep / resting-HR vs personal baselines
- [ ] **ANL-04**: A correlation insight analysis links sleep / HRV / subjective feel to performance, reporting "insufficient data" honestly until history accumulates
- [ ] **ANL-05**: Every report states per-source last-successful-sync / data-freshness so stale data is never trusted silently

### Plan & Reflect

- [ ] **PLAN-01**: User maintains upcoming races (date, distance, goal) in a simple markdown file Tempo reads for analysis context
- [ ] **PLAN-02**: User maintains a training plan in a simple markdown file Tempo reads for analysis context
- [ ] **JRNL-01**: A validated `tempo journal add` entrypoint records structured post-workout entries (RPE 1–10, how it felt, notes), resolving the activity by date+sport
- [ ] **JRNL-02**: Claude can capture journal entries by calling the validated entrypoint (never writing SQL directly)
- [ ] **JRNL-03**: Journal entries contribute an sRPE load track (RPE × duration) usable when pace/HR load is unavailable

### Garmin Ingestion

- [ ] **GRMN-01**: A `garminconnect`-backed connector implements the same connector interface as Strava and is isolated as a failure domain
- [ ] **GRMN-02**: Garmin auth happens once via an explicit `tempo garmin login`; tokens are persisted and reused; the scheduled job never triggers a fresh login
- [ ] **GRMN-03**: On a Garmin 429 / auth failure the run fails-logs-skips without retry, and Strava sync + analysis still complete
- [ ] **GRMN-04**: Garmin wellness (HRV, sleep, resting HR, body battery, stress, steps) is stored raw then transformed into a `wellness_day` table keyed by `calendarDate`
- [ ] **GRMN-05**: Personal rolling baselines for HRV / resting HR / sleep are computed (raw values are meaningless without a baseline)

### Scheduling & Delivery

- [ ] **SCHED-01**: A daily scheduled job (launchd, not cron) runs sync → transform → analyze and writes reports
- [ ] **SCHED-02**: The scheduler runs a missed job on wake (catch-up via watermark) rather than silently skipping
- [ ] **SCHED-03**: The daily analysis surfaces output only when noteworthy (threshold check), not noise every day
- [ ] **DELIV-01**: Analyses are written as dated markdown reports into a local (gitignored) `reports/` folder

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Nutrition

- **NUTR-01**: Ingest MyFitnessPal food/nutrition data via CSV-drop (no official API)
- **NUTR-02**: Join daily nutrition (calories, macros) onto the daily summary

### Advanced Analysis

- **ADV-01**: Effective VO2max / pace-at-fixed-HR fitness trend
- **ADV-02**: Structured plan-vs-actual engine (diff planned workouts against completed)
- **ADV-03**: Auto-derive threshold pace from best-effort data instead of manual config
- **ADV-04**: Marathon-shape / long-run readiness model

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| MyFitnessPal in v1 | No official API; scraping is fragile — deferred to v2 (CSV-drop) |
| Web / mobile / desktop UI | Single-user tool; interaction is CLI + markdown + Claude |
| Real-time / live activity tracking | Daily batch sync is sufficient |
| Multi-user, accounts, hosting | Personal local tool; only personal API tokens |
| Re-deriving Garmin's proprietary scores | Closed black-box; consume them as inputs instead |
| Auto-prescribing workouts | Out of intended scope; Tempo informs, the user decides |
| Social / sharing features | Personal use; no external sharing of data |
| Committing reports or health data to the repo | Public repo — health data and reports stay local/gitignored |

## Known Accepted Conflicts

| Conflict | Stance |
|----------|--------|
| Strava API Agreement (7-day cache limit; no feeding data to AI models) vs storing all-time data + Claude analysis | Accepted for private, single-user, never-shared self-data. Low practical enforcement risk. Documented, not ignored. |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FND-01 | Phase 1 | Complete |
| FND-02 | Phase 1 | Complete |
| FND-03 | Phase 1 | Complete |
| FND-04 | Phase 1 | Complete |
| FND-05 | Phase 1 | Complete |
| FND-06 | Phase 1 | Complete |
| STRV-01 | Phase 2 | Complete |
| STRV-02 | Phase 2 | Complete |
| STRV-03 | Phase 2 | Complete |
| STRV-04 | Phase 2 | Complete |
| STRV-05 | Phase 2 | Complete |
| STRV-06 | Phase 2 | Complete |
| STORE-01 | Phase 3 | Pending |
| STORE-02 | Phase 3 | Pending |
| STORE-03 | Phase 3 | Pending |
| STORE-04 | Phase 3 | Pending |
| STORE-05 | Phase 3 | Pending |
| LOAD-01 | Phase 4 | Pending |
| LOAD-02 | Phase 4 | Pending |
| LOAD-03 | Phase 4 | Pending |
| ANL-01 | Phase 4 | Pending |
| ANL-02 | Phase 4 | Pending |
| ANL-05 | Phase 4 | Pending |
| PLAN-01 | Phase 4 | Pending |
| PLAN-02 | Phase 4 | Pending |
| DELIV-01 | Phase 4 | Pending |
| JRNL-01 | Phase 5 | Pending |
| JRNL-02 | Phase 5 | Pending |
| JRNL-03 | Phase 5 | Pending |
| GRMN-01 | Phase 6 | Pending |
| GRMN-02 | Phase 6 | Pending |
| GRMN-03 | Phase 6 | Pending |
| GRMN-04 | Phase 6 | Pending |
| GRMN-05 | Phase 6 | Pending |
| ANL-03 | Phase 7 | Pending |
| ANL-04 | Phase 7 | Pending |
| SCHED-01 | Phase 7 | Pending |
| SCHED-02 | Phase 7 | Pending |
| SCHED-03 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 39 total
- Mapped to phases: 39 (100%)
- Unmapped: 0

---
*Requirements defined: 2026-05-26*
*Last updated: 2026-05-26 after Phase 2 (Strava Ingestion) completed — STRV-01..STRV-06 Complete*
