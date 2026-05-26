"""Shared pytest fixtures.

The key fixture redirects Tempo's data dir to a per-test temp directory via the
``TEMPO_DATA_DIR`` env var, so tests never touch the real ``~/.tempo`` and never
need network or real credentials.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tempo import db


@pytest.fixture
def tempo_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Tempo's data dir at a temp directory for the duration of a test."""
    data_dir = tmp_path / "tempo-data"
    monkeypatch.setenv("TEMPO_DATA_DIR", str(data_dir))
    # Ensure no stray real config leaks in from the environment.
    for key in (
        "TEMPO_STRAVA_CLIENT_ID",
        "TEMPO_STRAVA_CLIENT_SECRET",
        "TEMPO_STRAVA_REDIRECT_URI",
        "TEMPO_GARMIN_EMAIL",
        "TEMPO_GARMIN_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    # pydantic-settings also reads the `.env` FILE from the cwd, so a developer's
    # real ~/Projects/tempo/.env would leak credentials into "no credentials"
    # tests. Run from the temp dir (no .env there) to keep the suite hermetic.
    monkeypatch.chdir(tmp_path)
    yield data_dir


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised, migrated SQLite connection on a temp DB file."""
    connection = db.init_db(tmp_path / "tempo.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def fast_strava_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the Strava rate-limit backoff so 429-retry tests run instantly.

    Production keeps the real exponential backoff; tests just don't need to wait
    real seconds to exercise the give-up-and-checkpoint path.
    """
    from tempo.connectors import strava

    monkeypatch.setattr(strava, "RETRY_WAIT_MULTIPLIER", 0.0)
    monkeypatch.setattr(strava, "RETRY_WAIT_MIN", 0.0)
    monkeypatch.setattr(strava, "RETRY_WAIT_MAX", 0.0)
    monkeypatch.setattr(strava, "RETRY_ATTEMPTS", 2)
