# Roadmap: Tempo

## Overview

Tempo is built bottom-up along a strict data dependency chain: a secure, gitignored foundation (DB schema, secrets, CLI shell, date-bucketing rule) comes first, then the clean Strava source proves the full pull → store → transform → analyse → report pipeline end-to-end (the first shippable milestone). Journaling is added early so subjective history accumulates for later correlation. The fragile Garmin connector is isolated last among the ingestion sources, after the architecture is validated. The journey closes with recovery and correlation analysis plus a launchd scheduler that runs the whole loop daily and surfaces output only when noteworthy. Strava end-to-end (through Phase 4) ships before any Garmin work; the date spine and raw → structured layering are correctness prerequisites that land before any analysis.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Secure DB schema, secrets outside the tree, gitleaks hook, typer CLI shell, date-bucketing rule
- [x] **Phase 2: Strava Ingestion** - OAuth, atomic rotating-token persistence, resumable rate-limited backfill + incremental sync, raw-only writes
- [x] **Phase 3: Strava Transforms + Date Spine** - Pure rederivable transforms, zero-filled date spine, daily_summary view, tested timezone bucketing
- [x] **Phase 4: Load Metrics + First Analysis (Strava end-to-end milestone)** - rTSS/hrTSS, CTL/ATL/TSB, ACWR, load+trend and race-readiness reports with freshness headers
- [x] **Phase 5: Journaling via Claude** - Validated `tempo journal add` entrypoint, activity resolution, sRPE load track
- [x] **Phase 6: Garmin Ingestion** - Isolated fragile connector, login-once token persistence, no-retry-on-429, calendarDate wellness, baselines
- [x] **Phase 7: Recovery + Correlation + Scheduler** - Multi-signal recovery, honest correlation, launchd daily loop with catch-up and noteworthy-only surfacing

## Phase Details

### Phase 1: Foundation
**Goal**: A secure, runnable project skeleton exists — DB schema, secret handling outside the repo tree, a typed config, a working CLI shell, and a documented date-bucketing rule — before any data is ingested.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: FND-01, FND-02, FND-03, FND-04, FND-05, FND-06
**Success Criteria** (what must be TRUE):
  1. Running `tempo` initialises a SQLite DB (WAL mode on) containing `raw_response`, `date_spine`, and `sync_state` tables
  2. The DB, tokens, `.env`, and `reports/` live outside the committed tree (or are gitignored) so no secret or health data can reach the public repo, and a committed `.env.example` documents required config
  3. A pre-commit `gitleaks` scan blocks a deliberately-staged fake credential from being committed
  4. The `tempo` CLI exposes wired subcommands (`sync`, `transform`/`rederive`, `analyze`, `journal`) that run without error
  5. A documented local-date attribution (date-bucketing) rule is written down in the repo before any connector runs
**Plans**: TBD

### Phase 2: Strava Ingestion
**Goal**: Strava data flows into the raw store: a one-time OAuth handshake, durable rotating-token persistence, a resumable all-time backfill that survives rate limits and restarts, and a daily incremental sync — all writing verbatim to `raw_response` only.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: STRV-01, STRV-02, STRV-03, STRV-04, STRV-05, STRV-06
**Success Criteria** (what must be TRUE):
  1. User completes a one-time Strava OAuth handshake and tokens are stored locally
  2. Rotating refresh tokens are persisted atomically on every refresh (temp-write, fsync, rename) so a re-auth flow is never silently required
  3. User can run a resumable, checkpointed all-time backfill (via `backfill_cursor` in `sync_state`) that survives a mid-run rate-limit or restart and resumes without re-fetching
  4. Activity streams (HR, pace, GPS, power, cadence, elevation) are fetchable lazily without blowing the rate limit, and a daily incremental sync pulls only new activities since the last watermark
  5. Every Strava API response is stored verbatim in `raw_response` and connectors write to nothing but raw
**Plans**: TBD

### Phase 3: Strava Transforms + Date Spine
**Goal**: Raw Strava responses become trustworthy structured data: pure rederivable transforms, a zero-filled date spine giving every calendar day a row, a `daily_summary` view, and local-date bucketing proven correct for edge cases.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: STORE-01, STORE-02, STORE-03, STORE-04, STORE-05
**Success Criteria** (what must be TRUE):
  1. Pure transforms derive structured `activity` and `activity_stream` rows from stored raw responses
  2. `tempo rederive` rebuilds all structured tables from raw data with zero network calls
  3. A zero-filled `date_spine` gives every calendar day a row (rest days included), and a `daily_summary` view left-joins activities (and later wellness/journal) onto the spine at one row per day
  4. Local-date bucketing is correct and covered by tests for edge cases: late-night (11pm) activity, timezone travel, DST, and Strava's fake-`Z` `start_date_local`
**Plans**: TBD

### Phase 4: Load Metrics + First Analysis (Strava end-to-end milestone)
**Goal**: The first shippable milestone — Strava data is turned into per-activity load and fitness/fatigue/form series, and into dated markdown reports for training load, trends, and race readiness, each stating its own data freshness. Pull → store → analyse → report works end-to-end on real Strava data.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: LOAD-01, LOAD-02, LOAD-03, ANL-01, ANL-02, ANL-05, PLAN-01, PLAN-02, DELIV-01
**Success Criteria** (what must be TRUE):
  1. Per-activity load is computed as rTSS (pace-based, configurable threshold) with an hrTSS fallback, and each day's value flags which method produced it
  2. CTL / ATL / TSB daily series and an ACWR / ramp-rate guardrail are computed from the daily load series, flagging spikes outside the safe range
  3. Tempo reads user-maintained `races.md` and `plan.md` for analysis context
  4. A dated training-load & trend report and a race-readiness analysis (Riegel/VDOT + CTL/TSB form check) are written as markdown into a gitignored local `reports/` folder
  5. Every report states per-source last-successful-sync / data-freshness so stale data is never trusted silently
**Plans**: TBD

### Phase 5: Journaling via Claude
**Goal**: Subjective post-workout reflection starts accumulating early — a validated `tempo journal add` entrypoint records structured entries linked to the right activity, Claude captures them through that boundary (never raw SQL), and an sRPE load track exists for when pace/HR load is unavailable.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: JRNL-01, JRNL-02, JRNL-03
**Success Criteria** (what must be TRUE):
  1. A validated `tempo journal add` entrypoint records structured entries (RPE 1–10, how it felt, notes) and resolves the activity by date + sport
  2. Claude can capture a journal entry by calling the validated entrypoint and is never required to write SQL directly
  3. Journal entries appear in `daily_summary` and contribute an sRPE (RPE × duration) load track usable when pace/HR load is missing
**Plans**: TBD

### Phase 6: Garmin Ingestion
**Goal**: Garmin wellness is added as an isolated failure domain — a `garminconnect` connector implementing the same interface as Strava, login-once token persistence with no fresh login from the scheduled job, fail-log-skip on 429, and a `calendarDate`-keyed `wellness_day` table with personal rolling baselines.
**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: GRMN-01, GRMN-02, GRMN-03, GRMN-04, GRMN-05
**Success Criteria** (what must be TRUE):
  1. A `garminconnect`-backed connector implements the same `Connector` interface as Strava and is isolated so its failures cannot block Strava sync or analysis
  2. Garmin auth happens once via an explicit `tempo garmin login`; tokens are persisted and reused, and the scheduled job never triggers a fresh login
  3. On a Garmin 429 / auth failure the run fails-logs-skips without retry, and Strava sync + analysis still complete on existing data
  4. Garmin wellness (HRV, sleep, resting HR, body battery, stress, steps) is stored raw then transformed into a `wellness_day` table keyed by `calendarDate`, and `daily_summary` now joins wellness
  5. Personal rolling baselines for HRV / resting HR / sleep are computed so raw wellness values can be interpreted against personal norms
**Plans**: TBD

### Phase 7: Recovery + Correlation + Scheduler
**Goal**: The full analysis suite closes — multi-signal recovery/overtraining analysis against personal baselines and honest correlation insight — and a launchd scheduler runs sync → transform → analyze daily, catches up missed runs on wake, and surfaces output only when noteworthy.
**Mode:** mvp
**Depends on**: Phase 6
**Requirements**: ANL-03, ANL-04, SCHED-01, SCHED-02, SCHED-03
**Success Criteria** (what must be TRUE):
  1. A recovery / overtraining analysis combines rising load with HRV / sleep / resting-HR vs personal baselines and reports honestly when data is insufficient
  2. A correlation insight analysis links sleep / HRV / subjective feel to performance, reporting "insufficient data" until history accumulates rather than asserting weak signal
  3. A daily launchd job (not cron) runs sync → transform → analyze and writes reports, and runs a missed job on wake via watermark catch-up rather than silently skipping
  4. The daily analysis surfaces output only when noteworthy (threshold check), not noise every day
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 1/1 | Complete | 2008-05-26 |
| 2. Strava Ingestion | 1/1 | Complete | 2008-05-26 |
| 3. Strava Transforms + Date Spine | 1/1 | Complete | 2008-05-26 |
| 4. Load Metrics + First Analysis | 1/1 | Complete | 2008-05-26 |
| 5. Journaling via Claude | 1/1 | Complete | 2008-05-26 |
| 6. Garmin Ingestion | 1/1 | Complete | 2008-05-26 |
| 7. Recovery + Correlation + Scheduler | 1/1 | Complete | 2008-05-26 |
| 8. Modular Trackers + Heat Adaptation | 5/5 | Complete | 2026-05-27 |

**All 8 phases complete — Tempo v1 is feature-complete (plan.md retired in Phase 8).**

### Phase 8: Modular Trackers + Heat Adaptation

**Goal:** Replace the catch-all `plan.md` with focused, single-purpose tracker files. `races.md` keeps past + future races in one place (gains a `result:` field and auto-links by date to the matching Strava activity); a new `heat.md` captures sauna / heat-adaptation sessions as an append-only log surfaced in the recovery report. `plan.md` is retired entirely (the user does not currently want to track a forward-looking plan). Each tracker has its own lenient parser that degrades gracefully when the file is missing.
**Requirements**: TRACK-01, TRACK-02, TRACK-03, TRACK-04, TRACK-05, TRACK-06
**Depends on:** Phase 7
**Plans:** 5/5 plans executed — Phase 8 COMPLETE (2026-05-27)

Plans:
- [x] 08-01-PLAN.md — races.py rename + result: field + completed(today) helper + races.md.example update (TRACK-01, TRACK-02)
- [x] 08-02-PLAN.md — new heat.py parser + HeatRollup + heat.md.example + heat_path config (TRACK-04)
- [x] 08-03-PLAN.md — new race_link.py with 0/1/N edge cases (TRACK-03)
- [x] 08-04-PLAN.md — wire heat + race_link into recovery + race-readiness reports (incl. A4 lapsed nudge) (TRACK-05, TRACK-03)
- [x] 08-05-PLAN.md — plan.md retirement cleanup + context.py deletion (TRACK-06)
