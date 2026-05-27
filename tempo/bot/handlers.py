"""Telegram bot handlers.

Phase 9 only defines :func:`start_handler` (the ``/start`` reply). Phase 10
adds the voice-memo + text handlers on top of this same allowlist wiring.

Every handler in this module is gated twice: first at registration time by
``filters.Chat(chat_id=owner_chat_id)`` (set up in :mod:`tempo.bot.app`), and
second in-handler by re-checking ``update.effective_chat.id`` against the
``owner_chat_id`` stashed in ``application.bot_data``. The defence-in-depth
matters because a misconfigured catch-all handler added later could otherwise
silently see every chat that messages the bot (VOICE-01 allowlist).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

#: Fixed greeting reply for ``/start``. Plain text (no HTML special chars) but
#: sent with :data:`ParseMode.HTML` so Phase 10 handlers can format consistently.
GREETING: str = (
    "Tempo bot online. Send a voice memo to journal a session, or text for any other request."
)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to ``/start`` from the owner chat with the fixed greeting.

    Serves VOICE-01 (owner-only allowlist) with a defensive in-handler chat-id
    re-check on top of the registration-time ``filters.Chat`` gate. The
    expected owner chat id is read from ``context.application.bot_data`` (set
    by :func:`tempo.bot.app.build_application`) so the handler does not need
    to re-import :mod:`tempo.config`.
    """
    expected_owner_id = context.application.bot_data.get("owner_chat_id")
    if (
        update.effective_chat is None
        or expected_owner_id is None
        or update.effective_chat.id != expected_owner_id
    ):
        # Defence-in-depth: the filters.Chat gate should already have blocked
        # this, but if a misconfiguration ever lets a foreign chat through,
        # we drop it silently rather than confirming the bot's existence.
        return

    logger.info("start command received from owner")
    if update.message is None:
        return
    await update.message.reply_text(GREETING, parse_mode=ParseMode.HTML)
