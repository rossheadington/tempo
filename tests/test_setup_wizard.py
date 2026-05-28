"""Tests for the ``tempo setup`` wizard (Plan 14-02).

Pattern: every test runs in a per-test ``tmp_path`` cwd so the wizard's
``.env`` writes don't touch the real project root. ``HOME`` is redirected to a
tmp dir so the wizard's plist-presence checks and ``~/.tempo/`` defaults look
at fake locations. Every delegated helper (``_init``, ``build_strava_connector``,
``garmin_login``, ``install_plist``, ``install_telegram_bot_plist``,
``run_full_sync``) is monkeypatched at its origin module so the wizard's lazy
``from tempo.x import y`` resolves to the mock. ``typer.prompt`` /
``typer.confirm`` are patched once per test to feed scripted answers.

No test touches the network, the real ``~/Library/LaunchAgents/``, or
``launchctl``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from tempo.cli import app
from tempo.config import Settings
from tempo.setup.env_io import read_env
from tempo.setup.wizard import STEP_IDS, run_wizard
from tempo.sync.pipeline import SourceResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME so Path.home() / Library/LaunchAgents writes go to tmp."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "Library" / "LaunchAgents").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fresh_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """chdir to a fresh tmp dir so the wizard's .env writes are isolated."""
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(work)
    return work


@pytest.fixture
def fresh_settings(tmp_path: Path, fake_home: Path, fresh_cwd: Path) -> Settings:
    """A Settings instance pointing into tmp_path; ignores any real .env."""
    return Settings(data_dir=tmp_path / "data", _env_file=None)


@pytest.fixture
def patched_get_settings(monkeypatch: pytest.MonkeyPatch, fresh_settings: Settings):
    """Patch tempo.setup.wizard.get_settings to return our fresh_settings.

    The wizard re-reads settings after writing creds (Strava / Garmin steps)
    so pydantic-settings sees the new env keys. In tests we don't want it to
    pick up an unrelated real .env; we return the same fresh_settings every
    time so the test owns the value.
    """
    calls: list[int] = []

    def _fake_get_settings() -> Settings:
        calls.append(1)
        return fresh_settings

    monkeypatch.setattr("tempo.setup.wizard.get_settings", _fake_get_settings)
    return calls


@pytest.fixture(autouse=True)
def _no_real_browser_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the Strava step from launching the real browser during tests.

    Why: stubbed `authorization_url` returns `https://example/auth`; without
    this, `webbrowser.open` would actually open it in the user's default
    browser on each test run.
    """
    monkeypatch.setattr("tempo.setup.wizard._can_open_browser", lambda: False)


@pytest.fixture
def all_delegated_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace every delegated helper with a recording mock.

    Returns a dict with call counts (and captured args where useful). Each
    helper is patched at its ORIGIN module path so the wizard's lazy
    ``from tempo.x import y`` picks up the patched attribute.
    """
    state: dict[str, Any] = {
        "init": 0,
        "build_strava": 0,
        "exchange_code": 0,
        "exchange_code_env_snapshot": None,
        "garmin_login": 0,
        "install_plist": 0,
        "install_bot_plist": 0,
        "run_full_sync": 0,
        "run_full_sync_return": [
            SourceResult("strava", ok=True, detail="ok", rows=42),
            SourceResult("garmin", ok=False, detail="skipped: 429"),
        ],
    }

    def _fake_init() -> None:
        state["init"] += 1

    def _fake_exchange_code(code: str) -> SimpleNamespace:
        state["exchange_code"] += 1
        # Capture .env state at the moment exchange_code runs (for the
        # "creds written BEFORE OAuth" assertion).
        state["exchange_code_env_snapshot"] = read_env(Path(".env"))
        return SimpleNamespace(expires_at=12345)

    def _fake_build_strava(settings):  # noqa: ARG001
        state["build_strava"] += 1
        return SimpleNamespace(
            authorization_url=lambda redirect_uri: "https://example/auth",  # noqa: ARG005
            exchange_code=_fake_exchange_code,
        )

    def _fake_garmin_login(settings, *, prompt_mfa=None, client_factory=None):  # noqa: ARG001
        state["garmin_login"] += 1
        return Path("/fake/garmin/tokens")

    def _fake_install_plist(**kwargs) -> SimpleNamespace:
        state["install_plist"] += 1
        return SimpleNamespace(
            plist_path=Path("/fake/com.tempo.daily.plist"),
            installed_to_launch_agents=True,
            load_command="launchctl load -w /fake/com.tempo.daily.plist",
            unload_command="launchctl unload -w /fake/com.tempo.daily.plist",
        )

    def _fake_install_bot_plist(**kwargs) -> SimpleNamespace:
        state["install_bot_plist"] += 1
        return SimpleNamespace(
            plist_path=Path("/fake/com.tempo.telegram-bot.plist"),
            installed_to_launch_agents=True,
            plutil_lint_ok=True,
            load_command="launchctl load -w /fake/com.tempo.telegram-bot.plist",
            start_command="launchctl start com.tempo.telegram-bot",
            unload_command="launchctl unload -w /fake/com.tempo.telegram-bot.plist",
            logs_dir=Path("/fake/logs"),
        )

    def _fake_run_full_sync(conn, settings) -> list[SourceResult]:  # noqa: ARG001
        state["run_full_sync"] += 1
        return state["run_full_sync_return"]

    monkeypatch.setattr("tempo.cli._init", _fake_init, raising=True)
    monkeypatch.setattr(
        "tempo.connectors.factory.build_strava_connector", _fake_build_strava, raising=True
    )
    monkeypatch.setattr(
        "tempo.connectors.factory.garmin_login", _fake_garmin_login, raising=True
    )
    monkeypatch.setattr("tempo.scheduler.install_plist", _fake_install_plist, raising=True)
    monkeypatch.setattr(
        "tempo.scheduler.install_telegram_bot_plist", _fake_install_bot_plist, raising=True
    )
    monkeypatch.setattr(
        "tempo.sync.pipeline.run_full_sync", _fake_run_full_sync, raising=True
    )

    return state


def _all_yes_prompts(monkeypatch: pytest.MonkeyPatch, prompt_answers: dict[str, str]) -> None:
    """Make typer.confirm always True and typer.prompt look up by label substring."""

    def _fake_confirm(question, *args, **kwargs):  # noqa: ARG001
        return True

    def _fake_prompt(label, *args, **kwargs):  # noqa: ARG001
        for key, value in prompt_answers.items():
            if key in label:
                return value
        # Fallback to the explicit default if one was provided.
        if "default" in kwargs:
            return kwargs["default"]
        return "stub"

    monkeypatch.setattr("typer.prompt", _fake_prompt)
    monkeypatch.setattr("typer.confirm", _fake_confirm)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_step_ids_in_locked_order() -> None:
    """STEP_IDS is the LOCKED contract from CONTEXT § 'Step list'."""
    assert STEP_IDS == (
        "welcome",
        "db",
        "content",
        "strava",
        "garmin",
        "telegram",
        "scheduler",
        "bot-scheduler",
        "smoke",
        "finish",
    )


def test_wizard_runs_all_steps_in_order_fresh_install(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """Fresh install + sensible-yes prompts → every delegated mock fires once; exit 0."""
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "secret",
            "Paste the `code`": "abc",
            "Garmin email": "user@example.com",
            "Garmin password": "pass",
            "Telegram bot token": "tok",
            "Telegram chat id": "999",
            "Content dir path": str(fresh_cwd / "content"),
            "Hour": "5",
            "Minute": "30",
        },
    )
    exit_code = run_wizard(fresh_settings)
    assert exit_code == 0
    assert all_delegated_mocks["init"] == 1
    assert all_delegated_mocks["build_strava"] == 1
    assert all_delegated_mocks["exchange_code"] == 1
    assert all_delegated_mocks["garmin_login"] == 1
    assert all_delegated_mocks["install_plist"] == 1
    # Telegram completed this run so bot-scheduler should ALSO run.
    assert all_delegated_mocks["install_bot_plist"] == 1
    assert all_delegated_mocks["run_full_sync"] == 1


def test_wizard_only_filter_strava(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """--only=strava runs only the Strava step (welcome + finish always run)."""
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "secret",
            "Paste the `code`": "abc",
        },
    )
    exit_code = run_wizard(fresh_settings, only={"strava"})
    assert exit_code == 0
    assert all_delegated_mocks["build_strava"] == 1
    assert all_delegated_mocks["init"] == 0
    assert all_delegated_mocks["garmin_login"] == 0
    assert all_delegated_mocks["install_plist"] == 0
    assert all_delegated_mocks["install_bot_plist"] == 0
    assert all_delegated_mocks["run_full_sync"] == 0


def test_wizard_only_conflicts_with_skip_flag_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Asking for --only=garmin AND --skip-garmin is incoherent; exit 2 with a clear error.

    Regression: previously the --skip-* flag won silently, producing a no-op
    run with the user's explicit --only request quietly discarded. Now we
    fail fast at orchestrator entry with a red error line per conflicting
    step.
    """
    exit_code = run_wizard(
        fresh_settings,
        only={"garmin"},
        skip_garmin=True,
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--only=garmin" in captured.err
    assert "--skip-garmin" in captured.err
    # No delegated helper should have run -- we bailed before dispatch.
    assert all_delegated_mocks["build_strava"] == 0
    assert all_delegated_mocks["init"] == 0
    assert all_delegated_mocks["garmin_login"] == 0


def test_wizard_only_bot_scheduler_conflicts_with_skip_telegram(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--only=bot-scheduler + --skip-telegram (which implies --skip-bot-scheduler) errors.

    The error message must surface the --skip-telegram → --skip-bot-scheduler
    implication so the user knows WHY their --only request was rejected.
    """
    exit_code = run_wizard(
        fresh_settings,
        only={"bot-scheduler"},
        skip_telegram=True,
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--only=bot-scheduler" in captured.err
    assert "--skip-telegram" in captured.err


def test_wizard_skip_garmin(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """--skip-garmin: Garmin login mock NEVER called; Strava + scheduler still run."""
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "secret",
            "Paste the `code`": "abc",
            "Telegram bot token": "tok",
            "Telegram chat id": "999",
            "Content dir path": str(fresh_cwd / "content"),
            "Hour": "5",
            "Minute": "30",
        },
    )
    exit_code = run_wizard(fresh_settings, skip_garmin=True)
    assert exit_code == 0
    assert all_delegated_mocks["garmin_login"] == 0
    assert all_delegated_mocks["build_strava"] == 1
    assert all_delegated_mocks["install_plist"] == 1


def test_wizard_skip_telegram_implies_skip_bot_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """--skip-telegram implies --skip-bot-scheduler (CONTEXT line 44)."""
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "secret",
            "Paste the `code`": "abc",
            "Garmin email": "user@example.com",
            "Garmin password": "pass",
            "Content dir path": str(fresh_cwd / "content"),
            "Hour": "5",
            "Minute": "30",
        },
    )
    exit_code = run_wizard(fresh_settings, skip_telegram=True)
    assert exit_code == 0
    assert all_delegated_mocks["install_bot_plist"] == 0


def test_wizard_step_skipped_when_install_state_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
) -> None:
    """Fully configured install: every delegated mock NEVER called; exit 0."""
    from tempo import db as _db

    # Build a Settings with everything configured.
    settings = Settings(
        data_dir=tmp_path / "data",
        content_dir=tmp_path / "content",
        strava_client_id="12345",
        strava_client_secret="secret",  # noqa: S106 - test fixture
        garmin_email="user@example.com",
        garmin_password="pass",  # noqa: S106 - test fixture
        telegram_bot_token="tok",  # noqa: S106 - test fixture
        telegram_owner_chat_id=999,
        _env_file=None,
    )
    settings.ensure_dirs()
    # DB initialised at current schema.
    conn = _db.init_db(settings.db_path)
    conn.close()
    # Strava token file present.
    (settings.tokens_dir / "strava_tokens.json").write_text("{}", encoding="utf-8")
    # Garmin token dir present.
    (settings.tokens_dir / "garmin").mkdir(parents=True, exist_ok=True)
    # Both plists present in fake_home.
    (fake_home / "Library" / "LaunchAgents" / "com.tempo.daily.plist").write_text(
        "<plist/>", encoding="utf-8"
    )
    (fake_home / "Library" / "LaunchAgents" / "com.tempo.telegram-bot.plist").write_text(
        "<plist/>", encoding="utf-8"
    )

    # Patch get_settings to return this fully-configured Settings.
    monkeypatch.setattr("tempo.setup.wizard.get_settings", lambda: settings)
    # Pre-write .env so step_content sees TEMPO_CONTENT_DIR as already set.
    Path(".env").write_text(f"TEMPO_CONTENT_DIR={settings.content_dir}\n", encoding="utf-8")

    # Prompts that record they were never called.
    prompt_calls: list[str] = []
    confirm_calls: list[str] = []

    def _record_prompt(label, *args, **kwargs):  # noqa: ARG001
        prompt_calls.append(label)
        return kwargs.get("default", "")

    def _record_confirm(question, *args, **kwargs):  # noqa: ARG001
        confirm_calls.append(question)
        return False

    monkeypatch.setattr("typer.prompt", _record_prompt)
    monkeypatch.setattr("typer.confirm", _record_confirm)

    exit_code = run_wizard(settings)
    assert exit_code == 0
    # Every delegated helper should have been skipped.
    assert all_delegated_mocks["init"] == 0
    assert all_delegated_mocks["build_strava"] == 0
    assert all_delegated_mocks["garmin_login"] == 0
    assert all_delegated_mocks["install_plist"] == 0
    assert all_delegated_mocks["install_bot_plist"] == 0
    # Smoke STILL runs (not state-gated).
    assert all_delegated_mocks["run_full_sync"] == 1
    # No prompts were issued for any of the auto-skipped steps.
    assert prompt_calls == []


def test_wizard_strava_writes_creds_before_oauth(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """Order-of-operations: .env has client id+secret by the time exchange_code runs."""
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "topsecret",
            "Paste the `code`": "abc",
        },
    )
    exit_code = run_wizard(fresh_settings, only={"strava"})
    assert exit_code == 0
    snapshot = all_delegated_mocks["exchange_code_env_snapshot"]
    assert snapshot is not None
    assert snapshot.get("TEMPO_STRAVA_CLIENT_ID") == "12345"
    assert snapshot.get("TEMPO_STRAVA_CLIENT_SECRET") == "topsecret"


def test_wizard_strava_oauth_failure_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    patched_get_settings,
) -> None:
    """exchange_code raises → exit 1; .env still has the creds for retry."""

    def _fake_build_strava(settings):  # noqa: ARG001
        def _exchange(code):  # noqa: ARG001
            raise ValueError("invalid code")

        return SimpleNamespace(
            authorization_url=lambda redirect_uri: "https://example/auth",  # noqa: ARG005
            exchange_code=_exchange,
        )

    monkeypatch.setattr(
        "tempo.connectors.factory.build_strava_connector", _fake_build_strava, raising=True
    )
    _all_yes_prompts(
        monkeypatch,
        {
            "Strava Client ID": "12345",
            "Strava Client Secret": "topsecret",
            "Paste the `code`": "badcode",
        },
    )
    exit_code = run_wizard(fresh_settings, only={"strava"})
    assert exit_code == 1
    env_after = read_env(Path(".env"))
    assert env_after.get("TEMPO_STRAVA_CLIENT_ID") == "12345"
    assert env_after.get("TEMPO_STRAVA_CLIENT_SECRET") == "topsecret"


def test_wizard_smoke_reports_per_source_status(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    capsys: pytest.CaptureFixture,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """Smoke prints Strava OK row + Garmin skipped row; Garmin failure NOT terminal."""
    all_delegated_mocks["run_full_sync_return"] = [
        SourceResult("strava", ok=True, detail="ok", rows=42),
        SourceResult("garmin", ok=False, detail="429"),
    ]
    _all_yes_prompts(monkeypatch, {})
    exit_code = run_wizard(fresh_settings, only={"smoke"})
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "strava: OK (42 raw rows)" in out
    assert "garmin: skipped -- 429" in out


def test_wizard_smoke_strava_terminal_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    capsys: pytest.CaptureFixture,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """Strava ok=False → exit 1 + remediation hint."""
    all_delegated_mocks["run_full_sync_return"] = [
        SourceResult("strava", ok=False, detail="auth-error"),
    ]
    _all_yes_prompts(monkeypatch, {})
    exit_code = run_wizard(fresh_settings, only={"smoke"})
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "tempo setup --only=strava" in out


def test_wizard_exit_code_2_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """A prompt raising typer.Abort propagates to exit code 2."""

    def _abort_prompt(*args, **kwargs):  # noqa: ARG001
        raise typer.Abort()

    monkeypatch.setattr("typer.prompt", _abort_prompt)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)  # noqa: ARG005
    exit_code = run_wizard(fresh_settings, only={"content"})
    assert exit_code == 2


def test_wizard_non_interactive_fails_fast_on_missing_input(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """--non-interactive on a fresh content step raises Abort (exit 2)."""

    def _should_not_be_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("prompt should not be called in non_interactive mode")

    monkeypatch.setattr("typer.prompt", _should_not_be_called)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)  # noqa: ARG005
    exit_code = run_wizard(fresh_settings, only={"content"}, non_interactive=True)
    assert exit_code == 2


def test_bot_scheduler_offered_only_when_telegram_configured_in_this_run(
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: Settings,
    fake_home: Path,
    fresh_cwd: Path,
    all_delegated_mocks: dict[str, Any],
    patched_get_settings,
) -> None:
    """User declines Telegram on a fresh state → bot-scheduler is NOT offered.

    --skip-bot-scheduler is NOT set, but Telegram wasn't completed and not
    already configured, so install_telegram_bot_plist mock is never called.
    """

    def _fake_confirm(question, *args, **kwargs):  # noqa: ARG001
        # Decline Telegram, accept everything else.
        if "Telegram" in question:
            return False
        return kwargs.get("default", True)

    def _fake_prompt(label, *args, **kwargs):  # noqa: ARG001
        answers = {
            "Strava Client ID": "12345",
            "Strava Client Secret": "secret",
            "Paste the `code`": "abc",
            "Garmin email": "user@example.com",
            "Garmin password": "pass",
            "Content dir path": str(fresh_cwd / "content"),
            "Hour": "5",
            "Minute": "30",
        }
        for key, value in answers.items():
            if key in label:
                return value
        if "default" in kwargs:
            return kwargs["default"]
        return "stub"

    monkeypatch.setattr("typer.prompt", _fake_prompt)
    monkeypatch.setattr("typer.confirm", _fake_confirm)

    exit_code = run_wizard(fresh_settings)
    assert exit_code == 0
    assert all_delegated_mocks["install_bot_plist"] == 0


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


def test_setup_cmd_via_clirunner_unknown_only_step_exits_2(
    fake_home: Path, fresh_cwd: Path
) -> None:
    """`tempo setup --only=banana` → exit 2 + helpful error listing valid steps."""
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "--only=banana"])
    assert result.exit_code == 2
    # CliRunner captures both stdout + stderr in result.output by default in
    # typer >= 0.16; stderr goes to a separate stream otherwise.
    combined = (result.output or "") + (result.stderr or "" if hasattr(result, "stderr") else "")
    assert "banana" in combined


def test_setup_cmd_via_clirunner_help_lists_all_flags(
    fake_home: Path, fresh_cwd: Path
) -> None:
    """`tempo setup --help` lists all 7 locked flags."""
    runner = CliRunner()
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    for flag in (
        "--only",
        "--skip-garmin",
        "--skip-telegram",
        "--skip-scheduler",
        "--skip-bot-scheduler",
        "--skip-smoke",
        "--non-interactive",
    ):
        assert flag in result.output, f"flag {flag} missing from --help output"
