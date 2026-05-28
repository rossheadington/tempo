"""Tests for runos.config (typed settings, paths, dir creation)."""

from __future__ import annotations

import stat
from pathlib import Path

from runos.config import Settings, get_settings


def test_data_dir_from_env(runos_data_dir: Path) -> None:
    settings = get_settings()
    assert settings.data_dir == runos_data_dir


def test_default_data_dir_outside_repo(monkeypatch) -> None:
    # With no override, the data dir defaults under the home directory, i.e.
    # OUTSIDE the repository tree — the core privacy guarantee.
    monkeypatch.delenv("RUNOS_DATA_DIR", raising=False)
    settings = Settings(_env_file=None)
    assert settings.data_dir == Path.home() / ".runos"
    # And it is not inside the current working tree.
    assert Path.cwd() not in settings.data_dir.parents


def test_derived_paths(runos_data_dir: Path) -> None:
    settings = get_settings()
    assert settings.db_path == runos_data_dir / "runos.db"
    assert settings.tokens_dir == runos_data_dir / "tokens"
    assert settings.reports_dir == runos_data_dir / "reports"


def test_content_dir_defaults_to_data_dir(runos_data_dir: Path) -> None:
    # With no content_dir, races/heat/reports live under data_dir (back-compat).
    settings = get_settings()
    assert settings.content_root == runos_data_dir
    assert settings.reports_dir == runos_data_dir / "reports"
    assert settings.races_path == runos_data_dir / "races.md"
    assert settings.heat_path == runos_data_dir / "heat.md"


def test_preferences_path_derived_under_content_root(
    runos_data_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """Phase 17: preferences.md is resolved under content_root, alongside races.md / food.md."""
    # 1. With no RUNOS_CONTENT_DIR override, preferences.md lives under data_dir.
    settings = get_settings()
    assert settings.preferences_path == runos_data_dir / "preferences.md"

    # 2. With RUNOS_CONTENT_DIR set, it follows content_root.
    content = tmp_path / "training"
    monkeypatch.setenv("RUNOS_CONTENT_DIR", str(content))
    settings = get_settings()
    assert settings.preferences_path == content / "preferences.md"


def test_phase17_migrated_settings_fields_removed() -> None:
    """Phase 17 deleted the five .env-sourced physiology / nutrition knobs.

    They moved to preferences.md (parsed via runos.analysis.preferences); the
    Settings class must no longer expose them as attributes.
    """
    settings = Settings(_env_file=None)
    for removed in (
        "threshold_pace_s_per_km",
        "max_hr",
        "resting_hr",
        "threshold_hr",
        "target_kcal_default",
    ):
        assert not hasattr(settings, removed), (
            f"Settings still exposes deleted field {removed!r}"
        )


def test_content_dir_redirects_content_only(runos_data_dir: Path, tmp_path: Path) -> None:
    # content_dir moves races/heat/reports, but the DB and tokens stay in data_dir
    # (the privacy boundary): secrets/state never follow the human-readable files.
    content = tmp_path / "project-training"
    settings = Settings(data_dir=runos_data_dir, content_dir=content)
    assert settings.races_path == content / "races.md"
    assert settings.heat_path == content / "heat.md"
    assert settings.reports_dir == content / "reports"
    # DB + tokens unaffected.
    assert settings.db_path == runos_data_dir / "runos.db"
    assert settings.tokens_dir == runos_data_dir / "tokens"


def test_no_plan_path_attribute_on_settings() -> None:
    # Plan 08-05 retired plan.md infrastructure entirely; pin the removal.
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "plan_path")


def test_tilde_is_expanded(monkeypatch) -> None:
    monkeypatch.setenv("RUNOS_DATA_DIR", "~/somewhere/tempo")
    settings = get_settings()
    assert "~" not in str(settings.db_path)
    assert settings.data_dir.is_absolute()


def test_ensure_dirs_creates_private_dirs(runos_data_dir: Path) -> None:
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
    monkeypatch.setenv("RUNOS_STRAVA_CLIENT_ID", "12345")
    settings = Settings(_env_file=None)
    assert settings.strava_client_id == "12345"


# ---- Whisper transcription (Phase 10 / v1.1) --------------------------------
#
# All four tests defensively `delenv` every WHISPER_* var first because the
# shared ``runos_data_dir`` fixture only scrubs RUNOS_*-prefixed vars; a
# developer's real ``.env`` could otherwise leak whisper config into these
# tests. We use ``Settings(_env_file=None)`` for the same reason.


def _clear_whisper_env(monkeypatch) -> None:
    """Drop every WHISPER_* env var so a real .env cannot leak into tests."""
    for key in (
        "WHISPER_MODEL_NAME",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_DEVICE",
        "RUNOS_WHISPER_MODEL_NAME",
        "RUNOS_WHISPER_COMPUTE_TYPE",
        "RUNOS_WHISPER_DEVICE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_whisper_defaults(monkeypatch) -> None:
    _clear_whisper_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.whisper_model_name == "small.en"
    assert settings.whisper_compute_type == "int8"
    assert settings.whisper_device == "cpu"


def test_whisper_model_name_env_override(monkeypatch) -> None:
    _clear_whisper_env(monkeypatch)
    monkeypatch.setenv("WHISPER_MODEL_NAME", "base.en")
    # The prefixed form must NOT take effect -- validation_alias bypasses the
    # RUNOS_ env_prefix entirely (proves the alias overrides the prefix).
    monkeypatch.setenv("RUNOS_WHISPER_MODEL_NAME", "large-v3-turbo")
    settings = Settings(_env_file=None)
    assert settings.whisper_model_name == "base.en"


def test_whisper_compute_type_and_device_env_override(monkeypatch) -> None:
    _clear_whisper_env(monkeypatch)
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    settings = Settings(_env_file=None)
    assert settings.whisper_compute_type == "float16"
    assert settings.whisper_device == "cuda"


def test_voice_cache_dir_derived_under_content_root(
    runos_data_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    _clear_whisper_env(monkeypatch)
    # 1. With RUNOS_CONTENT_DIR unset, voice_cache_dir lives under data_dir.
    settings = Settings(_env_file=None)
    assert settings.voice_cache_dir == runos_data_dir / "voice"

    # 2. With RUNOS_CONTENT_DIR set, voice_cache_dir follows content_root.
    content = tmp_path / "training"
    monkeypatch.setenv("RUNOS_CONTENT_DIR", str(content))
    settings = Settings(_env_file=None)
    assert settings.voice_cache_dir == content / "voice"


def test_ensure_dirs_does_not_create_voice_cache_dir(runos_data_dir: Path) -> None:
    # Plan 10-01 explicitly keeps voice/ out of ensure_dirs() so `runos init`
    # does not surface it for users who never run the bot. The voice handler
    # (Plan 10-02) is responsible for lazy 0700 creation on first use.
    settings = get_settings()
    settings.ensure_dirs()
    assert not settings.voice_cache_dir.exists()


def test_voice_retention_days_defaults_to_zero(runos_data_dir: Path, monkeypatch) -> None:
    """Phase 12 default: delete voice files immediately after transcription."""
    monkeypatch.delenv("VOICE_RETENTION_DAYS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.voice_retention_days == 0


def test_voice_retention_days_env_override(runos_data_dir: Path, monkeypatch) -> None:
    """VOICE_RETENTION_DAYS (BARE env-var name, not RUNOS_-prefixed) parses to int."""
    monkeypatch.setenv("VOICE_RETENTION_DAYS", "7")
    settings = Settings(_env_file=None)
    assert settings.voice_retention_days == 7
    assert isinstance(settings.voice_retention_days, int)
