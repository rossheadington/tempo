"""Tests for the Telegram bot scaffold (config, allowlist, /start handler).

No network. No real bot token. Every test:

* deletes ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_OWNER_CHAT_ID`` from the env first
  (the shared ``tempo_data_dir`` fixture does not touch these because they are
  intentionally NOT prefixed with ``TEMPO_``); each test then sets only the env
  vars it cares about,
* loads :class:`Settings` with ``_env_file=None`` so the developer's real
  ``.env`` cannot leak in even if the cwd-chdir from the fixture missed it,
* uses PTB v22's ability to construct ``Update`` / ``Chat`` / ``Message`` /
  ``User`` dataclasses directly (no live Bot connection) plus a mocked
  ``Bot`` for the shortcut paths.

Async tests use ``asyncio.run(...)`` inline -- same idiom as
``tests/test_garmin_cli.py`` -- to avoid taking on ``pytest-asyncio`` as a new
dev dependency.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, filters

from tempo.bot import GREETING, start_handler
from tempo.bot.app import _require_telegram_config, build_application
from tempo.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the Telegram env vars; tests opt back in with ``setenv``."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_CHAT_ID", raising=False)


def _make_update(*, chat_id: int, update_id: int = 1, with_bot: bool = True) -> Update:
    """Build a ``/start`` Update from ``chat_id`` (no network, no live bot)."""
    user = User(id=1234, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    # CommandHandler.check_update requires a `bot_command` MessageEntity to
    # recognise "/start" as a command.
    entity = MessageEntity(type="bot_command", offset=0, length=6)
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="/start",
        entities=(entity,),
    )
    if with_bot:
        # CommandHandler.check_update calls message.get_bot(); without this we
        # hit RuntimeError("This object has no bot associated with it.").
        fake_bot = MagicMock()
        fake_bot.username = "tempo_test_bot"
        message.set_bot(fake_bot)
    update = Update(update_id=update_id, message=message)
    if with_bot:
        update.set_bot(message.get_bot())
    return update


def _find_start_handler(app) -> CommandHandler:
    """Locate the registered ``/start`` ``CommandHandler`` on ``app``."""
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler) and "start" in handler.commands:
                return handler
    raise AssertionError("/start CommandHandler not registered")


# ---------------------------------------------------------------------------
# Settings load + validator
# ---------------------------------------------------------------------------


def test_settings_load_without_telegram_vars(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither env var set, both fields are None (rest of tempo still works)."""
    _clear_telegram_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token is None
    assert settings.telegram_owner_chat_id is None


def test_settings_load_with_both_vars_set(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting both env vars produces a SecretStr token + int chat id; repr is scrubbed."""
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)
    # Token loaded as SecretStr; raw value retrievable via get_secret_value()
    # but absent from any repr.
    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value() == "test-token-123"
    assert "test-token-123" not in repr(settings)
    # Chat id loaded as an int.
    assert settings.telegram_owner_chat_id == 987654321
    assert isinstance(settings.telegram_owner_chat_id, int)


def test_require_telegram_config_missing_token_raises(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat id alone -> ValueError naming both env-var names and the docs link."""
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError) as excinfo:
        _require_telegram_config(settings)
    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_OWNER_CHAT_ID" in message
    assert "docs/TELEGRAM_BOT.md" in message


def test_require_telegram_config_missing_chat_id_raises(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token alone -> the same ValueError shape."""
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError) as excinfo:
        _require_telegram_config(settings)
    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_OWNER_CHAT_ID" in message
    assert "docs/TELEGRAM_BOT.md" in message


# ---------------------------------------------------------------------------
# build_application: handler registration + filter shape
# ---------------------------------------------------------------------------


def test_build_application_registers_owner_only_start_handler(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_application` registers exactly one `/start` CommandHandler whose
    filter is a `filters.Chat` configured for the owner chat id.
    """
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)

    app = build_application(settings)
    # Owner chat id stashed for in-handler defensive re-check.
    assert app.bot_data["owner_chat_id"] == 987654321

    handler = _find_start_handler(app)
    # Filter is the owner-only chat-id gate. PTB v22 exposes `.chat_ids` as a
    # frozenset on `filters.Chat`.
    assert isinstance(handler.filters, filters.Chat)
    assert handler.filters.chat_ids == frozenset({987654321})


def test_start_command_filter_drops_non_owner(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`filters.Chat` returns falsy for any non-owner Update -- the dispatcher
    silently drops it before any handler runs. This is the heart of VOICE-01.
    """
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)
    app = build_application(settings)
    handler = _find_start_handler(app)

    # Owner Update -- truthy.
    owner_update = _make_update(chat_id=987654321)
    assert handler.check_update(owner_update)

    # Non-owner Update -- falsy (False or None).
    non_owner_update = _make_update(chat_id=111, update_id=2)
    assert not handler.check_update(non_owner_update)


# ---------------------------------------------------------------------------
# start_handler behaviour
# ---------------------------------------------------------------------------


def test_start_handler_replies_to_owner_with_greeting(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start_handler`(owner Update) -> one reply_text call with GREETING + HTML."""
    _clear_telegram_env(monkeypatch)

    # PTB v22's :class:`Message` is a frozen TelegramObject with slot-based
    # attributes; instance-level mock assignment is rejected. Patch
    # ``Message.reply_text`` at the CLASS level via monkeypatch instead (auto-
    # undone at test teardown so the class shape doesn't leak between tests).
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    update = _make_update(chat_id=987654321, with_bot=False)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"owner_chat_id": 987654321}))

    async def _run() -> None:
        await start_handler(update, context)  # type: ignore[arg-type]

    asyncio.run(_run())

    mock_reply.assert_awaited_once()
    call = mock_reply.await_args
    # Greeting text is the first positional arg. Because we patched the method
    # at the class level with an AsyncMock (not a descriptor), the implicit
    # ``self`` is not bound, so ``call.args`` starts with the greeting itself.
    assert call.args[0] == GREETING
    assert "Tempo bot online" in call.args[0]
    # HTML parse mode (matches Phase 9 decision in CONTEXT.md).
    assert call.kwargs.get("parse_mode") == ParseMode.HTML


def test_start_handler_defensive_check_rejects_mismatched_chat(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-braces: even if the filter were bypassed, an in-handler
    chat-id mismatch -> silent drop (no reply, no log of bot existence).
    """
    _clear_telegram_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    # Owner is 987654321 in bot_data, but the Update comes from chat 111.
    update = _make_update(chat_id=111, with_bot=False)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"owner_chat_id": 987654321}))

    async def _run() -> None:
        await start_handler(update, context)  # type: ignore[arg-type]

    asyncio.run(_run())

    assert mock_reply.await_count == 0
