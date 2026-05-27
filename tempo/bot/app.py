"""Telegram bot Application builder + ``run`` entrypoint.

Serves VOICE-02 (config wiring + clear startup error when bot creds are missing)
and the registration half of VOICE-01 (owner-only allowlist via
``filters.Chat(chat_id=owner_chat_id)``). The actual blocking long-poll lives
in :func:`run`; the CLI wrapper in plan 09-02 just calls ``run()``.

Phase 11 plan 11-03 extends :func:`build_application`:

* registers the new :func:`text_handler` (non-command text -> agent loop) and
  :func:`new_command_handler` (``/new`` resets the per-chat session);
* stashes ``settings.db_path`` in ``bot_data`` so handlers can open
  short-lived sqlite connections without re-importing :mod:`tempo.config`;
* runs :func:`tempo.db.init_db` in the ``post_init`` hook so migration 0005
  (the ``bot_session`` table) is guaranteed applied before the first handler
  fires, even on a fresh checkout without a prior ``tempo init``;
* validates that the ``claude`` CLI is on ``PATH`` (VOICE-07) and raises a
  :class:`RuntimeError` naming the docs link if it is missing -- no silent
  boot followed by a Telegram-only "command not found".

Importing this module is side-effect-free: building the ``Application`` and
configuring logging only happen when :func:`build_application` / :func:`run`
are called.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from tempo.bot.error_handler import telegram_error_handler
from tempo.bot.handlers import (
    new_command_handler,
    start_handler,
    text_handler,
    voice_handler,
)
from tempo.bot.transcribe import warm_model
from tempo.config import Settings, get_settings
from tempo.db import init_db

logger = logging.getLogger("tempo.bot")

#: User-facing message when ``shutil.which("claude")`` returns ``None`` at
#: startup (VOICE-07). The bot exits cleanly with this in stderr rather than
#: booting and only surfacing the failure on the first agent turn.
CLAUDE_CLI_MISSING_ERROR: str = (
    "Set up the Claude Code CLI before starting the bot -- see docs/TELEGRAM_BOT.md "
    "Phase 11 prerequisites (Node 18+ + `claude login`)."
)


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


def _verify_claude_cli() -> None:
    """Raise :class:`RuntimeError` if ``shutil.which("claude")`` is None (VOICE-07)."""
    path = shutil.which("claude")
    if path is None:
        raise RuntimeError(CLAUDE_CLI_MISSING_ERROR)
    logger.info("Claude Code CLI found at %s", path)


def _sweep_voice_cache(voice_cache_dir: Path, retention_days: int) -> int:
    """Delete voice files older than ``retention_days`` from ``voice_cache_dir``.

    Phase 12 startup sweep: when ``VOICE_RETENTION_DAYS > 0``, the per-handler
    cleanup (which only fires on retention=0) leaves files on disk. The bot
    re-sweeps on every startup so a long-running bot that survives many
    restarts cannot accumulate unbounded audio. With ``retention_days == 0``
    or a missing cache dir the sweep is a no-op (the per-handler cleanup is
    already doing the immediate-delete work).

    Returns the count of files deleted, for the startup log line.
    """
    if retention_days <= 0:
        return 0
    if not voice_cache_dir.is_dir():
        return 0
    cutoff = time.time() - (retention_days * 86400)
    deleted = 0
    for entry in voice_cache_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError as exc:  # noqa: BLE001 - file vanished mid-sweep is fine
            logger.debug("voice sweep: stat failed for %s: %s", entry.name, exc)
            continue
        if mtime < cutoff:
            try:
                entry.unlink(missing_ok=True)
                deleted += 1
            except OSError as exc:  # noqa: BLE001 - best-effort
                logger.warning("voice sweep: unlink failed for %s: %s", entry.name, exc)
    return deleted


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
    VOICE-07: verifies the ``claude`` CLI is on PATH BEFORE any Telegram
    traffic starts; raises :class:`RuntimeError` with
    :data:`CLAUDE_CLI_MISSING_ERROR` if not.

    Calls a ``post_init`` hook that runs ``delete_webhook(drop_pending_updates=False)``
    before polling starts -- this is the documented PTB pitfall fix for 409
    Conflict when a webhook was previously configured on the bot (research.md
    "Pitfalls"). Pending updates are NOT dropped because offline messages
    (e.g. memos sent while the laptop slept) should still be processed. The
    same hook also calls :func:`tempo.db.init_db` to ensure migration 0005
    (the ``bot_session`` table) is applied on a fresh checkout, and warms the
    Whisper model so the first voice memo does not pay the load cost.

    ``concurrent_updates=True`` so Phase 10's voice handlers (which can take
    several seconds to transcribe) will not block each other. Safe here because
    no ``ConversationHandler`` is in use.
    """
    # VOICE-07: fail loudly at startup if the Claude Code CLI is missing.
    # Done BEFORE _require_telegram_config so the error message is the one
    # the user should fix first (no point asking for a token they don't
    # have a CLI to use yet).
    _verify_claude_cli()

    token, owner_chat_id = _require_telegram_config(settings)

    async def _post_init(application: Application) -> None:
        # Pitfall fix: clears any stale webhook so getUpdates doesn't 409.
        # Pending updates are preserved so offline messages still get handled.
        await application.bot.delete_webhook(drop_pending_updates=False)
        # Phase 11 (Plan 11-03): ensure migration 0005 (bot_session) is
        # applied before any handler runs. ``init_db`` is idempotent --
        # already-applied migrations are skipped -- and a no-op overhead
        # at every startup is well worth the guarantee on a fresh checkout
        # where the user runs ``tempo bot run`` without ``tempo init`` first.
        conn = await asyncio.to_thread(init_db, settings.db_path)
        conn.close()
        # VOICE-06: warm the WhisperModel ONCE so the first voice memo does
        # not pay the multi-second model load. ``asyncio.to_thread`` because
        # the WhisperModel constructor blocks (file I/O + native init) and
        # would otherwise stall PTB's event loop.
        await asyncio.to_thread(warm_model, settings)
        logger.info("Whisper model loaded and ready")
        # Phase 12: log the agent cwd. The launchd plist sets WorkingDirectory
        # to the project root; printing it at startup makes it trivial to
        # debug "claude wrote files to /" or other cwd surprises. Also
        # surfaces the resolved data dir for the same reason.
        logger.info("agent cwd = %s", Path.cwd().resolve())
        logger.info("data_dir = %s", settings.data_dir)
        # Phase 12 voice-retention startup sweep: for retention > 0, delete
        # cached voice files older than the policy. Bounded by retention_days
        # so a long-running bot cannot accumulate unbounded audio across
        # restarts. No-op when retention_days == 0 (the per-handler cleanup
        # already does the immediate-delete work).
        deleted = await asyncio.to_thread(
            _sweep_voice_cache, settings.voice_cache_dir, settings.voice_retention_days
        )
        if settings.voice_retention_days > 0:
            logger.info(
                "voice cache startup sweep: retention=%d days, deleted=%d",
                settings.voice_retention_days,
                deleted,
            )

    app: Application = (
        ApplicationBuilder().token(token).concurrent_updates(True).post_init(_post_init).build()
    )
    app.bot_data["owner_chat_id"] = owner_chat_id
    # Stash the validated Settings so Plan 10-02's voice handler can read
    # ``settings.voice_cache_dir`` without re-running ``get_settings()`` (and
    # without having to import the module from a handler).
    app.bot_data["settings"] = settings
    # Phase 11 (Plan 11-03): handlers open per-call sqlite connections via
    # ``tempo.db.connect(db_path)``. Stashing the path here means the
    # handler does not need to re-import ``tempo.config``.
    app.bot_data["db_path"] = settings.db_path

    owner_filter = filters.Chat(chat_id=owner_chat_id)
    app.add_handler(CommandHandler("start", start_handler, filters=owner_filter))
    # Phase 11 (Plan 11-03): /new resets the per-chat session id. Registered
    # BEFORE the generic TEXT handler -- ``filters.COMMAND`` and
    # ``~filters.COMMAND`` already make the dispatch unambiguous, but the
    # ordering is a defensive convention.
    app.add_handler(CommandHandler("new", new_command_handler, filters=owner_filter))
    # VOICE-03/04/06: owner-only voice intake. ``filters.VOICE & owner_filter``
    # means non-owner voice memos are silently dropped at the dispatcher,
    # same as non-owner /start (research/telegram-bot-research.md
    # "Single-chat allowlist").
    app.add_handler(MessageHandler(filters.VOICE & owner_filter, voice_handler))
    # Phase 11 (Plan 11-03): owner-only non-command text -> agent loop.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, text_handler))

    # Phase 12 (Plan 12-02): top-level error boundary (VOICE-12). Any
    # exception raised by a registered handler that the handler itself does
    # not catch routes through ``telegram_error_handler``: log full traceback
    # + send a fixed "something went wrong" reply to the offending chat +
    # never re-raise. Combined with the launchd KeepAlive plist (Plan 12-01),
    # this means a single bad message can never take the worker down.
    app.add_error_handler(telegram_error_handler)

    logger.info(
        "Bot configured -- owner_chat_id=%d, concurrent_updates=True, "
        "voice_handler=registered, text_handler=registered, new_command_handler=registered, "
        "error_handler=registered",
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
