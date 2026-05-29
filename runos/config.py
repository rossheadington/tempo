"""Typed configuration and secrets for RunOS.

Settings load from a gitignored ``.env`` (and the process environment) via
``pydantic-settings``. Crucially, *runtime data lives outside the repo tree* by
default: the SQLite DB, OAuth tokens, and generated reports all default to paths
under ``~/.runos/`` so an accidental ``git add .`` from the repo root can never
sweep up a secret or any health data. See ``.env.example`` for documented config.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """Default runtime data directory, outside the repository tree."""
    return Path.home() / ".runos"


class Settings(BaseSettings):
    """RunOS runtime settings.

    All paths default to locations *outside* the repository so that secrets and
    health data are physically incapable of being committed to the public repo.
    Override any value via environment variables prefixed with ``RUNOS_`` or via
    a gitignored ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="RUNOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Runtime data location (outside the repo tree) ----
    data_dir: Path = Field(
        default_factory=_default_data_dir,
        description="Root directory for secrets/state: the SQLite DB and tokens.",
    )

    # ---- Human-readable content location (plan, races, reports) ----
    content_dir: Path | None = Field(
        default=None,
        description=(
            "Directory for the files you read/edit -- the training plan, races, "
            "and generated reports. Defaults to data_dir. Point it at a gitignored "
            "folder in the project (RUNOS_CONTENT_DIR) to keep those files handy "
            "while the DB and tokens stay outside the repo in data_dir."
        ),
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

    # ---- Coros credentials (Phase 18) ----
    # Standard ``RUNOS_`` env-prefix applies, so these come from
    # RUNOS_COROS_EMAIL / RUNOS_COROS_PASSWORD in ``.env``. Password is
    # wrapped in ``SecretStr`` so it never prints in tracebacks or logs;
    # the connector unwraps it via ``.get_secret_value()`` only at the
    # MD5-hash call site.
    coros_email: str | None = Field(default=None, description="Coros account email.")
    coros_password: SecretStr | None = Field(
        default=None, description="Coros account password."
    )

    # ---- Telegram bot (Phase 9 / v1.1) ----
    # NOTE: these two fields are intentionally read from BARE env-var names
    # (TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_CHAT_ID) rather than the global
    # RUNOS_ prefix, so the standard Telegram convention is preserved.
    # validation_alias bypasses env_prefix for these specific fields.
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias="TELEGRAM_BOT_TOKEN",
        description=(
            "Telegram bot HTTP API token from @BotFather. Bare env-var name "
            "(NOT prefixed with RUNOS_) so the standard Telegram convention is "
            "preserved."
        ),
    )
    telegram_owner_chat_id: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_OWNER_CHAT_ID",
        description=(
            "Owner Telegram chat id (an integer). The bot only replies to this "
            "chat; everything else is silently dropped at the filter level."
        ),
    )

    # ---- Whisper transcription (Phase 10 / v1.1) ----
    # NOTE: as with the Telegram fields above, these three are read from BARE
    # env-var names (WHISPER_MODEL_NAME / WHISPER_COMPUTE_TYPE / WHISPER_DEVICE)
    # via validation_alias, bypassing the RUNOS_ prefix. faster-whisper itself
    # uses the WHISPER_* convention in its docs, so we preserve it.
    whisper_model_name: str = Field(
        default="small.en",
        validation_alias="WHISPER_MODEL_NAME",
        description=(
            "faster-whisper model name. Default small.en is the sane CPU choice "
            "on Apple Silicon; swap to base.en / medium.en / large-v3-turbo via "
            "env var."
        ),
    )
    whisper_compute_type: str = Field(
        default="int8",
        validation_alias="WHISPER_COMPUTE_TYPE",
        description=(
            "CTranslate2 compute type: int8 (default, fast on CPU) / "
            "int8_float16 / float16 / float32."
        ),
    )
    whisper_device: str = Field(
        default="cpu",
        validation_alias="WHISPER_DEVICE",
        description=(
            "cpu (default; CTranslate2 has no Metal/MPS support on Mac) or cuda on Linux+NVIDIA."
        ),
    )

    # ---- Voice cache retention (Phase 12 / v1.1) ----
    # Bare env-var name (NOT prefixed) so the convention matches the other
    # bot-side knobs (TELEGRAM_*, WHISPER_*). Default 0 = delete every voice
    # file immediately after transcription -- the privacy-safe default.
    voice_retention_days: int = Field(
        default=0,
        validation_alias="VOICE_RETENTION_DAYS",
        description=(
            "How many days to keep transcribed voice memos in voice_cache_dir. "
            "0 (default) = delete immediately after transcription. >0 = keep on "
            "disk for that many days; a startup sweep then deletes anything older."
        ),
    )

    # ---- Personal physiology + nutrition target ----
    # NOTE (Phase 17): the four physiology knobs (threshold_pace_s_per_km,
    # max_hr, resting_hr, threshold_hr) and the nutrition target
    # (target_kcal_default) used to live here as ``.env``-driven Fields.
    # They now live in a user-edited markdown file at
    # ``content_root/preferences.md`` and are loaded via
    # ``runos.analysis.preferences.parse_preferences(settings.preferences_path)``
    # which returns a frozen ``PreferencesContext`` carrying ``physiology`` /
    # ``units`` / ``nutrition`` typed sections. The ``preferences_path``
    # derived property below is the only configuration surface.

    @field_validator("data_dir", mode="after")
    @classmethod
    def _expand_data_dir(cls, value: Path) -> Path:
        """Expand ``~`` and resolve the data dir to an absolute path."""
        return value.expanduser()

    @field_validator("content_dir", mode="after")
    @classmethod
    def _expand_content_dir(cls, value: Path | None) -> Path | None:
        """Expand ``~`` for the content dir when set."""
        return value.expanduser() if value is not None else None

    # ---- Derived paths ----
    @property
    def content_root(self) -> Path:
        """Where human-readable content (plan, races, reports) lives.

        Defaults to ``data_dir`` but can be redirected via ``content_dir`` /
        ``RUNOS_CONTENT_DIR`` (e.g. a gitignored folder in the project).
        """
        return self.content_dir if self.content_dir is not None else self.data_dir

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.data_dir / "runos.db"

    @property
    def tokens_dir(self) -> Path:
        """Directory holding OAuth/session tokens (mode 0700)."""
        return self.data_dir / "tokens"

    @property
    def reports_dir(self) -> Path:
        """Directory for generated markdown analysis reports (gitignored, local)."""
        return self.content_root / "reports"

    @property
    def races_path(self) -> Path:
        """Path to the user-maintained races markdown (read for analysis context)."""
        return self.content_root / "races.md"

    @property
    def heat_path(self) -> Path:
        """Path to the user-maintained heat-adaptation log (read for recovery context)."""
        return self.content_root / "heat.md"

    @property
    def strength_path(self) -> Path:
        """Path to the user-maintained strength & conditioning log (read for recovery context)."""
        return self.content_root / "strength.md"

    @property
    def weight_path(self) -> Path:
        """Path to the user-maintained weight log (read for recovery context)."""
        return self.content_root / "weight.md"

    @property
    def food_path(self) -> Path:
        """Path to the user-maintained food log (read for nutrition rollup + recovery context)."""
        return self.content_root / "food.md"

    @property
    def preferences_path(self) -> Path:
        """Path to ``preferences.md`` (physiology + units + nutrition + prose)."""
        return self.content_root / "preferences.md"

    @property
    def voice_cache_dir(self) -> Path:
        """Directory where downloaded Telegram voice memos are cached (gitignored).

        Derived from ``content_root``; created lazily on first use by the voice
        handler (Plan 10-02) with 0700 permissions -- intentionally NOT created
        in :meth:`ensure_dirs` so ``runos init`` does not surface a voice/ dir
        for users who never run the bot.
        """
        return self.content_root / "voice"

    def ensure_dirs(self) -> None:
        """Create the data, tokens, and reports directories with safe perms.

        The data dir and tokens dir are created with 0700 so other local users
        cannot read tokens. The content/reports dir may live elsewhere (e.g. a
        gitignored project folder) and is created if missing. Idempotent.
        """
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.tokens_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a freshly-loaded :class:`Settings` instance.

    Not cached so tests can override the environment between calls.
    """
    return Settings()
