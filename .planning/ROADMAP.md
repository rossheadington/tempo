# Roadmap: Tempo

## Overview

Tempo is built bottom-up along a strict data dependency chain: a secure, gitignored foundation (DB schema, secrets, CLI shell, date-bucketing rule) comes first, then the clean Strava source proves the full pull → store → transform → analyse → report pipeline end-to-end (the first shippable milestone). Journaling is added early so subjective history accumulates for later correlation. The fragile Garmin connector is isolated last among the ingestion sources, after the architecture is validated. The journey closes with recovery and correlation analysis plus a launchd scheduler that runs the whole loop daily and surfaces output only when noteworthy. Strava end-to-end (through Phase 4) ships before any Garmin work; the date spine and raw → structured layering are correctness prerequisites that land before any analysis.

**v1.1 — Telegram Voice Coach (Mac)** extends the system with a new interaction shell. The same vertical-slice principle applies: each phase must produce a real, user-observable moment. Phase 9 = a bot exists and answers a `/start` from the owner only. Phase 10 = speak into Telegram, see the transcript come back — proves the local-Whisper pipeline before any agent wiring. Phase 11 = the agent loop, where transcripts become Claude Code sessions that reply via Telegram and remember the last 4 hours. Phase 12 = lifecycle + privacy hardening so the bot survives sleep/wake/crash, never leaks voice files, and never crashes the worker on a single bad message.

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
- [x] **Phase 8: Modular Trackers + Heat Adaptation** - races.md result + auto-link, heat.md tracker surfaced in recovery report, plan.md retired
- [x] **Phase 9: Telegram Bot Foundation (v1.1)** - python-telegram-bot scaffold, owner-only allowlist, `.env` secrets, `tempo bot run` subcommand
- [x] **Phase 10: Voice Intake + Local Transcription (v1.1)** - Voice download into gitignored cache, 20 MB guard, faster-whisper singleton (small.en int8 default), transcript echoed to chat
- [x] **Phase 11: Claude Code Agent Loop (v1.1)** - claude-agent-sdk wiring, per-chat session-id store with 4hr resume window, HTML reply formatting with 4096-char split, `/new` reset, per-turn token logging
- [x] **Phase 12: Lifecycle, Hardening, Privacy (v1.1)** - launchd LaunchAgent with KeepAlive, top-level error handler, voice-file retention policy, project-scoped working dir

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

### Phase 8: Modular Trackers + Heat Adaptation
**Goal**: Replace the catch-all `plan.md` with focused, single-purpose tracker files. `races.md` keeps past + future races in one place (gains a `result:` field and auto-links by date to the matching Strava activity); a new `heat.md` captures sauna / heat-adaptation sessions as an append-only log surfaced in the recovery report. `plan.md` is retired entirely (the user does not currently want to track a forward-looking plan). Each tracker has its own lenient parser that degrades gracefully when the file is missing.
**Mode:** mvp
**Depends on**: Phase 7
**Requirements**: TRACK-01, TRACK-02, TRACK-03, TRACK-04, TRACK-05, TRACK-06
**Success Criteria** (what must be TRUE):
  1. `races.md` supports an optional `result:` field per race and `RacesContext.completed(today)` mirrors `upcoming(today)` so recent races surface in reports
  2. Each race auto-links by local date to the day's Strava activity (0 / 1 / many handled honestly: ambiguous or missing → unlinked, single match → linked)
  3. A new `heat.md` captures heat-adaptation sessions as a lenient append-only log; missing fields don't break the parser; missing file degrades cleanly
  4. Parsed heat sessions surface in analyses — at minimum rolling-window count + total minutes (7 / 14 / 28 day) appears in the recovery report context
  5. `plan.md` is retired entirely (parser, `PlanContext`, config field, example file, report integration, docs mentions all removed) and race-readiness report degrades cleanly without it
**Plans**: 5/5 plans executed — Phase 8 COMPLETE (2026-05-27)

### Phase 9: Telegram Bot Foundation (v1.1)
**Goal**: A `python-telegram-bot` long-polling worker exists, locked to the owner's chat id, runnable via `tempo bot run`. No voice handling yet — this phase proves the scaffold, the secrets path, and the allowlist work in isolation before any audio or agent code lands.
**Mode:** mvp
**Depends on**: Phase 8
**Requirements**: VOICE-01, VOICE-02
**Success Criteria** (what must be TRUE):
  1. `tempo bot run` starts a long-polling worker (`Application.run_polling()`) against `TELEGRAM_BOT_TOKEN`, registers a `/start` handler gated on `filters.Chat(chat_id=TELEGRAM_OWNER_CHAT_ID)`, and replies to the owner with a hardcoded greeting
  2. A `/start` (or any message) from any chat id other than the owner produces NO reply and is silently dropped — verified by sending from a second Telegram account (or by mocking a non-owner update in tests)
  3. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` load via `pydantic-settings` from the gitignored `.env`; missing either raises a clear startup error (no silent failure); `.env.example` documents both variables
  4. README / `docs/TELEGRAM_BOT.md` documents the one-time setup (talk to @BotFather → `/newbot` → grab token; send a message to the bot then `GET /getUpdates` to find the owner chat id); a `chmod 600 .env` reminder is included
  5. The bot worker's stdout/stderr is suitable for capture by launchd (Phase 12) — no interactive prompts, structured logging, clean SIGTERM shutdown via PTB's default `stop_signals`
**Plans**: TBD
**UI hint**: yes

### Phase 10: Voice Intake + Local Transcription (v1.1)
**Goal**: The bot accepts a voice memo from the owner, downloads it into a gitignored local voice cache, transcribes it locally via `faster-whisper` (singleton, warmed at startup, `small.en` int8 default), and replies with the raw transcript in Telegram. Proves the full audio-in / text-out vertical end-to-end before any Claude wiring.
**Mode:** mvp
**Depends on**: Phase 9
**Requirements**: VOICE-03, VOICE-04, VOICE-05, VOICE-06
**Success Criteria** (what must be TRUE):
  1. A voice memo sent by the owner is downloaded to `<content_dir>/voice/<message_id>-<file_unique_id>.ogg` (gitignored, dir created with 0700); the path NEVER leaves the local machine
  2. Voice memos with `voice.file_size > 20 MB` are rejected with a clear reply (e.g. "Voice note too big — 20 MB max") rather than crashing the handler or calling `getFile`
  3. A single `faster-whisper.WhisperModel` instance is loaded at process startup (warmed by one dummy transcription so the first real memo doesn't pay download/load cost) and reused across all subsequent transcriptions — verified by a log line on startup AND by handler code that imports the singleton rather than instantiating per-call
  4. Default model is `small.en` with `compute_type="int8"`, `cpu_threads=4`, `vad_filter=True`; `TEMPO_TRANSCRIBE_MODEL` and `TEMPO_TRANSCRIBE_COMPUTE_TYPE` pydantic-settings fields let the user swap (`tiny.en` / `base.en` / `medium.en` / `large-v3-turbo`); models live under a gitignored `download_root` inside the data dir, NOT `~/.cache/huggingface`
  5. After a successful transcription the bot replies to the owner with the raw transcript text (HTML-escaped via `html.escape`) so the user can confirm the pipeline works end-to-end before any agent intelligence is wired
**Plans**: TBD
**UI hint**: yes

### Phase 11: Claude Code Agent Loop (v1.1)
**Goal**: Transcripts become Claude Code sessions. Each chat has a session id that resumes within a 4-hour rolling window via `claude --resume`; the agent runs in the Tempo project directory with full Claude Code tooling (Bash, Read, Write, Edit, GSD slash commands, `tempo` CLI); only the final assistant message is sent back as HTML-formatted Telegram messages (split at 4096 chars); per-turn token usage is logged.
**Mode:** mvp
**Depends on**: Phase 10
**Requirements**: VOICE-07, VOICE-08, VOICE-09, VOICE-10, VOICE-13
**Success Criteria** (what must be TRUE):
  1. A voice memo from the owner flows transcript → `claude-agent-sdk` query → final assistant message → Telegram reply; auth comes from the user's existing Claude Code login (their Claude subscription), NOT a separate `ANTHROPIC_API_KEY` — verified by running with `ANTHROPIC_API_KEY` unset and confirming the call still works
  2. A per-chat session-id store (SQLite table OR a small JSON file under the data dir) maps `chat_id → (session_id, last_message_ts)`; the next message within 4 hours of `last_message_ts` is sent via `claude --resume <session_id>`; the next message after 4 hours starts a fresh session and overwrites the stored id; `last_message_ts` updates on every successful turn
  3. An explicit `/new` command from the owner clears the stored session id for that chat so the next message starts a fresh Claude Code session — replies with confirmation (e.g. "New session started")
  4. The final assistant message from Claude Code is sent back to the chat as one or more Telegram messages, formatted as HTML (`parse_mode=ParseMode.HTML`); literal user / tool content is escaped via `html.escape` (only `& < >` need handling); messages longer than 4096 chars are split at paragraph boundaries before sending
  5. Tool-call activity inside the Claude Code session (Bash, Read, Edit, etc.) is NOT surfaced as separate Telegram messages — only the final assistant reply is sent back (intermediate `AssistantMessage` / `ToolUseBlock` / `ToolResultBlock` events are filtered out of the Telegram output path)
  6. Per-turn token usage (input / output / cache-read / cache-creation tokens AND the SDK-reported cost in USD, as returned on the `ResultMessage`) is logged in a structured form (logger field or SQLite `agent_turns` table) so the user can monitor Claude-subscription quota consumption over time
**Plans**: 3 plans
Plans:
- [x] 11-01-PLAN.md — Session-id store + migration 0005_bot_sessions (SCHEMA_VERSION → 5) + docs prereqs (VOICE-08)
- [x] 11-02-PLAN.md — claude-agent-sdk wrapper: AgentTurn + run_turn + format_for_telegram (VOICE-07/09/13)
- [x] 11-03-PLAN.md — Wire voice_handler + text_handler + /new + typing indicator + startup CLI check (VOICE-07/08/09/10/13)
**UI hint**: yes

### Phase 12: Lifecycle, Hardening, Privacy (v1.1)
**Goal**: The bot becomes a real always-on background service: launchd LaunchAgent with `KeepAlive=true` so it survives crashes, sleep/wake, and reboots; a top-level error handler that never lets a single bad message crash the worker; a voice-file retention policy so audio doesn't accumulate; and a confirmation that the agent session is scoped to the Tempo project directory only.
**Mode:** mvp
**Depends on**: Phase 11
**Requirements**: VOICE-11, VOICE-12, VOICE-14, VOICE-15
**Success Criteria** (what must be TRUE):
  1. A committed `launchd/com.tempo.telegram-bot.plist` template (secret-free, like the existing `com.tempo.daily.plist`) defines a `LaunchAgent` with `Label=com.tempo.telegram-bot`, absolute `ProgramArguments` invoking `uv run tempo bot run`, `WorkingDirectory` set to the project root, `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=10`, and `StandardOutPath` / `StandardErrorPath` pointing into a gitignored `logs/` directory; install docs cover `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/...` and `bootout` to load/unload
  2. A top-level error boundary wraps each Telegram update handler: any exception in the download → transcribe → agent → reply pipeline is caught, logged with structured context (chat id, message id, stage, exception type), and acknowledged in Telegram with a brief "something went wrong" reply — the bot worker process NEVER exits on a single bad message; verified by an injected fault test (e.g. force `transcribe()` to raise, confirm the worker is still polling for the next update)
  3. Voice files are deleted from the local cache after successful transcription by default; `TEMPO_VOICE_RETENTION_DAYS` pydantic-settings field (default `0` = delete-immediately) lets the user keep N days of audio; transcripts are NEVER deleted (kept for re-running analysis later); a startup pass purges files older than the retention window
  4. The `claude-agent-sdk` invocation passes `cwd=<tempo project dir>` (and any per-SDK options that scope filesystem access to that tree); no read or write access is configured outside the project root; a smoke test confirms a request like "what's outside this project?" is rejected by the agent's own working-dir scope
  5. The README's "Telegram Voice Coach (v1.1)" section documents the privacy contract end-to-end: voice bytes never leave the laptop (faster-whisper is CPU-local); transcripts + Claude Code calls flow through the user's existing Claude subscription (same surface they already accept by using Claude Code); Telegram carries the memo and the reply text; no additional cloud surface vs. baseline Claude Code use
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 1/1 | Complete | 2026-05-26 |
| 2. Strava Ingestion | 1/1 | Complete | 2026-05-26 |
| 3. Strava Transforms + Date Spine | 1/1 | Complete | 2026-05-26 |
| 4. Load Metrics + First Analysis | 1/1 | Complete | 2026-05-26 |
| 5. Journaling via Claude | 1/1 | Complete | 2026-05-26 |
| 6. Garmin Ingestion | 1/1 | Complete | 2026-05-26 |
| 7. Recovery + Correlation + Scheduler | 1/1 | Complete | 2026-05-26 |
| 8. Modular Trackers + Heat Adaptation | 5/5 | Complete | 2026-05-27 |
| 9. Telegram Bot Foundation | 2/2 | Complete | 2026-05-27 |
| 10. Voice Intake + Local Transcription | 2/2 | Complete | 2026-05-27 |
| 11. Claude Code Agent Loop | 3/3 | Complete | 2026-05-27 |
| 12. Lifecycle, Hardening, Privacy | 2/2 | Complete | 2026-05-28 |

**Milestone status:**
- **v1.0 (Phases 1–8):** COMPLETE — all 45 v1 + Phase-8 v1.1 requirements shipped (2026-05-27).
- **v1.1 Telegram Voice Coach (Phases 9–12):** COMPLETE — all 15 VOICE-* requirements shipped (2026-05-28). Bot runs unattended under launchd `KeepAlive`, voice memos transcribe locally + route through Claude Code with per-chat 4h session memory, top-level error boundary keeps the worker up, `VOICE_RETENTION_DAYS=0` default deletes audio immediately, privacy contract documented end-to-end in `docs/PRIVACY.md`.
