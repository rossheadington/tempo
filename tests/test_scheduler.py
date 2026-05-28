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


# ---------------------------------------------------------------------------
# Phase 12: long-running Telegram-bot LaunchAgent
# ---------------------------------------------------------------------------


def test_telegram_bot_template_committed_lints() -> None:
    """The COMMITTED telegram-bot template parses as plist + lints (placeholders
    are inside string elements, so the unrendered template must still be valid).
    """
    template = PROJECT_ROOT / "launchd" / "com.tempo.telegram-bot.plist"
    assert template.exists()
    parsed = plistlib.loads(template.read_text().encode())
    assert parsed["Label"] == "com.tempo.telegram-bot"
    assert parsed["KeepAlive"] is True
    assert parsed["ThrottleInterval"] == 10
    assert parsed["RunAtLoad"] is True
    # ProgramArguments terminate with `tempo bot run`.
    assert parsed["ProgramArguments"][-3:] == ["tempo", "bot", "run"]
    env = parsed["EnvironmentVariables"]
    assert env["OMP_NUM_THREADS"] == "4"
    if shutil.which("plutil") is not None:
        out = subprocess.run(["plutil", "-lint", str(template)], capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


def test_render_telegram_bot_plist_substitutes_placeholders(tmp_path: Path) -> None:
    """Render substitutes {{UV_BIN}} / {{PROJECT_ROOT}} / {{TZ}} and lints clean."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    text = scheduler.render_telegram_bot_plist(
        project_root=project_root,
        uv_bin="/fake/uv",
        tz="Europe/London",
    )
    # None of the three substitution placeholders remain (the literal token
    # "{{PLACEHOLDER}}" appears once in the comment as a docstring example,
    # which is fine -- it is inside the XML comment block and never reaches
    # the substituted output).
    for placeholder in ("{{UV_BIN}}", "{{PROJECT_ROOT}}", "{{TZ}}"):
        assert placeholder not in text, f"placeholder {placeholder} not substituted"
    parsed = plistlib.loads(text.encode())
    assert parsed["ProgramArguments"][0] == "/fake/uv"
    assert parsed["WorkingDirectory"] == str(project_root)
    assert parsed["EnvironmentVariables"]["TZ"] == "Europe/London"
    assert parsed["StandardOutPath"] == f"{project_root}/logs/telegram-bot.stdout.log"
    assert parsed["StandardErrorPath"] == f"{project_root}/logs/telegram-bot.stderr.log"


def test_install_telegram_bot_plist_writes_template_and_creates_logs(tmp_path: Path) -> None:
    """Default install writes a template under the project's launchd/ dir,
    ensures <project>/logs/ exists, and never auto-loads launchd.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()
    result = scheduler.install_telegram_bot_plist(
        project_root=project_root,
        uv_bin="/fake/uv",
        tz="UTC",
    )
    assert result.installed_to_launch_agents is False
    assert result.plist_path.exists()
    assert result.plist_path.parent == project_root / "launchd"
    assert result.logs_dir == project_root / "logs"
    assert result.logs_dir.is_dir()
    assert result.load_command.startswith("launchctl load")
    assert result.start_command == "launchctl start com.tempo.telegram-bot"
    assert result.unload_command.startswith("launchctl unload")
    # Rendered plist parses.
    parsed = plistlib.loads(result.plist_path.read_text().encode())
    assert parsed["Label"] == "com.tempo.telegram-bot"
    assert parsed["KeepAlive"] is True


def test_install_telegram_bot_plist_lints_when_plutil_available(tmp_path: Path) -> None:
    """When plutil is on PATH, the install runs `plutil -lint` and records the result."""
    if shutil.which("plutil") is None:
        return
    project_root = tmp_path / "proj"
    project_root.mkdir()
    result = scheduler.install_telegram_bot_plist(
        project_root=project_root,
        uv_bin="/fake/uv",
        tz="UTC",
    )
    assert result.plutil_lint_ok is True


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


# ---- Hourly sync LaunchAgent ---------------------------------------------


def _sync_paths(tmp_path: Path) -> scheduler.SchedulerPaths:
    return scheduler.resolve_hourly_sync_paths(
        project_dir=tmp_path / "proj", data_dir=tmp_path / "data"
    )


def test_render_hourly_sync_plist_parses_and_uses_sync_subcommand(tmp_path: Path) -> None:
    """Hourly plist runs `tempo sync --notify-on-failure`, not run-daily."""
    parsed = plistlib.loads(scheduler.render_hourly_sync_plist(_sync_paths(tmp_path)).encode())
    assert parsed["Label"] == scheduler.HOURLY_SYNC_LABEL
    assert parsed["ProgramArguments"][-2:] == ["sync", "--notify-on-failure"]


def test_render_hourly_sync_plist_uses_start_interval(tmp_path: Path) -> None:
    """StartInterval, not StartCalendarInterval -- fires every N seconds."""
    parsed = plistlib.loads(scheduler.render_hourly_sync_plist(_sync_paths(tmp_path)).encode())
    assert parsed["StartInterval"] == 3600
    assert "StartCalendarInterval" not in parsed


def test_render_hourly_sync_plist_respects_interval_override(tmp_path: Path) -> None:
    """Caller can override the interval (e.g. 900s = every 15 min)."""
    parsed = plistlib.loads(
        scheduler.render_hourly_sync_plist(_sync_paths(tmp_path), interval_s=900).encode()
    )
    assert parsed["StartInterval"] == 900


def test_render_hourly_sync_plist_does_not_run_at_load(tmp_path: Path) -> None:
    """Loading the agent must NOT immediately fire a sync (avoids thundering herd)."""
    parsed = plistlib.loads(scheduler.render_hourly_sync_plist(_sync_paths(tmp_path)).encode())
    assert parsed["RunAtLoad"] is False


def test_render_hourly_sync_plist_uses_hourly_sync_log_paths(tmp_path: Path) -> None:
    """Logs land under hourly-sync.{out,err}.log so they don't collide with daily.* ones."""
    parsed = plistlib.loads(scheduler.render_hourly_sync_plist(_sync_paths(tmp_path)).encode())
    assert parsed["StandardOutPath"].endswith("hourly-sync.out.log")
    assert parsed["StandardErrorPath"].endswith("hourly-sync.err.log")


def test_install_hourly_sync_writes_template_by_default(tmp_path: Path) -> None:
    """Default install writes a template under data/launchd/; never runs launchctl."""
    result = scheduler.install_hourly_sync_plist(
        project_dir=tmp_path / "proj", data_dir=tmp_path / "data"
    )
    assert result.installed_to_launch_agents is False
    assert result.plist_path.exists()
    assert result.plist_path.name == scheduler.plist_filename(scheduler.HOURLY_SYNC_LABEL)
    assert result.load_command.startswith("launchctl load")
    assert result.unload_command.startswith("launchctl unload")
    plistlib.loads(result.plist_path.read_text().encode())
