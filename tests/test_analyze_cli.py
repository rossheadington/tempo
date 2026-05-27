"""End-to-end CLI verification of `tempo analyze` (Phase 4).

Seeds the CLI's own DB (via the TEMPO_DATA_DIR temp dir), sets load config + the
races.md/plan.md context in that data dir, runs the commands as a user would, and
asserts dated markdown reports are written into the gitignored reports dir with
per-source freshness headers. No network, no credentials.
"""

from __future__ import annotations

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
from tests.strava_fakes import make_run

cli = CliRunner()


@pytest.fixture
def seeded_cli(tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A CLI data dir seeded with activities + load config + races/plan context."""
    monkeypatch.setenv("TEMPO_THRESHOLD_PACE_S_PER_KM", "240")
    monkeypatch.setenv("TEMPO_MAX_HR", "190")
    monkeypatch.setenv("TEMPO_RESTING_HR", "50")

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    start = date(2026, 1, 1)
    try:
        raw = RawWriter(conn, "strava")
        last_ts = None
        with conn:
            for i in range(50):
                if i % 3 == 2:
                    continue
                aid = 3000 + i
                d = start + timedelta(days=i)
                raw.put(
                    "activity_summary",
                    str(aid),
                    make_run(aid, day=d.isoformat(), average_speed=3.0),
                )
                last_ts = f"{d.isoformat()}T06:00:00Z"
            state.mark_synced(conn, "strava", last_entity_ts=last_ts)
        run_transform(conn, fill_to=start + timedelta(days=60))
    finally:
        conn.close()

    settings.races_path.write_text(
        "- Goal Race - date: 2099-05-01 | distance: 10k | goal: 50:00 | priority: A\n",
        encoding="utf-8",
    )
    settings.plan_path.write_text("Phase: Base\nFocus: aerobic\n", encoding="utf-8")
    # Seed a small heat.md within the last week so the recovery report renders
    # the full heat-adaptation rollup section (TRACK-04/05 wiring). The recovery
    # report anchors heat windows to the latest fitness-point day, so use the
    # seeded run's last date (2026-02-19, i=49) as the heat reference.
    last_seeded_day = start + timedelta(days=49)
    h1 = last_seeded_day - timedelta(days=1)
    h2 = last_seeded_day - timedelta(days=3)
    settings.heat_path.write_text(
        f"- {h1.isoformat()} - type: sauna | duration_min: 25 | temp_c: 85\n"
        f"- {h2.isoformat()} - type: sauna | duration_min: 30 | temp_c: 88\n",
        encoding="utf-8",
    )
    return tempo_data_dir


def test_analyze_runs_full_suite(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze"])
    assert result.exit_code == 0, result.output
    reports = list((seeded_cli / "reports").glob("*.md"))
    names = {p.name.split("-", 3)[-1] for p in reports}
    # Phase 7: the bare `tempo analyze` now runs the FULL suite (4 reports).
    assert {"load-trend.md", "race-readiness.md", "recovery.md", "correlations.md"} <= names


def test_analyze_load_trend_subcommand(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze", "load-trend"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-load-trend.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "## Data freshness" in text
    assert "**strava**: last successful sync" in text
    assert "CTL (fitness)" in text


def test_analyze_race_readiness_subcommand(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze", "race-readiness"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-race-readiness.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Goal Race" in text
    assert "Predicted time" in text
    assert "**phase**: Base" in text


def test_analyze_recovery_subcommand(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze", "recovery"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-recovery.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# Recovery & Overtraining" in text
    assert "## Data freshness" in text
    # No Garmin wellness seeded -> recovery markers report insufficient honestly.
    assert "insufficient" in text.lower() or "no data" in text.lower()
    # The seeded heat.md has recent sessions, so the heat section must surface.
    assert "## Heat adaptation" in text


def test_analyze_correlations_subcommand(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze", "correlations"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-correlations.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# Correlation Insight" in text
    # No wellness/journal seeded -> insufficient-data messaging present.
    assert "insufficient data" in text.lower()


def test_analyze_on_empty_db_degrades_not_crash(tempo_data_dir: Path) -> None:
    result = cli.invoke(app, ["analyze"])
    assert result.exit_code == 0, result.output
    reports = list((tempo_data_dir / "reports").glob("*.md"))
    assert len(reports) == 4  # full suite, all degrading to insufficient-data notes
    for p in reports:
        assert "## Data freshness" in p.read_text(encoding="utf-8")
