"""Pure read-only install-state detection for the setup wizard.

Answers a single question — *"what does this Tempo install look like right
now?"* — by reading the filesystem, the SQLite schema version, ``.env`` keys,
and the presence of launchd plist files. The function is **pure read-only**:

- never writes anything,
- never opens a network connection,
- never shells out to ``launchctl`` (Tempo never runs ``launchctl`` per
  project convention; plist-file presence is the contract),
- closes the SQLite connection it opens in a ``try/finally`` so detection
  never holds a writer lock.

The wizard step in Plan 14-02 uses these flags to decide whether each step
should auto-skip (``[done]``), prompt to keep/change, or run fresh.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tempo import db as _db
from tempo.config import Settings


@dataclass(frozen=True, slots=True)
class InstallState:
    """A snapshot of the seven things the setup wizard cares about.

    Each field is a boolean: ``True`` means the corresponding piece of setup
    is fully complete (env keys present **and** any required local artifact —
    token file, plist file, schema version — present). ``False`` means either
    fully absent or partially configured (one but not both). The wizard treats
    "partial" the same as "absent" for skip-vs-run decisions.
    """

    db_initialised: bool
    content_dir_set: bool
    strava_configured: bool
    garmin_configured: bool
    telegram_configured: bool
    daily_scheduler_installed: bool
    bot_scheduler_installed: bool


def _db_initialised(settings: Settings) -> bool:
    """Return ``True`` iff the SQLite DB exists AND is on the latest schema.

    A corrupt / non-Tempo SQLite file (or any DB-error) returns ``False`` so
    the wizard can offer to re-init rather than crashing on startup.
    """
    if not settings.db_path.exists():
        return False
    try:
        conn = _db.connect(settings.db_path)
    except sqlite3.DatabaseError:
        return False
    try:
        try:
            version = int(conn.execute("PRAGMA user_version;").fetchone()[0])
        except sqlite3.DatabaseError:
            return False
        return version == _db.SCHEMA_VERSION
    finally:
        conn.close()


def _strava_configured(settings: Settings) -> bool:
    """Both env (client id + secret) AND a saved token file must be present."""
    env_ok = settings.strava_client_id is not None and settings.strava_client_secret is not None
    token_ok = (settings.tokens_dir / "strava_tokens.json").exists()
    return env_ok and token_ok


def _garmin_configured(settings: Settings) -> bool:
    """Both env (email + password) AND the garminconnect token dir must be present.

    ``garminconnect`` stores multiple files under ``tokens_dir/garmin/`` after a
    successful login; the directory's existence is the simplest proof of a
    completed login.
    """
    env_ok = settings.garmin_email is not None and settings.garmin_password is not None
    token_ok = (settings.tokens_dir / "garmin").exists()
    return env_ok and token_ok


def _telegram_configured(settings: Settings) -> bool:
    """Bot token + owner chat id env must both be set; no local token file applies."""
    return settings.telegram_bot_token is not None and settings.telegram_owner_chat_id is not None


def detect_install_state(settings: Settings) -> InstallState:
    """Read-only snapshot of which setup steps are complete.

    All seven checks are stdlib filesystem reads plus a single read-only SQLite
    connection for the schema-version check. No network, no ``launchctl``, no
    writes.
    """
    home = Path.home()
    daily_plist = home / "Library" / "LaunchAgents" / "com.tempo.daily.plist"
    bot_plist = home / "Library" / "LaunchAgents" / "com.tempo.telegram-bot.plist"

    return InstallState(
        db_initialised=_db_initialised(settings),
        content_dir_set=settings.content_dir is not None,
        strava_configured=_strava_configured(settings),
        garmin_configured=_garmin_configured(settings),
        telegram_configured=_telegram_configured(settings),
        daily_scheduler_installed=daily_plist.exists(),
        bot_scheduler_installed=bot_plist.exists(),
    )
