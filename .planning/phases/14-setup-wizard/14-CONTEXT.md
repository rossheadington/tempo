# Phase 14: First-Run Setup Wizard — Context

**Gathered:** 2026-05-28
**Status:** Ready for planning
**Source:** Inline discussion (conversation 2026-05-28). Owner asked for "a launcher process like open claw has where it takes you through all the steps to get you setup. Like asking for your creds and everything like that." Approved scope; left implementation details to defaults.

<domain>
## Phase Boundary

**What this phase delivers:**

- A new `tempo setup` typer command that walks a new user (fresh clone, no DB, no `.env`, no tokens, no launchd plists) through every step required to reach a working `tempo run-daily` (and optionally a running Telegram bot).
- The wizard is **orchestration only** — it owns prompts, `.env` I/O, state detection, dispatch, and the final smoke test. Every credentialed step delegates to the existing `tempo` CLI surface; no duplicated auth handshake, no duplicated plist rendering, no duplicated MFA prompt.
- A new module `tempo/setup/` with submodules:
  - `wizard.py` — top-level orchestrator (welcome → tooling check → DB → content dir → strava → garmin → telegram → scheduler → bot-scheduler → smoke → finish)
  - `env_io.py` — atomic `.env` read/write helper (`read_env(path) -> dict[str, str]`, `atomic_write_env(path, updates: dict[str, str], delete_keys: set[str] = set()) -> None`)
  - `state.py` — state-detection helpers (`db_initialised(settings) -> bool`, `strava_configured(settings) -> bool`, etc.)
  - `prompts.py` — thin stdlib wrappers around `typer.prompt` / `typer.confirm` with consistent banner formatting and `[set]` / `[done]` indicators
- A new `docs/SETUP.md` end-to-end document that captures every step the wizard performs, so a user who prefers manual setup can follow it by hand.
- A README rewrite of the "Getting Started" section to lead with `tempo setup` (one command) and link to `docs/SETUP.md` for the manual / power-user path.
- Tests under `tests/test_setup_wizard.py` covering: state detection (all combinations of DB present / absent, .env keys set / unset, token files present / absent), atomic `.env` write (round-trips arbitrary key/value pairs, preserves untouched keys, 0600 perms, never leaves a partial file on crash), each step's dispatch logic (mocked CLI calls), `--only=<step>` filtering, `--skip-*` flags, and the final smoke-test reporting.

**What this phase does NOT deliver (out of scope, deferred):**

- A TUI / curses / rich-based interactive UI. Plain `typer.prompt` + `typer.secho` for banners. Adding `rich` as a dep is explicitly out of scope (`typer` already depends on `click` which has enough for what we need; no new top-level deps).
- A web-based onboarding page or QR-code-driven mobile setup. Single user, single laptop, terminal-only.
- Account creation / sign-up flows for Strava / Garmin / Telegram. The user already has those accounts; the wizard collects the credentials and runs the existing flows.
- A `tempo setup --uninstall` reverse path. Removing a Tempo install is a 3-line manual `rm` of `~/.tempo/`, `.env`, and the launchd plists; documenting it in `docs/SETUP.md` is enough.
- Migration from a prior install. The owner has the only existing install; if a re-run scenario appears in practice, `tempo setup` already handles "keys present, prompt to keep/change" so a re-run effectively IS a migration.
- A `tempo doctor` health check (diagnoses why a stuck install is stuck). Useful but separable; defer to a follow-up phase. The smoke-test step at the end of the wizard catches the most common "credentials don't actually work" case.
- Cross-platform support for Linux / Windows launchd-equivalents. Tempo is Mac-only today (per `branching_strategy: none` + the launchd plist convention). The Pi port (v1.2 previously planned) will need a systemd-equivalent step; out of scope here.
- Auto-detection of optimal Whisper model / threshold pace / max HR / resting HR. The wizard collects threshold pace, max HR, resting HR, and threshold HR as optional prompts but does not auto-estimate them.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Command shape

- `tempo setup` (top-level command, no subcommand) — runs the full wizard top-to-bottom.
- `tempo setup --only=<step>` — runs a single named step. Valid step names: `db`, `content`, `strava`, `garmin`, `telegram`, `scheduler`, `bot-scheduler`, `smoke`. Multiple `--only` flags can stack (`--only=telegram --only=bot-scheduler`).
- `tempo setup --skip-garmin` — skip the Garmin step in a full run.
- `tempo setup --skip-telegram` — skip Telegram bot setup (also implies `--skip-bot-scheduler`).
- `tempo setup --skip-scheduler` — skip daily launchd install.
- `tempo setup --skip-bot-scheduler` — skip bot launchd install (but still set up the bot creds).
- `tempo setup --skip-smoke` — skip the final `tempo sync` smoke test.
- `tempo setup --non-interactive` — fail fast on any prompt that would have been required (useful for testing; not for end-user use).
- All flags compose with `--only=<step>` so a power user can do `tempo setup --only=scheduler --hour=6` (the scheduler step accepts `--hour` / `--minute` forwarded to `tempo install-scheduler`).

### Step list (LOCKED, in order)

1. **Welcome + tooling check** (`step_id="welcome"`, always runs unless `--only` excludes it)
   - Banner: `Tempo first-run setup` + one-line summary of what's coming.
   - Check `sys.version_info >= (3, 14)` — fail with a clear message if not.
   - Check `shutil.which("uv")` — warn (not fail) if `uv` is missing (it might be `python -m tempo` instead).
   - Detect existing state via `state.detect_install_state(settings)` and print a one-line summary: `Detected: DB ✓ · Strava ✓ · Garmin ✗ · Telegram ✗ · daily-scheduler ✗ · bot-scheduler ✗`.
2. **DB init** (`step_id="db"`)
   - If `settings.db_path.exists()` AND `db.current_version(conn) == db.SCHEMA_VERSION` → print `[done]` and skip. Offer a `--force` re-run via prompt if the user is in `--only=db` (re-running init is idempotent).
   - Else invoke `tempo init` via direct call to the underlying `_init()` function (the helper at `cli.py:31` or wherever it lives — confirm at implementation time).
3. **Content dir picker** (`step_id="content"`)
   - If `TEMPO_CONTENT_DIR` is already set in `.env`, show it and offer keep / change.
   - Else default to `~/.tempo/` and offer two alternatives: `~/Projects/tempo/training/` (the owner's actual setup, only suggested if the project dir is detected) and `<custom>`.
   - Write the choice to `.env` via `atomic_write_env`.
   - Create the chosen directory if it does not exist, with 0700 perms.
4. **Strava creds + OAuth** (`step_id="strava"`)
   - If `TEMPO_STRAVA_CLIENT_ID` and `TEMPO_STRAVA_CLIENT_SECRET` are set AND a non-expired token file exists at `settings.strava_token_path`: print `[done]` and skip (offer re-auth on `--only=strava`).
   - Else: print instructions block:
     ```
     Strava setup
     ------------
     1. Open https://www.strava.com/settings/api
     2. Create an application (any name; callback domain = localhost)
     3. Copy the Client ID + Client Secret here:
     ```
   - Prompt for `TEMPO_STRAVA_CLIENT_ID` (visible).
   - Prompt for `TEMPO_STRAVA_CLIENT_SECRET` (hidden via `typer.prompt(..., hide_input=True)`).
   - Write both to `.env` atomically.
   - Run the OAuth handshake INLINE: import `build_strava_connector`, compute `connector.authorization_url(redirect_uri)`, print it, optionally `webbrowser.open(url)`, prompt user to paste the `code` from the redirect URL, call `connector.exchange_code(code)`.
   - On success print `[done]` with token path. On failure print the error and offer retry-or-skip.
5. **Garmin login** (`step_id="garmin"`, optional, skipped by `--skip-garmin`)
   - Prompt Y/N: "Do you also use Garmin (wellness data: HRV, sleep, resting HR)? [Y/n]".
   - If Y: prompt for `TEMPO_GARMIN_EMAIL` (visible) and `TEMPO_GARMIN_PASSWORD` (hidden), write to `.env`, then invoke `garmin_login(settings, prompt_mfa=typer.prompt)` (the same factory function `tempo garmin login` uses today).
   - If N: write nothing, skip the step, do NOT block later steps.
6. **Telegram bot creds** (`step_id="telegram"`, optional, skipped by `--skip-telegram`)
   - Prompt Y/N: "Do you want to run the Telegram voice/text bot? [y/N]".
   - If Y: print instructions block:
     ```
     Telegram bot setup
     ------------------
     1. In Telegram, open a chat with @BotFather. Send /newbot, follow the prompts.
        BotFather replies with a token like 1234567890:AAH... — copy it.
     2. Open a chat with @userinfobot and send /start to get your numeric chat id.
     ```
   - Prompt for `TELEGRAM_BOT_TOKEN` (hidden — it's a credential).
   - Prompt for `TELEGRAM_OWNER_CHAT_ID` (visible, numeric).
   - Write to `.env`. (Note: these two keys are bare, NOT prefixed `TEMPO_` — see `.env.example`.)
   - **Whisper model knobs** — offer a `[default / change]` prompt for `WHISPER_MODEL_NAME` / `WHISPER_COMPUTE_TYPE` / `WHISPER_DEVICE`; default = leave unset (so the code-level defaults apply).
   - **Voice retention** — offer a prompt for `VOICE_RETENTION_DAYS`; default = unset (i.e. delete-on-success).
7. **Daily launchd scheduler** (`step_id="scheduler"`, optional, skipped by `--skip-scheduler`)
   - Prompt Y/N: "Install the daily-sync launchd job to run automatically? [Y/n]".
   - If Y: prompt for hour (default 5) and minute (default 30); confirm; invoke `install_plist(project_dir, data_dir, hour, minute, to_launch_agents=True)` (the same function `tempo install-scheduler` calls).
   - Print the `launchctl bootstrap` command to run AFTER setup (Tempo never runs launchctl itself per existing convention).
8. **Bot launchd scheduler** (`step_id="bot-scheduler"`, optional, skipped by `--skip-bot-scheduler` OR when Telegram step was skipped/declined)
   - Only offered if step 6 was completed.
   - Prompt Y/N: "Install the Telegram-bot launchd job (KeepAlive=true so it survives crashes / sleep)? [Y/n]".
   - If Y: invoke the same code path as `tempo bot install-scheduler --to-launch-agents`. Print the `launchctl bootstrap` command.
9. **Smoke test** (`step_id="smoke"`, optional, skipped by `--skip-smoke`)
   - Run `tempo sync` programmatically (the `_sync()` helper, NOT a subprocess) and capture the per-source result tuple.
   - Print: `Strava: ✓ (N raw rows) · Garmin: ✓/✗ (reason)`.
   - If Strava failed terminally (auth-error, not rate-limit): print remediation hint pointing back to `tempo setup --only=strava`. Same for Garmin.
   - Exit code 0 if no non-skipped step failed terminally; exit code 1 otherwise.
10. **Finish banner** (always runs unless `--only` excludes everything)
    - Summary: `Tempo is set up. What's installed: Strava ✓ · Garmin ✓ · Telegram ✗ · daily-scheduler ✓ · bot-scheduler ✗`.
    - Next-step hints: how to view today's report (`cat ~/.tempo/reports/<latest>.md`), how to load the launchd plists (`launchctl bootstrap ...`), how to test the bot (`tempo bot run`).

### `.env` I/O (LOCKED)

- New helper module `tempo/setup/env_io.py`:
  - `read_env(path: Path) -> dict[str, str]` — parse a `.env` file into a dict. Preserves only the last value when a key appears multiple times. Strips quotes from values. Returns `{}` if file is missing.
  - `atomic_write_env(path: Path, updates: dict[str, str], delete_keys: set[str] = frozenset()) -> None` — atomic write modelled on `tempo/connectors/tokens.py::write_tokens`:
    1. Read existing file (if any) line-by-line, preserving comments and blank lines verbatim.
    2. For each existing key=value line: if key in `delete_keys`, drop the line; if key in `updates`, replace the value; else preserve.
    3. For keys in `updates` not present in the existing file, append them at the end with a leading blank line.
    4. Write to `<path>.tmp`, fsync, `os.replace(tmp, path)`, chmod 0600, fsync parent dir.
  - Values containing spaces or `$` are quoted with `"..."` on write (the simplest dotenv-compatible rule).
  - Secret values are NEVER logged or echoed by this module.
- **The wizard MUST NOT write `.env` outside this helper.** All other steps call into `atomic_write_env` for their writes.

### State detection (LOCKED)

- New helper module `tempo/setup/state.py`:
  - `class InstallState(frozen=True, slots=True)` — fields: `db_initialised: bool`, `content_dir_set: bool`, `strava_configured: bool`, `garmin_configured: bool`, `telegram_configured: bool`, `daily_scheduler_installed: bool`, `bot_scheduler_installed: bool`.
  - `detect_install_state(settings: Settings) -> InstallState` — pure read-only function over filesystem + DB schema version. Used by the welcome banner AND by each step's "is this already done?" check.
  - The check for `daily_scheduler_installed` looks at `~/Library/LaunchAgents/com.tempo.daily.plist`; same shape for the bot plist. The wizard does NOT verify the job is currently loaded (`launchctl list`) because Tempo never runs `launchctl`; the plist's presence is the contract.

### Delegation pattern (LOCKED, mirrors SETUP-04)

- Strava OAuth: import `build_strava_connector` from `tempo.connectors.factory` (or wherever the existing `strava_auth` command imports it) and call `connector.authorization_url(...)` / `connector.exchange_code(...)` directly. **Do NOT** invoke `tempo strava auth` as a subprocess.
- Garmin login: import `garmin_login` from `tempo.connectors.factory` and call it directly with `prompt_mfa=typer.prompt`.
- Daily scheduler: import `tempo.scheduler.install_plist` and call it directly.
- Bot scheduler: import the relevant function from `tempo.bot.scheduler` (or wherever `bot install-scheduler` lives — confirm at implementation time) and call it directly.
- DB init: call the existing `_init()` helper directly.
- Smoke `tempo sync`: call the existing `_sync()` helper (or whatever the `tempo sync` command body factors out to) directly.
- The reason: a subprocess call would lose the in-process settings, prompt callback, and exception context. Direct calls keep the wizard a single coherent flow.

### Re-run safety (LOCKED)

- Every step starts by detecting "is this already done?" via `state.py` (and for `.env` keys, via `read_env`).
- If done, the default action is **skip** with a `[done]` indicator.
- If the user passes `--only=<step>`, the step DOES run but the user is asked `[set] keep / change / fresh` for each `.env` key.
- The wizard NEVER silently overwrites a non-empty value without confirmation.
- A re-run is the documented way to recover from a stuck step. The phase summary copy must say this explicitly.

### Test scope (LOCKED)

- `tests/test_setup_wizard.py`:
  - `test_atomic_env_write_round_trip` — write a fresh `.env`, then update one key, then delete one key; assert content + 0600 perms + atomicity (no partial file via a synthetic mid-write fault).
  - `test_atomic_env_write_preserves_comments_and_unrelated_keys` — assert that running the helper with one key change does not reorder or drop the other lines.
  - `test_detect_install_state_combinations` — parametrise over `(db_present, strava_token_present, plist_present, ...)` and assert the right state flags.
  - `test_wizard_skips_when_all_done` — with a fixture install where everything is set, the wizard completes without prompting (verified via mocked prompts that record they were never called) and reports the right summary.
  - `test_wizard_only_filter` — `--only=strava` runs only the Strava step.
  - `test_wizard_skip_garmin` — `--skip-garmin` plus a fresh state: Garmin step never runs.
  - `test_wizard_handles_strava_oauth_failure_gracefully` — mocked `exchange_code` raises; wizard prints error and offers retry/skip; does not abort the whole run.
  - `test_smoke_step_runs_sync` — mocked `_sync()` returns a sample per-source result; wizard prints the right summary.
- Stdlib + pytest's `tmp_path` + `monkeypatch` only. No new test deps. Wizard prompts mocked via `monkeypatch.setattr(typer, "prompt", ...)` etc.

### Out-of-band safety items (LOCKED)

- The wizard MUST NOT add a `.env` to git. The `.gitignore` already covers `.env`; the wizard's `.env` writes go through `atomic_write_env` which writes to the project root with 0600 perms. Confirm `.env` is in `.gitignore` as part of the preflight in Plan 14-01.
- The wizard MUST NOT echo secrets after entry. All hidden-input prompts use `typer.prompt(..., hide_input=True)`. All `.env` reads/writes are silent (no debug print of values).
- The wizard MUST be safe to Ctrl-C at any prompt: a SIGINT mid-write is impossible (atomic rename) but a SIGINT mid-flow simply exits with the last-completed step's state intact (no partial step needed).

### Code organisation conventions

- New top-level module dir: `tempo/setup/` with `__init__.py`, `wizard.py`, `env_io.py`, `state.py`, `prompts.py`. Mirror `tempo/bot/` and `tempo/sync/` structure.
- `tempo/cli.py` gains exactly one new command — `@app.command("setup")` that imports `from tempo.setup.wizard import run_wizard` and calls `run_wizard(settings, ...)`. Keep the CLI surface thin; logic lives in `setup/wizard.py`.
- Tests: `tests/test_setup_wizard.py` covers wizard + smoke; `tests/test_setup_env_io.py` covers env_io; `tests/test_setup_state.py` covers state. Three files matches the three-submodule structure.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing CLI commands to delegate to

- `tempo/cli.py:64-67` — `tempo init` / `_init()`. The DB init step calls this directly.
- `tempo/cli.py:92-127` — `tempo strava auth` (two-step OAuth). The wizard's Strava step replicates this flow inline by calling `build_strava_connector` + `authorization_url` + `exchange_code` (functions imported from the same module as `strava_auth` does today).
- `tempo/cli.py:265-296` — `tempo garmin login`. The wizard's Garmin step calls `garmin_login(settings, prompt_mfa=typer.prompt)` directly (same factory function `garmin_login_cmd` uses).
- `tempo/cli.py:364-433` — `tempo bot install-scheduler`. The wizard's bot-scheduler step calls the same underlying function directly (confirm exact import at implementation time).
- `tempo/cli.py:884-929` — `tempo install-scheduler`. The wizard's scheduler step calls `tempo.scheduler.install_plist` directly with the same arguments.
- `tempo/cli.py:543-...` (the `tempo sync` command) — the smoke-test step calls the underlying sync function directly. Find the function the `sync` command delegates to.

### Atomic-write pattern (the model for `env_io.py`)

- `tempo/connectors/tokens.py` — the full atomic-write template: temp file → fsync → `os.replace` → fsync parent dir → chmod 0600. The wizard's `atomic_write_env` MUST mirror this pattern exactly.

### Config + secrets pattern

- `tempo/config.py:1-227` — `Settings` class with derived paths (`races_path`, `heat_path`, `strength_path`, `db_path`, `tokens_dir`, etc.). The wizard reads paths from `Settings`; it does not hardcode any of them.
- `.env.example` (committed) — the authoritative list of every env var Tempo reads, with comments. The wizard's prompts match the keys documented there. **DO NOT introduce new env vars in this phase.**

### Existing typer patterns

- `tempo/cli.py:280` — `typer.prompt("Garmin MFA code")` — the prompt style used elsewhere. Hidden inputs use `typer.prompt("...", hide_input=True)`.
- `tempo/cli.py:283-292` — the `try / except` shape for catching auth failures and printing a remediation message. The wizard's per-step error handling mirrors this.

### Test patterns

- `tests/test_strava_connector.py` (any file there) — pytest + tmp_path + monkeypatch + responses for HTTP mocks. The wizard's tests use the same building blocks (no `responses` needed; subprocess calls are replaced with direct in-process calls).
- `tests/test_garmin_*.py` — the `garmin_fakes.py` shape for mocking the Garmin client. The wizard's Garmin step tests mock `garmin_login` directly.

### Documentation pattern

- `docs/TELEGRAM_BOT.md` — long-form setup walkthrough with one-time steps + operational runbook. The new `docs/SETUP.md` follows this structure: one-time setup (manual fallback) + ongoing operations (re-runs / changing creds / uninstall).

</canonical_refs>

<specifics>
## Specific Ideas

- The owner's `.env` already has Strava + Garmin + Telegram + content-dir configured (visible from `git status` showing `tempo/bot/`, `tempo/cli.py` etc. as touched). The wizard is for FUTURE users (and for adding the bot to an install that started bot-less) — but should still run cleanly against the owner's existing setup, reporting `[done]` for every step.
- `webbrowser.open()` is stdlib; the Strava-OAuth step can helpfully open the auth URL for the user. Detect headless (`$DISPLAY` empty AND not macOS) and skip the auto-open in that case — just print the URL.
- The wizard's instructions blocks (Strava API page, BotFather, userinfobot) are LITERAL text — keep them verbatim in code so the wording matches the docs exactly. A short helper `_print_block(title: str, body: str)` for the visual indent.
- The owner runs Tempo via `uv run tempo ...`. `tempo setup` should be runnable that way too (it will be — typer's normal invocation works fine). Document it in `docs/SETUP.md`.
- The wizard's final summary line should mention `docs/SETUP.md` so the user knows where to look if they want to change something later without re-running the whole wizard.

</specifics>

<deferred>
## Deferred Ideas

- **`tempo doctor`** — diagnose-only health check (no writes) that runs all the state-detection checks plus a few extra (token expiry warnings, content-dir permissions, gitleaks installed, launchd plist syntactically valid). Useful but separable from setup. Follow-up phase.
- **TUI / rich-based UI** with checkboxes, progress bars, and a final dashboard. Out of scope. The wizard's plain prompts are fine for a single-user local tool.
- **Auto-discovery of optimal Whisper model** based on the user's CPU. The wizard offers the default and asks if they want to change it; auto-detection is fiddly and the default is correct for an M-series Mac.
- **Auto-detect threshold pace / max HR / resting HR** from a Strava history backfill. Useful for new users with existing Strava data but cross-cuts Phase 4 (`tempo/analysis/load.py`); follow-up phase.
- **Multi-user / multi-account support.** Tempo is single-user; the wizard reflects that. A "Tempo for teams" pivot is out of scope.
- **`tempo setup --uninstall`** — reverse path that removes the launchd plists and (optionally) the data dir + `.env`. Documented in `docs/SETUP.md` as a manual `rm` sequence; a one-shot CLI shortcut is a separable polish task.
- **Linux/Pi systemd-equivalent** of the launchd steps. Will land as part of the Pi-port milestone (previously v1.2, now deferred). The wizard's scheduler step will need a platform-detect branch then; for now it is Mac-only.
- **Auto-generated random Strava OAuth state token** for the redirect URL. The current code uses a fixed localhost callback and doesn't verify state; not a real risk in single-user local use but worth noting as a hardening item.

</deferred>

---

*Phase: 14-setup-wizard*
*Context gathered: 2026-05-28 via inline discussion*
