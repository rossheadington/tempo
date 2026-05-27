"""Tests for ``tempo.bot.handlers`` (Phase 10 + Phase 11 plan 11-03).

Covers the second half of Phase 10 (voice-memo handler ties the warmed
faster-whisper singleton to the Telegram dispatcher) PLUS Phase 11 plan
11-03's handler integration: voice + text + ``/new`` route through the
Claude Agent SDK; the raw-transcript echo from Phase 10 is GONE.

What this file proves:

* **20 MB pre-download guard (VOICE-03)** -- an oversized voice memo is
  rejected with the fixed user-facing reply and ``voice.get_file()`` is NEVER
  called (patched to raise so a regression would be loud).
* **Owner-only filter (VOICE-01 carry-over)** -- the registered
  ``MessageHandler`` only passes ``filters.VOICE & filters.Chat(owner)``;
  a non-owner voice Update returns falsy from ``check_update``.
* **Happy path (VOICE-04 + VOICE-06 + VOICE-07/08/09/13)** -- with mocks
  for ``transcribe_file``, ``get_file().download_to_drive``, ``run_turn``,
  and the session-store helpers, the handler writes the .ogg to
  ``<voice_cache_dir>/<message_id>-<file_unique_id>.ogg``, calls
  ``transcribe_file`` with that path, resolves the prior session,
  invokes the agent, persists the resolved session id, and replies with
  the agent's HTML-formatted text (NOT the raw transcript).
* **HTML escaping** -- the agent reply text is escaped before sending so
  ``<``/``>``/``&`` survive Telegram's HTML parser.
* **Defensive chat-id re-check** -- belt-and-braces: even if the registration
  filter were bypassed, an in-handler chat-id mismatch produces no reply.
* **Cache dir mode 0700** -- ``voice_cache_dir`` is created with mode 0700
  on first use, mirroring the existing ``data_dir``/``tokens_dir`` pattern.
* **Session resume / reset** -- a stored session id is forwarded to
  ``run_turn``; the resolved id is persisted via ``save_session``; ``/new``
  deletes the row.
* **Multi-chunk reply** -- long agent replies are split into multiple
  ``reply_text`` calls each <= 4096 chars.
* **Missing-CLI reply path** -- ``AgentInvocationError`` -> a single
  ``MISSING_CLI_REPLY`` message; no exception escapes the handler.

Every test:

* deletes ``TELEGRAM_*`` / ``WHISPER_*`` env vars first so a developer's real
  ``.env`` cannot leak in (the ``tempo_data_dir`` fixture does not touch
  these because they are intentionally NOT prefixed with ``TEMPO_``),
* loads :class:`Settings` with ``_env_file=None`` for the same reason,
* patches ``tempo.bot.handlers.transcribe_file`` with a ``MagicMock`` (NOT
  ``AsyncMock`` -- the handler wraps it in :func:`asyncio.to_thread`, which
  awaits a SYNC callable),
* patches ``telegram.Message.reply_text`` at the class level (PTB v22's
  ``Message`` is slotted-frozen; instance-level mock assignment is rejected).

Async tests use ``asyncio.run(...)`` inline -- matches ``test_bot_app.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User, Voice
from telegram.constants import ChatAction, ParseMode
from telegram.ext import MessageHandler, filters

from tempo.bot import (
    MAX_VOICE_BYTES,
    AgentInvocationError,
    AgentTurn,
    new_command_handler,
    text_handler,
    voice_handler,
)
from tempo.bot.app import build_application
from tempo.bot.handlers import (
    EMPTY_TRANSCRIPT_REPLY,
    MISSING_CLI_REPLY,
    NEW_SESSION_REPLY,
    OVERSIZED_REPLY,
)
from tempo.config import Settings
from tempo.db import init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop Telegram + Whisper env vars so the real ``.env`` cannot leak."""
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_ID",
        "WHISPER_MODEL_NAME",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_DEVICE",
        "TEMPO_WHISPER_MODEL_NAME",
        "TEMPO_WHISPER_COMPUTE_TYPE",
        "TEMPO_WHISPER_DEVICE",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_voice_update(
    *,
    chat_id: int,
    file_size: int,
    file_unique_id: str = "abc123",
    duration: int = 2,
    message_id: int = 42,
    with_bot: bool = True,
) -> Update:
    """Build a voice-message Update (no network, no live bot).

    Builds a real :class:`telegram.Voice` object so ``filters.VOICE`` matches.
    ``file_size`` is the value the 20 MB guard inspects.
    """
    user = User(id=1234, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    voice = Voice(
        file_id="voice-file-id",
        file_unique_id=file_unique_id,
        duration=duration,
        file_size=file_size,
        mime_type="audio/ogg",
    )
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        voice=voice,
    )
    if with_bot:
        fake_bot = MagicMock()
        fake_bot.username = "tempo_test_bot"
        message.set_bot(fake_bot)
    update = Update(update_id=message_id, message=message)
    if with_bot:
        update.set_bot(message.get_bot())
    return update


def _make_text_update(
    *,
    chat_id: int,
    text: str = "hello",
    message_id: int = 42,
    with_bot: bool = False,
) -> Update:
    """Build a non-command text Update (Plan 11-03 text_handler tests)."""
    user = User(id=1234, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
    )
    if with_bot:
        fake_bot = MagicMock()
        fake_bot.username = "tempo_test_bot"
        message.set_bot(fake_bot)
    update = Update(update_id=message_id, message=message)
    if with_bot:
        update.set_bot(message.get_bot())
    return update


def _make_new_command_update(
    *,
    chat_id: int,
    message_id: int = 42,
    with_bot: bool = False,
) -> Update:
    """Build a ``/new`` command Update."""
    user = User(id=1234, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    entity = MessageEntity(type="bot_command", offset=0, length=4)
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="/new",
        entities=(entity,),
    )
    if with_bot:
        fake_bot = MagicMock()
        fake_bot.username = "tempo_test_bot"
        message.set_bot(fake_bot)
    update = Update(update_id=message_id, message=message)
    if with_bot:
        update.set_bot(message.get_bot())
    return update


def _make_bot_data(*, owner_chat_id: int, tmp_path: Path, settings: Settings) -> dict:
    """Construct the ``bot_data`` dict handlers expect, including an inited DB."""
    db_path = tmp_path / "tempo.db"
    # Apply migrations so the bot_session table exists -- handlers open
    # per-call connections via tempo.db.connect(db_path) and assume the
    # schema is current.
    conn = init_db(db_path)
    conn.close()
    return {
        "owner_chat_id": owner_chat_id,
        "settings": settings,
        "db_path": db_path,
    }


def _make_agent_turn(
    text: str = "agent reply",
    session_id: str = "sess-NEW",
    tokens_in: int = 10,
    tokens_out: int = 20,
    cost_usd: float | None = 0.001,
    duration_s: float = 1.5,
) -> AgentTurn:
    """Construct an :class:`AgentTurn` for handler tests."""
    return AgentTurn(
        text=text,
        session_id=session_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        duration_s=duration_s,
    )


def _stub_typing(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub ``Chat.send_action`` so the typing keepalive does not poll forever."""
    mock_send_action = AsyncMock()
    monkeypatch.setattr(Chat, "send_action", mock_send_action)
    return mock_send_action


def _find_voice_handler(app) -> MessageHandler:
    """Locate the registered voice ``MessageHandler`` on ``app``."""
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, MessageHandler) and handler.callback is voice_handler:
                return handler
    raise AssertionError("voice_handler MessageHandler not registered")


# ---------------------------------------------------------------------------
# 20 MB guard: oversized memos are rejected BEFORE any network call
# ---------------------------------------------------------------------------


def test_voice_handler_rejects_oversized_with_no_download(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A voice memo > 20 MB -> fixed rejection reply, no get_file() call.

    Crucial for VOICE-03: getFile would raise BadRequest server-side anyway,
    so we want a defensive, friendly reply BEFORE the doomed network call.
    Patches ``Voice.get_file`` to raise so any code path that bypassed the
    guard would surface as a hard failure (proof of "no doomed network").
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    # transcribe_file must NEVER be called for an oversized rejection.
    no_transcribe = MagicMock(
        side_effect=AssertionError("transcribe_file MUST NOT be called for oversized memos"),
    )
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", no_transcribe)

    # get_file must NEVER be called for an oversized rejection.
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("Voice.get_file MUST NOT be called for oversized memos")

    monkeypatch.setattr(Voice, "get_file", _boom)

    settings = Settings(_env_file=None)
    # Just over the cap: 20 MB + 1 byte.
    update = _make_voice_update(
        chat_id=987654321,
        file_size=MAX_VOICE_BYTES + 1,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"owner_chat_id": 987654321, "settings": settings},
        ),
    )

    async def _run() -> None:
        await voice_handler(update, context)  # type: ignore[arg-type]

    asyncio.run(_run())

    # Exactly one reply, with the fixed rejection string + HTML parse mode.
    mock_reply.assert_awaited_once()
    call = mock_reply.await_args
    assert call.args[0] == OVERSIZED_REPLY
    assert "20 MB" in call.args[0]
    assert call.kwargs.get("parse_mode") == ParseMode.HTML
    # transcribe_file was not called (side_effect would have fired otherwise).
    no_transcribe.assert_not_called()
    # No cache dir was created for an oversized rejection.
    assert not settings.voice_cache_dir.exists()


# ---------------------------------------------------------------------------
# Filter wiring: non-owner voice Updates are dropped at the dispatcher
# ---------------------------------------------------------------------------


def test_voice_handler_filter_drops_non_owner(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`filters.VOICE & filters.Chat(owner)` returns falsy for non-owner voice.

    The voice MessageHandler must only accept Updates from the owner chat id.
    A non-owner voice Update -> ``check_update`` is falsy -> dispatcher
    silently drops it before voice_handler ever runs.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)

    app = build_application(settings)
    handler = _find_voice_handler(app)

    # Owner voice Update -- truthy (passes the filter).
    owner_update = _make_voice_update(chat_id=987654321, file_size=4096, with_bot=True)
    assert handler.check_update(owner_update)

    # Non-owner voice Update -- falsy.
    non_owner_update = _make_voice_update(chat_id=111, file_size=4096, message_id=99, with_bot=True)
    assert not handler.check_update(non_owner_update)


# ---------------------------------------------------------------------------
# Happy path: download, transcribe, reply
# ---------------------------------------------------------------------------


def test_voice_handler_happy_path_writes_file_transcribes_and_replies(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small owner voice memo flows download -> transcribe -> agent -> reply.

    Phase 11 plan 11-03 reshape: the reply is no longer ``<i>{transcript}</i>``
    but the agent's HTML-formatted answer (from a mocked ``run_turn``). The
    cache-dir + filename + ``transcribe_file`` behaviour is preserved from
    Phase 10. The session store is exercised end-to-end: a fresh chat (no
    prior session) -> ``get_or_create_session`` returns None -> ``run_turn``
    called with session_id=None -> the resolved id is persisted via
    ``save_session``.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    mock_send_action = _stub_typing(monkeypatch)

    fake_transcribe = MagicMock(return_value="hello world this is a test")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)

    fake_run_turn = AsyncMock(
        return_value=_make_agent_turn(text="agent reply", session_id="sess-NEW")
    )
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)

    fake_save = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.save_session", fake_save)

    # Mock voice.get_file -> File-like with an awaitable download_to_drive that
    # actually writes a stub byte to the target path (so the test can assert
    # file existence as a real on-disk check).
    written_paths: list[Path] = []

    async def fake_download(custom_path: object) -> None:
        path = Path(str(custom_path))
        written_paths.append(path)
        path.write_bytes(b"\x00")

    fake_file = SimpleNamespace(download_to_drive=fake_download)

    async def fake_get_file(self: object) -> object:
        return fake_file

    monkeypatch.setattr(Voice, "get_file", fake_get_file)

    settings = Settings(_env_file=None)
    update = _make_voice_update(
        chat_id=987654321,
        file_size=4096,
        file_unique_id="abc123",
        duration=2,
        message_id=42,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    async def _run() -> None:
        await voice_handler(update, context)  # type: ignore[arg-type]

    asyncio.run(_run())

    # Cache dir was created.
    assert settings.voice_cache_dir.exists()
    assert settings.voice_cache_dir.is_dir()

    # The .ogg WAS downloaded to the expected deterministic path
    # (captured via the fake_download write_bytes call).
    expected_path = settings.voice_cache_dir / "42-abc123.ogg"
    assert written_paths == [expected_path]
    # Phase 12 retention policy: with VOICE_RETENTION_DAYS=0 (default), the
    # handler deletes the .ogg in the finally-block after the agent turn.
    # Privacy-safe: audio is never retained after transcription.
    assert not expected_path.exists()

    # transcribe_file was called with the same path.
    fake_transcribe.assert_called_once()
    args, _kwargs = fake_transcribe.call_args
    assert args == (expected_path,)

    # The agent was called with the transcript (NOT the literal voice bytes)
    # and a None session_id (fresh chat, no prior bot_session row).
    fake_run_turn.assert_awaited_once()
    rt_args, rt_kwargs = fake_run_turn.call_args
    assert rt_args[0] == "hello world this is a test"
    assert rt_args[1] is None  # session_id resolved to None for first turn
    assert "cwd" in rt_kwargs

    # save_session called with the resolved session id from the agent turn.
    fake_save.assert_called_once()
    save_args = fake_save.call_args.args
    # (conn, chat_id, session_id)
    assert save_args[1] == 987654321
    assert save_args[2] == "sess-NEW"

    # Reply is the agent text (not the raw transcript), HTML parse mode.
    mock_reply.assert_awaited_once()
    call = mock_reply.await_args
    assert call.args[0] == "agent reply"
    assert call.kwargs.get("parse_mode") == ParseMode.HTML

    # The typing indicator was kicked at least once before run_turn.
    assert mock_send_action.await_count >= 1
    first_call = mock_send_action.await_args_list[0]
    assert first_call.args[0] == ChatAction.TYPING


def test_voice_handler_escapes_html_in_agent_reply(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent reply with HTML-special chars is escaped before sending.

    Phase 11 reshape: the Phase 10 test asserted the TRANSCRIPT was escaped.
    That code path is gone -- the transcript no longer reaches Telegram. We
    now assert that the AGENT REPLY (which is also untrusted user-facing
    content via the LLM) survives ``format_for_telegram`` -> ``html.escape``.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    _stub_typing(monkeypatch)

    fake_transcribe = MagicMock(return_value="hello")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)

    fake_run_turn = AsyncMock(
        return_value=_make_agent_turn(text="3 < 4 & 5 > 4", session_id="sess-X")
    )
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    async def fake_download(custom_path: object) -> None:
        Path(str(custom_path)).write_bytes(b"\x00")

    fake_file = SimpleNamespace(download_to_drive=fake_download)

    async def fake_get_file(self: object) -> object:
        return fake_file

    monkeypatch.setattr(Voice, "get_file", fake_get_file)

    settings = Settings(_env_file=None)
    update = _make_voice_update(
        chat_id=987654321,
        file_size=2048,
        file_unique_id="esc01",
        message_id=7,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    mock_reply.assert_awaited_once()
    body = mock_reply.await_args.args[0]
    # html.escape (quote=False) applied by format_for_telegram. No `<i>` wrapper.
    assert body == "3 &lt; 4 &amp; 5 &gt; 4"


# ---------------------------------------------------------------------------
# Defensive in-handler chat-id re-check
# ---------------------------------------------------------------------------


def test_voice_handler_defensive_check_rejects_mismatched_chat(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-braces: an in-handler chat-id mismatch -> silent drop.

    Even if a future misconfigured catch-all handler bypassed the
    registration filter, the defensive re-check inside ``voice_handler``
    must drop a non-owner Update without replying, downloading, or
    transcribing.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    no_transcribe = MagicMock(side_effect=AssertionError("transcribe must not be called"))
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", no_transcribe)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("Voice.get_file must not be called for non-owner")

    monkeypatch.setattr(Voice, "get_file", _boom)

    settings = Settings(_env_file=None)
    # bot_data owner is 987654321; the Update is from 111.
    update = _make_voice_update(chat_id=111, file_size=4096, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"owner_chat_id": 987654321, "settings": settings},
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    assert mock_reply.await_count == 0
    no_transcribe.assert_not_called()
    assert not settings.voice_cache_dir.exists()


# ---------------------------------------------------------------------------
# Cache dir is created with 0700 permissions
# ---------------------------------------------------------------------------


def test_voice_handler_creates_cache_dir_with_0700(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``voice_cache_dir`` is created with mode 0700 on first use.

    Mirrors the existing ``data_dir``/``tokens_dir`` 0700 convention -- a
    voice memo can contain audio of a confidential conversation, so other
    local users must not be able to read it.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    _stub_typing(monkeypatch)

    fake_transcribe = MagicMock(return_value="ok")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)
    # Phase 11: voice_handler now invokes the agent after transcription.
    fake_run_turn = AsyncMock(return_value=_make_agent_turn(text="ok", session_id="sess-X"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    async def fake_download(custom_path: object) -> None:
        Path(str(custom_path)).write_bytes(b"\x00")

    fake_file = SimpleNamespace(download_to_drive=fake_download)

    async def fake_get_file(self: object) -> object:
        return fake_file

    monkeypatch.setattr(Voice, "get_file", fake_get_file)

    settings = Settings(_env_file=None)
    # Pre-condition: the cache dir does not yet exist.
    assert not settings.voice_cache_dir.exists()

    update = _make_voice_update(
        chat_id=987654321,
        file_size=2048,
        file_unique_id="perm01",
        message_id=11,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    assert settings.voice_cache_dir.exists()
    # Mode 0700: owner rwx, no group/other. mask with 0o777 to ignore the
    # high bits that st_mode includes for the directory type.
    mode = settings.voice_cache_dir.stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


# ---------------------------------------------------------------------------
# build_application registers the voice handler with the owner filter
# ---------------------------------------------------------------------------


def test_build_application_registers_voice_handler(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_application` registers a voice ``MessageHandler`` behind
    ``filters.VOICE & filters.Chat(owner)``.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "987654321")
    settings = Settings(_env_file=None)

    app = build_application(settings)
    handler = _find_voice_handler(app)

    # The handler exists, its callback is voice_handler, and its filter must
    # match an owner voice Update but reject a non-owner voice Update.
    owner_update = _make_voice_update(chat_id=987654321, file_size=4096)
    non_owner_update = _make_voice_update(chat_id=111, file_size=4096, message_id=2)
    assert handler.check_update(owner_update)
    assert not handler.check_update(non_owner_update)

    # And the filter must reject a NON-voice Update from the owner: e.g. a
    # plain text message should not be picked up here. (Phase 11 wires a
    # separate text handler.) Constructing a text-only Update from the owner
    # and asserting check_update is falsy proves filters.VOICE is part of
    # the conjunction.
    text_user = User(id=1234, first_name="Tester", is_bot=False)
    text_chat = Chat(id=987654321, type="private")
    text_message = Message(
        message_id=1234,
        date=datetime.now(UTC),
        chat=text_chat,
        from_user=text_user,
        text="not a voice",
    )
    text_message.set_bot(MagicMock(username="tempo_test_bot"))
    text_update = Update(update_id=1234, message=text_message)
    text_update.set_bot(text_message.get_bot())
    assert not handler.check_update(text_update)
    # Filter shape is filters.VOICE & filters.Chat(...) -- belt-and-braces.
    assert isinstance(handler.filters, filters.BaseFilter)


# ---------------------------------------------------------------------------
# Phase 11 plan 11-03: agent-loop integration tests
# ---------------------------------------------------------------------------


def _setup_voice_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcript: str = "hello",
) -> dict[str, object]:
    """Wire all the common mocks for a voice-handler agent-flow test."""
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    mock_send_action = _stub_typing(monkeypatch)
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", MagicMock(return_value=transcript))

    async def fake_download(custom_path: object) -> None:
        Path(str(custom_path)).write_bytes(b"\x00")

    fake_file = SimpleNamespace(download_to_drive=fake_download)

    async def fake_get_file(self: object) -> object:
        return fake_file

    monkeypatch.setattr(Voice, "get_file", fake_get_file)
    return {"reply": mock_reply, "send_action": mock_send_action}


def test_voice_handler_resumes_existing_session_and_saves_new_id(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored session id is forwarded to run_turn; the resolved id persists.

    The session-store helpers are patched at the handler-module namespace
    (the handlers do ``from tempo.bot.sessions import ...`` so we patch the
    rebound names there). ``get_or_create_session`` returns ``sess-OLD``;
    ``run_turn`` must be awaited with that id; the AgentTurn surfaces the
    same id (Claude Code echoes resumed ids back); ``save_session`` is
    called with the same id.
    """
    _clear_env(monkeypatch)
    mocks = _setup_voice_mocks(monkeypatch)

    monkeypatch.setattr(
        "tempo.bot.handlers.get_or_create_session", MagicMock(return_value="sess-OLD")
    )
    fake_run_turn = AsyncMock(
        return_value=_make_agent_turn(text="resumed reply", session_id="sess-OLD")
    )
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    fake_save = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.save_session", fake_save)

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    rt_args, _ = fake_run_turn.call_args
    assert rt_args[1] == "sess-OLD"
    fake_save.assert_called_once()
    saved_args = fake_save.call_args.args
    assert saved_args[1] == 987654321
    assert saved_args[2] == "sess-OLD"
    body = mocks["reply"].await_args.args[0]  # type: ignore[index]
    assert body == "resumed reply"


def test_voice_handler_starts_fresh_session_when_window_expired(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_or_create_session` returns None -> run_turn called with None."""
    _clear_env(monkeypatch)
    _setup_voice_mocks(monkeypatch)

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    fake_run_turn = AsyncMock(
        return_value=_make_agent_turn(text="fresh reply", session_id="sess-FRESH")
    )
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    fake_save = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.save_session", fake_save)

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    rt_args, _ = fake_run_turn.call_args
    assert rt_args[1] is None
    fake_save.assert_called_once()
    assert fake_save.call_args.args[2] == "sess-FRESH"


def test_voice_handler_long_reply_is_split_into_chunks_with_prefix(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent text > 4096 chars -> multiple reply_text calls, each prefixed [k/N]."""
    _clear_env(monkeypatch)
    mocks = _setup_voice_mocks(monkeypatch)

    # Build a long agent reply by concatenating paragraphs that together
    # exceed 4096 chars. ``format_for_telegram`` will paragraph-split first
    # and then chunk; we want at least 2 chunks.
    paragraph = "x" * 1500
    long_text = "\n\n".join([paragraph] * 4)  # ~6000 chars
    assert len(long_text) > 4096

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    monkeypatch.setattr(
        "tempo.bot.handlers.run_turn",
        AsyncMock(return_value=_make_agent_turn(text=long_text, session_id="sess-L")),
    )
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    assert mocks["reply"].await_count >= 2  # type: ignore[attr-defined]
    # Each chunk is <= 4096 chars and starts with [k/N] for k = 1..N.
    seen_prefixes: list[str] = []
    for call in mocks["reply"].await_args_list:  # type: ignore[attr-defined]
        body = call.args[0]
        assert len(body) <= 4096
        assert body.startswith("[")
        # extract "[k/N] " prefix
        prefix_end = body.index(" ") + 1
        seen_prefixes.append(body[:prefix_end])
    # Prefixes are 1-indexed and contiguous: [1/N], [2/N], ...
    n = mocks["reply"].await_count  # type: ignore[attr-defined]
    expected = [f"[{i}/{n}] " for i in range(1, n + 1)]
    assert seen_prefixes == expected


def test_voice_handler_missing_cli_replies_with_clear_message(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AgentInvocationError` -> single MISSING_CLI_REPLY; no exception escapes."""
    _clear_env(monkeypatch)
    mocks = _setup_voice_mocks(monkeypatch)

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    monkeypatch.setattr(
        "tempo.bot.handlers.run_turn",
        AsyncMock(side_effect=AgentInvocationError("claude CLI not found")),
    )
    save_mock = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.save_session", save_mock)

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    # The handler must NOT re-raise.
    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    mocks["reply"].assert_awaited_once()  # type: ignore[attr-defined]
    body = mocks["reply"].await_args.args[0]  # type: ignore[index]
    assert body == MISSING_CLI_REPLY
    # No session was persisted -- agent failed before save_session.
    save_mock.assert_not_called()


def test_voice_handler_does_not_echo_raw_transcript_anymore(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative regression: the Phase 10 `<i>{transcript}</i>` echo is GONE.

    Even with the agent returning a known reply, NO reply body should equal
    the italics-wrapped transcript. Guards against accidental revival of
    the Phase 10 behaviour.
    """
    _clear_env(monkeypatch)
    mocks = _setup_voice_mocks(monkeypatch, transcript="hello world this is a test")

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    monkeypatch.setattr(
        "tempo.bot.handlers.run_turn",
        AsyncMock(return_value=_make_agent_turn(text="agent reply", session_id="sess-X")),
    )
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    for call in mocks["reply"].await_args_list:  # type: ignore[attr-defined]
        body = call.args[0]
        assert body != "<i>hello world this is a test</i>"


def test_voice_handler_empty_transcript_skips_agent(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty transcript (no speech) -> (no speech detected) reply, no run_turn."""
    _clear_env(monkeypatch)
    mocks = _setup_voice_mocks(monkeypatch, transcript="")

    no_run_turn = AsyncMock(
        side_effect=AssertionError("run_turn must not be called for empty transcripts")
    )
    monkeypatch.setattr("tempo.bot.handlers.run_turn", no_run_turn)

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    mocks["reply"].assert_awaited_once()  # type: ignore[attr-defined]
    body = mocks["reply"].await_args.args[0]  # type: ignore[index]
    assert body == EMPTY_TRANSCRIPT_REPLY
    no_run_turn.assert_not_called()


def test_voice_handler_logs_token_usage_at_info(
    tempo_data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-turn INFO log line includes chat id, short session id, tokens, cost, wall."""
    _clear_env(monkeypatch)
    _setup_voice_mocks(monkeypatch)

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    monkeypatch.setattr(
        "tempo.bot.handlers.run_turn",
        AsyncMock(
            return_value=_make_agent_turn(
                text="agent reply",
                session_id="sess-LONGENOUGH-12345",
                tokens_in=10,
                tokens_out=20,
                cost_usd=0.001,
                duration_s=1.5,
            )
        ),
    )
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    with caplog.at_level(logging.INFO, logger="tempo.bot.handlers"):
        asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    matching = [rec for rec in caplog.records if "agent turn" in rec.getMessage()]
    assert len(matching) == 1
    line = matching[0].getMessage()
    assert "chat=987654321" in line
    assert "session=sess-LON" in line  # first 8 chars of "sess-LONGENOUGH-12345"
    assert "tokens_in=10" in line
    assert "tokens_out=20" in line
    assert "cost=$0.0010" in line
    assert "wall=1.50s" in line


def test_voice_handler_logs_subscription_when_cost_is_none(
    tempo_data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When cost_usd is None (subscription users) the log line shows cost=subscription."""
    _clear_env(monkeypatch)
    _setup_voice_mocks(monkeypatch)

    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    monkeypatch.setattr(
        "tempo.bot.handlers.run_turn",
        AsyncMock(return_value=_make_agent_turn(text="reply", session_id="s1", cost_usd=None)),
    )
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    update = _make_voice_update(chat_id=987654321, file_size=2048, with_bot=False)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    with caplog.at_level(logging.INFO, logger="tempo.bot.handlers"):
        asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    matches = [r for r in caplog.records if "agent turn" in r.getMessage()]
    assert len(matches) == 1
    assert "cost=subscription" in matches[0].getMessage()


# ---------------------------------------------------------------------------
# text_handler
# ---------------------------------------------------------------------------


def test_text_handler_runs_agent_with_message_text_as_prompt(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-command text -> run_turn called with the message text as the prompt."""
    _clear_env(monkeypatch)
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    _stub_typing(monkeypatch)

    monkeypatch.setattr(
        "tempo.bot.handlers.get_or_create_session", MagicMock(return_value="sess-T")
    )
    fake_run_turn = AsyncMock(return_value=_make_agent_turn(text="text reply", session_id="sess-T"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    fake_save = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.save_session", fake_save)
    # transcribe_file must NEVER be touched on the text path.
    monkeypatch.setattr(
        "tempo.bot.handlers.transcribe_file",
        MagicMock(side_effect=AssertionError("transcribe must not be called for text")),
    )

    settings = Settings(_env_file=None)
    update = _make_text_update(chat_id=987654321, text="how's my training looking?")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(text_handler(update, context))  # type: ignore[arg-type]

    fake_run_turn.assert_awaited_once()
    args, _ = fake_run_turn.call_args
    assert args[0] == "how's my training looking?"
    assert args[1] == "sess-T"

    fake_save.assert_called_once()
    assert fake_save.call_args.args[1] == 987654321
    assert fake_save.call_args.args[2] == "sess-T"

    mock_reply.assert_awaited_once()
    assert mock_reply.await_args.args[0] == "text reply"
    assert mock_reply.await_args.kwargs.get("parse_mode") == ParseMode.HTML


def test_text_handler_empty_text_is_dropped_silently(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only text -> no run_turn, no reply."""
    _clear_env(monkeypatch)
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    _stub_typing(monkeypatch)

    no_run_turn = AsyncMock(side_effect=AssertionError("run_turn must not be called"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", no_run_turn)

    settings = Settings(_env_file=None)
    update = _make_text_update(chat_id=987654321, text="   \n\t  ")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(text_handler(update, context))  # type: ignore[arg-type]

    assert mock_reply.await_count == 0
    no_run_turn.assert_not_called()


def test_text_handler_defensive_check_rejects_mismatched_chat(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-handler chat-id mismatch -> silent drop, no agent call."""
    _clear_env(monkeypatch)
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    no_run_turn = AsyncMock(side_effect=AssertionError("run_turn must not be called"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", no_run_turn)

    settings = Settings(_env_file=None)
    update = _make_text_update(chat_id=111, text="hi")  # not owner
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(text_handler(update, context))  # type: ignore[arg-type]

    assert mock_reply.await_count == 0
    no_run_turn.assert_not_called()


# ---------------------------------------------------------------------------
# new_command_handler
# ---------------------------------------------------------------------------


def test_new_command_handler_resets_session_and_replies(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/new` -> reset_session called with the owner chat id; fixed reply sent."""
    _clear_env(monkeypatch)
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    fake_reset = MagicMock()
    monkeypatch.setattr("tempo.bot.handlers.reset_session", fake_reset)

    settings = Settings(_env_file=None)
    update = _make_new_command_update(chat_id=987654321)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(new_command_handler(update, context))  # type: ignore[arg-type]

    fake_reset.assert_called_once()
    # reset_session(conn, chat_id) -- second arg is chat_id
    assert fake_reset.call_args.args[1] == 987654321

    mock_reply.assert_awaited_once()
    assert mock_reply.await_args.args[0] == NEW_SESSION_REPLY
    assert mock_reply.await_args.kwargs.get("parse_mode") == ParseMode.HTML


def test_new_command_handler_defensive_check_rejects_mismatched_chat(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-handler chat-id mismatch -> silent drop, no reset_session call."""
    _clear_env(monkeypatch)
    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    no_reset = MagicMock(side_effect=AssertionError("reset_session must not be called"))
    monkeypatch.setattr("tempo.bot.handlers.reset_session", no_reset)

    settings = Settings(_env_file=None)
    update = _make_new_command_update(chat_id=111)  # not owner
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(new_command_handler(update, context))  # type: ignore[arg-type]

    assert mock_reply.await_count == 0


# ---------------------------------------------------------------------------
# Phase 12: voice-retention policy (VOICE_RETENTION_DAYS)
# ---------------------------------------------------------------------------


def test_cleanup_voice_file_deletes_when_retention_zero(tmp_path: Path) -> None:
    """retention=0 -> the file is unlinked. Privacy-safe default."""
    from tempo.bot.handlers import _cleanup_voice_file

    f = tmp_path / "msg.ogg"
    f.write_bytes(b"\x00\x01")
    _cleanup_voice_file(f, retention_days=0)
    assert not f.exists()


def test_cleanup_voice_file_keeps_when_retention_nonzero(tmp_path: Path) -> None:
    """retention>0 -> file untouched; startup sweep is responsible later."""
    from tempo.bot.handlers import _cleanup_voice_file

    f = tmp_path / "msg.ogg"
    f.write_bytes(b"\x00\x01")
    _cleanup_voice_file(f, retention_days=7)
    assert f.exists()


def test_cleanup_voice_file_idempotent_on_missing(tmp_path: Path) -> None:
    """retention=0 on a missing file is a no-op (no FileNotFoundError)."""
    from tempo.bot.handlers import _cleanup_voice_file

    # Should not raise.
    _cleanup_voice_file(tmp_path / "does-not-exist.ogg", retention_days=0)


def test_voice_handler_keeps_file_when_retention_is_positive(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With VOICE_RETENTION_DAYS=7 the handler leaves the .ogg on disk."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("VOICE_RETENTION_DAYS", "7")

    _setup_voice_mocks(monkeypatch)
    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))
    fake_run_turn = AsyncMock(return_value=_make_agent_turn(text="ok", session_id="sess-KEEP"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    assert settings.voice_retention_days == 7

    update = _make_voice_update(
        chat_id=987654321,
        file_size=2048,
        file_unique_id="keep01",
        message_id=99,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    # File survives the handler (the startup sweep deletes it later, not here).
    target = settings.voice_cache_dir / "99-keep01.ogg"
    assert target.exists()


def test_voice_handler_cleanup_runs_even_when_agent_raises(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retention=0 + run_turn raising != AgentInvocationError -> file still deleted.

    The finally-block guarantees no audio leak even on an unhandled exception
    inside the agent pipeline (matches the Phase 12 privacy invariant).
    """
    _clear_env(monkeypatch)
    _setup_voice_mocks(monkeypatch)
    monkeypatch.setattr("tempo.bot.handlers.get_or_create_session", MagicMock(return_value=None))

    fake_run_turn = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", fake_run_turn)
    monkeypatch.setattr("tempo.bot.handlers.save_session", MagicMock())

    settings = Settings(_env_file=None)
    update = _make_voice_update(
        chat_id=987654321,
        file_size=2048,
        file_unique_id="boom01",
        message_id=77,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    # The exception is allowed to propagate (Phase 11 LIGHT error handling).
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    target = settings.voice_cache_dir / "77-boom01.ogg"
    assert not target.exists(), "finally-block must delete the .ogg even on agent failure"


def test_voice_handler_cleans_up_empty_transcript_path(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty transcript short-circuits the agent but still honours retention=0."""
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", MagicMock(return_value=""))

    async def fake_download(custom_path: object) -> None:
        Path(str(custom_path)).write_bytes(b"\x00")

    fake_file = SimpleNamespace(download_to_drive=fake_download)

    async def fake_get_file(self: object) -> object:
        return fake_file

    monkeypatch.setattr(Voice, "get_file", fake_get_file)

    no_run_turn = AsyncMock(side_effect=AssertionError("run_turn must not be called"))
    monkeypatch.setattr("tempo.bot.handlers.run_turn", no_run_turn)

    settings = Settings(_env_file=None)
    update = _make_voice_update(
        chat_id=987654321,
        file_size=2048,
        file_unique_id="empty01",
        message_id=55,
        with_bot=False,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data=_make_bot_data(owner_chat_id=987654321, tmp_path=tmp_path, settings=settings),
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    # Empty-transcript reply went out + the .ogg got cleaned up.
    mock_reply.assert_awaited_once()
    target = settings.voice_cache_dir / "55-empty01.ogg"
    assert not target.exists()
