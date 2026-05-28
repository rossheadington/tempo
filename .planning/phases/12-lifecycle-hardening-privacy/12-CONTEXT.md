# Phase 12: Lifecycle + Hardening + Privacy — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline orchestrator (final v1.1 phase — derived from roadmap success criteria + user-endorsed architecture)

<domain>
## Phase Boundary

**What this phase delivers:**

- **launchd LaunchAgent** plist (`launchd/com.runos.telegram-bot.plist`) with `KeepAlive=true`, `ThrottleInterval=10`, log paths under `logs/`, `WorkingDirectory` set to the project root, `EnvironmentVariables` setting `OMP_NUM_THREADS=4` and `TZ` for predictable timestamps.
- New `runos bot install-scheduler` CLI subcommand that generates a user-specific plist from a committed template (mirrors the existing `runos install-scheduler` for the daily-analysis launchd job from Phase 7).
- **Top-level error boundary**: A PTB error handler (`Application.add_error_handler`) that catches anything that propagates from any handler (voice / text / /new), logs the full traceback, and replies to the chat with "Sorry — something went wrong on my end. Check the logs." (configurable text). Never crashes the worker.
- **Voice-file retention policy**: After successful transcription, delete the cached `.ogg` from `<content_dir>/voice/` immediately by default. Configurable retention window via `voice_retention_days: int = 0` (0 = delete on success; positive = keep N days then sweep via a startup cleanup pass).
- **Working-directory scoping confirmation**: Confirm + document that the Claude Agent SDK is invoked with `cwd=Path.cwd()` (set to the RunOS project root by launchd's `WorkingDirectory`). Add a startup log line stating the cwd so the user can verify.
- **Privacy contract doc** (`docs/PRIVACY.md`): One-page explicit document covering what data moves where (voice stays local; transcripts + agent context flow to user's Claude subscription; Telegram carries messages). Linked from README.
- **Logs hygiene**: ensure `logs/` is gitignored; structured JSON-style log lines for the per-turn observability already wired in Phase 11; rotate via launchd's stdout/stderr append behaviour (no separate rotation tool — for personal use, manual log truncation when needed is fine).
- **Cleanup CLI helper**: `runos bot purge-voice` to manually sweep the voice cache. Useful if the user wants to wipe everything before, say, lending the laptop.

**What this phase does NOT deliver (out of scope):**

- Raspberry Pi port (v1.2 milestone — explicit defer).
- Auto log rotation via logrotate / similar (manual is fine for personal use; revisit if needed).
- Health-check / heartbeat endpoint (single user, single chat — Telegram replies are the heartbeat).
- Web UI / dashboard for cost / token usage (logs are sufficient).
- Encrypted-at-rest voice cache (the cache is short-lived by default; encrypt the disk if you need that).
- Migration / removal of the journal store (out of scope).
- Multi-user support / per-user retention policies.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### launchd plist
- Committed template: `launchd/com.runos.telegram-bot.plist`. Mirrors the existing `launchd/com.runos.daily.plist` from Phase 7. Secret-free (uses absolute path placeholders the install-scheduler command substitutes).
- Key entries:
  - `Label`: `com.runos.telegram-bot`
  - `ProgramArguments`: absolute path to `uv` + `run` + `runos` + `bot` + `run`
  - `WorkingDirectory`: absolute path to the RunOS project root
  - `EnvironmentVariables`:
    - `PATH`: includes Homebrew (`/opt/homebrew/bin`) and the system PATH
    - `OMP_NUM_THREADS=4` (faster-whisper thread oversubscription guard)
    - `TZ=Europe/London` (or wherever the user is — make it configurable)
  - `RunAtLoad`: true (start on plist load)
  - `KeepAlive`: true (restart on crash)
  - `ThrottleInterval`: 10 (back off 10s on rapid crash-restart loops)
  - `StandardOutPath`: `<project>/logs/telegram-bot.stdout.log`
  - `StandardErrorPath`: `<project>/logs/telegram-bot.stderr.log`
- The committed template uses `{{PROJECT_ROOT}}` / `{{UV_BIN}}` / `{{TZ}}` placeholders that the install command resolves.

### install-scheduler subcommand
- New `runos bot install-scheduler` typer subcommand.
- Reads the template, substitutes placeholders with absolute values from the runtime environment + Settings, writes the resolved plist to `~/Library/LaunchAgents/com.runos.telegram-bot.plist`.
- Prints next steps (NEVER runs `launchctl` itself — explicit user step, mirrors existing pattern):
  ```
  launchctl unload ~/Library/LaunchAgents/com.runos.telegram-bot.plist  # if previously installed
  launchctl load   ~/Library/LaunchAgents/com.runos.telegram-bot.plist
  launchctl start  com.runos.telegram-bot
  ```
- Validates the resolved plist via `plutil -lint` and prints PASS/FAIL.

### Error boundary
- `runos/bot/error_handler.py` (new small module): `async def telegram_error_handler(update, context)` registered via `Application.add_error_handler(telegram_error_handler)` in `build_application`.
- Logs the full exception via `logging.exception("Bot handler crashed", extra={"chat_id": ..., "update_type": ...})`.
- Replies to the originating chat with: `"Sorry — something went wrong on my end. Check the logs."` (the canonical text).
- If reply itself fails (e.g. the chat is rate-limited), swallow that silently — never re-raise from the error handler.
- This handler catches everything that escapes per-handler logic, including AgentInvocationError (which is already handled inline in voice/text handlers, but defensive coverage is cheap).

### Voice-file retention
- New Settings field: `voice_retention_days: int = Field(default=0, validation_alias="VOICE_RETENTION_DAYS")`. `0` means delete-on-success (the default).
- After a successful transcription + agent reply in `voice_handler`, call `_cleanup_voice_file(path, retention_days=settings.voice_retention_days)`:
  - If `retention_days == 0`: `path.unlink(missing_ok=True)` immediately.
  - If `retention_days > 0`: leave file in place; rely on the startup sweep.
- Startup sweep: in `_post_init` (after `warm_model`), iterate `<content_dir>/voice/` and delete files older than `voice_retention_days` days. Idempotent. Logs `voice cache sweep: deleted N files`.
- Document this in `docs/PRIVACY.md` and `docs/TELEGRAM_BOT.md`.

### `runos bot purge-voice` CLI
- New subcommand: `runos bot purge-voice [--yes]`.
- Without `--yes`: interactive confirmation listing how many files / how much disk.
- With `--yes`: delete unconditionally.
- Tested via a small fixture dir.

### Working-directory scoping
- `voice_handler` and `text_handler` already pass `cwd=Path.cwd()` to `run_turn` (from Phase 11).
- launchd's `WorkingDirectory` directive sets that cwd to the project root when the bot runs as a service.
- Add a startup log line: `agent cwd = <abs-path>` so the user can confirm at boot.
- Document in `docs/PRIVACY.md` that the agent has bash access scoped to the project tree (Claude Code's default tool permissions).

### docs/PRIVACY.md (new)
One-page contract:

```markdown
# RunOS Privacy Contract

What stays on your machine:
- Voice memos (transcribed locally via faster-whisper; deleted by default after transcription)
- SQLite database (activities, wellness, journal, sessions)
- Tokens / secrets (.env, Strava + Garmin tokens)
- Reports (markdown analyses)

What flows through your Claude subscription:
- Transcribed text + Telegram message text → Claude Code via the Agent SDK
- Anything Claude Code itself reads/writes (subject to its own tool permissions)

What flows through Telegram:
- Voice memos (encrypted in transit; held briefly on Telegram servers per their bot API)
- Message text (your messages + agent replies)
- No raw audio leaves your laptop for transcription — Whisper runs locally on CPU.

What is NEVER sent anywhere:
- Voice files (after Whisper consumes them)
- Database contents (Strava activities, wellness, journal, sessions)
- Tokens or credentials
- Reports

Privacy knobs:
- `VOICE_RETENTION_DAYS=N` — keep cached voice files for N days (default 0 = delete on success)
- `runos bot purge-voice` — manually clear the voice cache
```

Linked from README.md.

### gitignore additions
- Ensure `logs/` is gitignored. (Probably already covered by `~/.runos/` umbrella if logs go there, but the plist points logs into `<project>/logs/`, so add an explicit `logs/` line.)
- Confirm `voice/` is covered (it's under `content_dir` which is gitignored).

### Testing strategy
- **launchd plist test**: render the template with fake placeholders, run `plutil -lint` against the rendered output, assert exit 0.
- **install-scheduler test**: monkeypatch `Path.home()`, run the command against a tmp dir, assert the resolved plist exists at the expected path + parses + has expected keys.
- **error handler test**: simulate an exception in a handler, verify `telegram_error_handler` logs + sends the canonical reply, verify reply-failure is swallowed.
- **voice retention test (retention=0)**: after successful flow, the .ogg is deleted.
- **voice retention test (retention=7)**: file kept; startup sweep deletes if older than threshold; younger files preserved.
- **purge-voice CLI test**: with `--yes`, deletes all files in voice cache; without `--yes`, interactive (mock prompt).
- **cwd log line test**: `build_application` logs `agent cwd = <path>` at startup.
- Expect ~7-9 new tests. Suite target: ~474-476.

### Documentation
- New `docs/PRIVACY.md`.
- Update `docs/TELEGRAM_BOT.md`: launchd setup section (run `runos bot install-scheduler` → launchctl commands), voice retention knob, error handler behavior, purge-voice.
- Update README "Telegram bot (v1.1)" section: brief mention of launchd lifecycle + link to PRIVACY.md.

### Closing the v1.1 milestone
- After Phase 12 lands and verifies, the v1.1 milestone is feature-complete.
- Update STATE.md: status `shipped`, all 4 phases complete, progress 100%.
- Update REQUIREMENTS.md: VOICE-11, 12, 14, 15 ticked.
- Optional: write a `MILESTONES.md` summary entry. (Skip if MILESTONES.md doesn't exist yet — keep ceremony low.)

</decisions>

<canonical_refs>
- `launchd/com.runos.daily.plist` — existing template for the daily-analysis job; mirror its shape.
- `runos/cli.py` — `install-scheduler` command pattern for the daily job.
- `runos/scheduler.py` — existing scheduler logic (if it covers helper utilities for plist generation; reuse where possible).
- `runos/bot/app.py` — `build_application` to register the error handler.
- `runos/bot/handlers.py` — `voice_handler` to add `_cleanup_voice_file` call after success.
- `runos/config.py` — Settings pattern to extend with `voice_retention_days`.
- `tests/test_scheduler.py` (if exists) — plist rendering test pattern.
- CLAUDE.md — RunOS conventions.

</canonical_refs>

<specifics>
- launchd's `KeepAlive=true` causes immediate restart on exit code != 0. `ThrottleInterval=10` enforces 10s minimum between restarts (prevents tight crash loops from eating CPU).
- `StandardOutPath` and `StandardErrorPath` are append-mode by default. For personal use, manual truncation is acceptable; no need for logrotate.
- `WorkingDirectory` must be an absolute path — relative paths don't work.
- The "user's launchd domain" is `gui/<uid>`. `launchctl load <plist>` is the user-facing command — the install-scheduler prints these so the user can run them manually (consistent with how the daily-sync job works).
- Telegram has a 4096-char body limit — error replies must respect that (the canonical "something went wrong" message is well under, so fine).
- PTB v22's error handler signature is `async def handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None`. `update` is typed as `object` because PTB error handlers can receive non-Update objects (e.g., callback-query errors). Use `isinstance(update, Update)` check before accessing `update.effective_chat`.

</specifics>

<deferred>
- Raspberry Pi port (v1.2 milestone).
- Auto log rotation.
- Health-check / heartbeat.
- Encrypted-at-rest cache.
- Multi-user retention policies.
- "RunOS dashboard" for cost/token tracking — logs are sufficient for now.
</deferred>

---

*Phase: 12-lifecycle-hardening-privacy*
*Context gathered: 2026-05-28 via inline orchestrator (research + roadmap + v1.1 closure)*
