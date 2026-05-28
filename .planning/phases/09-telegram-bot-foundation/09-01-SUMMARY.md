---
phase: 09-telegram-bot-foundation
plan: 01
subsystem: bot
tags: [telegram, scaffold, config, voice-02]
requires:
  - runos.config.Settings (extended in this plan)
provides:
  - runos.bot.build_application
  - runos.bot.run
  - runos.bot.start_handler
  - runos.bot.GREETING
  - Settings.telegram_bot_token
  - Settings.telegram_owner_chat_id
affects:
  - pyproject.toml (runtime deps)
  - .env.example (documentation)
tech_stack:
  added:
    - python-telegram-bot >= 22.7 (long-polling Telegram bot client)
  patterns:
    - "ValueError-with-env-var-hint for missing creds (mirrors runos/connectors/factory.py)"
    - "validation_alias to bypass env_prefix for cross-cutting env-var conventions"
    - "post_init hook for the PTB 409-Conflict pitfall fix"
key_files:
  created:
    - runos/bot/__init__.py
    - runos/bot/app.py
    - runos/bot/handlers.py
  modified:
    - pyproject.toml
    - uv.lock
    - runos/config.py
    - .env.example
decisions:
  - "Telegram env vars use the BARE names TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_CHAT_ID (not prefixed with RUNOS_) via Field(validation_alias=...) so the standard Telegram convention is preserved while the rest of tempo keeps its RUNOS_ prefix."
  - "Both Settings fields default to None so unconfigured runos (sync/analyze/journal) keeps working; the missing-config error lives in runos.bot.app._require_telegram_config rather than as a field_validator on Settings."
  - "concurrent_updates=True from day one so Phase 10's voice handlers (multi-second transcription) won't block each other; safe here because no ConversationHandler is in use."
  - "post_init hook calls delete_webhook(drop_pending_updates=False) -- preserves offline messages sent while the laptop slept, while clearing any stale webhook config that would otherwise cause a 409 Conflict on getUpdates."
  - "Defence-in-depth on the allowlist: filters.Chat at registration time AND an in-handler effective_chat.id re-check, with the expected owner_chat_id stashed in application.bot_data."
metrics:
  duration_minutes: 12
  tasks_completed: 2
  files_created: 3
  files_modified: 4
  commits: 2
  completed: 2026-05-27
---

# Phase 09 Plan 01: Telegram bot scaffold Summary

Wired the Telegram bot foundation: added the `python-telegram-bot>=22.7` runtime
dependency, extended `runos.config.Settings` with `telegram_bot_token: SecretStr | None`
and `telegram_owner_chat_id: int | None` (both bypassing the `RUNOS_` env prefix
via `validation_alias`), created the import-safe `runos/bot/` package exposing
`build_application(settings) -> Application` and `run()`, and documented the
two new env vars in `.env.example` with the one-time @BotFather + getUpdates
setup flow. No CLI wiring, no tests, no docs/TELEGRAM_BOT.md -- those land in
plan 09-02.

## What landed

### Config (runos/config.py)

Two new `Settings` fields, both default `None`:

```python
telegram_bot_token: SecretStr | None = Field(
    default=None,
    validation_alias="TELEGRAM_BOT_TOKEN",
    description="Telegram bot HTTP API token from @BotFather. Bare env-var "
                "name (NOT prefixed with RUNOS_) so the standard Telegram "
                "convention is preserved.",
)
telegram_owner_chat_id: int | None = Field(
    default=None,
    validation_alias="TELEGRAM_OWNER_CHAT_ID",
    description="Owner Telegram chat id (an integer). The bot only replies "
                "to this chat; everything else is silently dropped at the "
                "filter level.",
)
```

`SecretStr` keeps the token out of `repr(Settings)` and out of any accidental
log line; `validation_alias` makes the bare env var name override the
class-wide `env_prefix="RUNOS_"`. With both env vars unset, all 398 existing
tests pass unchanged -- the new fields are invisible to the rest of tempo.

### runos/bot/ package

* `__init__.py`: module-index docstring + `__all__ = ["GREETING", "build_application", "run", "start_handler"]`. Import-safe; no side effects.
* `handlers.py`:
  - `GREETING = "RunOS bot online. Send a voice memo to journal a session, or text for any other request."`
  - `async def start_handler(update, context) -> None`: reads `context.application.bot_data["owner_chat_id"]`, defensively re-checks `update.effective_chat.id`, logs at INFO, replies with `parse_mode=ParseMode.HTML`.
* `app.py`:
  - `_require_telegram_config(settings) -> tuple[str, int]`: raises `ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md")` when either field is None; otherwise returns `(token, int(owner_chat_id))`.
  - `build_application(settings) -> Application`: builds via `ApplicationBuilder().token(...).concurrent_updates(True).post_init(_post_init).build()`. The `_post_init` hook calls `await application.bot.delete_webhook(drop_pending_updates=False)` to dodge the 409-Conflict pitfall while preserving offline messages. Stashes `owner_chat_id` in `app.bot_data`, then registers `CommandHandler("start", start_handler, filters=filters.Chat(chat_id=owner_chat_id))`.
  - `run() -> None`: calls `_configure_logging()` (the only `basicConfig` call in the package; demotes httpx/httpcore to WARNING), loads settings, builds the app, logs `"Bot started -- waiting for messages..."`, and blocks on `app.run_polling()` (PTB's default `stop_signals` left intact so SIGINT/SIGTERM both shut it down cleanly).

### .env.example

Added a documentation block between the Garmin and load/analysis sections with:
- the one-time @BotFather flow (search @BotFather -> /newbot -> token),
- the `getUpdates` flow to find the owner chat id,
- the `chmod 600 .env` reminder,
- a cross-reference to `docs/TELEGRAM_BOT.md` (created in plan 09-02),
- the two commented assignments `# TELEGRAM_BOT_TOKEN=` and `# TELEGRAM_OWNER_CHAT_ID=`.

### pyproject.toml + uv.lock

`python-telegram-bot>=22.7` added to `[project].dependencies`. `uv add` pulled
in PTB 22.7 + transitive deps (`anyio`, `h11`, `httpcore`, `httpx`). No
dev-deps changed.

## Verification (success criteria from PLAN)

- `python-telegram-bot >= 22.7` is in `pyproject.toml [project].dependencies`.
- `Settings.telegram_bot_token: SecretStr | None` and `Settings.telegram_owner_chat_id: int | None`, both read from the bare env-var names; both default `None` so unconfigured runos still works.
- `runos.bot.app.build_application(settings)` returns a real PTB `Application` with `concurrent_updates=True`, a `post_init` that calls `delete_webhook`, `bot_data["owner_chat_id"]` populated, and a `CommandHandler("start", ...)` registered behind `filters.Chat(chat_id=owner_chat_id)`.
- `_require_telegram_config(Settings(_env_file=None))` raises `ValueError` whose message names BOTH `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` and points to `docs/TELEGRAM_BOT.md`.
- `.env.example` documents both env vars with the @BotFather + getUpdates setup steps.
- `uv run pytest tests/` -> 398 passed (baseline unchanged).
- `uv run ruff check runos/` + `uv run ruff format --check runos/` -> clean.
- `uv run python -c "import runos.bot"` -> no error, no network, no logging side effects.

## Commits

| Task | Description | Commit |
| ---- | ----------- | ------ |
| 1 | Add python-telegram-bot dep + Settings telegram fields + .env.example block | `6ca904f` |
| 2 | Scaffold runos/bot/ package (app + handlers + __init__) | `646bdfb` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking issue] Edits accidentally landed in the main repo instead of the worktree**

- **Found during:** Task 1 verify (the `Settings` class had no `telegram_*` fields after editing config.py).
- **Issue:** When this executor started, it used `Read` with the absolute main-repo path (`/Users/rossheadington/Projects/RunOS/runos/config.py`) supplied by the harness's pre-existing context, and the subsequent `Edit` calls inherited that absolute path. The edits wrote to the main repo's working tree instead of this worktree's, and `uv run` in the worktree pointed at the worktree's unmodified file, so verification failed.
- **Fix:** Reverted the accidental writes in the main repo with `git checkout -- runos/config.py pyproject.toml .env.example` (no main-repo commit existed), then re-applied identical edits in the worktree using relative paths derived from the worktree root. This is the documented `<absolute-path safety>` pitfall in the executor prompt.
- **Files re-modified:** `runos/config.py`, `pyproject.toml`, `.env.example` (all in the worktree this time).
- **Commits:** No separate commit; the corrected edits are folded into the Task 1 commit `6ca904f`.

**2. [Rule 3 - Blocking issue] `uv sync` did not pick up the new pyproject dep until `uv add` was run**

- **Found during:** Task 1 verify (`uv run python -c "import telegram"` failed with `ModuleNotFoundError` even after editing pyproject.toml and running `uv sync`).
- **Issue:** `uv sync` saw the pyproject change but did not re-resolve -- presumably because the lock was satisfied for the old dep set and uv didn't re-check the editable project's `pyproject.toml` against the lock. The lock file still listed only 5 runtime deps for `runos`.
- **Fix:** Ran `uv add 'python-telegram-bot>=22.7'`, which forced a re-resolution and installed PTB + anyio/h11/httpcore/httpx. The lock file now matches `pyproject.toml`.
- **Files modified by the fix:** `uv.lock` (now committed alongside `pyproject.toml`).
- **Commit:** Folded into `6ca904f`.

### Architectural/Plan Adherence

No architectural deviations. Plan executed exactly as specified: same module
layout, same handler signature, same `validation_alias` approach, same
`post_init` hook for the 409-Conflict fix, same defence-in-depth filter +
in-handler re-check.

## Authentication Gates

None. This plan only adds the config slots and scaffold -- the bot is not
launched here, so no Telegram token is needed during execution. The user will
supply `TELEGRAM_BOT_TOKEN` + `TELEGRAM_OWNER_CHAT_ID` after plan 09-02 wires
the CLI (`runos bot run`).

## Known Stubs

None. The two `Settings` fields default to `None` by design (so unconfigured
runos keeps working); they are not stubs. The CLI surface that triggers
`_require_telegram_config` lands in plan 09-02 -- the function itself is
already wired and raises the documented error today.

## Self-Check: PASSED

Verified:
- `runos/bot/__init__.py` exists.
- `runos/bot/app.py` exists.
- `runos/bot/handlers.py` exists.
- Commit `6ca904f` present in `git log --all`.
- Commit `646bdfb` present in `git log --all`.
- `from runos.bot import build_application, run, start_handler, GREETING` succeeds.
- `Settings.model_fields` includes `telegram_bot_token` and `telegram_owner_chat_id`.
- 398 existing tests pass.
- `ruff check` + `ruff format --check` clean on `runos/`.
