# Phase 9: Telegram Bot Foundation — Context

**Gathered:** 2026-05-27
**Status:** Ready for planning
**Source:** Inline orchestrator (derived from research + roadmap success criteria; user already endorsed full architecture in conversation)

<domain>
## Phase Boundary

**What this phase delivers:**

- A `python-telegram-bot` v22.x async long-polling worker.
- New `tempo bot run` typer subcommand that starts the worker.
- Two new pydantic-settings fields: `telegram_bot_token: SecretStr` and `telegram_owner_chat_id: int`. Both required (no default); missing → clear startup error. `.env.example` documents both.
- One `/start` handler gated on `filters.Chat(chat_id=settings.telegram_owner_chat_id)`. From the owner: hardcoded greeting. From anyone else: silent drop (`filters.Chat(...)` short-circuits at the dispatcher).
- Structured logging to stdout (suitable for launchd capture in Phase 12).
- Graceful SIGTERM shutdown via PTB's default `stop_signals=(SIGINT, SIGTERM, SIGABRT)`.
- One-time-setup docs (`docs/TELEGRAM_BOT.md`): @BotFather flow, finding the owner chat id via `getUpdates`, `chmod 600 .env` reminder.

**What this phase does NOT deliver (out of scope):**

- Voice message handling (Phase 10 owns).
- Transcription (Phase 10).
- Agent loop (Phase 11).
- launchd plist (Phase 12).
- Multi-user / multi-chat support — single-chat allowlist is by design.
- `/help`, `/new`, or any other slash command beyond `/start` (other commands land in later phases as needed).
- A web UI / admin panel.

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Library + transport
- `python-telegram-bot` v22.x (async). Confirmed in `.planning/research/telegram-bot-research.md` as the right pick; PTB is on `httpx` (already in Tempo's transitive deps), maturity > aiogram for this use case.
- **Long polling** via `Application.run_polling()`. No webhook, no public URL, no TLS, no port forwarding. Personal-volume bot.
- `concurrent_updates=True` enabled at the `Application` level so future voice handlers can process multiple in-flight messages without blocking each other.

### Module layout (new)
- New module: `tempo/bot/__init__.py` (package marker + module-index docstring mirroring `tempo/analysis/__init__.py`).
- New module: `tempo/bot/app.py` — builds the PTB `Application`, registers handlers, runs polling. `build_application(settings) -> Application` + `run() -> None` entrypoints.
- New module: `tempo/bot/handlers.py` — handler functions. For Phase 9: `start_handler` only.
- New module: `tests/test_bot_app.py` — config-load test, allowlist test (using PTB's `Update` builders), `/start` reply test.

### CLI surface
- New typer subcommand group: `tempo bot ...` with `tempo bot run` as its only command in Phase 9. Future phases can add `tempo bot test-token`, etc. Lives in `tempo/cli.py`.
- `tempo bot run` blocks; long-polling worker until SIGTERM/SIGINT. No detach (launchd handles that in Phase 12).

### Config additions
- Add to `tempo/config.py` (`Settings`):
  - `telegram_bot_token: SecretStr | None = Field(default=None, description="Telegram bot token from @BotFather")`
  - `telegram_owner_chat_id: int | None = Field(default=None, description="Owner's Telegram chat id (the only chat allowed to talk to the bot)")`
- Add validator: when `tempo bot run` is invoked, both must be non-None — raise a clear `ConfigError` ("Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env — see docs/TELEGRAM_BOT.md") if either is missing. Settings stay optional so the rest of `tempo` (analyze, journal, sync) keeps working without the bot configured.
- `.env.example` gets the two new keys with comments explaining the @BotFather + `getUpdates` setup path.

### Handler shape
- `start_handler` is the only handler. Reply text is a fixed greeting:
  > `Tempo bot online. Send a voice memo to journal a session, or text for any other request.`
- Reply uses `ParseMode.HTML` (the default for v1.1 per the research, NOT MarkdownV2).
- The chat-id allowlist is applied **at filter level**, not inside the handler:
  ```python
  owner_filter = filters.Chat(chat_id=settings.telegram_owner_chat_id)
  app.add_handler(CommandHandler("start", start_handler, filters=owner_filter))
  ```
  This means non-owner `/start` is silently dropped by the dispatcher — no handler invocation, no log line. (Defensive `update.effective_chat.id == settings.telegram_owner_chat_id` re-check inside the handler is added as a belt-and-braces safety per the research note.)

### Token storage + secrets hygiene
- `.env` already gitignored (Phase 1 invariant).
- `pydantic-settings` `SecretStr` for the token so logs / `repr(settings)` never accidentally print it.
- README docs warn user to `chmod 600 .env` (existing convention from Strava/Garmin token storage).

### Logging
- Standard Python `logging` configured at WARNING for `httpx`/`httpcore` (they're chatty) and INFO for `tempo.bot.*`. Log to stdout (launchd Phase 12 will capture).
- One log line on startup: `Bot started · owner_chat_id=<id> · model=NA · waiting for messages...`
- One log line per `/start` from the owner: `start command received from owner`.

### Testing strategy
- **Unit tests** for the handlers using `python-telegram-bot`'s test utilities — `Update.de_json()` builders + mocked `Bot` for `await update.message.reply_text(...)` assertion.
- **Config tests** — `Settings()` with both vars set works; with one missing the `bot run` path raises `ConfigError`.
- **No live-network tests** in Phase 9. Use PTB's `ApplicationBuilder` with a fake `Bot`. Live smoke-test is a manual step documented in `docs/TELEGRAM_BOT.md`.
- Expect ~6-8 tests; full suite (currently 398) should pass + grow.

### Documentation
- New `docs/TELEGRAM_BOT.md` covering the full one-time setup:
  1. `@BotFather` → `/newbot` → choose username → grab token.
  2. Add `TELEGRAM_BOT_TOKEN=<token>` to `.env`. `chmod 600 .env` if not already.
  3. Send any message to the bot from your phone.
  4. `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` → find `update.message.chat.id` → add `TELEGRAM_OWNER_CHAT_ID=<id>` to `.env`.
  5. `uv run tempo bot run` → expect "Bot started · waiting for messages..." → send `/start` from your phone → expect the greeting back.
  6. Sanity check: send `/start` from a different account (or family member's phone) → expect silence.
- Update README.md with a brief "Telegram bot (v1.1)" section linking to the new doc.

### Privacy / safety
- The token is the keys to the kingdom — defensively scrub from logs (handled by `SecretStr`).
- Owner chat id allowlist means even if the token leaks, only the owner can interact with the bot (the bot just won't reply to anyone else). Note: leak still means attacker could observe traffic to the bot from the owner; treat the token as a real secret regardless.

</decisions>

<canonical_refs>
## Canonical References

### Research (read fully before planning)
- `.planning/research/telegram-bot-research.md` — PTB v22 patterns, allowlist code shape, voice download API, formatting choice, launchd lifecycle, pitfalls.

### Existing patterns to mirror
- `tempo/config.py` — settings shape (`SecretStr`, `Field(description=...)`, validators).
- `tempo/cli.py` — typer subcommand registration pattern (see `garmin login`, `strava auth` as templates).
- `tempo/connectors/factory.py` — "missing credentials" clean-error pattern.
- `tempo/analysis/__init__.py` — module-index docstring style for `tempo/bot/__init__.py`.
- `tests/test_garmin_cli.py` — async CLI test pattern.
- `tests/test_config.py` — Settings test pattern.

</canonical_refs>

<specifics>
- BotFather's bot token format is `<bot_id>:<random_string>` (e.g. `123456789:ABCDEF...`). The `bot_id` prefix matters because PTB uses it for the API URL. No parsing needed — just pass to `ApplicationBuilder().token(...)`.
- `getUpdates` returns an array; the first message from the owner is what we want. The chat id is `result[0].message.chat.id` (an integer, usually positive for private chats).
- PTB v22's `Application.run_polling()` handles graceful shutdown by default — no custom signal handler needed.
- PTB has a 409 Conflict pitfall: if a webhook is set (from a prior experiment) or another poller is running with the same token, `run_polling()` errors out. Add a one-time `await bot.delete_webhook(drop_pending_updates=True)` at startup to be safe.
</specifics>

<deferred>
- `/help`, `/new`, `/verbose` slash commands — wait until they're actually needed in Phases 10–11.
- Persistent conversation state — comes with Phase 11's session-id store.
- Health-check endpoint / heartbeat — comes with Phase 12.
- Multi-user mode — explicitly never (single-user tool by design).
</deferred>

---

*Phase: 09-telegram-bot-foundation*
*Context gathered: 2026-05-27 via inline orchestrator (research + roadmap success criteria + endorsed architecture decisions)*
