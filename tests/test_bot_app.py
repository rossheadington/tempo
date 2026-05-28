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
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from tempo.bot import (
    CLAUDE_CLI_MISSING_ERROR,
    GREETING,
    clear_command_handler,
    start_handler,
    text_handler,
)
from tempo.bot.app import (
    _require_telegram_config,
    _sweep_voice_cache,
    _verify_claude_cli,
    build_application,
)
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


# ---------------------------------------------------------------------------
# Phase 11 plan 11-03: claude CLI startup check + new handler registration
# ---------------------------------------------------------------------------


def test_verify_claude_cli_passes_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_verify_claude_cli` is a no-op when `shutil.which("claude")` returns a path."""
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: "/usr/local/bin/claude")
    _verify_claude_cli()  # no exception


def test_verify_claude_cli_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_verify_claude_cli` raises RuntimeError(CLAUDE_CLI_MISSING_ERROR) on missing CLI."""
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError) as excinfo:
        _verify_claude_cli()
    assert str(excinfo.value) == CLAUDE_CLI_MISSING_ERROR
    # Sanity: the canonical message names Phase 11 prerequisites + the docs link.
    assert "Claude Code CLI" in CLAUDE_CLI_MISSING_ERROR
    assert "docs/TELEGRAM_BOT.md" in CLAUDE_CLI_MISSING_ERROR


def test_build_application_raises_when_claude_cli_missing(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_application` raises before any Telegram traffic when claude is missing."""
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: None)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError) as excinfo:
        build_application(settings)
    assert str(excinfo.value) == CLAUDE_CLI_MISSING_ERROR


def test_build_application_stashes_db_path(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_application` puts settings.db_path into bot_data for handler use."""
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: "/usr/local/bin/claude")
    settings = Settings(_env_file=None)

    app = build_application(settings)
    assert app.bot_data["db_path"] == settings.db_path


def _find_text_handler(app) -> MessageHandler:
    """Locate the registered text ``MessageHandler`` on ``app``."""
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, MessageHandler) and handler.callback is text_handler:
                return handler
    raise AssertionError("text_handler MessageHandler not registered")


def _find_clear_command_handler(app) -> CommandHandler:
    """Locate the registered /clear ``CommandHandler`` on ``app``."""
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler) and "clear" in handler.commands:
                return handler
    raise AssertionError("/clear CommandHandler not registered")


def test_build_application_registers_text_and_new_handlers(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11 wires /clear + text handlers behind the owner filter.

    The text handler must:
      - accept an owner non-command text Update,
      - reject an owner /start command Update (covered by ~filters.COMMAND),
      - reject a non-owner text Update.
    """
    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: "/usr/local/bin/claude")
    settings = Settings(_env_file=None)

    app = build_application(settings)

    # /clear CommandHandler exists, behind a filters.Chat(owner) filter.
    new_handler = _find_clear_command_handler(app)
    assert new_handler.callback is clear_command_handler

    # text MessageHandler exists, with the right callback.
    text_msg_handler = _find_text_handler(app)
    assert text_msg_handler.callback is text_handler

    # Owner text update -> passes; non-owner -> rejected.
    owner_text_user = User(id=1234, first_name="Tester", is_bot=False)
    owner_text_chat = Chat(id=987654321, type="private")
    owner_text_message = Message(
        message_id=100,
        date=datetime.now(UTC),
        chat=owner_text_chat,
        from_user=owner_text_user,
        text="how's training",
    )
    owner_text_message.set_bot(MagicMock(username="tempo_test_bot"))
    owner_text_update = Update(update_id=100, message=owner_text_message)
    owner_text_update.set_bot(owner_text_message.get_bot())
    assert text_msg_handler.check_update(owner_text_update)

    non_owner_chat = Chat(id=111, type="private")
    non_owner_message = Message(
        message_id=101,
        date=datetime.now(UTC),
        chat=non_owner_chat,
        from_user=owner_text_user,
        text="hi",
    )
    non_owner_message.set_bot(MagicMock(username="tempo_test_bot"))
    non_owner_update = Update(update_id=101, message=non_owner_message)
    non_owner_update.set_bot(non_owner_message.get_bot())
    assert not text_msg_handler.check_update(non_owner_update)

    # Owner /start command -> rejected by text handler (filters.COMMAND excludes).
    command_entity = MessageEntity(type="bot_command", offset=0, length=6)
    cmd_message = Message(
        message_id=102,
        date=datetime.now(UTC),
        chat=owner_text_chat,
        from_user=owner_text_user,
        text="/start",
        entities=(command_entity,),
    )
    cmd_message.set_bot(MagicMock(username="tempo_test_bot"))
    cmd_update = Update(update_id=102, message=cmd_message)
    cmd_update.set_bot(cmd_message.get_bot())
    assert not text_msg_handler.check_update(cmd_update)

    # /clear from owner -> accepted by the clear_command_handler.
    new_entity = MessageEntity(type="bot_command", offset=0, length=6)
    new_message = Message(
        message_id=103,
        date=datetime.now(UTC),
        chat=owner_text_chat,
        from_user=owner_text_user,
        text="/clear",
        entities=(new_entity,),
    )
    new_message.set_bot(MagicMock(username="tempo_test_bot"))
    new_update = Update(update_id=103, message=new_message)
    new_update.set_bot(new_message.get_bot())
    assert new_handler.check_update(new_update)

    # /clear from non-owner -> rejected.
    non_owner_new = Message(
        message_id=104,
        date=datetime.now(UTC),
        chat=non_owner_chat,
        from_user=owner_text_user,
        text="/clear",
        entities=(new_entity,),
    )
    non_owner_new.set_bot(MagicMock(username="tempo_test_bot"))
    non_owner_new_update = Update(update_id=104, message=non_owner_new)
    non_owner_new_update.set_bot(non_owner_new.get_bot())
    assert not new_handler.check_update(non_owner_new_update)


# ---------------------------------------------------------------------------
# Phase 12: voice cache startup sweep
# ---------------------------------------------------------------------------


def test_sweep_voice_cache_noop_when_retention_zero(tmp_path: Path) -> None:
    """retention=0 -> sweep is a no-op (per-handler cleanup already deletes)."""
    cache = tmp_path / "voice"
    cache.mkdir()
    f = cache / "x.ogg"
    f.write_bytes(b"\x00")
    deleted = _sweep_voice_cache(cache, retention_days=0)
    assert deleted == 0
    assert f.exists()


def test_sweep_voice_cache_noop_when_dir_missing(tmp_path: Path) -> None:
    """Missing cache dir is fine (bot never received a voice memo yet)."""
    cache = tmp_path / "voice"
    deleted = _sweep_voice_cache(cache, retention_days=7)
    assert deleted == 0


def test_sweep_voice_cache_deletes_old_keeps_recent(tmp_path: Path) -> None:
    """retention=7: file older than 7 days is removed; younger file kept.

    Fakes file mtimes via :func:`os.utime` so the test does not need to wait
    real seconds.
    """
    import os

    cache = tmp_path / "voice"
    cache.mkdir()
    old = cache / "old.ogg"
    young = cache / "young.ogg"
    old.write_bytes(b"\x00")
    young.write_bytes(b"\x00")

    now = time.time()
    # old: 10 days ago; young: 1 day ago.
    os.utime(old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(young, (now - 1 * 86400, now - 1 * 86400))

    deleted = _sweep_voice_cache(cache, retention_days=7)
    assert deleted == 1
    assert not old.exists()
    assert young.exists()


def test_sweep_voice_cache_handles_subdirectories(tmp_path: Path) -> None:
    """Subdirectories under voice_cache_dir are not deleted (iterdir + is_file)."""
    cache = tmp_path / "voice"
    cache.mkdir()
    (cache / "sub").mkdir()
    deleted = _sweep_voice_cache(cache, retention_days=1)
    assert deleted == 0
    assert (cache / "sub").is_dir()


# ---------------------------------------------------------------------------
# Phase 12: cwd log line at startup
# ---------------------------------------------------------------------------


def test_post_init_logs_agent_cwd_and_data_dir(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """`_post_init` emits an "agent cwd = <abs>" log line at startup.

    Builds the Application, captures the post_init hook directly (PTB stores
    it as ``app.post_init``), and runs it under asyncio.run with a mock bot.
    Stubs out ``delete_webhook``, ``init_db``, ``warm_model`` so the test
    does not touch the network or Whisper.
    """
    import logging

    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    monkeypatch.setenv("VOICE_RETENTION_DAYS", "3")
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: "/usr/local/bin/claude")
    # Stub out the work _post_init does so we can assert on the side log lines.
    monkeypatch.setattr("tempo.bot.app.init_db", lambda path: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("tempo.bot.app.warm_model", lambda settings: None)

    settings = Settings(_env_file=None)
    app = build_application(settings)

    # PTB v22 stores the post-init callback as ``app.post_init`` (a property
    # that returns the configured coroutine). We invoke it directly with a
    # MagicMock Application so the bot.delete_webhook call does not need a
    # live bot.
    fake_bot = MagicMock()
    fake_bot.delete_webhook = AsyncMock(return_value=None)
    fake_bot.set_my_commands = AsyncMock(return_value=None)
    fake_app = SimpleNamespace(bot=fake_bot)

    caplog.set_level(logging.INFO, logger="tempo.bot")
    asyncio.run(app.post_init(fake_app))  # type: ignore[arg-type]

    messages = [r.getMessage() for r in caplog.records]
    assert any("agent cwd =" in m for m in messages), messages
    assert any("data_dir =" in m for m in messages), messages
    # Sweep ran for retention > 0 (empty dir -> deleted=0 but the line fires).
    assert any("voice cache startup sweep" in m for m in messages), messages


def test_post_init_no_sweep_log_when_retention_zero(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """With VOICE_RETENTION_DAYS=0 (default) the sweep is silent (no log line)."""
    import logging

    _clear_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    monkeypatch.delenv("VOICE_RETENTION_DAYS", raising=False)
    monkeypatch.setattr("tempo.bot.app.shutil.which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr("tempo.bot.app.init_db", lambda path: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("tempo.bot.app.warm_model", lambda settings: None)

    settings = Settings(_env_file=None)
    app = build_application(settings)

    fake_bot = MagicMock()
    fake_bot.delete_webhook = AsyncMock(return_value=None)
    fake_bot.set_my_commands = AsyncMock(return_value=None)
    fake_app = SimpleNamespace(bot=fake_bot)

    caplog.set_level(logging.INFO, logger="tempo.bot")
    asyncio.run(app.post_init(fake_app))  # type: ignore[arg-type]

    messages = [r.getMessage() for r in caplog.records]
    assert not any("voice cache startup sweep" in m for m in messages)
    # The cwd line is still emitted regardless of retention.
    assert any("agent cwd =" in m for m in messages)
