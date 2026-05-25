"""Typed configuration and secrets for Tempo.

Settings load from a gitignored ``.env`` (and the process environment) via
``pydantic-settings``. Crucially, *runtime data lives outside the repo tree* by
default: the SQLite DB, OAuth tokens, and generated reports all default to paths
under ``~/.tempo/`` so an accidental ``git add .`` from the repo root can never
sweep up a secret or any health data. See ``.env.example`` for documented config.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Default runtime data directory, outside the repository tree."""
    return Path.home() / ".tempo"


class Settings(BaseSettings):
    """Tempo runtime settings.

    All paths default to locations *outside* the repository so that secrets and
    health data are physically incapable of being committed to the public repo.
    Override any value via environment variables prefixed with ``TEMPO_`` or via
    a gitignored ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="TEMPO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Runtime data location (outside the repo tree) ----
    data_dir: Path = Field(
        default_factory=_default_data_dir,
        description="Root directory for all local runtime data (DB, tokens, reports).",
    )

    # ---- Strava OAuth (Phase 2 wires these in; documented here from day one) ----
    strava_client_id: str | None = Field(
        default=None, description="Strava API application client ID."
    )
    strava_client_secret: str | None = Field(
        default=None, description="Strava API application client secret."
    )
    strava_redirect_uri: str = Field(
        default="http://localhost",
        description=(
            "OAuth redirect URI registered on the Strava API app. For the manual "
            "copy-the-code handshake, Strava only requires the host to match the "
            "app's 'Authorization Callback Domain' (localhost works)."
        ),
    )

    # ---- Garmin credentials (Phase 6) ----
    garmin_email: str | None = Field(default=None, description="Garmin Connect account email.")
    garmin_password: str | None = Field(
        default=None, description="Garmin Connect account password."
    )

    # ---- Load / analysis settings (Phase 4) ----
    threshold_pace_s_per_km: float | None = Field(
        default=None,
        description=(
            "Functional threshold pace in seconds per km (the pace you could hold "
            "for ~1 hour all-out). Required for pace-based rTSS load. e.g. 240 = "
            "4:00/km. If unset, load falls back to hrTSS when HR data exists."
        ),
    )
    max_hr: int | None = Field(
        default=None,
        description="Maximum heart rate (bpm). Used by the hrTSS load fallback.",
    )
    resting_hr: int | None = Field(
        default=None,
        description="Resting heart rate (bpm). Used by the hrTSS (HRR) load fallback.",
    )
    threshold_hr: int | None = Field(
        default=None,
        description=(
            "Lactate-threshold heart rate (bpm) -- the HR you could hold for ~1 hour. "
            "Anchors hrTSS so 1 hour at threshold HR scores ~100, matching rTSS. If "
            "unset, it is estimated as ~0.92 * max_hr."
        ),
    )

    @field_validator("data_dir", mode="after")
    @classmethod
    def _expand_data_dir(cls, value: Path) -> Path:
        """Expand ``~`` and resolve the data dir to an absolute path."""
        return value.expanduser()

    # ---- Derived paths ----
    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.data_dir / "tempo.db"

    @property
    def tokens_dir(self) -> Path:
        """Directory holding OAuth/session tokens (mode 0700)."""
        return self.data_dir / "tokens"

    @property
    def reports_dir(self) -> Path:
        """Directory for generated markdown analysis reports (gitignored, local)."""
        return self.data_dir / "reports"

    @property
    def races_path(self) -> Path:
        """Path to the user-maintained races markdown (read for analysis context)."""
        return self.data_dir / "races.md"

    @property
    def plan_path(self) -> Path:
        """Path to the user-maintained training-plan markdown (read for context)."""
        return self.data_dir / "plan.md"

    def ensure_dirs(self) -> None:
        """Create the data, tokens, and reports directories with safe perms.

        The data dir and tokens dir are created with 0700 so other local users
        cannot read tokens. Idempotent.
        """
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.tokens_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly-loaded :class:`Settings` instance.

    Not cached so tests can override the environment between calls.
    """
    return Settings()
