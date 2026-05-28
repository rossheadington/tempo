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
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

# The reverse-DNS LaunchAgent label. The plist filename mirrors this.
LABEL = "com.tempo.daily"

# Phase 12: the long-running Telegram bot LaunchAgent label.
TELEGRAM_BOT_LABEL = "com.tempo.telegram-bot"

# Hourly Strava+Garmin sync LaunchAgent label. Runs `tempo sync
# --notify-on-failure` every hour via StartInterval; the daily-report job
# (`tempo run-daily`) is intentionally NOT scheduled in this simplified
# operational model -- reports are generated on-demand via Telegram.
HOURLY_SYNC_LABEL = "com.tempo.hourly-sync"

# Hourly sync interval, in seconds. launchd re-fires a missed interval on
# wake (same catch-up behaviour as StartCalendarInterval) so a sleeping Mac
# never skips a sync indefinitely.
HOURLY_SYNC_INTERVAL_S = 3600

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


def _find_program(project_dir: Path, *subcommand: str) -> tuple[str, list[str]]:
    """Resolve how to invoke ``tempo <subcommand>`` with absolute paths.

    Prefers ``uv run tempo <subcommand>`` (matching how the project is run),
    falling back to the installed ``tempo`` console script, then to
    ``python -m tempo.cli <subcommand>``. All three are returned as
    absolute-path argv so launchd's stripped env works.

    Default subcommand is ``run-daily`` (preserves the original
    install_plist behaviour for backwards compatibility).
    """
    cmd = list(subcommand) if subcommand else ["run-daily"]
    uv = shutil.which("uv")
    if uv:
        return uv, [uv, "run", "tempo", *cmd]
    tempo = shutil.which("tempo")
    if tempo:
        return tempo, [tempo, *cmd]
    # Last resort: the current interpreter running the CLI module.
    return sys.executable, [sys.executable, "-m", "tempo.cli", *cmd]


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


# ---------------------------------------------------------------------------
# Hourly sync LaunchAgent: `tempo sync --notify-on-failure` every 3600s
# ---------------------------------------------------------------------------


def resolve_hourly_sync_paths(
    *, project_dir: Path, data_dir: Path
) -> SchedulerPaths:
    """Resolve absolute paths for the hourly-sync plist.

    The argv is ``tempo sync --notify-on-failure --with-recent-streams`` so
    every hourly run:

    * Pulls Strava + Garmin activity summaries into raw.
    * Runs transform so newly-synced activities land in the structured
      ``activity`` table.
    * Fetches HR streams for recent (last 24h) activities recorded with a
      HR monitor that don't yet have streams stored.
    * Runs transform again so the structured ``activity_stream`` table is
      up-to-date.
    * Sends a Telegram message ONLY if anything failed (silent on success).
    """
    program, args = _find_program(
        project_dir, "sync", "--notify-on-failure", "--with-recent-streams"
    )
    log_dir = data_dir / "logs"
    return SchedulerPaths(
        program=program,
        args=args,
        working_dir=str(project_dir),
        stdout_log=str(log_dir / "hourly-sync.out.log"),
        stderr_log=str(log_dir / "hourly-sync.err.log"),
        data_dir=str(data_dir),
    )


def render_hourly_sync_plist(
    paths: SchedulerPaths,
    *,
    interval_s: int = HOURLY_SYNC_INTERVAL_S,
    label: str = HOURLY_SYNC_LABEL,
) -> str:
    """Render a LaunchAgent plist that runs the sync every ``interval_s`` seconds.

    Uses ``StartInterval`` instead of ``StartCalendarInterval`` so the job
    just fires every N seconds regardless of wall-clock alignment. launchd
    fires a missed interval on wake, so a sleeping Mac doesn't skip
    indefinitely. ``RunAtLoad`` is false so loading the agent doesn't
    immediately fire a sync (avoids a thundering-herd on multi-load).
    """
    arg_items = "\n".join(f"        <string>{escape(a)}</string>" for a in paths.args)
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

    <!-- Every {interval_s}s. launchd re-fires a missed interval on wake;
         cron would silently skip while the Mac slept (PITFALLS 7). -->
    <key>StartInterval</key>
    <integer>{interval_s}</integer>

    <key>WorkingDirectory</key>
    <string>{escape(paths.working_dir)}</string>

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


def install_hourly_sync_plist(
    *,
    project_dir: Path,
    data_dir: Path,
    interval_s: int = HOURLY_SYNC_INTERVAL_S,
    to_launch_agents: bool = False,
    out_dir: Path | None = None,
    label: str = HOURLY_SYNC_LABEL,
) -> InstallResult:
    """Render + write the hourly-sync LaunchAgent plist; never auto-loads it.

    Mirrors :func:`install_plist` (the daily-run sibling) but renders the
    sync plist with ``StartInterval=3600`` instead of a fixed daily time,
    and points argv at ``tempo sync --notify-on-failure`` instead of
    ``tempo run-daily``. Tempo never runs ``launchctl`` itself -- the exact
    bootstrap/bootout commands are returned in :class:`InstallResult` for
    the user to paste.

    Default ``interval_s`` is 3600 (one hour). Pass a smaller value (e.g.
    900 for 15min) only if you understand the rate-limit implications
    (Strava 200/15min, 2000/day; tighter than Garmin's account-lockout
    risk on 429).
    """
    paths = resolve_hourly_sync_paths(project_dir=project_dir, data_dir=data_dir)
    text = render_hourly_sync_plist(paths, interval_s=interval_s, label=label)

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


# ---------------------------------------------------------------------------
# Phase 12: long-running Telegram-bot LaunchAgent (KeepAlive=true)
# ---------------------------------------------------------------------------

#: Project-root path to the committed Telegram-bot plist template.
TELEGRAM_BOT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "launchd" / "com.tempo.telegram-bot.plist"
)


def _resolve_uv_bin() -> str:
    """Locate the absolute path to ``uv`` for the bot LaunchAgent.

    launchd runs with a stripped PATH; the plist's ProgramArguments must be
    absolute. Falls back to the current ``sys.executable`` only if ``uv`` is
    not on PATH (extremely unusual on a uv-managed project, but keeps the
    function total).
    """
    uv = shutil.which("uv")
    if uv:
        return uv
    return sys.executable


def _default_timezone() -> str:
    """Resolve the local IANA timezone for the bot plist's ``TZ`` env var.

    Prefers the standard ``/etc/localtime`` symlink resolution (e.g.
    ``Europe/London``); falls back to ``UTC`` if the symlink is missing or
    cannot be parsed (e.g. inside a sandbox).
    """
    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve()
        parts = resolved.parts
        # /var/db/timezone/zoneinfo/Europe/London -> "Europe/London"
        # /usr/share/zoneinfo/UTC                -> "UTC"
        if "zoneinfo" in parts:
            idx = parts.index("zoneinfo")
            tz = "/".join(parts[idx + 1 :])
            if tz:
                return tz
    except OSError:
        pass
    return "UTC"


def render_telegram_bot_plist(
    *,
    project_root: Path,
    uv_bin: str | None = None,
    tz: str | None = None,
    template: Path | None = None,
) -> str:
    """Render the Telegram-bot plist by substituting ``{{PLACEHOLDER}}`` tokens.

    The committed template (``launchd/com.tempo.telegram-bot.plist``) is the
    source of truth for the plist *shape* (KeepAlive=true, ThrottleInterval=10,
    RunAtLoad=true, OMP_NUM_THREADS=4, the {{PROJECT_ROOT}}/logs/ paths). This
    function only fills in the three deployment-specific values:

    * ``{{UV_BIN}}``        -- absolute path to ``uv``
    * ``{{PROJECT_ROOT}}``  -- absolute path to the project checkout
    * ``{{TZ}}``            -- the IANA timezone (e.g. ``Europe/London``)

    Caller is expected to ``plutil -lint`` the result before writing it to
    ``~/Library/LaunchAgents/``.
    """
    src = (template or TELEGRAM_BOT_TEMPLATE).read_text(encoding="utf-8")
    return (
        src.replace("{{UV_BIN}}", escape(uv_bin or _resolve_uv_bin()))
        .replace("{{PROJECT_ROOT}}", escape(str(project_root)))
        .replace("{{TZ}}", escape(tz or _default_timezone()))
    )


@dataclass(frozen=True, slots=True)
class TelegramBotInstallResult:
    """Where the rendered Telegram-bot plist was written + manual launchctl steps."""

    plist_path: Path
    installed_to_launch_agents: bool
    plutil_lint_ok: bool
    load_command: str
    start_command: str
    unload_command: str
    logs_dir: Path


def install_telegram_bot_plist(
    *,
    project_root: Path,
    out_dir: Path | None = None,
    to_launch_agents: bool = False,
    uv_bin: str | None = None,
    tz: str | None = None,
) -> TelegramBotInstallResult:
    """Render + write the Telegram-bot LaunchAgent plist; never auto-load it.

    Mirrors :func:`install_plist` (the daily-run sibling). Defaults to writing
    the rendered plist under ``project_root/launchd/`` for inspection;
    ``to_launch_agents=True`` writes it into ``~/Library/LaunchAgents/`` so the
    user only has to ``launchctl load`` + ``launchctl start``. Always ensures
    ``project_root/logs/`` exists so the plist's StandardOutPath/ErrorPath are
    not orphaned. Always runs ``plutil -lint`` on the rendered output when
    ``plutil`` is on PATH; raises :class:`RuntimeError` on a lint failure
    rather than writing a broken plist into LaunchAgents.
    """
    text = render_telegram_bot_plist(project_root=project_root, uv_bin=uv_bin, tz=tz)

    # Always create the logs dir before the plist is loaded; launchd will not
    # create the StandardOutPath/StandardErrorPath parent dirs for you.
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if to_launch_agents:
        dest = launch_agents_dir() / plist_filename(TELEGRAM_BOT_LABEL)
    else:
        target_dir = out_dir or (project_root / "launchd")
        dest = target_dir / plist_filename(TELEGRAM_BOT_LABEL)

    # Lint BEFORE writing: validate the rendered text via a temp file so a
    # broken substitution never reaches ~/Library/LaunchAgents.
    plutil_lint_ok = False
    if shutil.which("plutil") is not None:
        # Use a sibling temp file so we don't mutate dest until lint passes.
        tmp = dest.parent / f".{dest.name}.tmp-{int(time.time() * 1000)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        try:
            result = subprocess.run(
                ["plutil", "-lint", str(tmp)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Rendered Telegram-bot plist failed plutil -lint:\n"
                    f"{result.stdout}{result.stderr}"
                )
            plutil_lint_ok = True
        finally:
            tmp.unlink(missing_ok=True)

    write_plist(text, dest)

    final = launch_agents_dir() / plist_filename(TELEGRAM_BOT_LABEL)
    return TelegramBotInstallResult(
        plist_path=dest,
        installed_to_launch_agents=to_launch_agents,
        plutil_lint_ok=plutil_lint_ok,
        load_command=f"launchctl load -w {final}",
        start_command=f"launchctl start {TELEGRAM_BOT_LABEL}",
        unload_command=f"launchctl unload -w {final}",
        logs_dir=logs_dir,
    )
