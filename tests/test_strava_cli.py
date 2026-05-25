"""CLI tests for the Strava commands (mocked connector, no network)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tempo import db
from tempo.cli import app

runner = CliRunner()


def test_strava_group_in_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "strava" in result.output


def test_strava_subcommands_listed() -> None:
    result = runner.invoke(app, ["strava", "--help"])
    assert result.exit_code == 0
    for cmd in ("auth", "backfill", "streams", "sync"):
        assert cmd in result.output


def test_sync_without_credentials_exits_nonzero(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "Strava credentials missing" in result.output


def test_strava_sync_without_credentials_exits_nonzero(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["strava", "sync"])
    assert result.exit_code == 1
    assert "credentials missing" in result.output.lower()


def test_strava_backfill_without_credentials_exits_nonzero(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["strava", "backfill"])
    assert result.exit_code == 1


def test_strava_auth_prints_url_with_credentials(tempo_data_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_ID", "424242")
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_SECRET", "shhh-not-real")
    result = runner.invoke(app, ["strava", "auth"])
    assert result.exit_code == 0, result.output
    assert "https://www.strava.com/oauth/authorize" in result.output
    assert "client_id=424242" in result.output


def test_strava_auth_with_code_stores_tokens(tempo_data_dir: Path, monkeypatch) -> None:
    """The full handshake completion path, with stravalib mocked at the source."""
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_ID", "424242")
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_SECRET", "shhh-not-real")

    from tests.strava_fakes import FakeStravaClient

    fake = FakeStravaClient(
        exchange_result={"access_token": "A", "refresh_token": "R", "expires_at": 1234}
    )
    # Patch the real stravalib Client so build_strava_connector wires the fake.
    monkeypatch.setattr("tempo.connectors.strava.Client", lambda *a, **k: fake)

    result = runner.invoke(app, ["strava", "auth", "--code", "the-code"])
    assert result.exit_code == 0, result.output
    assert "Strava authorised" in result.output
    # Token file landed in the temp tokens dir.
    assert (tempo_data_dir / "tokens" / "strava_tokens.json").exists()


def test_sync_runs_with_mocked_client(tempo_data_dir: Path, monkeypatch) -> None:
    """tempo sync end-to-end against a mocked stravalib client + seeded tokens."""
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_ID", "424242")
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_SECRET", "shhh-not-real")

    from tempo.connectors.tokens import TokenSet, TokenStore
    from tests.strava_fakes import FakeStravaClient, make_activity

    # Seed valid tokens so no refresh/network is needed.
    store = TokenStore(tempo_data_dir / "tokens", "strava")
    store.save(TokenSet("access", "refresh", 9_999_999_999))

    fake = FakeStravaClient(
        pages={
            1: [
                make_activity(
                    501, start_utc="2026-05-20T08:00:00Z", start_local="2026-05-20T08:00:00Z"
                )
            ],
            2: [],
        }
    )
    monkeypatch.setattr("tempo.connectors.strava.Client", lambda *a, **k: fake)

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Strava sync complete" in result.output

    conn = db.connect(tempo_data_dir / "tempo.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='strava'").fetchone()[
            0
        ]
        assert count == 1
    finally:
        conn.close()
