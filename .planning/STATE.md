---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: first-run-setup-wizard
status: shipped
stopped_at: v1.3 (Phase 14) complete; live `tempo setup` smoke test against real Strava/Garmin/Telegram pending.
last_updated: "2026-05-28T10:30:00.000Z"
last_activity: 2026-05-28 — Phase 14 (First-Run Setup Wizard) shipped end-to-end. New tempo/setup/ package (env_io.py atomic 0600 .env writes mirroring tokens.py + state.py read-only InstallState detection + prompts.py hidden-input wrappers + wizard.py 10-step orchestrator). New `tempo setup` typer command with --only/--skip-*/--non-interactive flags. docs/SETUP.md (all 10 steps with manual fallback + recover paths). README.md "Getting Started" rewritten to lead with `tempo setup`. 593 tests green (+63), ruff clean. Verifier PASS 5/5.
progress:
  total_phases: 1
  completed_phases: 1
  completed: [14]
  total_plans: 3
  completed_plans: 3
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** v1.3 complete (first-run setup wizard). v1.1 + v1.2 already shipped. Pi port deferred.

## Current Position

Phase: Phase 14 (First-Run Setup Wizard, v1.3) — COMPLETE
Plans: 14-01 + 14-02 + 14-03 — all COMPLETE
Status: v1.3 SHIPPED. The new `tempo setup` command walks a new user from a fresh clone (no DB, no `.env`, no tokens, no launchd plists) to a working `tempo run-daily` — 10 locked steps in order (welcome → db → content → strava → garmin → telegram → scheduler → bot-scheduler → smoke → finish), all credentialed steps delegating to the existing `tempo` CLI surface (no duplicated OAuth handshake, no duplicated plist render, no duplicated MFA prompt). Plan 14-01 added the atomic `.env` I/O (`tempo/setup/env_io.py`, mirrors `tempo/connectors/tokens.py`: mkstemp → fchmod 0o600 → fsync → `os.replace` → fsync parent → final chmod) and the read-only `InstallState` detector (`tempo/setup/state.py`). Plan 14-02 added the orchestrator (`tempo/setup/wizard.py`, ~670 LOC) plus the thin prompt wrappers (`tempo/setup/prompts.py`) and registered the `tempo setup` typer command in `tempo/cli.py`. Plan 14-03 added `docs/SETUP.md` (every step + manual fallback + recover path) and rewrote the README "Getting Started" section to lead with `tempo setup`. 593 tests green (+63 from Phase 13), ruff clean, no debt markers. All 5 SETUP-* requirements satisfied. Verifier PASS 5/5.
Last activity: 2026-05-28 — Phase 14 verified. v1.3 milestone closed. Pending: a live `tempo setup` smoke test against real Strava + Garmin + Telegram (deliberately scoped out of this phase; documented as the only remaining check).

## What's Done (Phase 14: First-Run Setup Wizard — v1.3 milestone)

- `tempo/setup/env_io.py` — `read_env(path) -> dict[str, str]` (lenient: missing → `{}`, blank/comment lines skipped, duplicate keys last-wins, surrounding double-quotes stripped) and `atomic_write_env(path, updates, delete_keys)`. The write template mirrors `tempo/connectors/tokens.py` exactly: `tempfile.mkstemp` in destination dir → `os.fchmod(fd, 0o600)` → write + `flush` + `fsync` → `os.replace(tmp, path)` → best-effort `fsync` on the parent dir → final `chmod 0o600`. Crash mid-write leaves either the prior complete `.env` or the new one, never a torn file. Comments + untouched key ordering preserved byte-identically; values with spaces / `$` / `#` / tab are double-quoted on write. Module never logs / echoes a value. (SETUP-03)

- `tempo/setup/state.py` — `@dataclass(frozen=True, slots=True) class InstallState` with 7 bool fields (`db_initialised`, `content_dir_set`, `strava_configured`, `garmin_configured`, `telegram_configured`, `daily_scheduler_installed`, `bot_scheduler_installed`); `detect_install_state(settings)` is pure read-only over filesystem + a single read-only SQLite connection (closed in `finally`) for the schema-version check. No network, no `launchctl`. Plist presence at `~/Library/LaunchAgents/com.tempo.{daily,telegram-bot}.plist` is the contract. (SETUP-02)

- `tempo/setup/prompts.py` — thin `typer.prompt` / `typer.confirm` / `typer.secho` wrappers with `[set]` / `[done]` / `[fresh]` / `[skip]` coloured indicators. `prompt_secret(label)` always passes `hide_input=True, confirmation_prompt=False`. Single mockable surface for tests. (SETUP-03)

- `tempo/setup/wizard.py` (~670 LOC) — the 10-step orchestrator. `STEP_IDS = ("welcome", "db", "content", "strava", "garmin", "telegram", "scheduler", "bot-scheduler", "smoke", "finish")`. One function per step; each starts with a state check and returns `[done]`+skipped when the corresponding `InstallState` bool is True. `run_wizard(settings, *, only, skip_garmin, skip_telegram, skip_scheduler, skip_bot_scheduler, skip_smoke, non_interactive)` iterates the dispatch list, re-detects state after every step (cheap), and returns the exit code (0 = ok / 1 = a non-skipped step failed terminally / 2 = `typer.Abort` from Ctrl-C or `--non-interactive` hitting a required prompt). `--skip-telegram` implies `--skip-bot-scheduler`. The bot-scheduler step is only offered if Telegram is configured (either already-state or completed-this-run). Credentials are always written to `.env` BEFORE the downstream delegated call, so a partial failure leaves creds in place for retry. (SETUP-01, SETUP-02, SETUP-05)

- **Delegation (SETUP-04, LOCKED)** — every credentialed step calls into the existing helper directly: DB → `tempo.cli._init`; Strava → `tempo.connectors.factory.build_strava_connector` + `connector.authorization_url` + `connector.exchange_code` (same triple `tempo strava auth` makes); Garmin → `tempo.connectors.factory.garmin_login(settings, prompt_mfa=…)`; daily scheduler → `tempo.scheduler.install_plist(...)`; bot scheduler → `tempo.scheduler.install_telegram_bot_plist(...)`; smoke → `tempo.sync.pipeline.run_full_sync(conn, settings)`. Zero subprocess calls; zero duplicated handshake / plist render / MFA prompt code.

- `tempo/cli.py` — new `@app.command("setup")` (L943-1008): thin wrapper that parses `--only` / `--skip-*` / `--non-interactive`, validates `--only` against `STEP_IDS - {welcome, finish}` (unknown → `typer.Exit(2)`), calls `run_wizard(settings, …)`, raises `typer.Exit(exit_code)` on non-zero return.

- `docs/SETUP.md` (~19 KB) — end-to-end walkthrough. Two paths (one-command + manual). All 10 steps documented in the locked order with *What it does* / *Wizard prompts* / *Files written* / *Manual equivalent* / *Skip* / *Recover* subsections. Closes with operational notes (re-runs, changing creds, manual uninstall).

- `README.md` — "Getting Started" rewritten to lead with the 4-line `git clone / cd / uv sync / uv run tempo setup` path. Links to `docs/SETUP.md` for the full walkthrough. Documents `--only=<step>` and `--skip-*` for power users. Manual fallback (raw `tempo init` / `tempo strava auth` / etc.) retained below.

- `tests/test_setup_env_io.py` — 30 tests covering: missing file → `{}`, basic key parsing, double-quote stripping, single-quote *non*-stripping, comment + blank skipping, last-value-wins, value-quoting on write, atomicity under `os.replace` failure, atomicity under fresh-file failure (no temp turd left), `fsync` is called, parent dir creation, byte-identical preservation of unchanged keys, comment preservation, line-ordering preservation, blank-line separator before appended keys, multi-key append, no leading blank in fresh file, delete-key, round-trip, 0600 final perms.

- `tests/test_setup_state.py` — 17 tests parametrised across DB-present / strava-token-present / both-plist-present / etc. combinations; explicit assertions that `InstallState` is frozen + has `__slots__`; corrupt-DB returns `False` rather than raising.

- `tests/test_setup_wizard.py` — 14 tests: STEP_IDS ordering, full-fresh-install dispatch counts (every delegated mock fires exactly once), `--only=strava` runs only Strava (others zero-count), `--skip-garmin`, `--skip-telegram` implies `--skip-bot-scheduler`, idempotent skip when state already says "done", `.env` write ordering during Strava (creds before OAuth call), Strava OAuth failure → `StepResult(failed)` + exit 1, smoke per-source reporting, smoke terminal-Strava-failure → exit 1, Ctrl-C / `typer.Abort` → exit 2, `--non-interactive` aborts on a required prompt, bot-scheduler only offered if Telegram completed this run, CLI runner: unknown `--only=<step>` exits 2, `tempo setup --help` lists every flag.

- **Test totals:** 593 tests green (was 530 after Phase 13; +63 from this phase). `ruff check tempo/ tests/` clean. Zero `TODO` / `FIXME` / `XXX` / `TBD` / `HACK` / `placeholder` markers in any `tempo/setup/*.py` or `tests/test_setup_*.py`. The one slow Whisper test stays deselected per project convention.

- **Verifier outcome:** PASS 5/5 success criteria. See `.planning/phases/14-setup-wizard/14-VERIFICATION.md`. Only out-of-scope item: a live `tempo setup` run against real Strava / Garmin / Telegram — deliberately deferred (the wizard is verified against mocked delegated symbols; a live smoke is a follow-up session task).

### Conventions established this phase

- **Atomic `.env` writes go through a single helper** (`tempo/setup/env_io.atomic_write_env`) modelled on `tempo/connectors/tokens.py`. No raw `open(.env, "w")` anywhere in the wizard. Comments + untouched key ordering preserved verbatim. Mode 0o600 enforced regardless of `umask`.

- **Setup is orchestration only.** The wizard owns prompts, dispatch, state detection, `.env` I/O, and the smoke test reporting. Every credentialed step delegates IN-PROCESS to the existing helper (no subprocess). Future credentialed steps follow the same shape.

- **State detection is pure read-only** and lives in one place (`tempo/setup/state.py::detect_install_state`). Steps consult it; they never duplicate detection logic. Plist *presence* is the contract — Tempo never runs `launchctl`.

- **Re-run safety**: a re-run picks up where the previous run left off. `[done]` short-circuits skip; `--only=<step>` re-arms a single step (with a `keep / change / fresh` confirm). The wizard never silently overwrites a non-empty `.env` key without an interactive confirm.

## What's Done (Phase 13: Strength & Conditioning Tracker — v1.2 milestone)

- `tempo/analysis/strength.py` — frozen+slots `StrengthSet` / `StrengthExercise` / `StrengthSession` / `StrengthContext` / `StrengthRollup` dataclasses + `parse_strength(path)` + `strength_rollup(sessions, today)`. Lenient parser modelled directly on `tempo/analysis/heat.py`: missing file → `present=False`, malformed lines skipped, unknown keys ignored, never raises. Handles weighted sets (`55x8`), bare-rep sets (`15`), timed holds (`1:00`), supersets (`[A]`/`[B]`), equipment / notes / rest metadata. (SC-01, SC-02)

- `tempo/config.py` — `Settings.strength_path` returns `<content_root>/strength.md` (mirrors `heat_path`). (SC-03)

- `tempo/analysis/recovery.py` + `tempo/analysis/runner.py` + `tempo/analysis/report.py` — recovery report gains a `## Strength & conditioning` section with the same 3-state degradation as the heat section (absent → omit / lapsed → one-line nudge / active → rollup with session count, total tonnage, last-session age). (SC-04, SC-05)

- `strength.md.example` + `docs/STRENGTH.md` — committed format reference + operational doc.

- `tests/test_strength.py` + recovery-report integration tests — 32 new tests; 530 total tests green.

## What's Done (Phase 12: lifecycle / hardening / privacy — v1.1 closing milestone)

- Plan 12-01: `tempo bot install-scheduler` + launchd `com.tempo.telegram-bot.plist` with `KeepAlive=true` so a crash / sleep / network blip auto-restarts the bot. `VOICE_RETENTION_DAYS` startup sweep + per-handler immediate-delete + `tempo bot purge-voice` manual hatch. Agent cwd + data_dir logged at startup.

- Plan 12-02: top-level `telegram_error_handler` (logs traceback, sends a fixed "something went wrong" reply, never re-raises). `docs/PRIVACY.md` is the single-source user-facing privacy contract. README + `docs/TELEGRAM_BOT.md` updated with launchd lifecycle, voice retention, and error-handler sections.

- 498 tests green; v1.1 closed.

## What's Done (Phase 11: Claude Code agent via SDK)

- `tempo/bot/agent.py` — wraps `claude-agent-sdk` (uses the user's Claude Code subscription, no `ANTHROPIC_API_KEY`). Per-chat `--resume` over a 4hr rolling window. Final assistant text → HTML reply, split at 4096 chars. Detects `AssistantMessage` by class name (the SDK 0.2.x message shapes have no `.role` / `.type` attrs). Empty assistant text → `"(agent finished without a reply)"` so Telegram doesn't reject an empty message.

- `tempo/bot/sessions.py` — per-chat session-id store with a 4-hour idle window; `/new` resets.

## What's Done (Phase 10: Telegram bot worker)

- `tempo/bot/app.py` — Telegram Application builder + handler registration + Whisper warmup + cwd log + voice sweep. Defensive `delete_webhook` in `post_init` to avoid 409 Conflicts.

- `tempo/bot/handlers.py` — `start`, `voice`, `text`, `/new` handlers. Owner-chat-id allowlist; the bot ignores everything else silently.

- `tempo/bot/transcribe.py` — `faster-whisper` singleton on CPU (no Metal/GPU on Mac). `small.en` int8 default. Eager `list(segments)` because the iterator is lazy.

## What's Done (Phase 9: Telegram + Whisper foundations)

- `pyproject.toml` deps: `python-telegram-bot`, `faster-whisper`, `claude-agent-sdk`. `WHISPER_MODEL_NAME` / `WHISPER_COMPUTE_TYPE` / `WHISPER_DEVICE` / `VOICE_RETENTION_DAYS` settings (with `validation_alias` for bare-name env keys).

- Voice cache under `<content_dir>/voice/`, gitignored. faster-whisper warmup on startup.

## What's Done (Phase 8: Modular Trackers + Heat Adaptation)

- `races.md` gains a `result:` field + auto-link from race → matching Strava activity (`tempo/analysis/race_link.py`).

- New `heat.md` tracker — appendable session log; `tempo/analysis/heat.py` lenient parser + 3-state rollup surfaced in recovery report.

- `plan.md` retired (training plan moved to whichever format the owner prefers; no more parser).

- `tempo/analysis/context.py` deleted; per-tracker modules now own their own parse + render shape.

## What's Done (Phases 1-7: v1.0 — Strava + Garmin → SQLite → analyses → daily launchd job)

- See `.planning/phases/01-foundation/` through `.planning/phases/07-recovery-correlation/` for the full per-phase shipped list. Summary: Strava OAuth + paged resumable backfill → raw store; Garmin (isolated failure domain, no-retry-on-429) → raw store; pure-stdlib transforms → structured layer + `daily_summary` view; `tempo/analysis/{load,fitness,race,recovery,correlation,noteworthy}.py` produce dated markdown reports; `tempo run-daily` launchd job runs the lot at 05:30 local time. 235 → 288 → 339 → 497 tests across phases.

## Performance Metrics

**Velocity:**

- Total plans completed (this milestone): 3
- Average duration: ~ unknown (parallel waves; not tracked per-plan in this phase)
- Total execution time (this milestone): single-day session

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 14. First-Run Setup Wizard (v1.3) | 3 | — | — |
| 13. Strength & Conditioning Tracker (v1.2) | 3 | — | — |
| 12. Lifecycle / hardening / privacy (v1.1 closing) | 2 | — | — |

**Recent Trend:**

- Last 3 plans: 14-01 (env_io + state), 14-02 (wizard + prompts + CLI cmd), 14-03 (docs + README)
- Trend: shipped same-day; 593 tests green; ruff clean.

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Strava-first milestone: prove pull → store → analyse end-to-end on the clean source before the fragile Garmin connector
- Two-layer raw → structured storage: connectors write only to `raw_response`; transforms read raw and write structured, enabling `tempo rederive` with no network
- Date spine in Phase 3 (not later): CTL/ATL EWMAs and ACWR windows are silently wrong without a zero-filled spine
- Journaling early (Phase 5): correlation analysis is data-hungry, so paired subjective history must start accumulating before Garmin
- **(Phase 14, 2026-05-28)** First-run setup is orchestration-only — every credentialed step delegates in-process to the existing helper. No subprocess; no duplicated OAuth handshake, MFA prompt, or plist render. `.env` writes go through a single atomic helper modelled on `tokens.py`.

### Roadmap Evolution

- Phase 8 added: Modular Trackers + Heat Adaptation — split plan.md into focused tracker files (`races.md` w/ result + auto-link, new `heat.md`); retire `plan.md`. (2026-05-27)
- Phase 14 added + shipped: First-Run Setup Wizard (v1.3) — `tempo setup` reduces clone-to-working-daily-sync from a multi-step README walkthrough to a single idempotent command. (2026-05-28)

### Pending Todos

- **Live `tempo setup` smoke** against real Strava + Garmin + Telegram (the wizard is verified against mocked delegated symbols; this is a follow-up session task).

### Blockers/Concerns

- [Phase 2 — RESOLVED] Strava API Agreement conflict documented as accepted (README + REQUIREMENTS Known Accepted Conflicts); private self-data, never shared.
- [Phase 2 — pending user] Live Strava pull needs the user's own API app: create at https://www.strava.com/settings/api, set TEMPO_STRAVA_CLIENT_ID/SECRET in .env, run `tempo strava auth`, then `tempo strava backfill`. All machinery (incl. Phase-4 analysis) proven against mocks/seeded data; this is the only remaining step before live reports.
- [Phase 4 — RESOLVED] rTSS uses `avg_pace_s_km` directly (no grade-adjusted/normalised pace in v1; NGP/GAP is a documented future refinement). hrTSS fallback uses HR-reserve anchored on threshold HR. Threshold pace is a configurable pydantic setting. Insufficient days are flagged, not invented.
- [Phase 6] `garminconnect` is the single fragile dependency (garth deprecated 2026-03-27); pin version, monitor upstream, budget for a version bump
- [Phase 7] HRV baseline cold-start and multi-signal recovery weighting may need a brief planning-time research pass; first weeks of Garmin data will be low-quality and must be flagged honestly

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Setup | `tempo doctor` (diagnose-only health check; separable from setup) | Deferred to follow-up phase | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | `tempo setup --uninstall` reverse path (3-line manual `rm` documented in `docs/SETUP.md`) | Deferred; manual is fine | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Pi / Linux systemd-equivalent of the launchd steps | Deferred until Pi-port milestone | 2026-05-28 (Phase 14 CONTEXT) |
| Setup | Auto-detect optimal Whisper model / threshold pace / max HR / resting HR | Deferred (cross-cuts Phase 4) | 2026-05-28 (Phase 14 CONTEXT) |

## Session Continuity

Last session: 2026-05-28T10:30:00.000Z
Stopped at: v1.3 SHIPPED. Phase 14 (First-Run Setup Wizard) verified PASS 5/5. New
`tempo setup` command walks 10 locked steps in order (welcome → db → content →
strava → garmin → telegram → scheduler → bot-scheduler → smoke → finish); every
credentialed step delegates in-process to the existing `tempo` helper (no
subprocess, no duplicated OAuth handshake, no duplicated MFA prompt, no
duplicated plist render). Atomic `.env` writes at 0600 perms mirror
`tempo/connectors/tokens.py`. `docs/SETUP.md` covers all 10 steps with manual
fallback + recover paths; README "Getting Started" leads with `tempo setup`.
593 tests green (+63), ruff clean. All 5 SETUP-* requirements satisfied.
Pending: live `tempo setup` smoke against real Strava / Garmin / Telegram
(deferred follow-up session).

Previous session: 2026-05-28T03:00:00.000Z. Stopped at: v1.2 SHIPPED. Phase 13
(Strength & Conditioning Tracker) verified PASS 5/5. `tempo/analysis/strength.py`
lenient parser (WxR + bare-reps + M:SS + supersets + equipment) +
`StrengthRollup` + `Settings.strength_path`; recovery report gains 3-state
`## Strength & conditioning` section (absent / lapsed / active with tonnage);
`strength.md.example` + `docs/STRENGTH.md` committed. 530 tests green (+32),
ruff clean.

Resume file: None
