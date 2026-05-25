"""Tests for tempo.config (typed settings, paths, dir creation)."""

from __future__ import annotations

import stat
from pathlib import Path

from tempo.config import Settings, get_settings


def test_data_dir_from_env(tempo_data_dir: Path) -> None:
    settings = get_settings()
    assert settings.data_dir == tempo_data_dir


def test_default_data_dir_outside_repo(monkeypatch) -> None:
    # With no override, the data dir defaults under the home directory, i.e.
    # OUTSIDE the repository tree — the core privacy guarantee.
    monkeypatch.delenv("TEMPO_DATA_DIR", raising=False)
    settings = Settings(_env_file=None)
    assert settings.data_dir == Path.home() / ".tempo"
    # And it is not inside the current working tree.
    assert Path.cwd() not in settings.data_dir.parents


def test_derived_paths(tempo_data_dir: Path) -> None:
    settings = get_settings()
    assert settings.db_path == tempo_data_dir / "tempo.db"
    assert settings.tokens_dir == tempo_data_dir / "tokens"
    assert settings.reports_dir == tempo_data_dir / "reports"


def test_tilde_is_expanded(monkeypatch) -> None:
    monkeypatch.setenv("TEMPO_DATA_DIR", "~/somewhere/tempo")
    settings = get_settings()
    assert "~" not in str(settings.db_path)
    assert settings.data_dir.is_absolute()


def test_ensure_dirs_creates_private_dirs(tempo_data_dir: Path) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    assert settings.data_dir.is_dir()
    assert settings.tokens_dir.is_dir()
    assert settings.reports_dir.is_dir()
    # Tokens dir should not be world/group readable (0700).
    mode = stat.S_IMODE(settings.tokens_dir.stat().st_mode)
    assert mode & 0o077 == 0


def test_secrets_default_to_none(monkeypatch) -> None:
    settings = Settings(_env_file=None)
    assert settings.strava_client_id is None
    assert settings.strava_client_secret is None
    assert settings.garmin_email is None
    assert settings.garmin_password is None


def test_env_overrides_secrets(monkeypatch) -> None:
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_ID", "12345")
    settings = Settings(_env_file=None)
    assert settings.strava_client_id == "12345"
