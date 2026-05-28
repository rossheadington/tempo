"""CLI tests for the Garmin commands (login / sync) -- all mocked, no network."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tempo import db
from tempo.cli import app
from tests.garmin_fakes import FakeGarminClient, make_day

runner = CliRunner()


def _recent_days() -> list[str]:
    from datetime import timedelta

    from tempo.connectors.garmin import GarminConnector

    today = GarminConnector._today()
    return [(today - timedelta(days=i)).isoformat() for i in range(5, -1, -1)]


# ---- tempo garmin login (interactive, one-time) ----------------------------


def test_garmin_login_persists_via_credential_client(tempo_data_dir: Path, monkeypatch) -> None:
    """`tempo garmin login` submits credentials once and dumps tokens (GRMN-02)."""
    monkeypatch.setenv("TEMPO_GARMIN_EMAIL", "runner@example.com")
    monkeypatch.setenv("TEMPO_GARMIN_PASSWORD", "not-a-real-pw")

    captured = {}

    def _factory(*, email, password, prompt_mfa):
        client = FakeGarminClient(credentialed=True)
        captured["client"] = client
        captured["email"] = email
        return client

    # Patch the factory's default credential client builder.
    import tempo.connectors.factory as factory

    monkeypatch.setattr(factory, "_real_garmin_credential_client", _factory)

    result = runner.invoke(app, ["garmin", "login"])
    assert result.exit_code == 0, result.output
    assert "Garmin authorised" in result.output
    # Exactly one real credential login happened; tokens dumped.
    assert captured["client"].credential_login_calls == 1
    assert captured["client"].dumped_to is not None
    assert captured["email"] == "runner@example.com"


def test_garmin_login_without_credentials_errors(tempo_data_dir: Path) -> None:
    """Missing Garmin credentials -> a clear error, exit 1 (no network)."""
    result = runner.invoke(app, ["garmin", "login"])
    assert result.exit_code == 1
    assert "credentials missing" in result.output.lower()


# ---- tempo garmin sync (token reuse; no fresh login) -----------------------


def test_garmin_sync_reuses_tokens_no_login(tempo_data_dir: Path, monkeypatch) -> None:
    """`tempo garmin sync` reuses tokens and performs NO credential login (GRMN-02)."""
    client = FakeGarminClient(days={d: make_day(d) for d in _recent_days()})

    import tempo.connectors.factory as factory

    # The connector's client_factory returns our token-only fake.
    monkeypatch.setattr(
        factory,
        "_real_garmin_login_client",
        lambda: client,
    )

    result = runner.invoke(app, ["garmin", "sync"])
    assert result.exit_code == 0, result.output
    assert "Garmin sync complete" in result.output
    assert client.credential_login_calls == 0  # NEVER a fresh login on sync
    assert client.token_login_calls == 1

    # Raw wellness rows landed.
    conn = db.connect(tempo_data_dir / "tempo.db")
    try:
        n = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='garmin'").fetchone()[0]
    finally:
        conn.close()
    assert n > 0


def test_garmin_sync_429_reports_skip_not_crash(tempo_data_dir: Path, monkeypatch) -> None:
    """A 429 on `tempo garmin sync` reports a skip, exit 0 (no retry, no crash)."""
    client = FakeGarminClient(
        days={d: make_day(d) for d in _recent_days()},
        raise_429_on="sleep",
    )
    import tempo.connectors.factory as factory

    monkeypatch.setattr(factory, "_real_garmin_login_client", lambda: client)

    result = runner.invoke(app, ["garmin", "sync"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output.lower()
    assert "429" in result.output


# ---- tempo sync: Strava + isolated Garmin ----------------------------------


def test_tempo_sync_reports_both_sources_garmin_isolated(tempo_data_dir: Path, monkeypatch) -> None:
    """`tempo sync` runs Strava then attempts Garmin; a Garmin 429 is isolated."""
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_ID", "424242")
    monkeypatch.setenv("TEMPO_STRAVA_CLIENT_SECRET", "shhh")

    from tempo.connectors.tokens import TokenSet, TokenStore
    from tests.strava_fakes import FakeStravaClient, make_activity

    store = TokenStore(tempo_data_dir / "tokens", "strava")
    store.save(TokenSet("access", "refresh", 9_999_999_999))
    strava_fake = FakeStravaClient(
        pages={
            1: [
                make_activity(
                    900,
                    start_utc="2026-05-20T08:00:00Z",
                    start_local="2026-05-20T08:00:00Z",
                )
            ],
            2: [],
        }
    )
    monkeypatch.setattr("tempo.connectors.strava.Client", lambda *a, **k: strava_fake)

    # Garmin 429s -> isolated.
    garmin_client = FakeGarminClient(
        days={d: make_day(d) for d in _recent_days()}, raise_429_on="sleep"
    )
    import tempo.connectors.factory as factory

    monkeypatch.setattr(factory, "_real_garmin_login_client", lambda: garmin_client)

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "strava: ok" in result.output
    assert "garmin: skipped" in result.output

    # Strava data landed despite the Garmin failure.
    conn = db.connect(tempo_data_dir / "tempo.db")
    try:
        n = conn.execute("SELECT COUNT(*) FROM raw_response WHERE source='strava'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
