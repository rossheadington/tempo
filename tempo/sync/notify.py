"""One-shot Telegram notifier for unattended jobs (cron / systemd / launchd).

The hourly ``tempo sync --notify-on-failure`` job uses this to send the owner a
single Telegram message when a sync run fails -- silent on success. We do NOT
pull in the full ``python-telegram-bot`` Application stack for one message:
``urllib.request`` against the Bot API is stdlib-only, fast, and works on the
Raspberry Pi without any extra wheels.

Design notes
------------
* **Silent if unconfigured.** Missing ``TELEGRAM_BOT_TOKEN`` or
  ``TELEGRAM_OWNER_CHAT_ID`` in settings -> the notifier is a no-op. Lets the
  same flag work on a machine where the bot isn't set up yet.
* **Best-effort.** A failed send is logged and swallowed -- the notifier
  exists to surface job failures, not to BE another failure source.
* **HTML mode.** Matches what the bot already uses everywhere (handlers reply
  with ``ParseMode.HTML``); keeps formatting consistent.
* **No retries.** A transient Telegram outage means we miss one notification;
  the next failure (if any) will retry.

Public surface
--------------
* :func:`format_failure_message` -- compose the HTML body from a list of
  :class:`tempo.sync.pipeline.SourceResult`.
* :func:`send_failure_alert` -- post the body to Telegram (silent on missing
  config; logs + swallows send failures).
* :func:`send_exception_alert` -- catastrophic-error variant for an uncaught
  exception around the whole sync invocation.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable

from tempo.config import Settings
from tempo.sync.pipeline import SourceResult

logger = logging.getLogger(__name__)

#: Telegram Bot API endpoint template. The bot token is interpolated into the
#: path at send-time; the body is a urlencoded form.
_TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

#: Send timeout (seconds). Telegram's median latency is ~200ms; 10s is
#: generous without risking a stuck cron job.
_SEND_TIMEOUT_S = 10


def _bot_credentials(settings: Settings) -> tuple[str, int] | None:
    """Return ``(bot_token, owner_chat_id)`` if both are set; else ``None``."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_owner_chat_id
    if token is None or chat_id is None:
        return None
    return token.get_secret_value(), int(chat_id)


def format_failure_message(results: Iterable[SourceResult]) -> str:
    """Format a HTML message describing per-source sync failures.

    Only ``ok=False`` results are listed -- a fully-successful run produces
    an empty string and the caller should skip the send. Each line names the
    source and quotes the connector's ``detail`` so the user can act without
    opening the log.
    """
    failures = [r for r in results if not r.ok]
    if not failures:
        return ""
    lines = ["<b>⚠️ Tempo sync failed</b>"]
    for r in failures:
        source = html.escape(str(r.source), quote=False)
        detail = html.escape(str(r.detail), quote=False)
        lines.append(f"• <b>{source}</b>: {detail}")
    return "\n".join(lines)


def format_exception_message(exc: BaseException) -> str:
    """Format a HTML message for a catastrophic uncaught sync exception."""
    cls = type(exc).__name__
    msg = html.escape(str(exc), quote=False) or "(no message)"
    return f"<b>❌ Tempo sync crashed</b>\n<code>{cls}</code>: {msg}"


def _post_message(token: str, chat_id: int, body: str) -> None:
    """POST ``body`` to Telegram as the configured bot. Raises on HTTP failure."""
    url = _TELEGRAM_SEND_URL.format(token=token)
    payload = urllib.parse.urlencode(
        {
            "chat_id": str(chat_id),
            "text": body,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT_S) as resp:  # noqa: S310 - fixed Telegram URL
        # Telegram returns 200 even for {"ok": false}; inspect the JSON.
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"telegram returned non-JSON: {raw[:200]!r}") from exc
        if not data.get("ok"):
            description = data.get("description", "(no description)")
            raise RuntimeError(f"telegram rejected message: {description}")


def send_failure_alert(settings: Settings, results: Iterable[SourceResult]) -> bool:
    """Send a per-source failure summary to the owner. Returns True if a message went out.

    Silent (returns False) when:

    * Telegram is not configured in ``.env`` (no token / no owner chat id), OR
    * every result has ``ok=True`` (nothing to notify about).

    A send failure is logged at WARNING and swallowed -- the notifier must
    never crash the calling job.
    """
    body = format_failure_message(results)
    if not body:
        return False
    return _send_or_log(settings, body)


def send_exception_alert(settings: Settings, exc: BaseException) -> bool:
    """Send a catastrophic-failure summary. Returns True if a message went out."""
    return _send_or_log(settings, format_exception_message(exc))


def _send_or_log(settings: Settings, body: str) -> bool:
    """Shared send path: resolve creds, post, swallow + log failures."""
    creds = _bot_credentials(settings)
    if creds is None:
        logger.debug("telegram notify skipped: bot not configured")
        return False
    token, chat_id = creds
    try:
        _post_message(token, chat_id, body)
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        logger.warning("telegram notify failed: %s", exc)
        return False
    return True
