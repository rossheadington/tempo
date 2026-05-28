---
phase: 09-telegram-bot-foundation
plan: 02
subsystem: telegram-bot
tags:
  - bot
  - cli
  - tests
  - docs
  - voice-coach
requirements:
  - VOICE-01
provides:
  - "runos bot run typer subcommand"
  - "Tests for owner-only allowlist (filters.Chat silent drop) + /start reply"
  - "docs/TELEGRAM_BOT.md user setup walkthrough"
  - "README Telegram bot (v1.1) section"
requires:
  - "runos.bot.app.build_application / run / _require_telegram_config (from 09-01)"
  - "runos.bot.handlers.start_handler / GREETING (from 09-01)"
  - "Settings.telegram_bot_token / telegram_owner_chat_id (from 09-01)"
affects:
  - runos/cli.py
  - tests/test_bot_app.py
  - docs/TELEGRAM_BOT.md
  - README.md
tech-stack:
  added: []
  patterns:
    - "Lazy intra-function import (`from runos.bot import run as bot_run`) inside the typer command body -- mirrors `sync()`'s lazy `from runos.sync import pipeline`."
    - "Class-level patching of `telegram.Message.reply_text` via `monkeypatch.setattr` for offline handler tests -- PTB v22 freezes instances so instance-level mock assignment is rejected."
    - "`asyncio.run(_inner())` inline for async tests -- avoids taking on `pytest-asyncio` as a new dev-dep (same idiom as tests/test_garmin_cli.py)."
key-files:
  created:
    - tests/test_bot_app.py
    - docs/TELEGRAM_BOT.md
    - .planning/phases/09-telegram-bot-foundation/09-02-PLAN.md
    - .planning/phases/09-telegram-bot-foundation/09-CONTEXT.md
  modified:
    - runos/cli.py
    - README.md
decisions:
  - "Patch `Message.reply_text` at the class level (not the instance) because PTB v22's `_TelegramObject.__setattr__` rejects instance attribute writes after construction. monkeypatch auto-unwinds at test teardown so it doesn't leak across tests."
  - "Use `asyncio.run(...)` inline for the two async handler tests rather than adding `pytest-asyncio` (per CONTEXT.md decision, and matches the existing pattern in tests/test_garmin_cli.py)."
  - "Place the typer `bot_app` block between the existing `garmin_app` block (ends ~line 351) and the `transform` command so subcommand groups remain visually grouped."
  - "Lazy-import `from runos.bot import run as bot_run` inside `bot_run_cmd` so a future machine that hasn't installed python-telegram-bot can still run `runos --help` (the failure is deferred to actually invoking the command)."
metrics:
  duration_sec: 440
  tasks_completed: 3
  files_changed: 4
  tests_added: 8
  tests_total: 406
  completed_at: "2026-05-27T21:38:28Z"
---

# Phase 9 Plan 02: Telegram Bot CLI + Tests + Docs Summary

Wired the already-shipped `runos.bot` package (from plan 09-01) into the
`runos` CLI as `runos bot run`, added 8 offline tests that prove the
owner-only allowlist and the `/start` reply, and wrote the one-page
`docs/TELEGRAM_BOT.md` user walkthrough + a short README section pointing at
it. Full pytest suite went from 398 to 406, all green. VOICE-01's silent-drop
guarantee is now proven offline at the dispatcher-filter level.

## What changed

### Task 1 — `runos bot run` typer subcommand (commit `2cfc4ba`)

Added a new `bot_app = typer.Typer(...)` group plus a single `@bot_app.command("run")`
under `runos/cli.py`, placed between the existing `garmin_app` block and the
`transform` command. The command body lazy-imports `runos.bot.run`, calls it,
and converts the `ValueError` raised by `_require_telegram_config` (in plan
09-01) into a clean `typer.Exit(1)` with the message printed in red to stderr.
That message — set by 09-01 — is exactly:

> `Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md`

Verified by `CliRunner().invoke(app, ["bot", "run"])` with both env vars unset
and no `.env` in the cwd: exit code 1, error names both env vars, references
`docs/TELEGRAM_BOT.md`.

`runos --help` now lists `bot` alongside `strava` / `garmin` / `journal` / `analyze`.
`runos bot --help` lists `run` with its docstring.

### Task 2 — `tests/test_bot_app.py` (commit `7d5ead4`)

Eight offline tests, zero network, no real bot token:

| # | Test | Asserts |
|---|------|---------|
| 1 | `test_settings_load_without_telegram_vars` | Both fields default to `None` so unconfigured runos still works. |
| 2 | `test_settings_load_with_both_vars_set` | `SecretStr` token scrubbed from `repr(settings)`; chat id is an `int`. |
| 3 | `test_require_telegram_config_missing_token_raises` | `ValueError` names BOTH env-var names AND `docs/TELEGRAM_BOT.md`. |
| 4 | `test_require_telegram_config_missing_chat_id_raises` | Same shape, mirror direction. |
| 5 | `test_build_application_registers_owner_only_start_handler` | `app.bot_data["owner_chat_id"] == 987654321`; the `/start` `CommandHandler`'s `filters` is a `filters.Chat` with `chat_ids == frozenset({987654321})`. |
| 6 | `test_start_command_filter_drops_non_owner` | `handler.check_update(non_owner_update)` is **falsy** (`False`); `handler.check_update(owner_update)` is **truthy** (`([], True)`). This is the heart of VOICE-01: the dispatcher silently drops non-owner before any handler runs. |
| 7 | `test_start_handler_replies_to_owner_with_greeting` | One awaited `reply_text` call; first positional arg is `GREETING`; `parse_mode=ParseMode.HTML`. |
| 8 | `test_start_handler_defensive_check_rejects_mismatched_chat` | When `bot_data["owner_chat_id"]` disagrees with `update.effective_chat.id`, zero `reply_text` calls — defence-in-depth re-check. |

Full pytest run: **406 passed** (398 baseline + 8 new), `ruff check` + `ruff format --check` clean.

### Task 3 — Docs (commit `f0f1d9d`)

`docs/TELEGRAM_BOT.md` (155 lines) walks zero-to-greeting:
- **Step 1** @BotFather: `/newbot` -> name -> username -> copy HTTP API token.
- **Step 2** `cp .env.example .env` -> `chmod 600 .env` -> set `TELEGRAM_BOT_TOKEN`, with an explicit note that this name is NOT prefixed with `RUNOS_` (the bare-name `validation_alias` set in 09-01).
- **Step 3** send any message to the bot -> visit `https://api.telegram.org/bot<TOKEN>/getUpdates` -> grab `result[0].message.chat.id` -> set `TELEGRAM_OWNER_CHAT_ID`.
- **Step 4** `uv run runos bot run` -> send `/start` from your phone -> expect the canonical greeting.
- **Step 5** sanity-check from a second account -> expect silence (no reply, nothing logged).
- **Troubleshooting**: 409 Conflict (manual `deleteWebhook`), non-replying bot (chat-id mismatch is by design), token rotation (`/revoke` in @BotFather), world-readable `.env`.
- **Privacy note** + forward-pointers to Phases 10/11/12.

`README.md` gets a `## Telegram bot (v1.1)` subsection between "Correlation insight" and "Scheduling (the daily run via launchd)", with a three-line fenced code block (one-time setup comments + `chmod 600 .env` + `uv run runos bot run`) and a markdown link to `docs/TELEGRAM_BOT.md`.

## How VOICE-01 + VOICE-02 are now verified

- **VOICE-01** (single-chat allowlist, silent drop on non-owner): proven
  offline by `test_start_command_filter_drops_non_owner` -- `filters.Chat(chat_id=owner).check_update(non_owner_update)` returns `False`, so the PTB dispatcher never invokes the handler. Defensive in-handler chat-id re-check covered by `test_start_handler_defensive_check_rejects_mismatched_chat`.
- **VOICE-02** (clear startup error when bot creds are missing): proven
  end-to-end at the CLI surface by both an inline `CliRunner` invocation (Task 1 verify) and by tests 3 + 4. The error message is the single line:

  > `Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md`

  That exact wording is set in `runos/bot/app::_require_telegram_config` (09-01) and is what `docs/TELEGRAM_BOT.md` matches verbatim.

Phase 9 ROADMAP success criteria 1, 2, 3, 4, and 5 (structured stdout logging from 09-01) are all satisfied by the combination of 09-01 + 09-02.

## Deviations from Plan

### Notable adjustments (in-plan, not deviations from rules)

**1. PTB v22 `Message` is frozen -- patch `reply_text` at the class level**

The plan's interfaces section suggested `update.message.reply_text = AsyncMock()`. PTB v22's `_TelegramObject.__setattr__` rejects instance attribute writes after construction with `AttributeError: Attribute reply_text of class Message can't be set!`, and `Message` is slot-based so `object.__setattr__` doesn't help either. The clean fix is `monkeypatch.setattr(Message, "reply_text", AsyncMock())` -- patches at the class level (auto-unwound at teardown so it doesn't leak between tests). The handler then calls `update.message.reply_text(...)` and Python's descriptor protocol resolves the class-level `AsyncMock` directly (no `self` binding because `AsyncMock` isn't a function descriptor), which means the awaited call's positional args start with the greeting itself (`call.args[0] == GREETING`), not with the message as a phantom first arg. Documented inline in both async tests.

**2. `MessageEntity(type="bot_command", offset=0, length=6)` required for `/start`**

`CommandHandler.check_update` only recognises `/start` as a command when the Message has a `bot_command` MessageEntity. The plan's interfaces section showed `Message(... text="/start")` without entities -- which makes `check_update` return `None` for *both* owner and non-owner. Adding the entity makes owner return `([], True)` (truthy) and non-owner return `False` (falsy), which is what the test asserts. This is documented inline in `_make_update`.

**3. `CommandHandler.check_update` also calls `message.get_bot()`**

A `RuntimeError: This object has no bot associated with it.` is raised unless the Message has a bot set. Solved with `message.set_bot(MagicMock(username="tempo_test_bot"))` + `update.set_bot(message.get_bot())`. The fake bot is never actually called -- only `get_bot()` returns it -- so no network.

None of these are "deviations" in the auto-fix sense (no behaviour change in production code; tests-only adjustments). The plan's `<output>` section asked for the exact `filters.Chat` attribute used -- **`chat_ids` (a `frozenset[int]`)** -- which matches what the plan predicted as the most likely name.

### Auto-fixed issues

None. The 09-01 scaffold was correct and the CLI + tests + docs slotted in cleanly.

## Files changed

| File | Action | Commit |
|------|--------|--------|
| `runos/cli.py` | Added `bot_app` typer group + `bot_run_cmd` (+31 lines, no other changes) | `2cfc4ba` |
| `tests/test_bot_app.py` | Created (253 lines, 8 tests) | `7d5ead4` |
| `docs/TELEGRAM_BOT.md` | Created (155 lines) | `f0f1d9d` |
| `README.md` | Added `## Telegram bot (v1.1)` subsection (17 lines added) | `f0f1d9d` |

## Verification

- `uv run pytest -q` -> **406 passed** (was 398).
- `uv run ruff check .` -> All checks passed.
- `uv run ruff format --check .` -> 82 files already formatted.
- `uv run runos --help` -> lists `bot` group.
- `uv run runos bot --help` -> lists `run`.
- `uv run runos bot run` with no env vars set and no `.env` -> exits 1, prints
  `Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md`.
- `grep -c BotFather docs/TELEGRAM_BOT.md` -> 6.
- `grep -c getUpdates docs/TELEGRAM_BOT.md` -> 3.
- `grep -c "chmod 600" docs/TELEGRAM_BOT.md` -> 3.
- `grep -c "docs/TELEGRAM_BOT.md" README.md` -> 2 (link + sentence).

## What's next

Phase 10: voice-memo download (`update.message.voice.get_file()` ->
`download_to_drive()`) + local `faster-whisper` transcription. The voice
handler hangs off the same `filters.Chat(chat_id=owner_chat_id)` filter
established here, so VOICE-01's silent-drop guarantee transparently covers it.

## Self-Check: PASSED

All claims verified:

- **Files exist:** `runos/cli.py` (modified), `tests/test_bot_app.py` (created), `docs/TELEGRAM_BOT.md` (created), `README.md` (modified), `.planning/phases/09-telegram-bot-foundation/09-02-PLAN.md` (tracked).
- **Commits exist:** `2cfc4ba` (Task 1), `7d5ead4` (Task 2), `f0f1d9d` (Task 3) all present in `git log --oneline`.
- **Test counts:** baseline 398 (verified at start) -> 406 (verified at end) = +8 tests as claimed.
- **CLI behaviour:** missing-config exit code 1 + error message verified by direct invocation.
