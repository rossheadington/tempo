---
phase: 12-lifecycle-hardening-privacy
plan: 12-01
subsystem: bot-lifecycle
tags: [launchd, telegram-bot, privacy, voice-retention, cli]
requires: [tempo.bot.app, tempo.bot.handlers, tempo.scheduler, tempo.config]
provides:
  - launchd com.tempo.telegram-bot LaunchAgent template (KeepAlive=true)
  - tempo bot install-scheduler CLI command
  - tempo bot purge-voice CLI command
  - VOICE_RETENTION_DAYS setting (privacy-safe default 0)
  - voice cache startup sweep + per-handler immediate-delete
  - agent cwd + data_dir startup log lines
affects:
  - tempo/cli.py (new bot subcommands)
  - tempo/scheduler.py (telegram-bot plist render + install)
  - tempo/config.py (voice_retention_days)
  - tempo/bot/handlers.py (cleanup helper + finally-block in voice_handler)
  - tempo/bot/app.py (sweep + cwd log in _post_init)
  - .gitignore (logs/)
key-files:
  created:
    - launchd/com.tempo.telegram-bot.plist
    - .planning/phases/12-lifecycle-hardening-privacy/12-01-SUMMARY.md
  modified:
    - tempo/cli.py
    - tempo/scheduler.py
    - tempo/config.py
    - tempo/bot/handlers.py
    - tempo/bot/app.py
    - .gitignore
    - tests/test_config.py
    - tests/test_scheduler.py
    - tests/test_phase7_cli.py
    - tests/test_bot_handlers.py
    - tests/test_bot_app.py
decisions:
  - "Privacy-safe default: VOICE_RETENTION_DAYS=0 means audio is deleted immediately after transcription. retention>0 keeps files for the operator's debug convenience, bounded by the startup sweep."
  - "launchd KeepAlive=true + ThrottleInterval=10 + RunAtLoad=true: bot survives crashes + wake-from-sleep without manual restart, but won't pin CPU spinning on fast crashes."
  - "Tempo NEVER auto-runs launchctl. install-scheduler prints the manual load/start/unload commands the user runs themselves."
  - "logs/ at <project>/logs/ (gitignored) rather than data_dir/logs/ -- the bot plist points there because the user inspects logs alongside the code."
  - "plutil -lint runs BEFORE writing the plist into LaunchAgents -- a broken {{PLACEHOLDER}} substitution can never reach launchd."
  - "cwd + data_dir logged at startup so the launchd WorkingDirectory and the resolved data dir are trivially visible in the startup log (debugging 'claude wrote files to /')."
metrics:
  duration_seconds: 598
  duration_human: "9m 58s"
  tasks_completed: 4
  files_created: 1
  files_modified: 11
  tests_added: 18
  tests_passing: 492
  completed: 2026-05-28
---

# Phase 12 Plan 12-01: Wave 1 Lifecycle Hardening + Privacy Summary

Long-running Telegram bot under launchd KeepAlive, immediate voice-file
cleanup on transcription, startup sweep for any retained audio, manual purge
hatch, and the cwd/data-dir startup log lines that make the resulting
launchd-managed process trivially debuggable.

## What was built

### 1. `VOICE_RETENTION_DAYS` setting + `logs/` gitignore

- `Settings.voice_retention_days: int = 0` (via bare `VOICE_RETENTION_DAYS`
  env var, matching the `TELEGRAM_*`/`WHISPER_*` convention).
- `.gitignore` adds `logs/` so the telegram-bot LaunchAgent's
  `StandardOutPath` / `StandardErrorPath` (under `<project>/logs/`) cannot
  accidentally land in the public repo.

### 2. Committed `launchd/com.tempo.telegram-bot.plist` template + `tempo bot install-scheduler`

- `launchd/com.tempo.telegram-bot.plist` ships as a committed template (lints
  clean under `plutil -lint`). Three placeholders the install command
  substitutes: `{{UV_BIN}}`, `{{PROJECT_ROOT}}`, `{{TZ}}`.
- `KeepAlive=true` + `ThrottleInterval=10` + `RunAtLoad=true`. launchd
  restarts the bot on crash + on wake-from-sleep; throttle caps the restart
  loop so a fast-crashing bot does not pin a CPU.
- `OMP_NUM_THREADS=4` caps faster-whisper / CTranslate2 thread
  oversubscription on M-series Macs.
- `StandardOutPath` / `StandardErrorPath` -> `<project>/logs/telegram-bot.{stdout,stderr}.log`.
- `tempo.scheduler` gains `render_telegram_bot_plist()` +
  `install_telegram_bot_plist()`, mirroring the existing `install_plist()`
  for the daily run. Auto-resolves `uv` via `shutil.which`, reads
  `/etc/localtime` for the IANA TZ, always creates `<project>/logs/`, and
  runs `plutil -lint` against the rendered output before writing -- a
  broken substitution can never reach `~/Library/LaunchAgents/`.
- `tempo bot install-scheduler` CLI command (mirrors `tempo install-scheduler`):
  writes the rendered plist under `<project>/launchd/` by default;
  `--to-launch-agents` writes into `~/Library/LaunchAgents/`. NEVER runs
  `launchctl` itself; prints the manual `launchctl load -w` +
  `launchctl start` commands the user runs themselves.

### 3. Voice-retention policy: per-handler cleanup + startup sweep + cwd log

- `_cleanup_voice_file(path, retention_days)`: `retention=0` -> `unlink(missing_ok=True)`
  immediately; `retention>0` -> leave for the sweep. Idempotent; OSError is
  logged + swallowed (best-effort).
- `voice_handler` wraps the agent turn in `try/finally` so cleanup happens
  even when `run_turn` raises an unhandled exception -- the Phase 12 privacy
  invariant (no audio leak on agent failure). Empty-transcript short-circuit
  also honours the retention policy.
- `_sweep_voice_cache(dir, retention_days)`: scans `voice_cache_dir` at
  startup, deletes files older than `retention_days * 86400` seconds.
  No-op for retention<=0 or missing dir. Bounded: iterates only the cache
  dir (no recursion); skips subdirectories.
- `_post_init` logs `agent cwd = <abs>` + `data_dir = ...` at every
  startup -- the launchd `WorkingDirectory` and the Tempo data dir become
  trivially visible in `<project>/logs/telegram-bot.stdout.log`.

### 4. `tempo bot purge-voice [--yes]` — manual privacy hatch

- Lists count + total KB in `voice_cache_dir`, asks for confirmation
  (interactive) unless `--yes`/`-y` is passed, then unlinks every file.
- Directory itself is preserved (next memo recreates lazily).
- Handles `dir does not exist` and `dir empty` cases gracefully (exit 0,
  clear message). Per-file failures collected + surfaced + exit 1.
- Use case: even with `VOICE_RETENTION_DAYS=7`, wipe the cache after a
  sensitive conversation without waiting 7 days for the startup sweep.

## Commits

| # | Type | Hash | Subject |
|---|------|------|---------|
| 1 | feat | `42d9336` | feat(12-01): add VOICE_RETENTION_DAYS setting + ignore logs/ |
| 2 | feat | `00f9c05` | feat(12-01): add Telegram-bot launchd plist + `tempo bot install-scheduler` |
| 3 | feat | `16703ae` | feat(12-01): voice-retention policy + startup sweep + cwd log |
| 4 | feat | `7fa242d` | feat(12-01): add `tempo bot purge-voice [--yes]` privacy hatch |
| 5 | style | `6ad4b3c` | style(12-01): ruff format Phase 12 test additions |

## Verification

- `uv run pytest tests/ --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` -> **492 passed, 1 deselected**
- `uv run ruff check tempo/ tests/` -> **All checks passed**
- `uv run ruff format --check tempo/ tests/` -> **clean**
- `uv run tempo --help` -> renders, no regressions
- `uv run tempo bot --help` -> shows `install-scheduler`, `purge-voice`, `run` subcommands
- `plutil -lint launchd/com.tempo.telegram-bot.plist` -> OK

## New tests (18)

`tests/test_config.py`:
- `test_voice_retention_days_defaults_to_zero`
- `test_voice_retention_days_env_override`

`tests/test_scheduler.py`:
- `test_telegram_bot_template_committed_lints`
- `test_render_telegram_bot_plist_substitutes_placeholders`
- `test_install_telegram_bot_plist_writes_template_and_creates_logs`
- `test_install_telegram_bot_plist_lints_when_plutil_available`

`tests/test_phase7_cli.py`:
- `test_bot_install_scheduler_writes_template_and_creates_logs`
- `test_bot_install_scheduler_does_not_touch_launch_agents`
- `test_bot_purge_voice_with_yes_deletes_all_files`
- `test_bot_purge_voice_no_files`
- `test_bot_purge_voice_no_dir`
- `test_bot_purge_voice_without_yes_prompts_and_aborts_on_no`
- `test_bot_purge_voice_without_yes_proceeds_on_yes`

`tests/test_bot_handlers.py`:
- `test_cleanup_voice_file_deletes_when_retention_zero`
- `test_cleanup_voice_file_keeps_when_retention_nonzero`
- `test_cleanup_voice_file_idempotent_on_missing`
- `test_voice_handler_keeps_file_when_retention_is_positive`
- `test_voice_handler_cleanup_runs_even_when_agent_raises`
- `test_voice_handler_cleans_up_empty_transcript_path`

`tests/test_bot_app.py`:
- `test_sweep_voice_cache_noop_when_retention_zero`
- `test_sweep_voice_cache_noop_when_dir_missing`
- `test_sweep_voice_cache_deletes_old_keeps_recent`
- `test_sweep_voice_cache_handles_subdirectories`
- `test_post_init_logs_agent_cwd_and_data_dir`
- `test_post_init_no_sweep_log_when_retention_zero`

Plus one updated test (`test_voice_handler_happy_path_writes_file_transcribes_and_replies`)
to assert the Phase 12 retention=0 default: the downloaded .ogg is gone after
the handler returns.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] XML comment double-hyphens broke `plistlib` parsing**
- **Found during:** Task 2 (committed-template test)
- **Issue:** The template's `<!-- ... -->` comment block used `--` as a
  visual separator (e.g. `KeepAlive=true means launchd restarts the bot --
  you do not need to ...`). `plutil` accepted it, but Python's `expat` parser
  (used by `plistlib.loads`) is strict per XML spec: `--` is not allowed
  inside a comment body. The committed-template parse test failed.
- **Fix:** Replaced the body-internal `--` separators with `:`. The
  rendered template still lints clean and parses under `plistlib`.
- **Files modified:** `launchd/com.tempo.telegram-bot.plist`
- **Commit:** `00f9c05`

**2. [Rule 1 - Bug] Stray `no_reset.assert_not_called()` line in new test**
- **Found during:** Task 3 test run
- **Issue:** A leftover assertion from a copy-paste pattern referenced an
  undefined `no_reset` mock in `test_voice_handler_cleans_up_empty_transcript_path`.
- **Fix:** Removed the stray line.
- **Files modified:** `tests/test_bot_handlers.py`
- **Commit:** included in `16703ae`

### Plan-Wide Adjustments

**Worktree fork drift:** The worktree branch had forked before Phases 9-11
landed, so `tempo/bot/` and the Phase 9-11 plan directories were missing on
spawn. Resolved by `git merge main --no-edit` per execution-context guidance
("If your worktree forked before recent merges, run `git merge origin/main`").
Verified `tempo/bot/` and Phase 11 SUMMARY existed before starting Task 1.
The execution context's plan file path (`.planning/phases/12-lifecycle-hardening-privacy/12-01-PLAN.md`)
did not yet exist anywhere in git history; the inline specification in the
execution context served as the source of truth for what to build.

## Known Stubs

None. All new functionality is wired end-to-end.

## Threat Flags

None. This plan reduces surface area (immediate voice-file deletion) rather
than adding it. No new network endpoints, no new auth paths, no schema
changes.

## Self-Check: PASSED

Verified via:
- `git log --oneline --all | grep -E "(42d9336|00f9c05|16703ae|7fa242d|6ad4b3c)"` -> all 5 commits present.
- `ls launchd/com.tempo.telegram-bot.plist` -> exists.
- `ls .planning/phases/12-lifecycle-hardening-privacy/12-01-SUMMARY.md` -> exists.
- `uv run pytest tests/ --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty` -> 492 passed.
