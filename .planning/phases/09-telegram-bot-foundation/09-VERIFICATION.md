---
phase: 09
status: passed
verified_at: 2026-05-27
score: 8/8 must-haves verified
---

# Phase 9 Verification

## Verdict
passed: Phase 9 delivers a runnable, owner-locked `python-telegram-bot` long-polling scaffold with clear config errors, complete docs, and 8 new offline tests proving VOICE-01 silent-drop and VOICE-02 missing-config behavior; full suite (406 tests) is green and ruff is clean.

## Goal-backward check

- **G1 ✓** — `runos bot run` exists as a typer subcommand and calls into the bot worker.
  - Evidence: `runos/cli.py:357-361` declares `bot_app = typer.Typer(...)` and `app.add_typer(bot_app, name="bot")`. `runos/cli.py:364-381` defines `@bot_app.command("run") def bot_run_cmd()` which lazy-imports `runos.bot.run` and invokes it, catching `ValueError` -> red stderr + `typer.Exit(1)`.
  - Smoke: `uv run runos --help` lists `bot`; `uv run runos bot --help` lists `run` with its docstring.

- **G2 ✓** — Chat-id allowlist enforced at filter level (non-owner messages silently dropped — no handler invocation, no reply).
  - Evidence: `runos/bot/app.py:92-93` registers the handler as `CommandHandler("start", start_handler, filters=filters.Chat(chat_id=owner_chat_id))`. `tests/test_bot_app.py:174-193` (`test_start_command_filter_drops_non_owner`) asserts `handler.check_update(non_owner_update)` is falsy and `handler.check_update(owner_update)` is truthy — proving the dispatcher drops non-owner before any handler runs. Defence-in-depth in-handler re-check at `runos/bot/handlers.py:40-49`.

- **G3 ✓** — Tests prove both owner-replies and non-owner-silent-drops behaviors.
  - Evidence: `test_start_handler_replies_to_owner_with_greeting` (lines 201-230) asserts `reply_text` awaited once with `GREETING` and `parse_mode=ParseMode.HTML`. `test_start_command_filter_drops_non_owner` proves the silent drop. `test_start_handler_defensive_check_rejects_mismatched_chat` (lines 233-253) proves the in-handler re-check produces zero `reply_text` calls when chat id mismatches.

- **G4 ✓** — Both env vars loaded via pydantic-settings with `validation_alias` (bare names, no `RUNOS_` prefix).
  - Evidence: `runos/config.py:83-99` declares both fields with `validation_alias="TELEGRAM_BOT_TOKEN"` and `validation_alias="TELEGRAM_OWNER_CHAT_ID"`, bypassing the class-wide `env_prefix="RUNOS_"`. Test `test_settings_load_with_both_vars_set` confirms the bare names work (chat id is `int`, token is `SecretStr` scrubbed from `repr`).

- **G5 ✓** — Missing env var produces a clear startup error (no cryptic stack trace).
  - Evidence: `runos/bot/app.py:26-42` `_require_telegram_config` raises `ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md")` when either field is `None`. CLI wrapper in `runos/cli.py:377-381` catches it and prints to red stderr + exit code 1.
  - Smoke (run with truly unset env): `env -i HOME=$HOME PATH=$PATH uv run --project /Users/rossheadington/Projects/RunOS runos bot run` -> exit 1, output: `Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md`. Tests 3 & 4 prove this path.

- **G6 ✓** — `.env.example` documents both vars.
  - Evidence: `grep -c "TELEGRAM_BOT_TOKEN\|TELEGRAM_OWNER_CHAT_ID" .env.example` -> 2. Lines 70-75 document the one-time @BotFather + `getUpdates` flow with cross-reference to `docs/TELEGRAM_BOT.md` and `chmod 600 .env` reminder at line 68.

- **G7 ✓** — `docs/TELEGRAM_BOT.md` walks @BotFather + getUpdates setup.
  - Evidence: 163-line file at `/Users/rossheadington/Projects/RunOS/docs/TELEGRAM_BOT.md`. Step 1 covers @BotFather `/newbot` + token grab (line 25-31). Step 2 covers `chmod 600 .env` and `TELEGRAM_BOT_TOKEN` (line 42). Step 3 covers `getUpdates` -> chat id (line 63). Troubleshooting section covers the 409 Conflict pitfall (line 121). README.md `## Telegram bot (v1.1)` section (line 189) links to it.

- **G8 ✓** — Full pytest suite green; ruff clean.
  - `uv run pytest tests/ -x` -> **406 passed in 1.84s** (398 baseline + 8 new).
  - `uv run ruff check runos/ tests/` -> **All checks passed!**.

## Roadmap Success Criteria coverage

- **SC1 (long-polling worker, `/start` handler, owner-greeting) ✓** — `runos/bot/app.py:60-99` builds the `Application` with `ApplicationBuilder().token(...).concurrent_updates(True).post_init(...).build()`, registers `CommandHandler("start", start_handler, filters=filters.Chat(chat_id=owner_chat_id))`, and `run()` blocks on `app.run_polling()`. `runos/bot/handlers.py:26-28` defines the hardcoded `GREETING` constant; `start_handler` replies via `update.message.reply_text(GREETING, parse_mode=ParseMode.HTML)`.
- **SC2 (non-owner messages silently dropped) ✓** — proven offline by `test_start_command_filter_drops_non_owner` (the `filters.Chat` rejects non-owner Update before the handler runs). Manual second-account smoke is deferred to live testing (documented in `docs/TELEGRAM_BOT.md` Step 5).
- **SC3 (env vars via pydantic-settings, clear startup error, .env.example) ✓** — see G4, G5, G6 above.
- **SC4 (README / docs cover @BotFather + getUpdates + chmod 600) ✓** — see G7. `docs/TELEGRAM_BOT.md` has BotFather (6 refs), getUpdates (3 refs), chmod 600 (3 refs); README has a `## Telegram bot (v1.1)` subsection linking to it.
- **SC5 (structured logging to stdout, sleep/wake graceful, no webhook conflict) ✓** — `runos/bot/app.py:45-57` `_configure_logging` calls `basicConfig(level=INFO, stream=sys.stdout)` and demotes httpx/httpcore to WARNING. PTB's default `stop_signals=(SIGINT, SIGTERM, SIGABRT)` left intact. `_post_init` hook at lines 82-85 calls `delete_webhook(drop_pending_updates=False)` to dodge the 409 Conflict pitfall while preserving offline messages.

## Requirement coverage

- **VOICE-01 ✓** — Single-chat allowlist with silent drop for non-owner. Implemented at the `filters.Chat(chat_id=...)` registration level (`runos/bot/app.py:92-93`) with defensive in-handler re-check (`runos/bot/handlers.py:40-49`). Tests prove both the filter-level drop and the defensive check.
- **VOICE-02 ✓** — Token + chat-id from `.env` via pydantic-settings; clear startup error if missing; `.env.example` documents both. Implemented via `validation_alias` in `runos/config.py:83-99`, error in `_require_telegram_config` (`runos/bot/app.py:26-42`), documented in `.env.example:70-75` and `docs/TELEGRAM_BOT.md`.

## Build/test evidence

- **pytest**: `uv run pytest tests/ -x` -> `406 passed in 1.84s`. All 8 new tests in `tests/test_bot_app.py` pass: settings-load (with/without vars), missing-token error, missing-chat-id error, handler registration + filter shape, filter drops non-owner, owner gets greeting, defensive re-check rejects mismatched chat.
- **ruff**: `uv run ruff check runos/ tests/` -> `All checks passed!`.
- **CLI smoke**:
  - `uv run runos --help | grep -i bot` -> `│ bot                Telegram bot (v1.1): owner-only long-polling worker. │`.
  - `uv run runos bot --help` -> lists `run` subcommand.
  - `env -i HOME=$HOME PATH=$PATH uv run runos bot run` (truly unset env) -> exits 1 with `Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- see docs/TELEGRAM_BOT.md`.
- **Doc grep**:
  - `grep -c "TELEGRAM_BOT_TOKEN\|TELEGRAM_OWNER_CHAT_ID" .env.example` -> 2.
  - `grep -c BotFather docs/TELEGRAM_BOT.md` -> 6.
  - `grep -c getUpdates docs/TELEGRAM_BOT.md` -> 3.
  - `grep -c "chmod 600" docs/TELEGRAM_BOT.md` -> 3.

## Artifacts (Level 1-3 + 4 where applicable)

| Artifact | Exists | Substantive | Wired | Data flows | Status |
|----------|--------|------------|-------|-----------|--------|
| `runos/bot/__init__.py` | ✓ | ✓ (29 lines, exports `GREETING`, `build_application`, `run`, `start_handler`) | ✓ (imported by tests + cli lazy import) | n/a | VERIFIED |
| `runos/bot/app.py` | ✓ | ✓ (`_require_telegram_config`, `_configure_logging`, `build_application`, `run`) | ✓ (used by `runos/cli.py:375`) | n/a | VERIFIED |
| `runos/bot/handlers.py` | ✓ | ✓ (`GREETING`, `start_handler` with HTML parse_mode) | ✓ (registered in app.py:93) | n/a | VERIFIED |
| `runos/config.py` (`telegram_bot_token`, `telegram_owner_chat_id`) | ✓ | ✓ (both fields with `validation_alias`, `SecretStr` + `int`) | ✓ (read by `_require_telegram_config`) | ✓ (Settings -> bot.run -> ApplicationBuilder) | VERIFIED |
| `runos/cli.py::bot_app` + `bot_run_cmd` | ✓ | ✓ (typer group + `run` command with ValueError->exit(1)) | ✓ (`app.add_typer(bot_app, name="bot")`) | n/a | VERIFIED |
| `tests/test_bot_app.py` (8 tests) | ✓ | ✓ (253 lines, 8 distinct tests) | ✓ (all 8 pass) | n/a | VERIFIED |
| `.env.example` (TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_CHAT_ID) | ✓ | ✓ (lines 70-75 with setup walkthrough) | n/a | n/a | VERIFIED |
| `docs/TELEGRAM_BOT.md` | ✓ | ✓ (163 lines, @BotFather + getUpdates + chmod 600 + troubleshooting) | ✓ (referenced from README + .env.example + error message) | n/a | VERIFIED |
| `README.md` (Telegram bot section) | ✓ | ✓ (line 189 `## Telegram bot (v1.1)` subsection with link) | ✓ (links to docs) | n/a | VERIFIED |

## Key links

| From | To | Via | Status |
|------|-----|-----|--------|
| `runos bot run` CLI | `runos.bot.run` | Lazy import `from runos.bot import run as bot_run` (`runos/cli.py:375`) | WIRED |
| `runos.bot.run` | `runos.config.get_settings` | direct call (`runos/bot/app.py:111`) | WIRED |
| `build_application` | `_require_telegram_config` (config -> ValueError) | direct call (`runos/bot/app.py:80`) | WIRED |
| `build_application` | `filters.Chat(chat_id=owner_chat_id)` | `CommandHandler("start", ..., filters=owner_filter)` (`runos/bot/app.py:92-93`) | WIRED |
| `start_handler` reply | `update.message.reply_text(GREETING, parse_mode=ParseMode.HTML)` | `runos/bot/handlers.py:54` | WIRED |
| `_post_init` | 409 Conflict mitigation | `await application.bot.delete_webhook(drop_pending_updates=False)` | WIRED |

## Anti-patterns scan

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| (none in modified files) | No `TODO/FIXME/XXX/HACK/PLACEHOLDER` debt markers found in `runos/bot/`, `runos/cli.py:bot_*`, `runos/config.py` Telegram block, `tests/test_bot_app.py`, `docs/TELEGRAM_BOT.md`. | — | — |

Note: forward-pointing references like "Phase 10 adds voice + text handlers" are documentation, not unresolved debt; they correctly scope the phase.

## Issues
None — all goal-backward checks, requirements, and roadmap success criteria pass with codebase evidence.

## Recommendation
Proceed to Phase 10 (Voice Intake + Local Transcription). The owner-only filter at `filters.Chat(chat_id=owner_chat_id)` and the `bot_data["owner_chat_id"]` defensive re-check are reusable by the voice handler that lands next; the secrets path and CLI surface are stable.

Note: SC2 ("verified by sending from a second Telegram account") is satisfied by the mocked-non-owner-update test path explicitly per the roadmap wording ("or by mocking a non-owner update in tests"). The live second-account smoke is documented in `docs/TELEGRAM_BOT.md` Step 5 for the user to run after wiring their token.
