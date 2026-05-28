"""End-to-end CLI verification of `runos transform` / `runos rederive` (Phase 3).

Seeds fake raw rows into the CLI's own DB (via the RUNOS_DATA_DIR temp dir), runs
the commands as a user would, and asserts the structured layer + spine +
daily_summary are produced. No network, no credentials.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from runos import db
from runos.cli import app
from runos.config import get_settings
from runos.connectors.base import RawWriter
from tests.strava_fakes import make_activity, make_streams

runner = CliRunner()


def _seed_cli_db() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        raw = RawWriter(conn, "strava")
        with conn:
            raw.put(
                "activity_summary",
                "1",
                make_activity(
                    1, start_utc="2026-05-01T22:00:00Z", start_local="2026-05-01T23:00:00Z"
                ),
            )
            raw.put(
                "activity_summary",
                "2",
                make_activity(
                    2, start_utc="2026-05-04T06:00:00Z", start_local="2026-05-04T06:00:00Z"
                ),
            )
            raw.put("streams", "1", make_streams())
    finally:
        conn.close()


def test_transform_cli_builds_structured_layer(runos_data_dir: Path) -> None:
    _seed_cli_db()
    result = runner.invoke(app, ["transform"])
    assert result.exit_code == 0, result.output
    assert "2 activities" in result.output
    assert "8 streams" in result.output

    conn = db.connect(runos_data_dir / "runos.db")
    try:
        # late-night run on 2026-05-01 (local), continuous spine through to today.
        assert (
            conn.execute("SELECT day FROM activity WHERE activity_id=1").fetchone()[0]
            == "2026-05-01"
        )
        days = [r[0] for r in conn.execute("SELECT day FROM date_spine ORDER BY day")]
        # rest days between the two activities are present.
        assert "2026-05-02" in days and "2026-05-03" in days
        # daily_summary has one row per spine day.
        spine = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
        summary = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
        assert summary == spine
    finally:
        conn.close()


def test_rederive_cli_rebuilds_from_raw(runos_data_dir: Path) -> None:
    _seed_cli_db()
    runner.invoke(app, ["transform"])
    result = runner.invoke(app, ["rederive"])
    assert result.exit_code == 0, result.output
    assert "no network" in result.output
    assert "2 activities" in result.output
