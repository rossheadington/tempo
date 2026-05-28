"""Tests for ``tempo.setup.state.detect_install_state`` (SETUP-02).

Combinatorial coverage of the seven InstallState fields across fresh / partial
/ full installs. Pure stdlib + pytest's ``tmp_path`` + ``monkeypatch``; no
network, no real ``~/Library/LaunchAgents/`` writes (HOME is redirected via
monkeypatch).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tempo import db as _db
from tempo.config import Settings
from tempo.setup.state import InstallState, detect_install_state


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` (and friends) to a per-test temp dir.

    ``Path.home()`` reads ``$HOME`` on macOS/Linux, so setting the env var
    before calling ``detect_install_state`` is sufficient to keep the test
    away from the real ``~/Library/LaunchAgents/``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _build_settings(tmp_path: Path, **overrides) -> Settings:
    """Construct a ``Settings`` pointing at ``tmp_path/data`` with overrides applied.

    pydantic-settings still reads ``.env`` if present — we set
    ``_env_file=None`` to disable that so individual tests have a clean slate.
    """
    return Settings(data_dir=tmp_path / "data", _env_file=None, **overrides)


def _init_fresh_db(settings: Settings) -> None:
    """Create + migrate the SQLite DB to the current SCHEMA_VERSION."""
    conn = _db.init_db(settings.db_path)
    conn.close()


def _touch_strava_token(settings: Settings) -> None:
    settings.tokens_dir.mkdir(parents=True, exist_ok=True)
    (settings.tokens_dir / "strava_tokens.json").write_text("{}", encoding="utf-8")


def _touch_garmin_token_dir(settings: Settings) -> None:
    (settings.tokens_dir / "garmin").mkdir(parents=True, exist_ok=True)


def _touch_launch_agent(plist_name: str, home: Path) -> None:
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / plist_name).write_text("<plist/>", encoding="utf-8")


# ---- Fresh install ----


def test_detect_install_state_fresh_install(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path)
    state = detect_install_state(settings)
    assert state == InstallState(
        db_initialised=False,
        content_dir_set=False,
        strava_configured=False,
        garmin_configured=False,
        telegram_configured=False,
        daily_scheduler_installed=False,
        bot_scheduler_installed=False,
    )


# ---- DB detection ----


def test_detect_install_state_db_initialised_only(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path)
    _init_fresh_db(settings)
    state = detect_install_state(settings)
    assert state.db_initialised is True
    # Everything else still False.
    assert state.strava_configured is False
    assert state.garmin_configured is False


def test_detect_install_state_corrupt_db_returns_false(
    tmp_path: Path, fake_home: Path
) -> None:
    settings = _build_settings(tmp_path)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.db_path.write_bytes(b"not a sqlite file at all")
    state = detect_install_state(settings)
    # Did not raise; reports as not-initialised.
    assert state.db_initialised is False


# ---- content_dir ----


def test_detect_install_state_content_dir_set(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path, content_dir=tmp_path / "my_content")
    state = detect_install_state(settings)
    assert state.content_dir_set is True


def test_detect_install_state_content_dir_unset(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path)  # no content_dir override
    state = detect_install_state(settings)
    assert state.content_dir_set is False


# ---- Strava requires BOTH env AND token ----


def test_strava_env_only_is_not_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path, strava_client_id="abc", strava_client_secret="def"
    )
    state = detect_install_state(settings)
    assert state.strava_configured is False


def test_strava_token_only_is_not_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path)
    _touch_strava_token(settings)
    state = detect_install_state(settings)
    assert state.strava_configured is False


def test_strava_env_and_token_is_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path, strava_client_id="abc", strava_client_secret="def"
    )
    _touch_strava_token(settings)
    state = detect_install_state(settings)
    assert state.strava_configured is True


# ---- Garmin requires BOTH env AND token dir ----


def test_garmin_env_only_is_not_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path, garmin_email="x@example.com", garmin_password="pw"
    )
    state = detect_install_state(settings)
    assert state.garmin_configured is False


def test_garmin_token_dir_only_is_not_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(tmp_path)
    _touch_garmin_token_dir(settings)
    state = detect_install_state(settings)
    assert state.garmin_configured is False


def test_garmin_env_and_token_dir_is_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path, garmin_email="x@example.com", garmin_password="pw"
    )
    _touch_garmin_token_dir(settings)
    state = detect_install_state(settings)
    assert state.garmin_configured is True


# ---- Telegram is env-only ----


def test_telegram_both_env_set_is_configured(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path,
        TELEGRAM_BOT_TOKEN="123:abc",
        TELEGRAM_OWNER_CHAT_ID=12345,
    )
    state = detect_install_state(settings)
    assert state.telegram_configured is True


def test_telegram_only_token_set_is_not_configured(
    tmp_path: Path, fake_home: Path
) -> None:
    settings = _build_settings(tmp_path, TELEGRAM_BOT_TOKEN="123:abc")
    state = detect_install_state(settings)
    assert state.telegram_configured is False


def test_telegram_only_chat_id_set_is_not_configured(
    tmp_path: Path, fake_home: Path
) -> None:
    settings = _build_settings(tmp_path, TELEGRAM_OWNER_CHAT_ID=12345)
    state = detect_install_state(settings)
    assert state.telegram_configured is False


# ---- launchd plists by file presence ----


def test_daily_scheduler_installed_from_launchagents_plist(
    tmp_path: Path, fake_home: Path
) -> None:
    settings = _build_settings(tmp_path)
    _touch_launch_agent("com.tempo.daily.plist", fake_home)
    state = detect_install_state(settings)
    assert state.daily_scheduler_installed is True
    assert state.bot_scheduler_installed is False


def test_bot_scheduler_installed_from_launchagents_plist(
    tmp_path: Path, fake_home: Path
) -> None:
    settings = _build_settings(tmp_path)
    _touch_launch_agent("com.tempo.telegram-bot.plist", fake_home)
    state = detect_install_state(settings)
    assert state.bot_scheduler_installed is True
    assert state.daily_scheduler_installed is False


# ---- Full install ----


def test_detect_install_state_all_present(tmp_path: Path, fake_home: Path) -> None:
    settings = _build_settings(
        tmp_path,
        content_dir=tmp_path / "my_content",
        strava_client_id="abc",
        strava_client_secret="def",
        garmin_email="x@example.com",
        garmin_password="pw",
        TELEGRAM_BOT_TOKEN="123:abc",
        TELEGRAM_OWNER_CHAT_ID=12345,
    )
    _init_fresh_db(settings)
    _touch_strava_token(settings)
    _touch_garmin_token_dir(settings)
    _touch_launch_agent("com.tempo.daily.plist", fake_home)
    _touch_launch_agent("com.tempo.telegram-bot.plist", fake_home)

    state = detect_install_state(settings)
    assert state == InstallState(
        db_initialised=True,
        content_dir_set=True,
        strava_configured=True,
        garmin_configured=True,
        telegram_configured=True,
        daily_scheduler_installed=True,
        bot_scheduler_installed=True,
    )


# ---- InstallState is frozen + slots ----


def test_install_state_is_frozen() -> None:
    state = InstallState(
        db_initialised=False,
        content_dir_set=False,
        strava_configured=False,
        garmin_configured=False,
        telegram_configured=False,
        daily_scheduler_installed=False,
        bot_scheduler_installed=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.db_initialised = True  # type: ignore[misc]


def test_install_state_has_slots() -> None:
    state = InstallState(
        db_initialised=False,
        content_dir_set=False,
        strava_configured=False,
        garmin_configured=False,
        telegram_configured=False,
        daily_scheduler_installed=False,
        bot_scheduler_installed=False,
    )
    # slots=True means no __dict__ — attribute assignment would fail even
    # without frozen, but frozen catches it first; the cleanest probe is the
    # absence of __dict__ entirely.
    assert not hasattr(state, "__dict__")
