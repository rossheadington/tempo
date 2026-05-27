"""Telegram bot handlers.

Phase 9 defined :func:`start_handler` (the ``/start`` reply); Phase 10 plan
10-02 adds :func:`voice_handler` -- the owner-only voice-memo intake that
ties the warmed faster-whisper singleton (Plan 10-01's
:mod:`tempo.bot.transcribe`) to the Telegram dispatcher.

Every handler in this module is gated twice: first at registration time by
``filters.Chat(chat_id=owner_chat_id)`` (set up in :mod:`tempo.bot.app`), and
second in-handler by re-checking ``update.effective_chat.id`` against the
``owner_chat_id`` stashed in ``application.bot_data``. The defence-in-depth
matters because a misconfigured catch-all handler added later could otherwise
silently see every chat that messages the bot (VOICE-01 allowlist).
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from tempo.bot.transcribe import transcribe_file
from tempo.config import Settings

logger = logging.getLogger(__name__)

#: Fixed greeting reply for ``/start``. Plain text (no HTML special chars) but
#: sent with :data:`ParseMode.HTML` so Phase 10 handlers can format consistently.
GREETING: str = (
    "Tempo bot online. Send a voice memo to journal a session, or text for any other request."
)

#: Telegram bot API hard cap on getFile downloads (VOICE-03). Voice memos
#: larger than this are rejected with a clear user-facing reply BEFORE any
#: network call so a doomed ``getFile`` never fires. At Telegram's ~16 kbps
#: Opus encoding, 20 MB is ~2.5 hours of voice -- guard is for safety, not
#: the common case.
MAX_VOICE_BYTES: int = 20 * 1024 * 1024

#: User-facing reply for the 20 MB rejection (VOICE-03). Fixed string so we
#: never accidentally leak file-size / chat-id specifics in the reply, and
#: tests can assert against it directly.
OVERSIZED_REPLY: str = (
    "Sorry -- that voice memo is over Telegram's 20 MB bot API limit. "
    "Try a shorter recording or split it."
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


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only voice-memo handler (VOICE-03/04/06).

    Flow: defensive chat-id re-check -> 20 MB guard -> download to
    ``<voice_cache_dir>/<msg_id>-<file_uid>.ogg`` -> transcribe via the
    warmed singleton in a worker thread (so the event loop stays responsive)
    -> reply with the transcript in HTML-escaped italics.

    Per 10-CONTEXT.md, there is intentionally no top-level error handler in
    this phase -- exceptions propagate up to PTB's default error handler.
    Phase 12 wraps the full pipeline in a "something went wrong" boundary.
    """
    # Defence-in-depth chat-id re-check (mirrors start_handler).
    expected_owner_id = context.application.bot_data.get("owner_chat_id")
    if (
        update.effective_chat is None
        or expected_owner_id is None
        or update.effective_chat.id != expected_owner_id
    ):
        return
    if update.message is None or update.message.voice is None:
        return

    voice = update.message.voice

    # VOICE-03: pre-download 20 MB guard. Check file_size BEFORE calling
    # get_file() so an oversized memo never triggers a doomed network call.
    # If file_size is None (rare; Telegram returns it as optional), proceed
    # past the guard per 10-CONTEXT.md -- "let get_file() raise, that's a
    # Telegram bug, not our problem".
    if voice.file_size is not None and voice.file_size > MAX_VOICE_BYTES:
        logger.info("voice rejected: file_size=%d > 20 MB cap", voice.file_size)
        await update.message.reply_text(OVERSIZED_REPLY, parse_mode=ParseMode.HTML)
        return

    # VOICE-04: deterministic, collision-free filename under the gitignored
    # voice cache dir. ``message_id`` is per-chat unique; ``file_unique_id``
    # is stable across Telegram CDN servers. Combo is collision-free.
    settings: Settings = context.application.bot_data["settings"]
    voice_cache_dir = settings.voice_cache_dir
    voice_cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target_path: Path = voice_cache_dir / f"{update.message.message_id}-{voice.file_unique_id}.ogg"

    tg_file = await voice.get_file()
    await tg_file.download_to_drive(custom_path=target_path)

    # VOICE-06: transcribe via the WARMED singleton. ``asyncio.to_thread``
    # keeps the event loop responsive -- ``faster_whisper.WhisperModel.transcribe``
    # holds the GIL and runs for seconds even on small.en.
    start = time.monotonic()
    transcript = await asyncio.to_thread(transcribe_file, target_path)
    wall_s = time.monotonic() - start

    logger.info(
        "transcribed %s -- audio_s=%s wall_s=%.2f model=%s",
        target_path.name,
        voice.duration if voice.duration is not None else "?",
        wall_s,
        settings.whisper_model_name,
    )

    # Empty transcript still goes back so the user sees the pipeline ran.
    # ``html.escape`` handles untrusted transcript text -- &/</> in
    # speech-to-text output would otherwise be parsed as HTML by Telegram
    # and trigger a BadRequest (or, worse, render as markup).
    reply_body = html.escape(transcript) if transcript else "(no speech detected)"
    await update.message.reply_text(
        f"<i>{reply_body}</i>",
        parse_mode=ParseMode.HTML,
    )
