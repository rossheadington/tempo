"""End-to-end CLI verification of `runos journal add` / `runos journal list`.

Seeds an activity into the CLI's own DB (via the RUNOS_DATA_DIR temp dir), runs
the commands as a user (or Claude) would, and asserts the row is created, linked,
sRPE computed, and visible in daily_summary. No network, no credentials.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from runos import db
from runos.cli import app
from runos.config import get_settings
from runos.connectors.base import RawWriter
from runos.transforms.runner import run_transform
from tests.strava_fakes import make_run

runner = CliRunner()


def _seed_activity_in_cli_db(day: str, *, moving_time: int = 3600) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        raw = RawWriter(conn, "strava")
        with conn:
            raw.put("activity_summary", "1", make_run(1, day=day, moving_time=moving_time))
        run_transform(conn)
    finally:
        conn.close()


def test_journal_add_links_and_computes_srpe(runos_data_dir: Path) -> None:
    _seed_activity_in_cli_db("2026-05-10", moving_time=3600)
    result = runner.invoke(
        app,
        [
            "journal",
            "add",
            "--rpe",
            "7",
            "--feel",
            "strong",
            "--day",
            "2026-05-10",
            "--sport",
            "Run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "linked to activity 1" in result.output
    assert "sRPE 420" in result.output  # 7 * 60

    conn = db.connect(runos_data_dir / "runos.db")
    try:
        row = conn.execute(
            "SELECT activity_id, rpe, srpe FROM journal WHERE day='2026-05-10'"
        ).fetchone()
        assert row["activity_id"] == 1
        assert row["rpe"] == 7
        assert row["srpe"] == 420.0
        # Visible in daily_summary.
        summary = conn.execute(
            "SELECT rpe, srpe, has_journal FROM daily_summary WHERE day='2026-05-10'"
        ).fetchone()
        assert summary["rpe"] == 7
        assert summary["srpe"] == 420.0
        assert summary["has_journal"] == 1
    finally:
        conn.close()


def test_journal_add_rejects_bad_rpe(runos_data_dir: Path) -> None:
    result = runner.invoke(app, ["journal", "add", "--rpe", "11", "--day", "2026-05-10"])
    assert result.exit_code == 1, result.output
    assert "between 1 and 10" in result.output


def test_journal_add_ambiguous_requires_id(runos_data_dir: Path) -> None:
    # Two Runs on the same day -> add without --activity-id errors with candidates.
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        raw = RawWriter(conn, "strava")
        with conn:
            raw.put("activity_summary", "1", make_run(1, day="2026-05-11"))
            raw.put("activity_summary", "2", make_run(2, day="2026-05-11"))
        run_transform(conn)
    finally:
        conn.close()

    result = runner.invoke(
        app, ["journal", "add", "--rpe", "6", "--day", "2026-05-11", "--sport", "Run"]
    )
    assert result.exit_code == 1, result.output
    assert "disambiguate" in result.output

    # With an explicit id it succeeds.
    ok = runner.invoke(
        app,
        ["journal", "add", "--rpe", "6", "--day", "2026-05-11", "--activity-id", "2"],
    )
    assert ok.exit_code == 0, ok.output
    assert "linked to activity 2" in ok.output


def test_journal_add_crosstraining_no_activity(runos_data_dir: Path) -> None:
    # No activity; explicit duration drives sRPE; rest-day reflection allowed.
    result = runner.invoke(
        app,
        [
            "journal",
            "add",
            "--rpe",
            "5",
            "--sport",
            "Strength",
            "--day",
            "2026-05-12",
            "--duration-min",
            "45",
            "--notes",
            "gym session",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no activity linked" in result.output
    assert "sRPE 225" in result.output  # 5 * 45


def test_journal_list_shows_entries(runos_data_dir: Path) -> None:
    runner.invoke(
        app, ["journal", "add", "--rpe", "6", "--day", "2026-05-13", "--duration-min", "30"]
    )
    result = runner.invoke(app, ["journal", "list"])
    assert result.exit_code == 0, result.output
    assert "2026-05-13" in result.output
    assert "RPE 6" in result.output
