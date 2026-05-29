"""Shared pytest fixtures.

The key fixture redirects RunOS's data dir to a per-test temp directory via the
``RUNOS_DATA_DIR`` env var, so tests never touch the real ``~/.runos`` and never
need network or real credentials.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from runos import db


@pytest.fixture
def runos_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point RunOS's data dir at a temp directory for the duration of a test."""
    data_dir = tmp_path / "runos-data"
    monkeypatch.setenv("RUNOS_DATA_DIR", str(data_dir))
    # Ensure no stray real config leaks in from the environment.
    for key in (
        "RUNOS_CONTENT_DIR",
        "RUNOS_STRAVA_CLIENT_ID",
        "RUNOS_STRAVA_CLIENT_SECRET",
        "RUNOS_STRAVA_REDIRECT_URI",
        "RUNOS_GARMIN_EMAIL",
        "RUNOS_GARMIN_PASSWORD",
        "RUNOS_COROS_EMAIL",
        "RUNOS_COROS_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    # pydantic-settings also reads the `.env` FILE from the cwd, so a developer's
    # real ~/Projects/RunOS/.env would leak credentials into "no credentials"
    # tests. Run from the temp dir (no .env there) to keep the suite hermetic.
    monkeypatch.chdir(tmp_path)
    yield data_dir


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialised, migrated SQLite connection on a temp DB file."""
    connection = db.init_db(tmp_path / "runos.db")
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
    from runos.connectors import strava

    monkeypatch.setattr(strava, "RETRY_WAIT_MULTIPLIER", 0.0)
    monkeypatch.setattr(strava, "RETRY_WAIT_MIN", 0.0)
    monkeypatch.setattr(strava, "RETRY_WAIT_MAX", 0.0)
    monkeypatch.setattr(strava, "RETRY_ATTEMPTS", 2)
