"""Tests for the top-level Telegram bot error handler (VOICE-12).

The error handler is registered on the PTB :class:`Application` via
``add_error_handler`` and runs whenever a registered handler raises an
exception that the handler itself did not catch. Its job is:

1. log the full traceback via :func:`logging.exception` so the developer
   sees the failure in the launchd-managed log file,
2. send a brief, fixed "something went wrong" reply to the chat that
   triggered it, so the user knows the bot is alive but their last message
   didn't go through,
3. NEVER re-raise -- if the reply itself fails (network blip, Telegram
   rejected the message, etc.) the exception is swallowed and logged so
   the worker process stays up.

No network. Async tests use ``asyncio.run(...)`` inline to avoid taking on
``pytest-asyncio`` as a new dev dependency, matching the rest of the bot
test suite.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import Application

from tempo.bot.error_handler import (
    ERROR_REPLY,
    telegram_error_handler,
)


def _make_update(*, chat_id: int = 987654321) -> Update:
    """Build a minimal Update with a Chat + Message for chat-aware replies."""
    user = User(id=1234, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="hello",
    )
    fake_bot = MagicMock()
    fake_bot.username = "tempo_test_bot"
    message.set_bot(fake_bot)
    update = Update(update_id=1, message=message)
    update.set_bot(fake_bot)
    return update


def _make_context_with_error(exc: BaseException, *, send_message: AsyncMock | None = None):
    """Build a minimal context exposing ``context.error`` and a fake bot.

    PTB passes the exception that fired on ``context.error``; the canonical
    reply path goes through ``update.effective_chat.send_message`` (or the
    application's bot). We expose a fake ``bot.send_message`` so the test
    can assert / fail it.
    """
    if send_message is None:
        send_message = AsyncMock()
    fake_bot = MagicMock()
    fake_bot.send_message = send_message
    return SimpleNamespace(
        error=exc,
        bot=fake_bot,
        application=SimpleNamespace(bot_data={}),
    )


# ---------------------------------------------------------------------------
# Behaviour: handler exception -> log + canonical reply
# ---------------------------------------------------------------------------


def test_error_handler_logs_exception_and_sends_canonical_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A handler crash routes through telegram_error_handler:
    * ``logging.exception`` is called (full traceback in the log),
    * a single ``send_message`` is fired with :data:`ERROR_REPLY` and the
      offending chat id.
    """
    send_message = AsyncMock()
    context = _make_context_with_error(RuntimeError("boom"), send_message=send_message)
    update = _make_update(chat_id=987654321)

    with caplog.at_level(logging.ERROR, logger="tempo.bot.error_handler"):

        async def _run() -> None:
            await telegram_error_handler(update, context)

        asyncio.run(_run())

    # logging.exception emits at ERROR level. The message text is fixed; the
    # traceback is attached automatically (we don't assert it directly because
    # caplog captures records, not formatted output).
    assert any(
        record.levelno == logging.ERROR and "Bot handler crashed" in record.getMessage()
        for record in caplog.records
    ), "expected an ERROR-level 'Bot handler crashed' log record"

    # Canonical reply sent exactly once, to the right chat, with the right text.
    send_message.assert_awaited_once()
    call = send_message.await_args
    assert call.kwargs.get("chat_id") == 987654321 or (call.args and call.args[0] == 987654321)
    # The reply text is the fixed constant; assert against it directly so
    # accidental rewording fails a test.
    text_arg = call.kwargs.get("text")
    if text_arg is None and len(call.args) >= 2:
        text_arg = call.args[1]
    assert text_arg == ERROR_REPLY


# ---------------------------------------------------------------------------
# Behaviour: reply failure swallowed (never re-raises)
# ---------------------------------------------------------------------------


def test_error_handler_swallows_reply_failure(caplog: pytest.LogCaptureFixture) -> None:
    """If the canonical reply itself raises (Telegram down, network blip,
    chat blocked the bot, etc.), :func:`telegram_error_handler` swallows it
    and logs. It MUST NOT re-raise -- a bad reply must never crash the worker.
    """
    send_message = AsyncMock(side_effect=RuntimeError("telegram unreachable"))
    context = _make_context_with_error(ValueError("orig"), send_message=send_message)
    update = _make_update(chat_id=987654321)

    with caplog.at_level(logging.ERROR, logger="tempo.bot.error_handler"):

        async def _run() -> None:
            # No pytest.raises -- handler must NOT propagate.
            await telegram_error_handler(update, context)

        asyncio.run(_run())

    # Reply was attempted exactly once.
    send_message.assert_awaited_once()
    # Two distinct error log records: the original handler crash + the
    # follow-up "reply failed" diagnostic. Both must reach the log so the
    # developer can see what actually happened.
    error_messages = [
        record.getMessage() for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert any("Bot handler crashed" in m for m in error_messages)
    assert any("error reply failed" in m.lower() for m in error_messages)


# ---------------------------------------------------------------------------
# Behaviour: non-Update update object -> log only, no reply
# ---------------------------------------------------------------------------


def test_error_handler_handles_non_update_object(caplog: pytest.LogCaptureFixture) -> None:
    """PTB also fires the error handler for non-Update failures (e.g. a
    CallbackQueryHandler job crash, or an internal updater error). When
    ``update`` is not an :class:`Update`, the handler must log the exception
    and NOT attempt a reply (no chat to reply to).
    """
    send_message = AsyncMock()
    context = _make_context_with_error(RuntimeError("internal"), send_message=send_message)

    # Pass a plain object (could be None, a dict, a JobQueue job, etc.)
    not_an_update: object = "not-an-update-object"

    with caplog.at_level(logging.ERROR, logger="tempo.bot.error_handler"):

        async def _run() -> None:
            await telegram_error_handler(not_an_update, context)

        asyncio.run(_run())

    # No reply attempted -- there is no chat to reply to.
    assert send_message.await_count == 0
    # The exception is still logged for visibility.
    assert any(
        record.levelno == logging.ERROR and "Bot handler crashed" in record.getMessage()
        for record in caplog.records
    )


def test_error_handler_handles_update_without_effective_chat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An Update with no ``effective_chat`` (rare but possible -- e.g. an
    inline-query result with the chat stripped) routes to the same path:
    log, do not attempt a reply.
    """
    send_message = AsyncMock()
    context = _make_context_with_error(KeyError("missing"), send_message=send_message)

    # Construct an Update with no message/chat fields populated.
    update = Update(update_id=42)

    with caplog.at_level(logging.ERROR, logger="tempo.bot.error_handler"):

        async def _run() -> None:
            await telegram_error_handler(update, context)

        asyncio.run(_run())

    assert send_message.await_count == 0
    assert any(
        record.levelno == logging.ERROR and "Bot handler crashed" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Registration: build_application wires the error handler on the Application
# ---------------------------------------------------------------------------


def test_build_application_registers_error_handler(
    tempo_data_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_application` must call ``app.add_error_handler(telegram_error_handler)``
    so PTB routes uncaught handler exceptions through our boundary.

    PTB v22 stores registered error callbacks in ``Application.error_handlers``
    (a dict keyed by the callback). The check is therefore: our callback
    appears as a key.
    """
    from tempo.bot.app import build_application
    from tempo.config import Settings

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)

    app: Application = build_application(settings)
    assert telegram_error_handler in app.error_handlers, (
        "telegram_error_handler must be registered via app.add_error_handler "
        "in build_application -- VOICE-12 requires the worker survive single-message failures"
    )
