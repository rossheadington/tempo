"""launchd LaunchAgent generation for the daily run (SCHED-01/02).

macOS scheduling uses **launchd, NOT cron** (STACK; PITFALLS 7). The reasons,
documented for the user in the README and here:

* cron on macOS **silently skips** jobs while the Mac is asleep -- the daily sync
  would simply not run, leaving silent data gaps. launchd runs a missed
  ``StartCalendarInterval`` job **on wake**.
* cron runs in a **stripped environment** (no login-shell ``PATH``), so ``uv`` /
  the venv are often not found. The generated plist uses **absolute paths** and an
  explicit ``PATH`` so the job runs the same way the terminal does.
* stdout/stderr are captured to a log file under the data dir so a failure is
  diagnosable after the fact, and the daily run surfaces staleness rather than
  failing silently.

This module only *generates* the plist text and, optionally, writes it to
``~/Library/LaunchAgents/``. It deliberately does **not** run ``launchctl load``:
loading is a system-level side effect that must be an explicit, informed human
action (the README documents the exact ``launchctl`` commands). Tempo never loads
itself into the user's launchd silently.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

# The reverse-DNS LaunchAgent label. The plist filename mirrors this.
LABEL = "com.tempo.daily"

# Default time-of-day the daily job fires (local time). Late enough that the
# overnight Garmin sync + Strava activities of the prior day are available.
DEFAULT_HOUR = 5
DEFAULT_MINUTE = 30


@dataclass(frozen=True, slots=True)
class SchedulerPaths:
    """Resolved paths used to render the plist."""

    program: str  # absolute path to the launcher (uv or the tempo entrypoint)
    args: list[str]  # full argv (program + subcommand)
    working_dir: str
    stdout_log: str
    stderr_log: str
    data_dir: str


def _find_program(project_dir: Path) -> tuple[str, list[str]]:
    """Resolve how to invoke ``tempo run-daily`` with absolute paths (no PATH reliance).

    Prefers ``uv run tempo run-daily`` (matching how the project is run), falling
    back to the installed ``tempo`` console script, then to ``python -m tempo.cli``.
    All three are returned as absolute-path argv so launchd's stripped env works.
    """
    uv = shutil.which("uv")
    if uv:
        return uv, [uv, "run", "tempo", "run-daily"]
    tempo = shutil.which("tempo")
    if tempo:
        return tempo, [tempo, "run-daily"]
    # Last resort: the current interpreter running the CLI module.
    return sys.executable, [sys.executable, "-m", "tempo.cli", "run-daily"]


def resolve_paths(*, project_dir: Path, data_dir: Path) -> SchedulerPaths:
    """Resolve all absolute paths the plist needs."""
    program, args = _find_program(project_dir)
    log_dir = data_dir / "logs"
    return SchedulerPaths(
        program=program,
        args=args,
        working_dir=str(project_dir),
        stdout_log=str(log_dir / "daily.out.log"),
        stderr_log=str(log_dir / "daily.err.log"),
        data_dir=str(data_dir),
    )


def render_plist(
    paths: SchedulerPaths,
    *,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    label: str = LABEL,
) -> str:
    """Render a valid LaunchAgent plist for the daily run (SCHED-01/02).

    Uses ``StartCalendarInterval`` (the catch-up-on-wake mechanism) with an explicit
    absolute ``PATH``, the project working dir, ``TEMPO_DATA_DIR`` so the scheduled
    run uses the same data dir as the interactive one, and stdout/stderr captured to
    log files. ``RunAtLoad`` is false so loading the agent doesn't immediately fire a
    sync.
    """
    arg_items = "\n".join(f"        <string>{escape(a)}</string>" for a in paths.args)
    # An explicit PATH that includes common Homebrew + system locations so `uv`
    # and any tools it shells out to resolve under launchd's stripped environment.
    path_env = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{escape(label)}</string>

    <key>ProgramArguments</key>
    <array>
{arg_items}
    </array>

    <!-- launchd runs a MISSED StartCalendarInterval job on wake; cron would
         silently skip it while the Mac slept (PITFALLS 7 / SCHED-02). -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>

    <key>WorkingDirectory</key>
    <string>{escape(paths.working_dir)}</string>

    <!-- Absolute PATH + TEMPO_DATA_DIR so the stripped launchd env runs the job
         exactly like the interactive shell does (PITFALLS 7). -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{escape(path_env)}</string>
        <key>TEMPO_DATA_DIR</key>
        <string>{escape(paths.data_dir)}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>{escape(paths.stdout_log)}</string>
    <key>StandardErrorPath</key>
    <string>{escape(paths.stderr_log)}</string>

    <!-- Don't fire a sync just because the agent was (re)loaded. -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def launch_agents_dir() -> Path:
    """The per-user LaunchAgents directory (``~/Library/LaunchAgents``)."""
    return Path.home() / "Library" / "LaunchAgents"


def plist_filename(label: str = LABEL) -> str:
    return f"{label}.plist"


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Where the plist was written + the manual launchctl steps to enable it."""

    plist_path: Path
    installed_to_launch_agents: bool
    load_command: str
    unload_command: str


def write_plist(text: str, dest: Path, *, mkdir: bool = True) -> Path:
    """Write the plist text to ``dest`` (creating parent dirs). Returns the path."""
    if mkdir:
        dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def install_plist(
    *,
    project_dir: Path,
    data_dir: Path,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    to_launch_agents: bool = False,
    out_dir: Path | None = None,
    label: str = LABEL,
) -> InstallResult:
    """Render + write the LaunchAgent plist; never auto-loads it into launchd.

    By default writes the plist to ``out_dir`` (a template location the user can
    inspect). With ``to_launch_agents=True`` it writes into
    ``~/Library/LaunchAgents/`` so the user only has to ``launchctl load`` it -- but
    Tempo still does **not** run ``launchctl`` itself (that explicit step is left to
    the user, and the exact commands are returned in :class:`InstallResult`).
    """
    paths = resolve_paths(project_dir=project_dir, data_dir=data_dir)
    text = render_plist(paths, hour=hour, minute=minute, label=label)

    if to_launch_agents:
        dest = launch_agents_dir() / plist_filename(label)
    else:
        target_dir = out_dir or (data_dir / "launchd")
        dest = target_dir / plist_filename(label)

    write_plist(text, dest)

    final = launch_agents_dir() / plist_filename(label)
    return InstallResult(
        plist_path=dest,
        installed_to_launch_agents=to_launch_agents,
        load_command=f"launchctl load -w {final}",
        unload_command=f"launchctl unload -w {final}",
    )
