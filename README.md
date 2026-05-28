# RunOS

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

## Getting Started

Single user. Local-first. Mac (for now). ~5 minutes from clone to a working
daily-sync pipeline.

### One command (recommended)

```bash
git clone https://github.com/rossheadington/RunOS
cd RunOS
uv sync
uv run runos setup
```

The interactive wizard walks you through DB init → Strava OAuth →
optional Garmin login → optional Telegram bot → optional launchd jobs →
smoke `runos sync`. Idempotent: re-runs pick up where you left off, with
already-done steps showing `[done]` and partial-state values showing
`[set] keep / change / fresh`.

See [`docs/SETUP.md`](docs/SETUP.md) for the full end-to-end walkthrough
(every step, every prompt, every flag, recovery paths, manual uninstall).

### Re-running a single step

```bash
uv run runos setup --only=telegram          # add the bot to an existing install
uv run runos setup --only=strava            # rotate Strava creds
uv run runos setup --skip-garmin            # full run, no Garmin
```

### Manual setup (advanced)

If you prefer to run each step by hand (or you're debugging a stuck
install), the equivalent commands are:

```bash
# Edit .env with RUNOS_STRAVA_CLIENT_ID + RUNOS_STRAVA_CLIENT_SECRET first
# (see .env.example for the full variable list).

uv run runos init                                   # DB schema
uv run runos strava auth                            # prints OAuth URL
uv run runos strava auth --code <CODE>              # completes handshake

# Optional: Garmin
uv run runos garmin login

# Optional: Telegram bot (see docs/TELEGRAM_BOT.md for the full walkthrough)
uv run runos bot run                                # foreground; launchd for background

# Optional: launchd
uv run runos install-scheduler --to-launch-agents --hour 5 --minute 30
uv run runos bot install-scheduler --to-launch-agents

# Smoke
uv run runos sync
```

Full details + per-step recovery: [`docs/SETUP.md`](docs/SETUP.md). The
sections below give the deep dive on each data source for users who want
to understand the moving parts.

## Strava setup (one-time)

RunOS pulls your Strava data through the official OAuth2 API. You provide your
own API application credentials; nothing is shared and no secret ever enters
this repo (tokens live under `~/.runos/tokens/`, mode 0600, gitignored).

1. **Create a Strava API application** at
   <https://www.strava.com/settings/api>. Set the *Authorization Callback
   Domain* to `localhost`. Note the **Client ID** and **Client Secret**.
2. **Configure RunOS.** Copy `.env.example` to `.env` and fill in:
   ```
   RUNOS_STRAVA_CLIENT_ID=<your client id>
   RUNOS_STRAVA_CLIENT_SECRET=<your client secret>
   ```
3. **Authorise (one time).** Run `runos strava auth`, open the printed URL in a
   browser, approve access, then copy the `code` query parameter from the
   redirected `localhost` URL and run:
   ```
   runos strava auth --code <CODE>
   ```
   Tokens are stored locally and atomically. Strava rotates refresh tokens on
   every refresh; RunOS persists the new one durably so you never have to repeat
   this step.

### Pulling data

```
runos strava backfill            # resumable all-time activity history
runos strava backfill --page-budget 5   # spread a large history across runs/days
runos strava streams --limit 20  # lazily fetch HR/pace/GPS/power/cadence streams
runos sync                       # daily incremental: only activities since the watermark
```

The backfill is checkpointed: if it hits Strava's rate limit (200 req/15 min,
2000/day) or is interrupted, just run it again — it resumes from a
`backfill_cursor` and never re-fetches what's already stored. Connectors write
**only** verbatim responses to the raw store; structured tables are derived later
(Phase 3) and can be rebuilt from raw without re-fetching.

> **API terms.** Strava's API Agreement includes a 7-day cache limit and a
> restriction on feeding data to AI models. RunOS's use is private, single-user,
> self-data that is never shared; this is an accepted, documented stance (see
> `.planning/REQUIREMENTS.md` → Known Accepted Conflicts), not an oversight.

## Garmin setup (one-time)

RunOS pulls your Garmin **wellness** data (sleep, HRV, resting HR, body battery,
stress, steps) through the unofficial `garminconnect` library. Garmin has no
official personal API, so this is the most fragile source — RunOS treats it as an
**isolated failure domain**: if Garmin breaks (a site change, an account 429,
expired session), the daily run logs it and skips, and your Strava sync + all
analysis still complete on existing data. No Garmin data is ever lost (it is
re-derivable from the raw store).

> **Privacy & safety:** your Garmin password lives only in the gitignored `.env`.
> Garmin session tokens are stored under `~/.runos/tokens/garmin/` (mode 0600),
> outside the repo tree, and are gitignored. Nothing Garmin-related is ever
> committed.

1. **Configure credentials.** In your `.env`:
   ```
   RUNOS_GARMIN_EMAIL=<your Garmin Connect email>
   RUNOS_GARMIN_PASSWORD=<your Garmin Connect password>
   ```
2. **Log in once (interactive).** Run:
   ```
   runos garmin login
   ```
   This is the **only** command that submits your credentials. If Garmin asks for
   a multi-factor (MFA) code, RunOS prompts for it. On success, session tokens are
   persisted and **reused** — every later sync loads those tokens and **never logs
   in again**. This matters: Garmin aggressively rate-limits logins *per account*,
   and repeated logins can lock you out (of the app too) for 48h+. The scheduled
   sync therefore never triggers a fresh login.

   > **If you ever see repeated `429 Too Many Requests`: STOP.** Do not retry —
   > retries compound an account-level lockout. Wait a few hours. RunOS itself
   > never retries a Garmin 429 (it fails-logs-skips immediately) for exactly this
   > reason.

### Pulling wellness data

```
runos garmin backfill --days 60   # one-time: trailing 60 days of wellness history
runos garmin sync                 # incremental: recent days (reuses tokens)
runos sync                        # daily: runs Strava, THEN attempts Garmin (isolated)
```

`runos sync` reports per-source status, e.g.:
```
Sync complete (per-source status):
  strava: ok (1234 raw rows)
  garmin: skipped -- not authenticated: ...   # never blocks Strava
```

Raw Garmin responses are stored verbatim, then transformed (`runos transform` /
`runos rederive`, zero network) into a `wellness_day` table — **one row per local
calendar day**, keyed by Garmin's `calendarDate` (the wake-up day it assigns to
overnight sleep/HRV, which removes the cross-midnight ambiguity). The
`daily_summary` view left-joins wellness so every day carries its activity,
wellness, and journal context in one row (rest days with only sleep included).

### Personal baselines

Raw HRV / resting HR / sleep numbers are meaningless without a personal norm, so
RunOS computes **rolling personal baselines** (trailing-window mean + SD with a
z-score, plus an EWMA) per metric from `wellness_day`. A reading is compared only
to the user's own recent history; with too little history a baseline honestly
reports "insufficient data" rather than inventing a norm. These feed the recovery
analysis in Phase 7.

> **Library fragility.** `garminconnect` is unofficial and can break when Garmin
> changes its auth/site (e.g. the `garth` foundation was deprecated in March
> 2026). RunOS isolates it behind the connector seam so a breakage degrades
> Garmin only. If it ever stops working, bump the library when upstream patches
> it; in the meantime Strava + analysis keep running, and you can fall back to
> Garmin's manual FIT/CSV export.

## Analysis & reports

Once activities are synced and transformed, RunOS turns them into per-activity
**load** (rTSS pace-based, with an hrTSS fallback), fitness/fatigue/form
(**CTL/ATL/TSB**), an **ACWR / ramp-rate** guardrail, and **race predictions**
(Riegel/VDOT), written as dated markdown reports.

```
runos analyze                 # the FULL suite (all four reports below)
runos analyze load-trend      # CTL/ATL/TSB, ACWR/ramp, weekly volume
runos analyze race-readiness  # Riegel/VDOT vs goal + CTL/TSB form check
runos analyze recovery        # multi-signal recovery / overtraining vs baselines
runos analyze correlations    # sleep / HRV / RPE vs performance (honest n-gating)
```

Reports land in the gitignored reports dir (`~/.runos/reports/` by default) as
`YYYY-MM-DD-load-trend.md`, `-race-readiness.md`, `-recovery.md`,
`-correlations.md`. Every report opens with a **per-source data-freshness header**
(last successful sync + staleness flag) so a stale dataset is never trusted
silently; thin data degrades to an explicit "insufficient data" note rather than
an invented number.

### Recovery / overtraining (multi-signal)

`runos analyze recovery` combines the **rising-load** half (CTL ramp rate / ACWR)
with **baseline-relative recovery markers** (HRV, resting HR, sleep vs your own
personal rolling baselines). The high-confidence overtraining pattern is rising
load *and* recovery markers diverging from baseline. A key subtlety it encodes:
**HRV is judged in BOTH directions** — a drop below baseline is the classic
suppressed-recovery signal, but in deep overtraining HRV can paradoxically *rise*
(parasympathetic saturation), so it flags the *magnitude* of the deviation, not
just "low". When baselines lack history it reports **insufficient data** rather
than guessing.

### Correlation insight (honest about small n)

`runos analyze correlations` links candidate predictors (prior-night sleep / HRV,
subjective RPE) to outcomes (training load as a performance proxy, RPE). Because
correlation is data-hungry, a relationship is reported **only with at least 20
paired days**; below that floor each pair shows an explicit *"insufficient data —
N paired days, need 20"* note instead of asserting a weak signal from too little
history. Correlation is not causation — relationships are a prompt to investigate.

## Telegram bot (v1.1)

A personal, owner-only Telegram bot that runs as a local long-polling worker —
the **v1.1 voice-coach intake**. Single chat allowlisted at the filter level;
any other chat is silently dropped before any handler runs. Voice memos are
transcribed locally with `faster-whisper`, text and transcripts route through
Claude Code via the `claude-agent-sdk`, the worker survives single-message
failures via a top-level error boundary, and the whole thing runs unattended
under a launchd `LaunchAgent` with `KeepAlive=true`. See
[`docs/PRIVACY.md`](docs/PRIVACY.md) for the full privacy contract.

```
# One-time setup: see docs/TELEGRAM_BOT.md (full @BotFather + getUpdates walkthrough).
# Add TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID to .env (no RUNOS_ prefix).
chmod 600 .env
uv run runos bot run
```

See [`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) for the @BotFather +
`getUpdates` walkthrough, the sanity-check from a second account, and the
troubleshooting list (409 Conflict, token rotation).

### Voice intake (v1.1 / Phase 10)

With the bot running (`uv run runos bot run`), **record a voice memo in the
owner's Telegram chat — RunOS transcribes it locally and replies with the
text in italics.** No audio bytes ever leave the laptop: transcription runs
through [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (which
bundles PyAV — no system `ffmpeg` needed) using a CPU-only model that is
**warmed at bot startup**, so the very first voice memo does not pay the
multi-second cold-start cost.

Under the hood:

- The `.ogg` is saved to `<content_dir>/voice/<message_id>-<file_unique_id>.ogg`
  (gitignored; directory created on first use with mode 0700).
- Transcription is dispatched off the asyncio event loop via
  `asyncio.to_thread`, so the bot stays responsive even mid-transcription.
- The reply is the transcript wrapped in `<i>...</i>`, with HTML-special
  chars (`<` `>` `&`) escaped — Telegram never tries to parse user speech
  as markup.

**Model configuration.** Three bare env vars (the standard `WHISPER_*`
convention, no `RUNOS_` prefix) override the locked defaults:

```
# Defaults shown -- all three are commented out in .env.example.
# WHISPER_MODEL_NAME=small.en      # ~480 MB; tiny.en/base.en/medium.en/large-v3-turbo also valid
# WHISPER_COMPUTE_TYPE=int8        # int8 / int8_float16 / float16 / float32
# WHISPER_DEVICE=cpu               # on Mac, cpu is the only path that works
```

> **On macOS, `WHISPER_DEVICE=cpu` is the only working option.** CTranslate2
> (faster-whisper's backend) has no Metal/MPS support — `cuda` only works on
> Linux + NVIDIA. The default `small.en` int8 on an M-series CPU transcribes
> a 60-second memo in ~8-12 seconds, which is the sweet spot for accuracy on
> runner jargon without an unacceptable wait.

**First-run startup cost.** On the very first `runos bot run` after setting
`WHISPER_MODEL_NAME=...` (or on the very first run at the default
`small.en`), `faster-whisper` downloads the model from Hugging Face Hub
(~480 MB for `small.en`, cached under `~/.cache/huggingface/hub/`). The
download happens **once** in the bot's `post_init` hook BEFORE polling
begins, so by the time the bot starts listening it is fully warm — a real
voice memo never pays the download or load cost. Subsequent restarts only
pay the ~1–3-second model load.

**The 20 MB cap.** Telegram's bot API caps `getFile` downloads at 20 MB.
Voice memos larger than that are rejected with a clear reply — *"Sorry —
that voice memo is over Telegram's 20 MB bot API limit. Try a shorter
recording or split it."* — and no doomed network call is attempted. At
Telegram's ~16 kbps Opus encoding, 20 MB is roughly 2.5 hours of voice, so
in normal use you will never hit this; the guard is for safety, not the
common case.

**Voice retention.** `VOICE_RETENTION_DAYS=0` (the privacy-safe default)
deletes the raw `.ogg` immediately after the transcript reaches the agent.
Set it to N>0 in `.env` to keep recent memos for N days for debugging
Whisper misfires; a startup sweep purges anything older. Flush the cache
manually any time with `uv run runos bot purge-voice [--yes]`. Full
details in [`docs/PRIVACY.md`](docs/PRIVACY.md).

### Claude Code agent loop (v1.1 / Phase 11)

With Phase 11 plan 11-03 wired, voice memos and text messages route
through Claude Code via the [`claude-agent-sdk`](https://github.com/anthropics/claude-agent-sdk-python)
Python package; the final assistant reply comes back to Telegram as HTML
and is split across the 4096-character per-message cap with `[k/N] `
prefixes when needed. Per-chat session memory resumes within a 4-hour
window via the `bot_session` table; send `/new` to start a fresh session
on demand. Per-turn token usage and cost are logged at INFO
(`agent turn · chat=… · session=… · tokens_in=… · tokens_out=… · cost=$… · wall=…s`).
The bot uses your existing `claude login` — **no** `ANTHROPIC_API_KEY` is
needed or used. If the `claude` CLI is missing from PATH the bot exits at
startup with a clear error before any Telegram traffic. See
[`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) "Phase 11: the agent loop".

### Always-on bot via launchd (v1.1 / Phase 12)

For unattended operation across reboots, sleep/wake, and crashes, the bot
runs as a `launchd` `LaunchAgent` with `KeepAlive=true` + `RunAtLoad=true`
+ `ThrottleInterval=10`. A top-level error handler
(`runos/bot/error_handler.py`) is registered on the PTB `Application` so
any uncaught handler exception logs the full traceback + sends a fixed
*"Sorry — something went wrong on my end. Check the logs."* reply to the
offending chat without ever re-raising — combined with launchd
`KeepAlive`, a single bad message can never take the worker down.

```bash
uv run runos bot install-scheduler       # writes plist + prints next steps; doesn't run launchctl
cp ~/.runos/launchd/com.runos.telegram-bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.runos.telegram-bot.plist
launchctl kickstart -k gui/$(id -u)/com.runos.telegram-bot
```

Logs land in `logs/runos-bot.out.log` and `logs/runos-bot.err.log`
(gitignored). See
[`docs/TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) "Always-on under launchd"
for the full lifecycle (install, kickstart, bootout, removal) and
[`docs/PRIVACY.md`](docs/PRIVACY.md) for what the unattended worker
touches.

## Scheduling (the daily run via launchd)

The daily loop — `runos run-daily` — runs **sync → transform → analyze** and
writes the report suite. It is **idempotent and catch-up-aware**: sync is
watermark-driven and raw writes are idempotent, so running it twice is harmless
and a **missed day is recovered on the next run** (everything since the last
successful watermark is pulled, never just "today"). Garmin stays isolated (a 429
or breakage is skipped; Strava + analysis still complete).

It surfaces output **only when noteworthy** (SCHED-03): all four reports are always
written, but the run only prints a `NOTEWORTHY` block (and writes a
`reports/NOTEWORTHY.md` marker) when a threshold is crossed — ACWR out of the safe
range, an aggressive ramp, a `monitor`/`elevated` recovery verdict, a strong
baseline z-score, a target race within ~14 days, or a stale source. The thresholds
live (configurable + documented) in `runos/analysis/noteworthy.py`.

```
runos run-daily          # sync -> transform -> analyze (the launchd job runs this)
runos run-daily --no-sync   # transform + analyze existing data only (no network)
```

### Enable the launchd LaunchAgent (macOS)

RunOS uses **launchd, not cron**. On macOS, cron **silently skips** jobs while the
Mac is asleep (your daily sync would just never run) and runs in a stripped
environment that often can't find `uv`/Python. launchd's `StartCalendarInterval`
runs a **missed job on wake**, and RunOS's generated plist uses absolute paths +
an explicit `PATH`/`RUNOS_DATA_DIR` so the scheduled run behaves exactly like your
terminal. stdout/stderr are captured to a log file under the data dir.

Generate the plist, then load it (RunOS **never** runs `launchctl` for you — that
explicit, informed step is yours):

```
runos install-scheduler --hour 5 --minute 30     # writes a template under ~/.runos/launchd/
# inspect it, then:
cp ~/.runos/launchd/com.runos.daily.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.runos.daily.plist     # enable
# to disable later:
launchctl unload -w ~/Library/LaunchAgents/com.runos.daily.plist
```

`runos install-scheduler --to-launch-agents` writes the plist straight into
`~/Library/LaunchAgents/` (still without loading it). A committed, secret-free
template lives at [`launchd/com.runos.daily.plist`](launchd/com.runos.daily.plist).

**Load config** (`.env`): set `RUNOS_THRESHOLD_PACE_S_PER_KM` (required for
rTSS) and optionally `RUNOS_MAX_HR` / `RUNOS_RESTING_HR` / `RUNOS_THRESHOLD_HR`
(the hrTSS fallback). See `.env.example`.

**Tracker files**: copy `races.md.example`, `heat.md.example`,
`strength.md.example`, `weight.md.example`, and `food.md.example` into
your content dir as `races.md` / `heat.md` / `strength.md` / `weight.md`
/ `food.md` (default `~/.runos/`) and edit them. RunOS reads `races.md`
for race-readiness context, `heat.md` for heat-adaptation context,
`strength.md` for S&C context (see
[`docs/STRENGTH.md`](docs/STRENGTH.md)), `weight.md` for body-weight
context with a 7d/28d/EWMA rollup (see
[`docs/WEIGHT.md`](docs/WEIGHT.md)), and `food.md` for daily nutrition
with a 7d-trailing rollup (lenient parser accepts both inline and
block-per-meal formats; see [`docs/NUTRITION.md`](docs/NUTRITION.md)) in
the recovery report and the standalone `runos analyze nutrition` report;
none are ever committed.

## Status

**All 7 phases complete — RunOS is feature-complete for v1.** The full pipeline
runs end to end: pull → store → transform → analyze → report, on a daily schedule.

- **Phase 4** was the Strava end-to-end milestone (load → CTL/ATL/TSB → ACWR/ramp →
  load-trend + race-readiness reports on real Strava data).
- **Phase 6** added Garmin wellness as an isolated source (login-once token reuse,
  no-retry-on-429 fail-log-skip, a `calendarDate`-keyed `wellness_day` joined into
  `daily_summary`, personal rolling baselines).
- **Phase 7** closed the analysis suite — multi-signal recovery/overtraining and
  honest n-gated correlation insight — plus the launchd daily scheduler with
  watermark catch-up and noteworthy-only surfacing.

The four target analyses (recovery, load & trends, race readiness, correlations)
are all delivered as dated markdown reports with freshness headers and honest
"insufficient data" degradation. See `.planning/` for the roadmap and requirement
traceability.

## Stack

Python 3.14 · SQLite · uv · stravalib · garminconnect · tenacity · pydantic-settings
