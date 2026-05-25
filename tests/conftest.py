"""Shared pytest fixtures.

The key fixture redirects Tempo's data dir to a per-test temp directory via the
``TEMPO_DATA_DIR`` env var, so tests never touch the real ``~/.tempo`` and never
need network or real credentials.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tempo_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Tempo's data dir at a temp directory for the duration of a test."""
    data_dir = tmp_path / "tempo-data"
    monkeypatch.setenv("TEMPO_DATA_DIR", str(data_dir))
    # Ensure no stray real config leaks in from the environment.
    for key in (
        "TEMPO_STRAVA_CLIENT_ID",
        "TEMPO_STRAVA_CLIENT_SECRET",
        "TEMPO_GARMIN_EMAIL",
        "TEMPO_GARMIN_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    yield data_dir
