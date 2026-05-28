"""Top-level Telegram bot error handler (VOICE-12).

Registered on the python-telegram-bot :class:`Application` via
``add_error_handler`` in :func:`runos.bot.app.build_application`. PTB
routes any exception raised by a handler (and not caught by it) through
this callback before the worker would otherwise log it as an "uncaught
task exception" and move on.

Contract (VOICE-12, locked in 12-CONTEXT.md):

1. **Log the full traceback** via :func:`logging.exception` so the
   developer sees what actually failed in the launchd-managed log file
   (the "structured error" half of the requirement).
2. **Send a brief acknowledgement** to the chat that triggered the
   failure -- :data:`ERROR_REPLY` -- so the user knows the bot is alive
   but their last message didn't go through.
3. **Never re-raise.** A handler crash already aborted one message; a
   crash in the error handler itself would take down the worker process
   (defeating the whole point of VOICE-12 and launchd KeepAlive). Reply
   failures are swallowed and logged at ERROR so the next launchd log
   tail shows both the original crash AND the reply failure.

Phase 11's :func:`runos.bot.handlers._run_agent_turn` still maps
:class:`AgentInvocationError` to its own friendly reply (the "Claude
Code isn't running" path); that catch fires BEFORE the exception would
ever reach this top-level handler. Everything else -- a sqlite error
opening the session store, a faster-whisper crash, an OSError on the
voice cache write, a python-telegram-bot internal failure -- routes
through here.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

#: Fixed user-facing reply for the top-level error boundary. Plain text
#: (no HTML special chars) so it can be sent without a parse mode without
#: any escaping concerns. Tests assert against this constant directly so
#: an accidental rewording fails a test.
ERROR_REPLY: str = "Sorry -- something went wrong on my end. Check the logs."


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top-level error boundary for every registered Telegram bot handler.

    PTB calls this with ``update`` set to the offending :class:`Update`
    when one was available (the handler crashed while processing a real
    message), or to a non-Update sentinel otherwise (e.g. a job queue
    callback or an internal updater error). ``context.error`` carries
    the exception that fired.

    Behaviour:

    * Always logs the exception at ERROR with the full traceback so the
      developer sees what happened.
    * If ``update`` is an :class:`Update` AND ``update.effective_chat``
      is populated, sends :data:`ERROR_REPLY` to that chat via
      ``context.bot.send_message`` (no parse_mode -- the reply is plain
      text and safe to send raw).
    * Any exception from the reply itself is caught and logged at ERROR;
      the function NEVER re-raises. A bad reply must not crash the worker.

    Args:
        update: the :class:`Update` whose handler raised, or a non-Update
            object for jobqueue / internal failures.
        context: PTB context, including ``context.error`` and ``context.bot``.
    """
    err: BaseException | None = getattr(context, "error", None)
    # Use logger.exception when we actually have an exception object so the
    # traceback is attached; fall back to logger.error for the unlikely case
    # of an error handler firing without context.error populated.
    if err is not None:
        # ``exc_info=err`` is the explicit form -- works whether or not we are
        # currently inside an ``except`` block.
        logger.error("Bot handler crashed: %r", err, exc_info=err)
    else:
        logger.error("Bot handler crashed (no exception attached to context)")

    # Determine the chat to reply to. Both checks matter:
    # * isinstance(update, Update) excludes the jobqueue / internal-error path
    # * effective_chat may be None even on a real Update (rare; defensive)
    chat_id: int | None = None
    if isinstance(update, Update):
        effective_chat = update.effective_chat
        if effective_chat is not None:
            chat_id = effective_chat.id

    if chat_id is None:
        # Nothing to reply to -- internal failure, jobqueue crash, or an
        # Update without a chat. The log line above is the only artifact.
        return

    # Best-effort reply. Any failure here is swallowed -- if the bot can't
    # talk to Telegram, retrying or re-raising here will only break the
    # worker; the launchd log already has the original traceback.
    bot: Any = getattr(context, "bot", None)
    if bot is None:
        logger.error("error reply skipped: context.bot is None (chat=%d)", chat_id)
        return
    try:
        await bot.send_message(chat_id=chat_id, text=ERROR_REPLY)
    except Exception as reply_exc:  # noqa: BLE001 - intentional broad swallow
        # We are the last line of defence; nothing higher up will catch this
        # if we re-raise. Log + return so PTB's run loop continues serving
        # subsequent messages.
        logger.error(
            "error reply failed: chat=%d original=%r reply_error=%r",
            chat_id,
            err,
            reply_exc,
        )
