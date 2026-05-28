"""Telegram bot handlers.

Phase 9 defined :func:`start_handler` (the ``/start`` reply); Phase 10
plan 10-02 added :func:`voice_handler` -- the owner-only voice-memo intake
that ties the warmed faster-whisper singleton (Plan 10-01's
:mod:`runos.bot.transcribe`) to the Telegram dispatcher. Phase 11 plan
11-03 reworks :func:`voice_handler`'s post-transcription tail, adds the
matching :func:`text_handler` (non-command messages -> agent), the
:func:`clear_command_handler` (``/clear`` resets the per-chat session), and a
private :func:`_keep_typing` helper that refreshes Telegram's typing
indicator while ``run_turn`` is in flight.

Every handler in this module is gated twice: first at registration time by
``filters.Chat(chat_id=owner_chat_id)`` (set up in :mod:`runos.bot.app`), and
second in-handler by re-checking ``update.effective_chat.id`` against the
``owner_chat_id`` stashed in ``application.bot_data``. The defence-in-depth
matters because a misconfigured catch-all handler added later could otherwise
silently see every chat that messages the bot (VOICE-01 allowlist).
"""

from __future__ import annotations

import asyncio
import html
import logging
import sqlite3
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from runos.bot.agent import (
    AgentInvocationError,
    format_for_telegram,
    run_turn,
)
from runos.bot.sessions import (
    get_or_create_session,
    reset_session,
    save_session,
)
from runos.bot.transcribe import transcribe_file
from runos.config import Settings
from runos.db import connect

logger = logging.getLogger(__name__)

#: Fixed greeting reply for ``/start``. Plain text (no HTML special chars) but
#: sent with :data:`ParseMode.HTML` so Phase 10 handlers can format consistently.
GREETING: str = (
    "RunOS bot online. Send a voice memo to journal a session, or text for any other request."
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

#: Fixed reply when the Claude Agent SDK cannot reach the ``claude`` CLI
#: (VOICE-07). No backticks so HTML quoting is trivial. Tests assert
#: against this constant directly.
MISSING_CLI_REPLY: str = "Claude Code isn't running. Try claude login in a terminal."

#: Fixed reply for ``/clear``: the previous session id has been deleted from
#: ``bot_session`` and the next message will start a fresh Claude Code session.
CLEAR_SESSION_REPLY: str = "Cleared. Next message starts a fresh Claude Code session."

#: Fixed reply when the voice transcript is empty (Whisper detected no
#: speech). The agent is NOT called for empty transcripts -- we just tell
#: the user the pipeline ran but found nothing.
EMPTY_TRANSCRIPT_REPLY: str = "<i>(no speech detected)</i>"

#: Typing-indicator refresh interval. Telegram's TYPING ChatAction lasts ~5s
#: server-side; refreshing at 4s leaves headroom so there is no gap in the
#: indicator while ``run_turn`` is in flight.
_TYPING_REFRESH_S: float = 4.0


def _cleanup_voice_file(path: Path, retention_days: int) -> None:
    """Delete a transcribed voice file when retention=0; otherwise leave it.

    Phase 12 voice-retention policy (VOICE_RETENTION_DAYS):

    * ``retention_days == 0`` (default): delete the ``.ogg`` immediately after
      the transcript flows to the agent. Privacy-safe: the audio is never
      retained on disk after the text has been extracted.
    * ``retention_days > 0``: leave the file in place; the startup sweep in
      :func:`runos.bot.app._post_init` deletes anything older than that many
      days. This lets the operator keep recent memos for debugging Whisper
      misfires without committing to permanent retention.

    Idempotent: ``missing_ok=True`` so a file already cleaned up by a sibling
    handler (or by the startup sweep racing with this call) is not an error.
    """
    if retention_days != 0:
        return
    try:
        path.unlink(missing_ok=True)
        logger.debug("voice file deleted (retention=0): %s", path.name)
    except OSError as exc:  # noqa: BLE001 - log + swallow; deletion is best-effort
        logger.warning("voice file cleanup failed for %s: %s", path.name, exc)


async def _keep_typing(chat: object) -> None:
    """Refresh Telegram's TYPING indicator until the task is cancelled.

    Loops :meth:`chat.send_action(ChatAction.TYPING)` every
    :data:`_TYPING_REFRESH_S` seconds. Cancellation is the only exit path:
    callers must wrap this in :func:`asyncio.create_task` + ``task.cancel()``
    inside a ``try/finally`` so a slow ``run_turn`` shows continuous typing.

    The caller is expected to absorb the resulting :class:`asyncio.CancelledError`
    via ``await asyncio.gather(task, return_exceptions=True)`` so the cancellation
    does not surface as an uncaught task exception.
    """
    while True:
        await chat.send_action(ChatAction.TYPING)  # type: ignore[attr-defined]
        await asyncio.sleep(_TYPING_REFRESH_S)


def _format_cost(cost_usd: float | None) -> str:
    """Render the per-turn cost for the INFO log line.

    Claude-subscription users see ``cost_usd is None`` on every turn because
    the SDK does not surface a per-turn cost figure for them; we log
    ``"subscription"`` in that case rather than ``$0.0000`` so the log line
    is honest about the unknown.
    """
    if cost_usd is None:
        return "subscription"
    return f"${cost_usd:.4f}"


async def _run_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    chat_id: int,
) -> None:
    """Shared post-transcription / post-text pipeline (VOICE-07/08/09/10/13).

    1. Open a short-lived sqlite connection via :func:`runos.db.connect`.
    2. Resolve the prior session id (within the 4h window) or ``None``.
    3. Start the typing-indicator keepalive task.
    4. Await :func:`run_turn` with the prompt and resolved session id.
    5. Cancel the keepalive (absorbing CancelledError via ``gather``).
    6. Persist the resolved session id (UPSERT semantics in Plan 11-01).
    7. Split the reply via :func:`format_for_telegram` and send each chunk
       with :data:`ParseMode.HTML`.
    8. Log the per-turn token / cost / wall-clock line at INFO.

    :class:`AgentInvocationError` is caught and mapped to a single reply
    using :data:`MISSING_CLI_REPLY`; no other exception types are caught
    here (Phase 12 owns the top-level error boundary per 11-CONTEXT.md
    <decisions>).
    """
    if update.effective_chat is None or update.message is None:
        return

    db_path: Path = context.application.bot_data["db_path"]
    conn = connect(db_path)
    try:
        session_id = get_or_create_session(conn, chat_id)

        # First TYPING ping before the keepalive task spins up so the
        # indicator shows immediately, not after the first 4s sleep.
        await update.effective_chat.send_action(ChatAction.TYPING)
        typing_task = asyncio.create_task(_keep_typing(update.effective_chat))

        # 11-03 PLAN: passes Path.cwd() because the launchd plist (Phase 12)
        # will set WorkingDirectory to the RunOS project root. Until then,
        # `runos bot run` is launched from the repo root by convention.
        try:
            turn = await run_turn(prompt, session_id, cwd=Path.cwd())
        except AgentInvocationError:
            logger.warning(
                "agent invocation failed -- claude CLI missing or unauthed (chat=%d)",
                chat_id,
            )
            await update.message.reply_text(
                MISSING_CLI_REPLY,
                parse_mode=ParseMode.HTML,
            )
            return
        finally:
            typing_task.cancel()
            # gather(..., return_exceptions=True) absorbs the CancelledError
            # so it doesn't surface as an uncaught task exception.
            await asyncio.gather(typing_task, return_exceptions=True)

        save_session(conn, chat_id, turn.session_id)

        # Guard: a Claude Code turn that ends on a tool call (no trailing
        # assistant text) yields an empty string. Telegram rejects empty
        # messages with BadRequest("Message text is empty"). Reply with a
        # short placeholder so the user knows the turn ran but produced
        # no spoken response.
        chunks = [c for c in format_for_telegram(turn.text) if c.strip()]
        if not chunks:
            chunks = ["(agent finished without a reply)"]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        logger.info(
            "agent turn · chat=%d · session=%s · tokens_in=%d · tokens_out=%d "
            "· cost=%s · wall=%.2fs",
            chat_id,
            turn.session_id[:8],
            turn.tokens_in,
            turn.tokens_out,
            _format_cost(turn.cost_usd),
            turn.duration_s,
        )
    finally:
        conn.close()


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to ``/start`` from the owner chat with the fixed greeting.

    Serves VOICE-01 (owner-only allowlist) with a defensive in-handler chat-id
    re-check on top of the registration-time ``filters.Chat`` gate. The
    expected owner chat id is read from ``context.application.bot_data`` (set
    by :func:`runos.bot.app.build_application`) so the handler does not need
    to re-import :mod:`runos.config`.
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
    """Owner-only voice-memo handler (VOICE-03/04/06/07/08/09/10/13).

    Flow: defensive chat-id re-check -> 20 MB guard -> download to
    ``<voice_cache_dir>/<msg_id>-<file_uid>.ogg`` -> transcribe via the
    warmed singleton in a worker thread (so the event loop stays responsive)
    -> hand the transcript to the Claude Agent loop via :func:`_run_agent_turn`
    -> reply with the agent's HTML-formatted answer (possibly multi-chunk).

    Phase 10 used to reply with the raw transcript in italics; Phase 11
    plan 11-03 removes that echo. The transcript is still logged at INFO
    for developer visibility but is no longer sent to Telegram. Empty
    transcripts (Whisper detected no speech) short-circuit the agent call
    and reply with :data:`EMPTY_TRANSCRIPT_REPLY`.

    Per 11-CONTEXT.md ``<decisions>`` "error handling (LIGHT in Phase 11)",
    only :class:`AgentInvocationError` is mapped to a friendly reply here;
    every other exception propagates to PTB's default handler (Phase 12 will
    wrap the full pipeline in a top-level "something went wrong" boundary).
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

    # Wrap the entire post-target-path flow in a single try/finally so the
    # voice cache file is cleaned up under VOICE_RETENTION_DAYS=0 even if
    # download_to_drive or transcribe_file raises mid-write. Previously the
    # cleanup only ran inside the agent-turn finally, so a download failure
    # (or transcription crash) leaked a partial .ogg on disk -- violating
    # the privacy-safe default. _cleanup_voice_file is missing_ok=True so a
    # never-written target_path is fine.
    try:
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

        # Empty / whitespace-only transcript: skip the agent and tell the user
        # the pipeline ran but found nothing. Don't burn an agent turn on it.
        if not transcript or not transcript.strip():
            await update.message.reply_text(
                EMPTY_TRANSCRIPT_REPLY,
                parse_mode=ParseMode.HTML,
            )
            return

        # Log the raw transcript at INFO so the developer can still see what
        # Whisper produced.
        logger.info("voice transcript (chat=%d): %s", update.effective_chat.id, transcript)

        # Echo the transcript back to the user FIRST (before the agent turn,
        # which can take 10-30s) so they can confirm Whisper heard them right
        # -- and have a written record without scrolling up past the agent's
        # reply. Italics keep it visually distinct from agent text.
        await update.message.reply_text(
            f"<i>📝 Heard: {html.escape(transcript, quote=False)}</i>",
            parse_mode=ParseMode.HTML,
        )

        chat_id = update.effective_chat.id
        await _run_agent_turn(update, context, transcript, chat_id)
    finally:
        # Phase 12 voice-retention policy: delete immediately when
        # VOICE_RETENTION_DAYS=0 (the privacy-safe default). Single point of
        # cleanup covering every exit path -- success, empty-transcript,
        # download failure, transcription crash, agent turn failure.
        _cleanup_voice_file(target_path, settings.voice_retention_days)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only text-message handler (VOICE-07/08/09/10/13).

    Same shape as the post-transcription half of :func:`voice_handler`, but
    the prompt is :attr:`update.message.text` directly -- no Whisper step.
    Registered behind ``filters.TEXT & ~filters.COMMAND & filters.Chat(owner)``
    so ``/clear`` and ``/start`` route to their own CommandHandlers.

    Empty / whitespace-only text is dropped silently with a DEBUG log: the
    TEXT filter already excludes Voice/Photo/Sticker etc., so anything here
    that fails the strip() check is almost certainly a quirk (e.g. an empty
    edit) we don't want to waste an agent turn on.
    """
    expected_owner_id = context.application.bot_data.get("owner_chat_id")
    if (
        update.effective_chat is None
        or expected_owner_id is None
        or update.effective_chat.id != expected_owner_id
    ):
        return
    if update.message is None or update.message.text is None:
        return

    prompt = update.message.text
    if not prompt.strip():
        logger.debug("text_handler: empty / whitespace-only message dropped")
        return

    chat_id = update.effective_chat.id
    await _run_agent_turn(update, context, prompt, chat_id)


#: Reply when the user runs ``/sync`` and at least one source ran successfully.
#: ``{lines}`` is replaced with one line per source result, in the same shape
#: as ``runos sync`` CLI output.
SYNC_REPLY_PREFIX: str = "<b>Sync complete</b>\n"

#: Reply when ``/sync`` was triggered but Strava credentials are missing -- the
#: ``ValueError`` from ``pipeline.run_full_sync`` is mapped to this short line.
SYNC_CONFIG_ERROR: str = (
    "Sync skipped -- Strava credentials missing. Run <code>runos strava auth</code> "
    "in a terminal first."
)


async def sync_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only ``/sync`` command: run Strava + isolated Garmin sync (VOICE-08 / GRMN-03).

    Mirrors the ``runos sync`` CLI: Strava is authoritative; Garmin is wrapped
    in a try/except inside the pipeline so a 429 / breakage logs+skips rather
    than blocks the run. Reports per-source status back to the chat as an
    HTML reply. Synchronous work (HTTP + SQLite) runs in
    ``asyncio.to_thread`` so the event loop stays responsive while the pull
    is in flight. Typing indicator keeps the user informed -- a fresh
    Strava + Garmin pull can take 30-90s.
    """
    expected_owner_id = context.application.bot_data.get("owner_chat_id")
    if (
        update.effective_chat is None
        or expected_owner_id is None
        or update.effective_chat.id != expected_owner_id
    ):
        return
    if update.message is None:
        return

    # Import inside the handler so a bot startup never pays the cost of
    # importing the connectors (stravalib, garminconnect) when /sync is
    # never used.
    from runos.config import get_settings
    from runos.sync import pipeline

    settings = get_settings()
    db_path: Path = context.application.bot_data["db_path"]

    await update.effective_chat.send_action(ChatAction.TYPING)
    typing_task = asyncio.create_task(_keep_typing(update.effective_chat))

    def _do_sync() -> list[object]:
        conn = connect(db_path)
        try:
            return list(pipeline.run_full_sync(conn, settings))
        finally:
            conn.close()

    try:
        results = await asyncio.to_thread(_do_sync)
    except ValueError as exc:
        logger.info("/sync skipped: %s", exc)
        await update.message.reply_text(SYNC_CONFIG_ERROR, parse_mode=ParseMode.HTML)
        return
    finally:
        typing_task.cancel()
        await asyncio.gather(typing_task, return_exceptions=True)

    lines: list[str] = []
    for r in results:
        source = html.escape(str(getattr(r, "source", "?")), quote=False)
        if getattr(r, "ok", False):
            rows = int(getattr(r, "rows", 0))
            lines.append(f"  {source}: ok ({rows} raw rows)")
        else:
            err = html.escape(str(getattr(r, "error", "?")), quote=False)
            lines.append(f"  {source}: skipped ({err})")
    body = SYNC_REPLY_PREFIX + "<pre>" + "\n".join(lines) + "</pre>"
    logger.info("/sync: %s", "; ".join(line.strip() for line in lines))
    await update.message.reply_text(body, parse_mode=ParseMode.HTML)


async def clear_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only ``/clear`` command: delete the stored session id (VOICE-08).

    The DELETE is idempotent (no row -> still success); the user gets the
    same confirmation either way so a redundant ``/clear`` is harmless.
    """
    expected_owner_id = context.application.bot_data.get("owner_chat_id")
    if (
        update.effective_chat is None
        or expected_owner_id is None
        or update.effective_chat.id != expected_owner_id
    ):
        return
    if update.message is None:
        return

    chat_id = update.effective_chat.id
    db_path: Path = context.application.bot_data["db_path"]
    conn: sqlite3.Connection = connect(db_path)
    try:
        reset_session(conn, chat_id)
    finally:
        conn.close()

    logger.info("session reset · chat=%d", chat_id)
    await update.message.reply_text(CLEAR_SESSION_REPLY, parse_mode=ParseMode.HTML)
