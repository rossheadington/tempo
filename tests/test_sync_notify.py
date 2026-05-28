"""Unit tests for the one-shot Telegram failure notifier (runos/sync/notify.py).

The notifier hits the Telegram Bot API directly via ``urllib.request`` so we
have a single seam to mock: ``runos.sync.notify._post_message``. Tests cover
the no-Telegram-configured silent path, the success path, the all-sources-ok
silent path, the per-source-failure message body, the catastrophic-exception
message body, and the send-failure-is-swallowed contract.
"""

from __future__ import annotations

from typing import Any
from urllib.error import URLError

import pytest

from runos.config import Settings
from runos.sync import notify
from runos.sync.pipeline import SourceResult


def _settings_with_bot(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Settings:
    """Settings with Telegram bot configured. ``_env_file=None`` neutralises the
    developer's real `.env` (which has the live bot token); env vars supply
    fake values instead."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-123")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "555")
    return Settings(_env_file=None)


def _settings_without_bot(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Settings:
    """Settings with NO Telegram bot configured. ``_env_file=None`` is
    essential here -- the dev's real .env would otherwise leak in."""
    monkeypatch.setenv("RUNOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_OWNER_CHAT_ID", raising=False)
    return Settings(_env_file=None)


# ---- format_failure_message -----------------------------------------------


def test_format_failure_message_empty_when_all_ok() -> None:
    """All sources ok -> empty string, caller skips the send."""
    results = [
        SourceResult("strava", ok=True, detail="ok", rows=42),
        SourceResult("garmin", ok=True, detail="ok", rows=3),
    ]
    assert notify.format_failure_message(results) == ""


def test_format_failure_message_lists_only_failures() -> None:
    """Only ok=False sources appear; ok ones are omitted."""
    results = [
        SourceResult("strava", ok=True, detail="ok", rows=10),
        SourceResult("garmin", ok=False, detail="429 rate-limit"),
    ]
    body = notify.format_failure_message(results)
    assert "RunOS sync failed" in body
    assert "garmin" in body
    assert "429 rate-limit" in body
    assert "strava" not in body


def test_format_failure_message_escapes_html_in_detail() -> None:
    """A detail string with < / > / & must be HTML-escaped to keep parse_mode HTML valid."""
    results = [SourceResult("garmin", ok=False, detail="bad <tag> & </tag>")]
    body = notify.format_failure_message(results)
    assert "&lt;tag&gt;" in body
    assert "&amp;" in body
    assert "<tag>" not in body


# ---- format_exception_message ---------------------------------------------


def test_format_exception_message_includes_class_and_str() -> None:
    """Catastrophic message names the exception class + message."""
    exc = RuntimeError("connector unavailable")
    body = notify.format_exception_message(exc)
    assert "RunOS sync crashed" in body
    assert "RuntimeError" in body
    assert "connector unavailable" in body


def test_format_exception_message_handles_empty_str_message() -> None:
    """An exception with no message still produces a readable body."""
    exc = RuntimeError()
    body = notify.format_exception_message(exc)
    assert "RuntimeError" in body
    assert "(no message)" in body


# ---- send_failure_alert ---------------------------------------------------


def test_send_failure_alert_silent_when_bot_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Missing TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_CHAT_ID -> no-op, returns False."""
    settings = _settings_without_bot(monkeypatch, tmp_path)
    posted: list[Any] = []
    monkeypatch.setattr(
        notify,
        "_post_message",
        lambda *args, **kwargs: posted.append((args, kwargs)),
    )
    results = [SourceResult("garmin", ok=False, detail="429")]
    sent = notify.send_failure_alert(settings, results)
    assert sent is False
    assert posted == []


def test_send_failure_alert_silent_when_all_results_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """All ok -> no message even when bot is configured. Returns False."""
    settings = _settings_with_bot(monkeypatch, tmp_path)
    posted: list[Any] = []
    monkeypatch.setattr(
        notify,
        "_post_message",
        lambda *args, **kwargs: posted.append((args, kwargs)),
    )
    results = [
        SourceResult("strava", ok=True, detail="ok", rows=10),
        SourceResult("garmin", ok=True, detail="ok", rows=3),
    ]
    sent = notify.send_failure_alert(settings, results)
    assert sent is False
    assert posted == []


def test_send_failure_alert_posts_when_any_source_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Any ok=False source triggers a real send with the failure body."""
    settings = _settings_with_bot(monkeypatch, tmp_path)
    posted: list[tuple[str, int, str]] = []

    def fake_post(token: str, chat_id: int, body: str) -> None:
        posted.append((token, chat_id, body))

    monkeypatch.setattr(notify, "_post_message", fake_post)

    results = [
        SourceResult("strava", ok=True, detail="ok", rows=10),
        SourceResult("garmin", ok=False, detail="429 rate-limit"),
    ]
    sent = notify.send_failure_alert(settings, results)
    assert sent is True
    assert len(posted) == 1
    token, chat_id, body = posted[0]
    assert token == "fake-token-123"
    assert chat_id == 555
    assert "RunOS sync failed" in body
    assert "garmin" in body
    assert "429 rate-limit" in body


def test_send_failure_alert_swallows_telegram_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A network/HTTP failure during send is logged and swallowed; returns False.

    The notifier exists to surface job failures, not to BE another failure
    source -- a Telegram outage must never crash the calling sync job.
    """
    settings = _settings_with_bot(monkeypatch, tmp_path)

    def fake_post(token: str, chat_id: int, body: str) -> None:
        raise URLError("DNS lookup failed")

    monkeypatch.setattr(notify, "_post_message", fake_post)

    results = [SourceResult("garmin", ok=False, detail="429")]
    with caplog.at_level("WARNING", logger="runos.sync.notify"):
        sent = notify.send_failure_alert(settings, results)
    assert sent is False
    assert "telegram notify failed" in caplog.text


def test_send_failure_alert_swallows_runtime_telegram_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A Telegram-side rejection (RuntimeError from _post_message) is also swallowed."""
    settings = _settings_with_bot(monkeypatch, tmp_path)

    def fake_post(token: str, chat_id: int, body: str) -> None:
        raise RuntimeError("telegram rejected message: chat not found")

    monkeypatch.setattr(notify, "_post_message", fake_post)

    results = [SourceResult("garmin", ok=False, detail="429")]
    with caplog.at_level("WARNING", logger="runos.sync.notify"):
        sent = notify.send_failure_alert(settings, results)
    assert sent is False
    assert "telegram notify failed" in caplog.text


# ---- send_exception_alert -------------------------------------------------


def test_send_exception_alert_posts_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """An uncaught exception triggers a 'crashed' message."""
    settings = _settings_with_bot(monkeypatch, tmp_path)
    posted: list[tuple[str, int, str]] = []

    def fake_post(token: str, chat_id: int, body: str) -> None:
        posted.append((token, chat_id, body))

    monkeypatch.setattr(notify, "_post_message", fake_post)

    sent = notify.send_exception_alert(settings, ValueError("creds missing"))
    assert sent is True
    assert len(posted) == 1
    _, _, body = posted[0]
    assert "RunOS sync crashed" in body
    assert "ValueError" in body
    assert "creds missing" in body


def test_send_exception_alert_silent_when_bot_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """No bot configured -> no-op even for catastrophic exceptions."""
    settings = _settings_without_bot(monkeypatch, tmp_path)
    posted: list[Any] = []
    monkeypatch.setattr(
        notify,
        "_post_message",
        lambda *args, **kwargs: posted.append((args, kwargs)),
    )
    sent = notify.send_exception_alert(settings, RuntimeError("boom"))
    assert sent is False
    assert posted == []
