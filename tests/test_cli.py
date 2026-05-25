"""Tests for the tempo CLI: init + every wired subcommand invokes without error."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempo import __version__, db
from tempo.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("sync", "transform", "rederive", "analyze", "journal", "init"):
        assert cmd in result.output


def test_bare_invocation_initialises_db(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert "Foundation initialised." in result.output
    # DB file actually created in the temp data dir with WAL + foundation tables.
    db_path = tempo_data_dir / "tempo.db"
    assert db_path.exists()
    conn = db.connect(db_path)
    try:
        assert db.journal_mode(conn) == "wal"
        assert db.table_names(conn) >= set(db.FOUNDATION_TABLES)
    finally:
        conn.close()


def test_init_command(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tempo_data_dir / "tempo.db").exists()
    assert (tempo_data_dir / "tokens").is_dir()
    assert (tempo_data_dir / "reports").is_dir()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_analyze_runs_on_empty_db(tempo_data_dir: Path) -> None:
    # `analyze` is wired to real analyses in Phase 4 (see test_analyze_cli).
    # On an empty DB it degrades gracefully and still writes both reports.
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0, result.output
    assert "Reports written" in result.output


@pytest.mark.parametrize("cmd", ["transform", "rederive"])
def test_transform_commands_run_on_empty_db(cmd: str, tempo_data_dir: Path) -> None:
    # With no raw data they run cleanly (no network) and report zero counts.
    result = runner.invoke(app, [cmd])
    assert result.exit_code == 0, result.output
    assert "0 activities" in result.output


def test_journal_group_runs(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["journal"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.output


def test_journal_add_runs(tempo_data_dir: Path) -> None:
    result = runner.invoke(app, ["journal", "add"])
    assert result.exit_code == 0, result.output
    assert "not yet implemented" in result.output
