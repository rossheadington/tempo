"""Tests for ``tempo.bot.handlers.voice_handler`` (Phase 10 / VOICE-03/04/06).

Covers the second half of Phase 10: the voice-memo handler that ties the
warmed faster-whisper singleton (Plan 10-01) to the Telegram dispatcher.

What this file proves:

* **20 MB pre-download guard (VOICE-03)** -- an oversized voice memo is
  rejected with the fixed user-facing reply and ``voice.get_file()`` is NEVER
  called (patched to raise so a regression would be loud).
* **Owner-only filter (VOICE-01 carry-over)** -- the registered
  ``MessageHandler`` only passes ``filters.VOICE & filters.Chat(owner)``;
  a non-owner voice Update returns falsy from ``check_update``.
* **Happy path (VOICE-04 + VOICE-06)** -- with a mocked ``transcribe_file``
  and a mocked ``get_file().download_to_drive``, the handler writes the .ogg
  to ``<voice_cache_dir>/<message_id>-<file_unique_id>.ogg``, calls
  ``transcribe_file`` with that path, and replies with
  ``<i>{html.escape(transcript)}</i>`` + ``ParseMode.HTML``.
* **HTML escaping** -- transcripts containing ``<``/``>``/``&`` survive
  through ``html.escape`` so Telegram does not parse them as markup.
* **Defensive chat-id re-check** -- belt-and-braces: even if the registration
  filter were bypassed, an in-handler chat-id mismatch produces no reply.
* **Cache dir mode 0700** -- ``voice_cache_dir`` is created with mode 0700
  on first use, mirroring the existing ``data_dir``/``tokens_dir`` pattern.

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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update, User, Voice
from telegram.constants import ParseMode
from telegram.ext import MessageHandler, filters

from tempo.bot import MAX_VOICE_BYTES, voice_handler
from tempo.bot.app import build_application
from tempo.bot.handlers import OVERSIZED_REPLY
from tempo.config import Settings

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
    non_owner_update = _make_voice_update(
        chat_id=111, file_size=4096, message_id=99, with_bot=True
    )
    assert not handler.check_update(non_owner_update)


# ---------------------------------------------------------------------------
# Happy path: download, transcribe, reply
# ---------------------------------------------------------------------------


def test_voice_handler_happy_path_writes_file_transcribes_and_replies(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small owner voice memo flows download -> transcribe -> italics reply.

    Proves the full VOICE-04 + VOICE-06 chain end-to-end with the model and
    the network mocked out: the cache dir is auto-created, the .ogg lands at
    ``<voice_cache_dir>/<message_id>-<file_unique_id>.ogg``, ``transcribe_file``
    is called with that path, and the reply is the transcript in italics.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    # MagicMock (NOT AsyncMock): the handler wraps the call in asyncio.to_thread,
    # which awaits a SYNC callable. Returning an AsyncMock would surface as a
    # coroutine string, not the transcript.
    fake_transcribe = MagicMock(return_value="hello world this is a test")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)

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
            bot_data={"owner_chat_id": 987654321, "settings": settings},
        ),
    )

    async def _run() -> None:
        await voice_handler(update, context)  # type: ignore[arg-type]

    asyncio.run(_run())

    # Cache dir was created.
    assert settings.voice_cache_dir.exists()
    assert settings.voice_cache_dir.is_dir()

    # The .ogg landed at the expected deterministic path.
    expected_path = settings.voice_cache_dir / "42-abc123.ogg"
    assert expected_path.exists()
    assert written_paths == [expected_path]

    # transcribe_file was called with the same path.
    fake_transcribe.assert_called_once()
    args, _kwargs = fake_transcribe.call_args
    assert args == (expected_path,)

    # Reply is the transcript wrapped in italics, HTML parse mode.
    mock_reply.assert_awaited_once()
    call = mock_reply.await_args
    assert call.args[0] == "<i>hello world this is a test</i>"
    assert call.kwargs.get("parse_mode") == ParseMode.HTML


def test_voice_handler_escapes_html_in_transcript(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transcripts with HTML-special chars are escaped before sending.

    Without ``html.escape``, a transcript like "3 < 4 & 5 > 4" would either
    be parsed as malformed HTML by Telegram or trigger a BadRequest. Escape
    is mandatory because transcript text is untrusted user-generated content.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    fake_transcribe = MagicMock(return_value="3 < 4 & 5 > 4")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)

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
            bot_data={"owner_chat_id": 987654321, "settings": settings},
        ),
    )

    asyncio.run(voice_handler(update, context))  # type: ignore[arg-type]

    mock_reply.assert_awaited_once()
    body = mock_reply.await_args.args[0]
    assert body == "<i>3 &lt; 4 &amp; 5 &gt; 4</i>"


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
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``voice_cache_dir`` is created with mode 0700 on first use.

    Mirrors the existing ``data_dir``/``tokens_dir`` 0700 convention -- a
    voice memo can contain audio of a confidential conversation, so other
    local users must not be able to read it.
    """
    _clear_env(monkeypatch)

    mock_reply = AsyncMock()
    monkeypatch.setattr(Message, "reply_text", mock_reply)

    fake_transcribe = MagicMock(return_value="ok")
    monkeypatch.setattr("tempo.bot.handlers.transcribe_file", fake_transcribe)

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
            bot_data={"owner_chat_id": 987654321, "settings": settings},
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
