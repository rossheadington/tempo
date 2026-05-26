"""Tests for the launchd LaunchAgent generation (SCHED-01/02).

Covers:
* the generated plist is valid (parses + plutil -lint when available);
* it uses launchd's StartCalendarInterval (the catch-up-on-wake mechanism);
* it uses absolute paths + an explicit env (stripped-launchd-env safe);
* install writes a template by default and never runs launchctl;
* the committed template file is valid.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

from tempo import scheduler

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _paths(tmp_path: Path) -> scheduler.SchedulerPaths:
    return scheduler.resolve_paths(project_dir=tmp_path / "proj", data_dir=tmp_path / "data")


def test_render_plist_is_valid_xml_and_parses(tmp_path: Path) -> None:
    text = scheduler.render_plist(_paths(tmp_path))
    parsed = plistlib.loads(text.encode("utf-8"))
    assert parsed["Label"] == scheduler.LABEL
    assert "ProgramArguments" in parsed
    assert parsed["ProgramArguments"][-1] == "run-daily"


def test_plist_uses_start_calendar_interval(tmp_path: Path) -> None:
    """launchd's StartCalendarInterval is the catch-up-on-wake mechanism (SCHED-02)."""
    parsed = plistlib.loads(scheduler.render_plist(_paths(tmp_path), hour=6, minute=15).encode())
    assert parsed["StartCalendarInterval"]["Hour"] == 6
    assert parsed["StartCalendarInterval"]["Minute"] == 15


def test_plist_uses_absolute_paths_and_explicit_env(tmp_path: Path) -> None:
    """Absolute ProgramArguments[0] + an explicit PATH = stripped-env safe (PITFALLS 7)."""
    parsed = plistlib.loads(scheduler.render_plist(_paths(tmp_path)).encode())
    program = parsed["ProgramArguments"][0]
    assert Path(program).is_absolute()
    env = parsed["EnvironmentVariables"]
    assert "PATH" in env and ":" in env["PATH"]
    assert env["TEMPO_DATA_DIR"].endswith("data")


def test_plist_captures_logs_and_does_not_run_at_load(tmp_path: Path) -> None:
    parsed = plistlib.loads(scheduler.render_plist(_paths(tmp_path)).encode())
    assert parsed["StandardOutPath"].endswith("daily.out.log")
    assert parsed["StandardErrorPath"].endswith("daily.err.log")
    assert parsed["RunAtLoad"] is False


def test_plutil_lint_passes_if_available(tmp_path: Path) -> None:
    if shutil.which("plutil") is None:
        return  # not on macOS / no plutil; the plistlib parse above already validates
    p = tmp_path / "t.plist"
    p.write_text(scheduler.render_plist(_paths(tmp_path)), encoding="utf-8")
    out = subprocess.run(["plutil", "-lint", str(p)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_install_writes_template_by_default(tmp_path: Path) -> None:
    """Default install writes a template under the data dir; NEVER runs launchctl."""
    result = scheduler.install_plist(project_dir=tmp_path / "proj", data_dir=tmp_path / "data")
    assert result.installed_to_launch_agents is False
    assert result.plist_path.exists()
    assert result.plist_path.parent == tmp_path / "data" / "launchd"
    # The returned commands are the manual steps the USER runs -- not auto-run.
    assert result.load_command.startswith("launchctl load")
    assert result.unload_command.startswith("launchctl unload")
    # And it parses.
    plistlib.loads(result.plist_path.read_text().encode())


def test_install_to_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "custom"
    result = scheduler.install_plist(
        project_dir=tmp_path / "proj",
        data_dir=tmp_path / "data",
        out_dir=out,
    )
    assert result.plist_path.parent == out


def test_committed_template_is_valid_plist() -> None:
    """The committed launchd/com.tempo.daily.plist template parses + lints."""
    template = PROJECT_ROOT / "launchd" / "com.tempo.daily.plist"
    assert template.exists()
    parsed = plistlib.loads(template.read_text().encode())
    assert parsed["Label"] == "com.tempo.daily"
    assert parsed["ProgramArguments"][-1] == "run-daily"
    assert parsed["RunAtLoad"] is False
    assert "StartCalendarInterval" in parsed
    if shutil.which("plutil") is not None:
        out = subprocess.run(["plutil", "-lint", str(template)], capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


def test_find_program_prefers_uv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler.shutil, "which", lambda name: "/fake/uv" if name == "uv" else None
    )
    program, args = scheduler._find_program(tmp_path)
    assert program == "/fake/uv"
    assert args == ["/fake/uv", "run", "tempo", "run-daily"]


def test_find_program_falls_back_to_python(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler.shutil, "which", lambda name: None)
    program, args = scheduler._find_program(tmp_path)
    assert args[-2:] == ["tempo.cli", "run-daily"] or args[-1] == "run-daily"
