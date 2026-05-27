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

- [x] **STORE-01**: Pure transforms derive structured `activity` and `activity_stream` rows from raw responses
- [x] **STORE-02**: `tempo rederive` rebuilds all structured tables from stored raw data with no network calls
- [x] **STORE-03**: A zero-filled `date_spine` gives every calendar day a row (rest days included)
- [x] **STORE-04**: A `daily_summary` view left-joins activities, wellness, and journal onto the date spine (one row per day)
- [x] **STORE-05**: Local-date bucketing is correct and tested for edge cases (late-night activity, timezone travel, DST, Garmin overnight sleep)

### Load Metrics & Analysis

- [x] **LOAD-01**: Per-activity training load is computed as rTSS (pace-based, configurable threshold) with an hrTSS fallback, flagging which method was used
- [x] **LOAD-02**: CTL / ATL / TSB (fitness / fatigue / form) daily series are computed from the daily load series
- [x] **LOAD-03**: An ACWR / ramp-rate guardrail flags load spikes outside the safe range
- [x] **ANL-01**: A training-load & trend report (weekly volume, intensity mix, CTL/ramp) is generated as dated markdown
- [x] **ANL-02**: A race-readiness analysis estimates progress toward goal races (Riegel/VDOT + CTL/TSB form check)
- [x] **ANL-03**: A recovery / overtraining analysis combines rising load with HRV / sleep / resting-HR vs personal baselines
- [x] **ANL-04**: A correlation insight analysis links sleep / HRV / subjective feel to performance, reporting "insufficient data" honestly until history accumulates
- [x] **ANL-05**: Every report states per-source last-successful-sync / data-freshness so stale data is never trusted silently

### Plan & Reflect

- [x] **PLAN-01**: User maintains upcoming races (date, distance, goal) in a simple markdown file Tempo reads for analysis context
- [x] **PLAN-02**: User maintains a training plan in a simple markdown file Tempo reads for analysis context
- [x] **JRNL-01**: A validated `tempo journal add` entrypoint records structured post-workout entries (RPE 1–10, how it felt, notes), resolving the activity by date+sport
- [x] **JRNL-02**: Claude can capture journal entries by calling the validated entrypoint (never writing SQL directly)
- [x] **JRNL-03**: Journal entries contribute an sRPE load track (RPE × duration) usable when pace/HR load is unavailable

### Garmin Ingestion

- [x] **GRMN-01**: A `garminconnect`-backed connector implements the same connector interface as Strava and is isolated as a failure domain
- [x] **GRMN-02**: Garmin auth happens once via an explicit `tempo garmin login`; tokens are persisted and reused; the scheduled job never triggers a fresh login
- [x] **GRMN-03**: On a Garmin 429 / auth failure the run fails-logs-skips without retry, and Strava sync + analysis still complete
- [x] **GRMN-04**: Garmin wellness (HRV, sleep, resting HR, body battery, stress, steps) is stored raw then transformed into a `wellness_day` table keyed by `calendarDate`
- [x] **GRMN-05**: Personal rolling baselines for HRV / resting HR / sleep are computed (raw values are meaningless without a baseline)

### Scheduling & Delivery

- [x] **SCHED-01**: A daily scheduled job (launchd, not cron) runs sync → transform → analyze and writes reports
- [x] **SCHED-02**: The scheduler runs a missed job on wake (catch-up via watermark) rather than silently skipping
- [x] **SCHED-03**: The daily analysis surfaces output only when noteworthy (threshold check), not noise every day
- [x] **DELIV-01**: Analyses are written as dated markdown reports into a local (gitignored) `reports/` folder

## v1.1 Requirements (post-v1)

Iterative refinements on the v1 base. Mapped to Phase 8+.

### Modular Trackers + Heat Adaptation (Phase 8)

- [x] **TRACK-01**: `races.md` supports an optional `result:` field per race (free-form time like `3:17:42` or text like `DNF`); past races remain in the same file
- [x] **TRACK-02**: `RacesContext` exposes a `completed(today)` helper (mirroring `upcoming(today)`) so reports can surface recent races with their results
- [x] **TRACK-03**: Each race in `races.md` auto-links by local date to the Strava activity on that day (0 / 1 / many handled honestly: ambiguous or missing → unlinked, single match → linked); race-readiness report can show the actual time vs. goal when linked
- [x] **TRACK-04**: A new `heat.md` (in the content dir) captures heat-adaptation sessions as an append-only list (date + type like `sauna` / `hot-bath` / `hot-run` + duration + optional temp / HR / notes); parsing is lenient (missing fields don't break the file)
- [x] **TRACK-05**: Parsed heat sessions surface in analyses — at minimum a rolling-window count + total minutes (last 7 / 14 / 28 days) appears in the recovery report context so Claude knows current heat-adaptation status
- [x] **TRACK-06**: `plan.md` is retired entirely — the parser, `PlanContext`, config field, example file, report integration, and CLAUDE.md / docs mentions all removed; race-readiness report degrades cleanly without it

## v1.1 Requirements — Telegram Voice Coach (current milestone)

Voice memos sent to a personal Telegram chat → local Whisper transcription → Claude Code session driven via the Claude Agent SDK → reply back via Telegram. Uses the user's existing Claude subscription (not the Anthropic API). Runs on Mac via launchd; Pi port deferred to v1.2.

### Telegram bot intake

- [ ] **VOICE-01**: A Telegram bot runs as a long-polling local process and accepts voice messages ONLY from a single allowlisted chat id (the project owner); messages from any other chat are silently dropped
- [ ] **VOICE-02**: Bot token and owner chat id load from `.env` via `pydantic-settings` (gitignored); `.env.example` documents both
- [ ] **VOICE-03**: Inbound voice messages > 20 MB (Telegram's bot-API cap) are gracefully rejected with a clear reply rather than crashing the handler

### Local transcription

- [x] **VOICE-04**: Voice memos (Opus-in-OGG) are downloaded to a gitignored local voice cache and transcribed locally via `faster-whisper` — no audio bytes ever leave the laptop
- [x] **VOICE-05**: Default Whisper model is `small.en` int8 (CPU-only on Apple Silicon — faster-whisper has no Metal support); model is configurable via `pydantic-settings` so the user can swap to `base.en` / `medium.en` / `large-v3-turbo` if they choose to wait
- [x] **VOICE-06**: Model is loaded once at process start (singleton) and reused across transcriptions; first-run model download is warmed at startup, not on first request

### Claude Code session loop

- [x] **VOICE-07**: The transcribed text is fed to Claude Code via the Claude Agent SDK (the `claude-agent-sdk` Python package), which spawns the `claude` CLI as a subprocess — auth comes from the user's existing Claude Code login (their Claude subscription), NOT a separate `ANTHROPIC_API_KEY`
- [x] **VOICE-08**: Each Telegram chat has a session id that is reused via `--resume` for follow-up messages within 4 hours; after 4 hours of silence (or on the explicit `/new` command) the next message starts a fresh session
- [x] **VOICE-09**: Claude Code's final assistant message is captured and sent back to the chat as a single Telegram message (split across messages if it exceeds Telegram's 4096-char body cap), formatted as HTML (`& < >` escaped — NOT MarkdownV2)
- [x] **VOICE-10**: Tool-call activity inside the Claude Code session (Bash, Edit, etc.) is not surfaced as separate Telegram messages by default; only the final assistant reply goes back — keeps the UX clean. (A `/verbose` toggle for tool-call streaming is OUT OF SCOPE for v1.1.)

### Lifecycle, errors, observability

- [ ] **VOICE-11**: The bot runs as a launchd LaunchAgent with `KeepAlive=true` so it survives crashes, sleep/wake, and reboots without manual restart; logs land in a gitignored `logs/` directory
- [ ] **VOICE-12**: Any failure in the voice → transcript → Claude Code → reply pipeline is caught, logged with a structured error, and acknowledged in Telegram with a brief "something went wrong" message — the bot worker never crashes on a single bad message
- [x] **VOICE-13**: Per-turn token usage and cost (as reported by the Claude Code subprocess) is logged so the user can monitor Claude-subscription quota consumption over time

### Privacy + safety boundaries

- [ ] **VOICE-14**: Voice files are deleted from local cache after successful transcription (configurable retention window, default = delete immediately on success); transcripts are kept so the user can re-run analysis later
- [ ] **VOICE-15**: The agent session runs in the Tempo project directory so Claude Code has access to `tempo` CLI + GSD slash commands; no additional working-directory permissions are required and no read/write access is granted outside the project tree

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
| STORE-01 | Phase 3 | Complete |
| STORE-02 | Phase 3 | Complete |
| STORE-03 | Phase 3 | Complete |
| STORE-04 | Phase 3 | Complete |
| STORE-05 | Phase 3 | Complete |
| LOAD-01 | Phase 4 | Complete |
| LOAD-02 | Phase 4 | Complete |
| LOAD-03 | Phase 4 | Complete |
| ANL-01 | Phase 4 | Complete |
| ANL-02 | Phase 4 | Complete |
| ANL-05 | Phase 4 | Complete |
| PLAN-01 | Phase 4 | Complete |
| PLAN-02 | Phase 4 | Complete |
| DELIV-01 | Phase 4 | Complete |
| JRNL-01 | Phase 5 | Complete |
| JRNL-02 | Phase 5 | Complete |
| JRNL-03 | Phase 5 | Complete |
| GRMN-01 | Phase 6 | Complete |
| GRMN-02 | Phase 6 | Complete |
| GRMN-03 | Phase 6 | Complete |
| GRMN-04 | Phase 6 | Complete |
| GRMN-05 | Phase 6 | Complete |
| ANL-03 | Phase 7 | Complete |
| ANL-04 | Phase 7 | Complete |
| SCHED-01 | Phase 7 | Complete |
| SCHED-02 | Phase 7 | Complete |
| SCHED-03 | Phase 7 | Complete |
| TRACK-01 | Phase 8 | Complete |
| TRACK-02 | Phase 8 | Complete |
| TRACK-03 | Phase 8 | Complete |
| TRACK-04 | Phase 8 | Complete |
| TRACK-05 | Phase 8 | Complete |
| TRACK-06 | Phase 8 | Complete |
| VOICE-01 | Phase 9 | Pending |
| VOICE-02 | Phase 9 | Pending |
| VOICE-03 | Phase 10 | Pending |
| VOICE-04 | Phase 10 | Complete |
| VOICE-05 | Phase 10 | Complete |
| VOICE-06 | Phase 10 | Complete |
| VOICE-07 | Phase 11 | Complete |
| VOICE-08 | Phase 11 | Complete |
| VOICE-09 | Phase 11 | Complete |
| VOICE-10 | Phase 11 | Complete |
| VOICE-13 | Phase 11 | Complete |
| VOICE-11 | Phase 12 | Pending |
| VOICE-12 | Phase 12 | Pending |
| VOICE-14 | Phase 12 | Pending |
| VOICE-15 | Phase 12 | Pending |

**Coverage:**
- v1 requirements: 39 total — Complete (100%)
- v1.1 requirements (Phase 8 TRACK-*): 6 total — Complete (100%)
- v1.1 requirements (Phases 9–12 VOICE-*): 15 total — Pending (0%)
- All requirements mapped to phases (100%); 0 unmapped

---
*Requirements defined: 2026-05-26*
*Last updated: 2026-05-27 — v1.1 Telegram Voice Coach milestone roadmapped. 15 VOICE-* requirements mapped to four new phases: Phase 9 (Telegram bot scaffold + owner allowlist + secrets — VOICE-01/02), Phase 10 (voice download + 20 MB guard + faster-whisper singleton with small.en int8 default + transcript echo — VOICE-03/04/05/06), Phase 11 (claude-agent-sdk wiring + per-chat session-id store with 4hr resume + /new reset + HTML reply with 4096-char split + tool-call suppression + per-turn token logging — VOICE-07/08/09/10/13), Phase 12 (launchd LaunchAgent with KeepAlive + top-level error boundary + voice-file retention policy + project-scoped agent cwd + privacy contract docs — VOICE-11/12/14/15). All four phases are vertical slices: end of Phase 9 = owner gets a /start reply (others silently dropped); end of Phase 10 = speak into Telegram, see the transcript come back; end of Phase 11 = full agent loop with conversation memory; end of Phase 12 = always-on, hardened, retention-managed. Phase 8 TRACK-* rows backfilled into the traceability table. Coverage: 60/60 = 100% (45 complete + 15 pending).*
*Previously updated: 2026-05-26 after Phase 7 (Recovery + Correlation + Scheduler) completed — ANL-03, ANL-04, SCHED-01..03 Complete. **This completes v1: all 39 requirements across all 7 phases are done.** A multi-signal recovery / overtraining analysis (`tempo analyze recovery`, `tempo/analysis/recovery.py`) combines the rising-load half (CTL ramp rate / ACWR from `fitness.py`) with baseline-relative recovery markers (HRV / resting HR / sleep z-scored against personal rolling baselines from `baselines.py`); it encodes the "HRV abnormal in EITHER direction" subtlety (a drop = suppressed recovery; a spike = possible parasympathetic saturation in deep overtraining), flagging deviation magnitude not just direction, and degrades to "insufficient data" when baselines lack history (ANL-03). An honest correlation insight (`tempo analyze correlations`, `tempo/analysis/correlation.py`, stdlib Pearson) links prior-night sleep/HRV and subjective RPE to training load (performance proxy) and RPE, reporting a relationship ONLY at >= 20 paired days and otherwise emitting an explicit "insufficient data — N paired days, need 20" message rather than asserting a weak signal (ANL-04). A daily launchd LaunchAgent (NOT cron — `tempo install-scheduler` generates a `StartCalendarInterval` plist with absolute paths + explicit PATH/TEMPO_DATA_DIR + log capture; committed secret-free template at `launchd/com.tempo.daily.plist`; Tempo never runs `launchctl` itself) drives `tempo run-daily` = sync → transform → analyze (`tempo/sync/daily.py`), which is idempotent and catch-up-aware: a missed day is recovered on the next run via the watermark-driven resumable sync, with Garmin still isolated (SCHED-01/02). The daily run surfaces output only when noteworthy (`tempo/analysis/noteworthy.py`, configurable+documented thresholds: ACWR out of safe range, aggressive ramp, monitor/elevated recovery, strong baseline z, race within ~14 days, source staleness) — all reports are always written but a NOTEWORTHY log block + `reports/NOTEWORTHY.md` marker appear only on a threshold crossing (SCHED-03). 358 pytest tests (70 new), all mocked/seeded, no network; ruff clean; plist validated with plutil.*
*Previously updated: 2026-05-26 after Phase 6 (Garmin Ingestion) completed — GRMN-01..05 Complete. A `garminconnect`-backed connector implements the same `Connector` protocol as Strava and is isolated as a failure domain: a 429 / auth break / library exception is caught, logged, and skipped with NO retry (the connector never retries a 429; the `tempo.sync.pipeline` wraps it so Strava sync + transforms + analysis still complete on existing data — verified by an end-to-end `tempo sync` where a simulated Garmin 429 left Strava ok and transform/analyze succeeding). Garmin auth happens once via interactive `tempo garmin login` (MFA via prompt callback); session tokens are persisted under `~/.tempo/tokens/garmin` and REUSED — the scheduled `sync`/`backfill` load only from the token store and never trigger a fresh SSO login (verified: 0 credential logins on backfill). Wellness (HRV, sleep score/duration/stages, resting HR, body battery, stress, steps) is stored raw (endpoints `sleep`/`hrv`/`stats`, keyed by ISO date) then collapsed by pure no-network transforms into a `wellness_day` table keyed by Garmin's `calendarDate` (the local wake-up day); `daily_summary` LEFT-JOINs wellness preserving one-row-per-spine-day (wellness-only rest days included). Personal rolling baselines (trailing mean+SD z-score + EWMA) for HRV / resting HR / sleep are computed from `wellness_day` (`tempo/analysis/baselines.py`), reporting "insufficient data" until enough history accumulates, exposed for Phase 7's recovery analysis. All proven with a fake garminconnect client (`tests/garmin_fakes.py`) — no real credentials.*
*Previously updated: 2026-05-26 after Phase 5 (Journaling via Claude) completed — JRNL-01..03 Complete. A validated `tempo journal add` entrypoint records structured subjective entries (RPE 1–10, feel, notes), resolves the activity by date+sport, computes an sRPE (RPE × duration) load track, and surfaces journal fields in `daily_summary` (one row per spine day preserved); sRPE fills the daily load on otherwise-insufficient days, flagged `sRPE`. Claude captures entries only through this boundary — never raw SQL (docs/JOURNALING.md).*
*Previously updated: 2026-05-26 after Phase 4 (Load Metrics + First Analysis) completed — LOAD-01..03, ANL-01, ANL-02, ANL-05, PLAN-01, PLAN-02, DELIV-01 Complete. This is the Strava end-to-end milestone: pull → store → transform → analyze → report works end-to-end on stored data.*
*Previously updated: 2026-05-26 after Phase 3 (Strava Transforms + Date Spine) completed — STORE-01..STORE-05 Complete.*
*Note on STORE-04/05 scope: the `daily_summary` view LEFT-JOINs from `date_spine` and is shaped so the wellness (Phase 6) and journal (Phase 5) sources can be LEFT-JOINed in when they exist; the bucketing rule and tests already cover Garmin's `calendarDate` (overnight) attribution so those sources will bucket correctly on arrival. Phase 3 delivers the Strava activity join, the spine, and the proven bucketing rule end-to-end.*
