"""Telegram bot Application builder + ``run`` entrypoint.

Serves VOICE-02 (config wiring + clear startup error when bot creds are missing)
and the registration half of VOICE-01 (owner-only allowlist via
``filters.Chat(chat_id=owner_chat_id)``). The actual blocking long-poll lives
in :func:`run`; the CLI wrapper in plan 09-02 just calls ``run()``.

Importing this module is side-effect-free: building the ``Application`` and
configuring logging only happen when :func:`build_application` / :func:`run`
are called.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import Application, ApplicationBuilder, CommandHandler, filters

from tempo.bot.handlers import start_handler
from tempo.bot.transcribe import warm_model
from tempo.config import Settings, get_settings

logger = logging.getLogger("tempo.bot")


def _require_telegram_config(settings: Settings) -> tuple[str, int]:
    """Return ``(token, owner_chat_id)`` or raise :class:`ValueError`.

    VOICE-02: missing either env var raises a single, actionable error that
    names BOTH env-var names so the user knows exactly what to set. The CLI
    in plan 09-02 catches this and converts it to a clean exit. Matches the
    existing pattern in :mod:`tempo.connectors.factory`.
    """
    if settings.telegram_bot_token is None or settings.telegram_owner_chat_id is None:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID in your .env -- "
            "see docs/TELEGRAM_BOT.md"
        )
    return (
        settings.telegram_bot_token.get_secret_value(),
        int(settings.telegram_owner_chat_id),
    )


def _configure_logging() -> None:
    """Bot-only logging setup. Called from :func:`run`, never at import time.

    INFO-level lines for tempo + python-telegram-bot; httpx/httpcore demoted
    to WARNING so each long-poll cycle does not spam the log with HTTP chatter.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_application(settings: Settings) -> Application:
    """Build a configured python-telegram-bot :class:`Application`.

    VOICE-02: validates the two Telegram env vars (raises a clear
    :class:`ValueError` naming both names if either is missing).
    VOICE-01: registers every handler behind ``filters.Chat(chat_id=...)`` so
    the bot only ever sees messages from the owner chat. The owner chat id is
    also stashed in ``application.bot_data["owner_chat_id"]`` so handlers can
    do a defensive in-handler re-check without re-reading config.

    Calls a ``post_init`` hook that runs ``delete_webhook(drop_pending_updates=False)``
    before polling starts -- this is the documented PTB pitfall fix for 409
    Conflict when a webhook was previously configured on the bot (research.md
    "Pitfalls"). Pending updates are NOT dropped because offline messages
    (e.g. memos sent while the laptop slept) should still be processed.

    ``concurrent_updates=True`` so Phase 10's voice handlers (which can take
    several seconds to transcribe) will not block each other. Safe here because
    no ``ConversationHandler`` is in use.
    """
    token, owner_chat_id = _require_telegram_config(settings)

    async def _post_init(application: Application) -> None:
        # Pitfall fix: clears any stale webhook so getUpdates doesn't 409.
        # Pending updates are preserved so offline messages still get handled.
        await application.bot.delete_webhook(drop_pending_updates=False)
        # VOICE-06: warm the WhisperModel ONCE so the first voice memo does
        # not pay the multi-second model load. ``asyncio.to_thread`` because
        # the WhisperModel constructor blocks (file I/O + native init) and
        # would otherwise stall PTB's event loop.
        await asyncio.to_thread(warm_model, settings)
        logger.info("Whisper model loaded and ready")

    app: Application = (
        ApplicationBuilder().token(token).concurrent_updates(True).post_init(_post_init).build()
    )
    app.bot_data["owner_chat_id"] = owner_chat_id
    # Stash the validated Settings so Plan 10-02's voice handler can read
    # ``settings.voice_cache_dir`` without re-running ``get_settings()`` (and
    # without having to import the module from a handler).
    app.bot_data["settings"] = settings

    owner_filter = filters.Chat(chat_id=owner_chat_id)
    app.add_handler(CommandHandler("start", start_handler, filters=owner_filter))

    logger.info(
        "Bot configured -- owner_chat_id=%d, concurrent_updates=True",
        owner_chat_id,
    )
    return app


def run() -> None:
    """Configure logging, build the application, and block on long-polling.

    VOICE-02 entrypoint. PTB's :meth:`Application.run_polling` handles
    SIGINT/SIGTERM/SIGABRT by default -- we deliberately do NOT pass
    ``stop_signals=()`` so Ctrl-C and launchd's SIGTERM both shut the bot
    down cleanly.
    """
    _configure_logging()
    settings = get_settings()
    app = build_application(settings)
    logger.info("Bot started -- waiting for messages...")
    app.run_polling()
