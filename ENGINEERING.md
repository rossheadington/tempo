# ENGINEERING.md

The technical reference for changing code in this project. **Read this in full before you make any change to Tempo's code, schema, or behaviour.** The coach persona and product context live in `CLAUDE.md`; this document is purely the engineering contract.

If you got here from a Telegram conversation that turned into an engineering task, read this end-to-end, then proceed. Don't skim from memory.

## Project

**Tempo** — a personal, local-first training and health system for a runner. Pulls running + wellness data from Strava and Garmin into a structured SQLite store, captures subjective reflection via a journal + heat-adaptation log + strength + weight + food trackers, exposes the whole thing through a Telegram voice/text bot driven by a Claude Code agent loop, and runs an hourly Strava+Garmin sync that notifies on failure.

**Single user — the project owner. Not a product.**

**Core value:** turn scattered training + health data into trustworthy structured signal that says when to push, when to back off, and whether goal races are on track.

## Current state (2026-05-28)

**v1.0 + v1.1 + v1.2 + v1.3 + v1.4 + v1.5 shipped, all on `main` at https://github.com/rossheadington/tempo (public, code-only).**

| Milestone | Phases | What it ships |
|-----------|--------|---------------|
| v1.0 | 1–7 | Strava + Garmin sync → SQLite → load/fitness/recovery/correlation analyses → daily launchd job |
| v1.1 | 8 | Modular tracker files (races.md gains `result:` + auto-link to Strava; heat.md log surfaced in recovery report; plan.md retired) |
| v1.1 | 9–12 | Telegram bot intake → local faster-whisper transcription → Claude Code agent loop via claude-agent-sdk → launchd KeepAlive + error boundary + privacy contract |
| v1.2 | 13 | Strength & conditioning tracker (`strength.md`) surfaced in recovery report |
| v1.3 | 14 | First-run setup wizard (`tempo setup`) |
| v1.4 | 15 | Weight tracker (`weight.md`) with EWMA trend, kg/lb normalisation, recovery report integration |
| v1.5 | 16 | Nutrition tracker (`food.md`) with two-format parser, `tempo analyze nutrition` standalone report, recovery report integration |

**Operational model (post-simplification, 2026-05-28):**

- Hourly: `tempo sync --notify-on-failure` via launchd (`com.tempo.hourly-sync.plist`). Strava + Garmin → raw. Telegram message only on failure; silent on success.
- Always-on: `tempo bot run` via launchd (`com.tempo.telegram-bot.plist`, KeepAlive=true). Listens for owner-only messages, routes to a Claude Code session.
- **No scheduled reports.** Reports are generated on-demand by asking the bot ("how am I doing for recovery?") — the agent runs `tempo analyze <type>` and returns the rendered markdown. The old daily plist is deprecated; remove with `launchctl unload -w ~/Library/LaunchAgents/com.tempo.daily.plist && rm ~/Library/LaunchAgents/com.tempo.daily.plist`.

**Live in active use:** the hourly sync keeps raw data fresh. The Telegram bot is the primary interaction surface.

**Next planned:** Raspberry Pi port (systemd unit + timer for hourly sync, ARM `curl_cffi` wheel work).

## Constraints

- **Stack:** Python 3.14, `uv` for packaging/deps, raw `sqlite3` (no ORM). Pure-stdlib analysis layer (no pandas/polars).
- **Privacy:** Public repo holds **code only**. Credentials, tokens, DB, reports, voice cache stay local + gitignored. Non-negotiable. **`docs/PRIVACY.md` is the authoritative contract** — read it before changing anything that touches user data.
- **v1.1 privacy shift:** voice files transcribe locally (never leave the laptop), but transcribed text + tool calls flow through the user's existing Claude subscription via the Claude Agent SDK. Telegram carries messages. This is documented + accepted.
- **Garmin is fragile:** unofficial `garminconnect` lib via `curl_cffi` Cloudflare bypass. Isolated as a failure domain — a Garmin 429 NEVER blocks Strava or analyses. Symmetric: a Strava failure NEVER blocks Garmin or analyses either (fixed in the simplify pass, 2026-05-28).
- **Strava rate limits:** 200/15min, 2000/day. Pull paths must be paged + resumable + tenacity-backoff-on-429-then-checkpoint-and-exit (never hammer).
- **Local-first:** No servers, no hosted DB. Everything runs on the owner's Mac (Pi later). The hourly sync is the only scheduled job.

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
   │ (pure stdlib)   │                              │ races/heat/strength/│
   │ load, fitness,  │◀──── lenient parsers ───────┤ weight/food (.md)   │
   │ recovery, race, │                              │ in content_dir,     │
   │ correlation,    │                              │ gitignored          │
   │ noteworthy,     │                              └─────────────────────┘
   │ nutrition       │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────────────────────────────────────┐
   │ dated markdown reports in reports/              │
   │ (load-trend, race-readiness, recovery,          │
   │  correlations, nutrition, NOTEWORTHY marker)    │
   │ generated ON-DEMAND via the bot, not on a       │
   │ schedule                                        │
   └─────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────┐
   │ hourly: tempo sync --notify-on-failure          │
   │   launchd StartInterval=3600                    │
   │   Strava + Garmin -> raw, transform automatic   │
   │   Telegram message ONLY on source failure       │
   └─────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────┐
   │ tempo bot run (long-poll Telegram → owner chat) │
   │   voice memo                                    │
   │     → faster-whisper (local CPU, no upload)     │
   │     → transcript echoed back to user            │
   │   text message ──┐                              │
   │                  ▼                              │
   │   Claude Code session via claude-agent-sdk      │
   │     • uses user's Claude subscription auth      │
   │     • cwd = project root, all tools available   │
   │     • --resume per chat (persists until /clear) │
   │     • coach mode default (CLAUDE.md)            │
   │     • engineering mode on demand (ENGINEERING.md)│
   │     • Markdown tables → <pre> blocks            │
   │     • final assistant text → HTML reply         │
   │       (split at 4096 chars)                     │
   │   Slash commands: /start, /clear, /sync          │
   └─────────────────────────────────────────────────┘
```

**Package layout (`tempo/`):**

| Module | Role |
|--------|------|
| `cli.py` | typer entrypoint; all subcommands (`sync`, `transform`, `analyze`, `journal`, `bot`, `setup`, `install-hourly-sync`, etc.) |
| `config.py` | pydantic-settings; runtime data dir = `~/.tempo/` by default (configurable); derived paths for DB / tokens / reports / content / voice cache; `get_settings()` is intentionally NOT cached |
| `db.py` | raw sqlite3 connection (WAL on, FK on), integer `user_version` migration runner. `SCHEMA_VERSION` = 5. |
| `migrations/0001..0005_*.sql` | hand-written SQL migrations |
| `connectors/` | `Connector` protocol; `strava.py` (OAuth + paged backfill + sync + `stored_activity_ids(prefer_with_hr=True)`), `garmin.py` (isolated, no-retry-on-429), `tokens.py` (atomic rotating-token store, 0600), `factory.py` |
| `sync/` | `pipeline.py` (symmetric isolation: both Strava and Garmin wrapped → SourceResult), `state.py` (watermark + backfill cursor), `daily.py` (sync → transform → analyze; **on-demand only post-simplification, not scheduled**), `notify.py` (one-shot Telegram failure notifier, stdlib urllib) |
| `transforms/` | pure raw→structured: `bucketing.py` (DATE_BUCKETING invariant), `strava.py`, `wellness.py`, `spine.py`, `runner.py` (post-transform hook auto-links orphan journal entries) |
| `analysis/` | pure stdlib metric math + report rendering: `load.py` (rTSS/hrTSS/sRPE), `fitness.py` (CTL/ATL/TSB/ACWR), `race.py` (Riegel/VDOT), `baselines.py` (z-score vs personal rolling), `recovery.py`, `correlation.py`, `races.py` (parser, canonical home post-Phase-8), `heat.py` (parser + rollup), `strength.py` (parser + rollup), `weight.py` (parser + EWMA rollup), `nutrition.py` (parser + daily + 7d rollup), `nutrition_report.py` (standalone report), `race_link.py` (race↔activity auto-link), `noteworthy.py`, `runner.py`, `report.py`, `data.py` (read-only DB) |
| `journal/service.py` | validated boundary for subjective entries (`add_entry`, RPE 1-10, date+sport activity resolution, sRPE) + `link_orphan_entries` (post-transform sweep) |
| `bot/` | Telegram bot: `app.py` (Application builder + handler registration + Whisper warmup + cwd log + voice sweep + `setMyCommands` menu publish), `handlers.py` (start, voice (echoes transcript), text, `/clear`, `/sync`), `transcribe.py` (faster-whisper singleton), `sessions.py` (per-chat session-id store, persists until `/clear`), `agent.py` (claude-agent-sdk wrapper + 4096-char HTML split + Markdown-tables→`<pre>` rendering), `error_handler.py` (top-level boundary) |
| `setup/` | First-run wizard: `env_io.py` (atomic `.env` writes), `state.py` (install-state detection), `prompts.py`, `wizard.py` (10-step orchestrator) |
| `scheduler.py` | launchd plist render + install: daily (`com.tempo.daily.plist`, deprecated post-simplification), hourly sync (`com.tempo.hourly-sync.plist`), bot (`com.tempo.telegram-bot.plist`) |

## Conventions (load-bearing — follow these)

**Storage**
- **Two-layer raw → structured.** Connectors write ONLY to `raw_response`. Anything in `activity`, `wellness_day`, `journal`, `bot_session`, `daily_summary` is derived. `tempo rederive` rebuilds from raw with zero network. NEVER backfill structured tables from outside the transforms layer.
- **`daily_summary` is a VIEW.** Always fresh. One row per spine day. Rebuild it whenever a new structured table is added (see `0002_structured.sql`, `0003_journal.sql`, `0004_wellness.sql`).
- **Date bucketing has ONE rule** in `tempo/transforms/bucketing.py`. Strava's `start_date_local[:10]` is wall-clock — the trailing `Z` is FAKE, not UTC. Garmin's `calendarDate` is verbatim. Never re-project to UTC.
- **Migrations:** hand-written `.sql` files in `tempo/migrations/`, numbered. Bump `tempo/db.SCHEMA_VERSION`. Idempotent (already-applied skipped). No ORM, no Alembic.

**Analysis layer**
- **Read-only, pure stdlib, NO network.** `tempo/analysis/*` modules NEVER call out. They read structured tables + user markdown trackers + journal, that's it. (A `socket`-blocking test in `tests/test_rederive.py` enforces this for `rederive`.)
- **Frozen + slots dataclasses for context objects:** `@dataclass(frozen=True, slots=True)`. Examples: `Race`, `RacesContext`, `HeatSession`, `HeatRollup`, `StrengthSession`, `WeightEntry`, `FoodEntry`, `RaceLink`, `AgentTurn`. Don't deviate.
- **Lenient parsers for user-maintained markdown:** missing file → `present=False`, malformed lines skipped, unknown keys ignored, NEVER raise. See `tempo/analysis/races.py::parse_races` as the model. Weight + nutrition parsers also expose `malformed_lines`; older parsers (races, heat, strength) do not yet — known asymmetry.
- **Reports degrade gracefully:** every report header states per-source data freshness (`tempo/analysis/report.py`). Insufficient data → flag "insufficient", never invent. Empty/missing sections are omitted entirely, not rendered with "N/A".
- **3-state degradation rule** for tracker sections in the recovery report (heat / strength / weight / nutrition): absent → omit; stale → one-line nudge; current → full rollup. Encoded four times across `_render_<X>_section` functions — known structural duplication; refactor when adding the 5th tracker.

**Subjective data writes (journal, heat, strength, weight, food)**
- **Subjective rows are written ONLY via validated boundaries.** `tempo.journal.service.add_entry` for journal entries. No free-form SQL paths for Claude. `tempo journal add` CLI is a thin wrapper.
- **Markdown trackers are append-only by convention.** Corrections happen by appending a fresh entry with the same date (latest-wins) — never destructively rewrite existing lines.
- **0/1/many activity resolution** is the convention for date-keyed lookups. 0 → unlinked, 1 → auto-link, many → refuse to guess (raise for journal; `unlinked_ambiguous` for race-link).

**Connectors**
- **Symmetric isolation.** Both Strava and Garmin are wrapped in `sync/pipeline.py` → `SourceResult(ok=False)` instead of raising. Either failing never blocks the other or the downstream analyses.
- **Garmin 429 = no retry.** Account-lockout risk. Strava 429 = tenacity backoff then checkpoint-and-exit.
- **Tokens stored atomically:** temp-write → fsync → rename, mode 0600. A crash mid-write never leaves a corrupt token file. (See `tempo/connectors/tokens.py`.)
- **`Connector` protocol** (`backfill(raw)` / `sync(raw, since)`) is the shape both Strava and Garmin implement. Future connectors mirror it.

**CLI**
- **typer subcommand groups** for cohesive surfaces: `tempo strava ...`, `tempo garmin ...`, `tempo journal ...`, `tempo bot ...`, `tempo analyze ...`.
- **No `tempo db` direct-SQL surface.** All writes go through validated paths.

**Config + secrets**
- **`pydantic-settings` reading gitignored `.env`** at the repo root. `SecretStr` for token-like fields.
- **`validation_alias`** for bare env var names (`TELEGRAM_BOT_TOKEN`, `WHISPER_MODEL_NAME`, `VOICE_RETENTION_DAYS`, `TEMPO_TARGET_KCAL`) where matching external conventions matters; otherwise the `TEMPO_*` prefix applies.
- **`.env.example` is committed** + always documents new vars with explanatory comments.
- **`get_settings()` is intentionally NOT cached** so tests can override env between calls.

**Testing**
- **pytest, ruff, stdlib-only test fixtures.** No live network in tests (use `responses` for Strava-shape mocks, `garmin_fakes.py` for Garmin client, mocked `claude_agent_sdk.query` for agent).
- **Tests mirror modules:** `tempo/x/y.py` → `tests/test_y.py`. Tests that exercise full pipelines (CLI, end-to-end) live in `tests/test_*_cli.py`.
- **Always run `uv run pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` + `uv run ruff check tempo/ tests/` before commit.** ~680 tests currently; should always be green before merge.
- **The one slow test** (`tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`) loads the real `small.en` model. Skip it routinely in dev with `--deselect`.

**Files + dirs**
- All runtime data under `~/.tempo/` by default (override via `TEMPO_DATA_DIR`). NEVER inside the repo tree.
- Content dir (markdown trackers — `races.md`, `heat.md`, `strength.md`, `weight.md`, `food.md`) defaults to `~/.tempo/` but can be redirected via `TEMPO_CONTENT_DIR` to a more convenient project-local folder (e.g. the `training/` dir).
- `logs/` (gitignored) for launchd stdout/stderr capture. `voice/` cache (under content dir, gitignored) for transcribed-then-deleted voice memos.

**Commits + branching**
- `branching_strategy: none`. All work commits directly to `main` (this is a solo project, public repo for code-only).
- Atomic per-task commits using GSD subagents. Conventional commit prefixes: `feat(NN-MM):`, `test(NN-MM):`, `docs(NN-MM):`, `chore(NN-MM):`, `fix(scope):`, `merge:`. Standard `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` trailer on AI-authored commits.
- Pre-commit `gitleaks` scan on staged changes. Never bypass with `--no-verify` unless explicitly authorized.

## How to use Tempo (the CLI surface)

```bash
# One-time setup
tempo setup                             # interactive first-run wizard (recommended)
# OR manual:
tempo init                              # create DB + schema
tempo strava auth                       # OAuth handshake (browser)
tempo garmin login                      # interactive, one-time (MFA prompt)
tempo install-hourly-sync               # hourly Strava+Garmin sync, notify on failure
tempo bot install-scheduler             # always-on Telegram bot (KeepAlive)

# Hourly (via launchd, automatic)
tempo sync --notify-on-failure          # what the hourly LaunchAgent runs

# On-demand
tempo sync                              # Strava + Garmin → raw (interactive, no notify)
tempo transform                         # raw → structured
tempo analyze recovery                  # one specific report
tempo analyze                           # all reports
tempo strava streams --prefer-with-hr --limit 200   # backfill HR streams for HR-recorded activities

# Subjective capture
tempo journal add --rpe 6 --feel strong --notes "..." --day 2026-05-28 --sport Run
tempo journal list
tempo journal link-orphans              # post-transform hook also runs this automatically

# Telegram bot
tempo bot run                           # foreground (testing); launchd runs this in background

# Maintenance
tempo rederive                          # rebuild all structured tables from raw, zero network
tempo bot purge-voice [--yes]           # wipe voice cache

# Deprecated (kept for compatibility, no longer scheduled)
tempo run-daily                         # the OLD full pipeline (sync+transform+analyze); run on-demand only
tempo install-scheduler                 # OLD daily plist; superseded by install-hourly-sync
```

## Where to look when

| Need | Look at |
|------|---------|
| What's shipped + when | `.planning/STATE.md` (current focus + per-phase "What's Done" sections) |
| All requirements + status | `.planning/REQUIREMENTS.md` |
| Phase plans + verification | `.planning/phases/NN-name/` (CONTEXT, PLAN, SUMMARY, VERIFICATION per phase) |
| Roadmap + future phases | `.planning/ROADMAP.md` |
| Research notes (Strava, Garmin, Whisper, Telegram, Agent SDK) | `.planning/research/*.md` |
| Privacy contract | `docs/PRIVACY.md` |
| Telegram bot setup walkthrough | `docs/TELEGRAM_BOT.md` |
| Date-bucketing invariant | `docs/DATE_BUCKETING.md` |
| Journal contract for Claude | `docs/JOURNALING.md` |
| Setup wizard walkthrough | `docs/SETUP.md` |
| Strength tracker format | `docs/STRENGTH.md` |
| Weight tracker format | `docs/WEIGHT.md` |
| Nutrition tracker format | `docs/NUTRITION.md` |
| Example tracker formats | `*.md.example` (repo root) |
| Raspberry Pi deployment target | `docs/RASPBERRY_PI.md` |

## Known pitfalls (caught the hard way)

These are real failures that happened and got fixed. Don't re-introduce them.

- **`getUpdates` chat-id confusion.** Telegram bots can't message themselves — if you set `TELEGRAM_OWNER_CHAT_ID` to the bot's own id, every message gets allowlist-rejected silently. Use @userinfobot on Telegram to find YOUR numeric id.
- **SQLite cross-thread error.** `sqlite3.Connection` is bound to the thread that created it. If you open a connection in `asyncio.to_thread`, you must close it in the same `to_thread` call. (Was a bug in `tempo/bot/app.py::_post_init`; fixed in the simplify pass.)
- **`claude-agent-sdk` 0.2.x message shapes.** `AssistantMessage` has NO `role` attribute; `TextBlock` has NO `.type` attribute (just `.text`). Detect by class name (`type(msg).__name__ == "AssistantMessage"`). (Fixed in `tempo/bot/agent.py`.)
- **Telegram rejects empty messages** with `BadRequest("Message text is empty")`. Always guard reply text — Claude Code turns that end on a tool call produce empty assistant text. (`tempo/bot/handlers.py::_run_agent_turn` substitutes `"(agent finished without a reply)"`.)
- **faster-whisper `segments` is a generator.** Iterate it eagerly (`list(segments)`) or transcription silently doesn't run. faster-whisper also has NO Metal/GPU on Mac — `large-v3-turbo` is too slow on CPU. Default `small.en` int8 = ~8-12s for 60s audio.
- **Telegram bot 409 Conflict** on startup if a webhook is set or a second poller is running. Our `post_init` calls `delete_webhook` defensively.
- **PEP 758 unparenthesised except clauses parse in 3.14** but look like Python-2 syntax to every reviewer. Always parenthesise: `except (TypeError, ValueError):`, not `except TypeError, ValueError:`.
- **Strava stream lazy-fetch + selection bias.** `tempo strava sync` doesn't pull streams by default. Of 1,814 HR-recorded activities only ~2 had streams pulled (the rest of the 733 streamed ones happened to be non-HR). Use `tempo strava streams --prefer-with-hr --limit 200` to drain the HR-having queue rather than walking ascending.
- **Voice file cleanup on download failure.** Wrap the entire post-target-path flow in `voice_handler` in a single try/finally — partial downloads or transcription crashes would otherwise leak the .ogg under `VOICE_RETENTION_DAYS=0` (the privacy default).
- **No `ANTHROPIC_API_KEY`.** The bot uses the user's Claude subscription via Claude Code login (`claude-agent-sdk` spawns `claude` CLI as subprocess). Don't add API-key fallback paths — it's a feature, not a missing piece.
- **`get_settings()` is not cached.** Wizard re-reads settings after writing `.env` and relies on fresh values. Don't add `@lru_cache`.

## Project Skills

User skills live in `.claude/skills/<name>/SKILL.md`. Eight skills back the coach persona (see CLAUDE.md for the index):

- `log-run-journal`, `log-strength-session`, `log-heat-session`, `log-weight`, `log-food`, `update-race-result`, `generate-report`, `coach-readout`

When you add a new skill, follow the existing shape: frontmatter (`name`, `description`) + body with concrete trigger phrases, exact CLI commands or file operations, edge cases, and what to say back to Ross.

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools for non-trivial engineering tasks, start work through a GSD command so planning artifacts and execution context stay in sync.

Entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work
- `/gsd-plan-phase` to break down a new phase before executing
- `/gsd-new-milestone` to start a new milestone cycle

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.

**Wave-based execution pattern (established across Phases 8-16):**
1. Pre-write `CONTEXT.md` for the phase from research + roadmap success criteria (avoids redundant discuss-phase when context is clear from conversation).
2. Spawn `gsd-planner` agent → produces wave-grouped `PLAN.md` files.
3. Spawn `gsd-executor` agents per plan in **parallel worktrees** when files don't overlap; sequential when they do.
4. Merge each worktree back to main (`git stash` planner artifacts first if they collide).
5. Run `gsd-verifier` for goal-backward check → write `VERIFICATION.md`.
6. Commit + push.

Worktree gotcha: executor worktrees may fork from a commit predating recent merges. The executor agent prompt tells them to `git merge origin/main` themselves if needed — give them that license explicitly.

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
