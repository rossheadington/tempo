"""CLI verification of the Phase-7 commands: run-daily + install-scheduler.

Drives the commands as a user would (via the temp TEMPO_DATA_DIR), with the
network sync skipped (``--no-sync``) or with faked connectors, so no credentials
or network are needed. Asserts the daily loop writes the report suite and the
scheduler command writes a valid plist template without ever running launchctl.
"""

from __future__ import annotations

import plistlib
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo import db
from tempo.cli import app
from tempo.config import get_settings
from tempo.connectors.base import RawWriter
from tempo.sync import state
from tempo.transforms.runner import run_transform
from tests.garmin_fakes import make_hrv, make_sleep, make_stats
from tests.strava_fakes import make_run

cli = CliRunner()


@pytest.fixture
def seeded_data_dir(tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEMPO_THRESHOLD_PACE_S_PER_KM", "240")
    monkeypatch.setenv("TEMPO_MAX_HR", "190")
    monkeypatch.setenv("TEMPO_RESTING_HR", "48")
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    d0 = date(2026, 1, 1)
    n = 70
    try:
        sraw = RawWriter(conn, "strava")
        graw = RawWriter(conn, "garmin")
        with conn:
            last = None
            for i in range(n):
                d = d0 + timedelta(days=i)
                if i % 3 != 2:
                    aid = 4000 + i
                    sraw.put("activity_summary", str(aid), make_run(aid, day=d.isoformat()))
                    last = f"{d.isoformat()}T06:00:00Z"
                graw.put("sleep", d.isoformat(), make_sleep(d.isoformat()))
                graw.put(
                    "hrv", d.isoformat(), make_hrv(d.isoformat(), last_night_avg=60.0 + (i % 5))
                )
                graw.put("stats", d.isoformat(), make_stats(d.isoformat()))
            state.mark_synced(conn, "strava", last_entity_ts=last)
        run_transform(conn, fill_to=d0 + timedelta(days=n - 1))
    finally:
        conn.close()
    return tempo_data_dir


def test_run_daily_no_sync_writes_suite(seeded_data_dir: Path) -> None:
    result = cli.invoke(app, ["run-daily", "--no-sync"])
    assert result.exit_code == 0, result.output
    reports = list((seeded_data_dir / "reports").glob("*.md"))
    names = {p.name.split("-", 3)[-1] for p in reports}
    assert {"load-trend.md", "race-readiness.md", "recovery.md", "correlations.md"} <= names
    assert "Reports written:" in result.output


def test_run_daily_reports_noteworthy_or_quiet(seeded_data_dir: Path) -> None:
    result = cli.invoke(app, ["run-daily", "--no-sync"])
    assert result.exit_code == 0, result.output
    # Either it surfaces a NOTEWORTHY block or it states it was quiet -- never silent.
    assert ("NOTEWORTHY today:" in result.output) or ("Nothing noteworthy today" in result.output)


def test_install_scheduler_writes_template(tempo_data_dir: Path) -> None:
    result = cli.invoke(app, ["install-scheduler", "--hour", "6", "--minute", "0"])
    assert result.exit_code == 0, result.output
    assert "launchctl load" in result.output  # the manual step is shown
    assert "never runs launchctl for you" in result.output.lower()
    plist = tempo_data_dir / "launchd" / "com.tempo.daily.plist"
    assert plist.exists()
    parsed = plistlib.loads(plist.read_text().encode())
    assert parsed["StartCalendarInterval"]["Hour"] == 6
    assert parsed["ProgramArguments"][-1] == "run-daily"


def test_install_scheduler_does_not_touch_launch_agents(tempo_data_dir: Path) -> None:
    """Default install must NOT write into ~/Library/LaunchAgents (no system side effect)."""
    from tempo import scheduler

    result = cli.invoke(app, ["install-scheduler"])
    assert result.exit_code == 0, result.output
    # The output path is the template under the data dir, not LaunchAgents.
    assert str(tempo_data_dir / "launchd") in result.output
    assert str(scheduler.launch_agents_dir()) not in result.output.split("To enable")[0]


# ---------------------------------------------------------------------------
# Phase 12: `tempo bot install-scheduler` (long-running KeepAlive plist)
# ---------------------------------------------------------------------------


def test_bot_install_scheduler_writes_template_and_creates_logs(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tempo bot install-scheduler` writes a valid plist under the project's
    launchd/ dir AND ensures <project>/logs/ exists (StandardOut/ErrorPath
    parent dirs), without ever running launchctl.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    result = cli.invoke(
        app, ["bot", "install-scheduler", "--uv-bin", "/fake/uv", "--tz", "Europe/London"]
    )
    assert result.exit_code == 0, result.output
    # Manual launchctl steps are surfaced but never auto-run.
    assert "launchctl load" in result.output
    assert "launchctl start com.tempo.telegram-bot" in result.output
    assert "never runs launchctl for you" in result.output.lower()

    plist = project_root / "launchd" / "com.tempo.telegram-bot.plist"
    assert plist.exists()
    parsed = plistlib.loads(plist.read_text().encode())
    assert parsed["Label"] == "com.tempo.telegram-bot"
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["ThrottleInterval"] == 10
    assert parsed["ProgramArguments"][0] == "/fake/uv"
    assert parsed["WorkingDirectory"] == str(project_root)
    assert parsed["EnvironmentVariables"]["TZ"] == "Europe/London"
    assert parsed["EnvironmentVariables"]["OMP_NUM_THREADS"] == "4"

    # Logs dir is created so launchd does not fail to open StandardOutPath.
    assert (project_root / "logs").is_dir()


def test_bot_install_scheduler_does_not_touch_launch_agents(
    tempo_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default `bot install-scheduler` writes under the project, not LaunchAgents."""
    from tempo import scheduler

    project_root = tmp_path / "proj"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    result = cli.invoke(
        app, ["bot", "install-scheduler", "--uv-bin", "/fake/uv", "--tz", "UTC"]
    )
    assert result.exit_code == 0, result.output
    assert str(project_root / "launchd") in result.output
    assert str(scheduler.launch_agents_dir()) not in result.output.split("To enable")[0]
