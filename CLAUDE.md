<!-- GSD:project-start source:PROJECT.md -->
## Project

**Tempo** — a personal, local-first training and health system for a runner. Pulls running + wellness data from Strava and Garmin into a structured SQLite store, captures subjective reflection via a journal + heat-adaptation log, runs scheduled Claude analyses that write markdown reports (recovery, load, race readiness, correlations), and exposes the whole thing through a Telegram voice/text bot driven by a Claude Code agent loop.

**Single user — the project owner. Not a product.**

**Core value:** turn scattered training + health data into trustworthy structured signal that says when to push, when to back off, and whether goal races are on track.
<!-- GSD:project-end -->

## Current state (2026-05-28)

**v1.0 + v1.1 shipped, all on `main` at https://github.com/rossheadington/tempo (public, code-only).**

| Milestone | Phases | What it ships |
|-----------|--------|---------------|
| v1.0 | 1–7 | Strava + Garmin sync → SQLite → load/fitness/recovery/correlation analyses → daily launchd job |
| v1.1 | 8 | Modular tracker files (races.md gains `result:` + auto-link to Strava; new heat.md log surfaced in recovery report; plan.md retired) |
| v1.1 | 9–12 | Telegram bot intake → local faster-whisper transcription → Claude Code agent loop via claude-agent-sdk → launchd KeepAlive + error boundary + privacy contract |

**Live in active use:** the daily sync (`tempo run-daily` via launchd) keeps Strava + Garmin fresh. The Telegram bot (`tempo bot run` via launchd) listens for voice + text messages from the owner's chat and routes them to a Claude Code session that can call any `tempo` CLI command.

**Next planned:** v1.2 — Raspberry Pi port (systemd, ARM `curl_cffi` wheel work). Other backlog: weekly time-in-zone report (needs HR-stream backfill + zone-anchor config).

## Constraints

- **Stack:** Python 3.14, `uv` for packaging/deps, raw `sqlite3` (no ORM). Pure-stdlib analysis layer (no pandas/polars).
- **Privacy:** Public repo holds **code only**. Credentials, tokens, DB, reports, voice cache stay local + gitignored. Non-negotiable. **`docs/PRIVACY.md` is the authoritative contract** — read it before changing anything that touches user data.
- **v1.1 privacy shift:** voice files transcribe locally (never leave the laptop), but transcribed text + tool calls flow through the user's existing Claude subscription via the Claude Agent SDK. Telegram carries messages. This is documented + accepted.
- **Garmin is fragile:** unofficial `garminconnect` lib via `curl_cffi` Cloudflare bypass. Isolated as a failure domain — a Garmin 429 NEVER blocks Strava or analyses.
- **Strava rate limits:** 200/15min, 2000/day. Pull paths must be paged + resumable + tenacity-backoff-on-429-then-checkpoint-and-exit (never hammer).
- **Local-first:** No servers, no hosted DB. Everything runs on the owner's Mac (Pi later). Daily analyses run on a launchd schedule.

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Two-layer raw → structured store. Connectors write **only** to `raw_response`. Pure transforms read raw and write structured tables. The structured layer is rebuildable from raw with **zero network** via `tempo rederive`.

```
┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
│ Strava / Garmin    │───▶│ raw_response       │───▶│ structured tables  │
│ APIs (lazy fetch)  │    │ (verbatim JSON)    │    │ activity, stream,  │
│                    │    │                    │    │ wellness_day,      │
│                    │    │                    │    │ journal, bot_      │
│                    │    │                    │    │ session, date_     │
│                    │    │                    │    │ spine + view       │
└────────────────────┘    └────────────────────┘    │ daily_summary      │
                                                    └────────┬───────────┘
                                                             │
            ┌────────────────────────────────────────────────┴────┐
            │                                                     │
            ▼                                                     ▼
   ┌─────────────────┐                              ┌─────────────────────┐
   │ analyses        │                              │ markdown trackers   │
   │ (pure stdlib)   │                              │ races.md + heat.md  │
   │ load, fitness,  │◀──── lenient parsers ───────┤ (in content_dir,    │
   │ recovery, race, │                              │ gitignored)         │
   │ correlation,    │                              └─────────────────────┘
   │ noteworthy      │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────────────────────────────────────┐
   │ dated markdown reports in reports/              │
   │ (load-trend, race-readiness, recovery,          │
   │  correlations, NOTEWORTHY marker)               │
   └─────────────────────────────────────────────────┘

            ┌─────────────────────────────────────────────────┐
            │ tempo bot run (long-poll Telegram → owner chat) │
            │   voice memo                                    │
            │     → faster-whisper (local CPU, no upload)     │
            │     → transcript                                │
            │   text message ──┐                              │
            │                  ▼                              │
            │   Claude Code session via claude-agent-sdk      │
            │     • uses user's Claude subscription auth      │
            │     • cwd = project root, all tools available   │
            │     • --resume per chat (4hr rolling window)    │
            │     • final assistant text → HTML reply         │
            │       (split at 4096 chars)                     │
            └─────────────────────────────────────────────────┘
```

**Package layout (`tempo/`):**

| Module | Role |
|--------|------|
| `cli.py` | typer entrypoint; all subcommands (`sync`, `transform`, `analyze`, `journal`, `bot`, etc.) |
| `config.py` | pydantic-settings; runtime data dir = `~/.tempo/` by default (configurable); derived paths for DB / tokens / reports / content / voice cache |
| `db.py` | raw sqlite3 connection (WAL on, FK on), integer `user_version` migration runner. `SCHEMA_VERSION` = 5. |
| `migrations/0001..0005_*.sql` | hand-written SQL migrations |
| `connectors/` | `Connector` protocol; `strava.py` (OAuth + paged backfill + sync), `garmin.py` (isolated, no-retry-on-429), `tokens.py` (atomic rotating-token store, 0600), `factory.py` |
| `sync/` | `pipeline.py` (Strava-then-isolated-Garmin), `state.py` (watermark + backfill cursor), `daily.py` (sync → transform → analyze) |
| `transforms/` | pure raw→structured: `bucketing.py` (DATE_BUCKETING invariant), `strava.py`, `wellness.py`, `spine.py`, `runner.py` |
| `analysis/` | pure stdlib metric math + report rendering: `load.py` (rTSS/hrTSS/sRPE), `fitness.py` (CTL/ATL/TSB/ACWR), `race.py` (Riegel/VDOT), `baselines.py` (z-score vs personal rolling), `recovery.py`, `correlation.py`, `races.py` (parser, canonical home post-Phase-8), `heat.py` (parser + rollup), `race_link.py` (race↔activity auto-link), `noteworthy.py`, `runner.py`, `report.py`, `data.py` (read-only DB), `context.py` ⚠ DELETED Phase 8 |
| `journal/service.py` | validated boundary for subjective entries (`add_entry`, RPE 1-10, date+sport activity resolution, sRPE) |
| `bot/` | Telegram bot: `app.py` (Application builder + handler registration + Whisper warmup + cwd log + voice sweep), `handlers.py` (start, voice, text, /new), `transcribe.py` (faster-whisper singleton), `sessions.py` (per-chat session-id store, 4hr window), `agent.py` (claude-agent-sdk wrapper + 4096-char HTML split), `error_handler.py` (top-level boundary) |
| `scheduler.py` | launchd plist render + install (`com.tempo.daily.plist` for sync, `com.tempo.telegram-bot.plist` for bot) |
<!-- GSD:architecture-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions (load-bearing — follow these)

**Storage**
- **Two-layer raw → structured.** Connectors write ONLY to `raw_response`. Anything in `activity`, `wellness_day`, `journal`, `bot_session`, `daily_summary` is derived. `tempo rederive` rebuilds from raw with zero network. NEVER backfill structured tables from outside the transforms layer.
- **`daily_summary` is a VIEW.** Always fresh. One row per spine day. Rebuild it whenever a new structured table is added (see `0002_structured.sql`, `0003_journal.sql`, `0004_wellness.sql`).
- **Date bucketing has ONE rule** in `tempo/transforms/bucketing.py`. Strava's `start_date_local[:10]` is wall-clock — the trailing `Z` is FAKE, not UTC. Garmin's `calendarDate` is verbatim. Never re-project to UTC.
- **Migrations:** hand-written `.sql` files in `tempo/migrations/`, numbered. Bump `tempo/db.SCHEMA_VERSION`. Idempotent (already-applied skipped). No ORM, no Alembic.

**Analysis layer**
- **Read-only, pure stdlib, NO network.** `tempo/analysis/*` modules NEVER call out. They read structured tables + user markdown trackers + journal, that's it. (A `socket`-blocking test in `tests/test_rederive.py` enforces this for `rederive`.)
- **Frozen + slots dataclasses for context objects:** `@dataclass(frozen=True, slots=True)`. Examples: `Race`, `RacesContext`, `HeatSession`, `HeatRollup`, `RaceLink`, `AgentTurn`. Don't deviate.
- **Lenient parsers for user-maintained markdown:** missing file → `present=False`, malformed lines skipped, unknown keys ignored, NEVER raise. See `tempo/analysis/races.py::parse_races` as the model.
- **Reports degrade gracefully:** every report header states per-source data freshness (`tempo/analysis/report.py`). Insufficient data → flag "insufficient", never invent. Empty/missing sections are omitted entirely, not rendered with "N/A".

**Subjective data writes (journal, heat)**
- **Subjective rows are written ONLY via validated boundaries.** `tempo.journal.service.add_entry` for journal entries. No free-form SQL paths for Claude. `tempo journal add` CLI is a thin wrapper.
- **0/1/many activity resolution** is the convention for date-keyed lookups. 0 → unlinked, 1 → auto-link, many → refuse to guess (raise for journal; `unlinked_ambiguous` for race-link).

**Connectors**
- **Garmin is an isolated failure domain.** `tempo/sync/pipeline.py` wraps it in try/except → `SourceResult(ok=False)`, NEVER raises. A Garmin failure can't block Strava or analyses.
- **Garmin 429 = no retry.** Account-lockout risk. Strava 429 = tenacity backoff then checkpoint-and-exit.
- **Tokens stored atomically:** temp-write → fsync → rename, mode 0600. A crash mid-write never leaves a corrupt token file. (See `tempo/connectors/tokens.py`.)
- **`Connector` protocol** (`backfill(raw)` / `sync(raw, since)`) is the shape both Strava and Garmin implement. Future connectors mirror it.

**CLI**
- **typer subcommand groups** for cohesive surfaces: `tempo strava ...`, `tempo garmin ...`, `tempo journal ...`, `tempo bot ...`, `tempo analyze ...`.
- **No `tempo db` direct-SQL surface.** All writes go through validated paths.

**Config + secrets**
- **`pydantic-settings` reading gitignored `.env`** at the repo root. `SecretStr` for token-like fields.
- **`validation_alias`** for bare env var names (`TELEGRAM_BOT_TOKEN`, `WHISPER_MODEL_NAME`, `VOICE_RETENTION_DAYS`) where matching external conventions matters; otherwise the `TEMPO_*` prefix applies.
- **`.env.example` is committed** + always documents new vars with explanatory comments.

**Testing**
- **pytest, ruff, stdlib-only test fixtures.** No live network in tests (use `responses` for Strava-shape mocks, `garmin_fakes.py` for Garmin client, mocked `claude_agent_sdk.query` for agent).
- **Tests mirror modules:** `tempo/x/y.py` → `tests/test_y.py`. Tests that exercise full pipelines (CLI, end-to-end) live in `tests/test_*_cli.py`.
- **Always run `uv run pytest tests/ -x` + `uv run ruff check tempo/ tests/` before commit.** 497 tests currently; should always be green before merge.
- **The one slow test** (`tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`) loads the real `small.en` model. Skip it routinely in dev with `--deselect`.

**Files + dirs**
- All runtime data under `~/.tempo/` by default (override via `TEMPO_DATA_DIR`). NEVER inside the repo tree.
- Content dir (markdown trackers — `races.md`, `heat.md`) defaults to `~/.tempo/` but can be redirected via `TEMPO_CONTENT_DIR` to a more convenient project-local folder (e.g. the `training/` dir).
- `logs/` (gitignored) for launchd stdout/stderr capture. `voice/` cache (under content dir, gitignored) for transcribed-then-deleted voice memos.

**Commits + branching**
- `branching_strategy: none`. All work commits directly to `main` (this is a solo project, public repo for code-only).
- Atomic per-task commits using GSD subagents. Conventional commit prefixes: `feat(NN-MM):`, `test(NN-MM):`, `docs(NN-MM):`, `chore(NN-MM):`, `merge:`. Standard `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` trailer on AI-authored commits.
- Pre-commit `gitleaks` scan on staged changes. Never bypass with `--no-verify` unless explicitly authorized.
<!-- GSD:conventions-end -->

## How to use Tempo (the CLI surface)

```bash
# One-time setup
tempo init                              # create DB + schema
tempo strava auth                       # OAuth handshake (browser)
tempo garmin login                      # interactive, one-time (MFA prompt)
tempo bot install-scheduler             # writes ~/Library/LaunchAgents/com.tempo.telegram-bot.plist

# Day-to-day (mostly via launchd)
tempo sync                              # Strava + Garmin → raw_response
tempo transform                         # raw → structured
tempo analyze                           # all reports (load-trend, race-readiness, recovery, correlations)
tempo run-daily                         # the full pipeline (what launchd runs)

# Subjective capture
tempo journal add --rpe 6 --feel strong --notes "..." --day 2026-05-28 --sport Run
tempo journal list

# Telegram bot
tempo bot run                           # foreground (testing); launchd runs this in background

# Maintenance
tempo rederive                          # rebuild all structured tables from raw, zero network
tempo bot purge-voice [--yes]           # wipe voice cache
tempo install-scheduler                 # writes the daily-analysis plist (Phase 7)
```

## Where to look when

| Need | Look at |
|------|---------|
| What's shipped + when | `.planning/STATE.md` (current focus + per-phase "What's Done" sections) |
| All requirements + status | `.planning/REQUIREMENTS.md` (60 reqs across v1.0 + v1.1) |
| Phase plans + verification | `.planning/phases/NN-name/` (CONTEXT, PLAN, SUMMARY, VERIFICATION per phase) |
| Roadmap + future phases | `.planning/ROADMAP.md` |
| Research notes (Strava, Garmin, Whisper, Telegram, Agent SDK) | `.planning/research/*.md` |
| Privacy contract | `docs/PRIVACY.md` |
| Telegram bot setup walkthrough | `docs/TELEGRAM_BOT.md` |
| Date-bucketing invariant | `docs/DATE_BUCKETING.md` |
| Journal contract for Claude | `docs/JOURNALING.md` |
| Example tracker formats | `races.md.example`, `heat.md.example` (repo root) |

## Known pitfalls (caught the hard way)

- **`getUpdates` chat-id confusion.** Telegram bots can't message themselves — if you set `TELEGRAM_OWNER_CHAT_ID` to the bot's own id, every message gets allowlist-rejected silently. Use @userinfobot on Telegram to find YOUR numeric id.
- **SQLite cross-thread error.** `sqlite3.Connection` is bound to the thread that created it. If you open a connection in `asyncio.to_thread`, you must close it in the same `to_thread` call. Bit us in `tempo/bot/app.py::_post_init`; fixed.
- **`claude-agent-sdk` 0.2.x message shapes.** `AssistantMessage` has NO `role` attribute; `TextBlock` has NO `.type` attribute (just `.text`). Detect by class name (`type(msg).__name__ == "AssistantMessage"`). Bit us in `tempo/bot/agent.py`; fixed.
- **Telegram rejects empty messages** with `BadRequest("Message text is empty")`. Always guard reply text — Claude Code turns that end on a tool call produce empty assistant text. (`tempo/bot/handlers.py::_run_agent_turn` now substitutes `"(agent finished without a reply)"`.)
- **faster-whisper `segments` is a generator.** Iterate it eagerly (`list(segments)`) or transcription silently doesn't run. faster-whisper also has NO Metal/GPU on Mac — `large-v3-turbo` is too slow on CPU. Default `small.en` int8 = ~8-12s for 60s audio.
- **Telegram bot 409 Conflict** on startup if a webhook is set or a second poller is running. Our `post_init` calls `delete_webhook` defensively.
- **Strava stream lazy-fetch.** `tempo strava sync` doesn't pull streams by default — they're fetched on demand. As of 2026-05-28 we have GPS streams for ~720 activities but only **2 HR streams**. Need a `tempo strava backfill-streams --type heartrate` pass before any time-in-zone analysis can ship.
- **No `ANTHROPIC_API_KEY`.** The bot uses the user's Claude subscription via Claude Code login (`claude-agent-sdk` spawns `claude` CLI as subprocess). Don't add API-key fallback paths — it's a feature, not a missing piece.

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work
- `/gsd-plan-phase` to break down a new phase before executing
- `/gsd-new-milestone` to start a new milestone cycle

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.

**Wave-based execution pattern (established across Phases 8-12):**
1. Pre-write `CONTEXT.md` for the phase from research + roadmap success criteria (avoids redundant discuss-phase when context is clear from conversation).
2. Spawn `gsd-planner` agent → produces wave-grouped `PLAN.md` files.
3. Spawn `gsd-executor` agents per plan in **parallel worktrees** when files don't overlap; sequential when they do.
4. Merge each worktree back to main (`git stash` planner artifacts first if they collide).
5. Run `gsd-verifier` for goal-backward check → write `VERIFICATION.md`.
6. Commit + push.

Worktree gotcha: executor worktrees may fork from a commit predating recent merges. The executor agent prompt tells them to `git merge origin/main` themselves if needed — give them that license explicitly.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
