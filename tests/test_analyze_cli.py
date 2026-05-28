"""End-to-end CLI verification of `runos analyze` (Phase 4).

Seeds the CLI's own DB (via the RUNOS_DATA_DIR temp dir), sets load config + the
races.md/heat.md context in that data dir, runs the commands as a user would, and
asserts dated markdown reports are written into the gitignored reports dir with
per-source freshness headers. No network, no credentials.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from runos import db
from runos.cli import app
from runos.config import get_settings
from runos.connectors.base import RawWriter
from runos.sync import state
from runos.transforms.runner import run_transform
from tests.strava_fakes import make_run

cli = CliRunner()


@pytest.fixture
def seeded_cli(runos_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A CLI data dir seeded with activities + load config + races/heat context."""
    # Phase 17: physiology config now lives in preferences.md, not .env vars.
    settings = get_settings()
    settings.ensure_dirs()
    settings.preferences_path.write_text(
        "# Preferences\n\n"
        "## Physiology\n"
        "threshold_pace: 240 s/km\n"
        "max_hr: 190\n"
        "resting_hr: 50\n",
        encoding="utf-8",
    )
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
    return runos_data_dir


def test_analyze_runs_full_suite(seeded_cli: Path) -> None:
    result = cli.invoke(app, ["analyze"])
    assert result.exit_code == 0, result.output
    reports = list((seeded_cli / "reports").glob("*.md"))
    names = {p.name.split("-", 3)[-1] for p in reports}
    # Phase 7: the bare `runos analyze` now runs the FULL suite (4 reports).
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


def test_analyze_on_empty_db_degrades_not_crash(runos_data_dir: Path) -> None:
    result = cli.invoke(app, ["analyze"])
    assert result.exit_code == 0, result.output
    reports = list((runos_data_dir / "reports").glob("*.md"))
    # Full suite = load-trend + race-readiness + recovery + correlations + nutrition.
    assert len(reports) == 5
    # The first four share the ``## Data freshness`` block; nutrition uses an
    # inline ``Data: food.md ...`` line instead (no SQL sources to age).
    for p in reports:
        text = p.read_text(encoding="utf-8")
        if p.name.endswith("-nutrition.md"):
            assert "Data: food.md" in text
        else:
            assert "## Data freshness" in text


def test_analyze_nutrition_reads_target_kcal_from_preferences_md(
    runos_data_dir: Path,
) -> None:
    """Phase 17: the CLI must honour `target_kcal` in preferences.md (not .env).

    Writes a preferences.md with a `## Nutrition` target, seeds a food.md with
    enough entries that the 7-day rollup can compute a delta, then asserts the
    rendered nutrition report surfaces the goal line — which only renders when
    `target_kcal` is present.
    """
    from datetime import UTC, datetime

    from runos.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    settings.preferences_path.write_text(
        "# Preferences\n\n## Nutrition\ntarget_kcal: 2400\n",
        encoding="utf-8",
    )
    today = datetime.now(UTC).date()
    # Seed a single day's food entry so the rollup has at least one logged day.
    settings.food_path.write_text(
        f"- {today.isoformat()} breakfast: oats | p:13 c:54 f:6 cal:300\n",
        encoding="utf-8",
    )

    result = cli.invoke(app, ["analyze", "nutrition"])
    assert result.exit_code == 0, result.output
    files = list((runos_data_dir / "reports").glob("*-nutrition.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # The Goal section only renders when target_kcal is non-None — which means
    # the CLI successfully sourced it from preferences.md (not the deleted
    # RUNOS_TARGET_KCAL env var).
    assert "2400" in text
    assert "Goal" in text or "Target" in text


def test_analyze_load_trend_renders_miles_when_preferences_says_miles(
    seeded_cli: Path,
) -> None:
    """Phase 17: the load-trend report's distance column honours `## Units` in preferences.md."""
    from runos.config import get_settings

    settings = get_settings()
    # Replace the seeded preferences.md (which has Physiology only) with one
    # that also picks miles for distance display.
    settings.preferences_path.write_text(
        "# Preferences\n\n"
        "## Physiology\n"
        "threshold_pace: 240 s/km\n"
        "max_hr: 190\n"
        "resting_hr: 50\n\n"
        "## Units\n"
        "distance: miles\n",
        encoding="utf-8",
    )

    result = cli.invoke(app, ["analyze", "load-trend"])
    assert result.exit_code == 0, result.output
    files = list((seeded_cli / "reports").glob("*-load-trend.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # Column header reflects miles, not km.
    assert "Distance (mi)" in text
    assert "Distance (km)" not in text
