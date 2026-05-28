"""First-run setup wizard orchestrator (``runos setup``).

Pure orchestration over the 10 LOCKED steps in CONTEXT § "Step list":

    welcome -> db -> content -> strava -> garmin -> telegram ->
    scheduler -> bot-scheduler -> smoke -> finish

Design rules (load-bearing):

* Every credentialed step **delegates** to an existing helper: DB →
  :func:`runos.cli._init`; Strava → :func:`runos.connectors.factory.build_strava_connector`
  + ``connector.authorization_url`` + ``connector.exchange_code`` (the same
  three calls ``runos strava auth`` makes); Garmin →
  :func:`runos.connectors.factory.garmin_login`; daily scheduler →
  :func:`runos.scheduler.install_plist`; bot scheduler →
  :func:`runos.scheduler.install_telegram_bot_plist`; smoke →
  :func:`runos.sync.pipeline.run_full_sync`. No duplicated handshakes, no
  duplicated plist renders, no duplicated MFA prompt.
* Every ``.env`` write goes through :func:`runos.setup.env_io.atomic_write_env`.
  Credentials are written **before** the downstream call so a partial failure
  leaves creds in place for retry.
* Every state check goes through :func:`runos.setup.state.detect_install_state`.
* Each step is safe to Ctrl-C: a :class:`typer.Abort` propagates to the
  orchestrator which returns exit code 2.
* Idempotent re-run: every step starts by consulting ``InstallState`` and
  prints ``[done]`` + skips when the corresponding bool is ``True``.

The orchestrator owns prompts, dispatch, the indicator decision tree, and the
final smoke-test reporting. It owns NO ``.env`` I/O surface of its own and NO
state-detection surface of its own.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import typer

from runos.config import Settings, get_settings
from runos.setup.env_io import atomic_write_env, read_env
from runos.setup.prompts import (
    confirm_yn,
    print_block,
    print_indicator,
    print_step_banner,
    prompt_int,
    prompt_secret,
    prompt_visible,
)
from runos.setup.state import InstallState, detect_install_state

# Locked step order. The orchestrator iterates this tuple; tests assert on it.
STEP_IDS = (
    "welcome",
    "db",
    "content",
    "strava",
    "garmin",
    "telegram",
    "scheduler",
    "bot-scheduler",
    "smoke",
    "finish",
)

# Project-root .env path. pydantic-settings reads .env from cwd by default, so
# we match that convention.
_ENV_PATH = Path(".env")


@dataclass(frozen=True, slots=True)
class StepResult:
    """The outcome of one step. ``status`` is one of done|skipped|completed|failed."""

    step_id: str
    status: str
    detail: str = ""


def _check_non_interactive(non_interactive: bool, what: str) -> None:
    """Raise :class:`typer.Abort` if a prompt would have run under --non-interactive.

    --non-interactive is a fail-fast test mode (CONTEXT line 48). It must NOT
    silently default; an exit-2 is the contract.
    """
    if non_interactive:
        typer.secho(
            f"--non-interactive: would have prompted for {what}; aborting",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Abort()


def _can_open_browser() -> bool:
    """Return True if we can reasonably auto-open a URL (macOS, or DISPLAY set)."""
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY"))


def _summary_line(state: InstallState) -> str:
    """One-line install summary; used by both welcome + finish banners."""
    return (
        f"DB {'OK' if state.db_initialised else 'x'} - "
        f"Strava {'OK' if state.strava_configured else 'x'} - "
        f"Garmin {'OK' if state.garmin_configured else 'x'} - "
        f"Telegram {'OK' if state.telegram_configured else 'x'} - "
        f"daily-scheduler {'OK' if state.daily_scheduler_installed else 'x'} - "
        f"bot-scheduler {'OK' if state.bot_scheduler_installed else 'x'}"
    )


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


def step_welcome(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Banner + Python version check + uv-on-PATH warning + detected-state line."""
    print_step_banner("welcome", "RunOS first-run setup")
    typer.echo(
        "Walks you from zero (no DB, no .env, no tokens) to a working `runos run-daily`."
    )

    if sys.version_info < (3, 14):  # noqa: UP036 - runtime guard for misconfigured installs
        v = sys.version_info
        typer.secho(
            f"RunOS requires Python 3.14+ (found {v.major}.{v.minor}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if shutil.which("uv") is None:
        typer.secho(
            "Warning: `uv` not found on PATH. That's OK if you run with `python -m runos`.",
            fg=typer.colors.YELLOW,
        )

    typer.echo(f"Detected: {_summary_line(state)}")
    return StepResult("welcome", "completed")


def step_db(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Init the SQLite DB (or skip if already at latest schema)."""
    print_step_banner("db", "SQLite DB initialisation")

    if state.db_initialised:
        print_indicator(f"DB at {settings.db_path}", "done")
        if only and "db" in only:
            if non_interactive:
                # In --only mode + non-interactive, treat already-done as skipped.
                return StepResult("db", "skipped", "already at latest schema")
            if not confirm_yn("Re-run init? (idempotent)", default=False):
                return StepResult("db", "skipped", "already at latest schema")
        else:
            return StepResult("db", "skipped", "already at latest schema")

    # Lazy import: re-uses the existing CLI helper (preflight note: known smell,
    # but cleaner than refactoring _init out of cli.py for this single caller).
    try:
        from runos.cli import _init

        _init()
    except Exception as exc:  # noqa: BLE001 - report any init failure cleanly
        print_indicator(f"init failed: {exc}", "skip")
        return StepResult("db", "failed", str(exc))
    return StepResult("db", "completed")


def step_content(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Pick the content directory (races.md, heat.md, strength.md location)."""
    print_step_banner("content", "Content directory (races.md, heat.md, strength.md)")

    env = read_env(_ENV_PATH)
    existing = env.get("RUNOS_CONTENT_DIR")
    if existing:
        print_indicator(f"RUNOS_CONTENT_DIR={existing}", "set")
        if only and "content" in only:
            if non_interactive:
                return StepResult("content", "skipped", "already set")
            if not confirm_yn("Change to a different content dir?", default=False):
                return StepResult("content", "skipped", "already set")
        else:
            return StepResult("content", "skipped", "already set")

    _check_non_interactive(non_interactive, "RUNOS_CONTENT_DIR")
    default = str(Path.home() / ".runos")
    # Detect project-local option: only relevant when run from the RunOS repo.
    project_local = Path.cwd() / "training"
    if (Path.cwd() / "runos" / "cli.py").exists():
        typer.echo(f"  (tip: '{project_local}' keeps trackers handy inside the project)")

    value = prompt_visible("Content dir path", default=default)
    chosen = Path(value).expanduser().resolve()
    chosen.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(PermissionError):
        os.chmod(chosen, 0o700)

    atomic_write_env(_ENV_PATH, {"RUNOS_CONTENT_DIR": str(chosen)})
    print_indicator(f"RUNOS_CONTENT_DIR={chosen}", "fresh")
    return StepResult("content", "completed", str(chosen))


def step_strava(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Strava client id + secret + OAuth handshake (delegates to build_strava_connector)."""
    print_step_banner("strava", "Strava API credentials + OAuth handshake")

    if state.strava_configured and (not only or "strava" not in only):
        print_indicator("Strava configured", "done")
        return StepResult("strava", "skipped", "creds + token present")

    print_block(
        "Strava API setup",
        "1. Open https://www.strava.com/settings/api\n"
        "2. Create an application (any name; callback domain = localhost)\n"
        "3. Copy the Client ID + Client Secret below.",
    )
    _check_non_interactive(non_interactive, "Strava client id + secret")
    client_id = prompt_visible("Strava Client ID")
    client_secret = prompt_secret("Strava Client Secret")

    # Write creds to .env BEFORE the OAuth handshake so a partial failure leaves
    # the creds for retry. This is a LOCKED truth in the plan.
    atomic_write_env(
        _ENV_PATH,
        {
            "RUNOS_STRAVA_CLIENT_ID": client_id,
            "RUNOS_STRAVA_CLIENT_SECRET": client_secret,
        },
    )
    # Re-read settings so build_strava_connector sees the new env keys.
    settings = get_settings()

    from runos.connectors.factory import build_strava_connector

    try:
        connector = build_strava_connector(settings)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return StepResult("strava", "failed", str(exc))

    url = connector.authorization_url(settings.strava_redirect_uri)
    typer.echo(
        f"\nOpen this URL, approve, then paste the `code` query parameter back:\n  {url}\n"
    )
    if _can_open_browser():
        with contextlib.suppress(Exception):
            webbrowser.open(url)

    code = prompt_visible("Paste the `code` parameter from the redirect URL")
    try:
        connector.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - report any exchange failure cleanly
        typer.secho(f"Strava OAuth failed: {exc}", fg=typer.colors.RED, err=True)
        typer.echo("Re-run with `runos setup --only=strava` to retry.")
        return StepResult("strava", "failed", str(exc))

    print_indicator("Strava authorised", "fresh")
    return StepResult("strava", "completed")


def step_garmin(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Garmin login (optional). Delegates to factory.garmin_login."""
    print_step_banner("garmin", "Garmin login (optional - wellness data)")

    if state.garmin_configured and (not only or "garmin" not in only):
        print_indicator("Garmin configured", "done")
        return StepResult("garmin", "skipped", "creds + tokens present")

    # Full-wizard run: ask whether the user wants Garmin at all.
    if not only:
        if non_interactive:
            # Default to skipping Garmin in non-interactive mode (it's optional).
            return StepResult("garmin", "skipped", "non-interactive default: declined")
        if not confirm_yn(
            "Do you also use Garmin (wellness data: HRV, sleep, resting HR)?",
            default=True,
        ):
            return StepResult("garmin", "skipped", "user declined")

    _check_non_interactive(non_interactive, "Garmin email + password")
    email = prompt_visible("Garmin email")
    password = prompt_secret("Garmin password")
    atomic_write_env(
        _ENV_PATH,
        {"RUNOS_GARMIN_EMAIL": email, "RUNOS_GARMIN_PASSWORD": password},
    )
    settings = get_settings()

    from runos.connectors.factory import garmin_login

    def _prompt_mfa() -> str:
        return prompt_visible("Garmin MFA code")

    try:
        token_dir = garmin_login(settings, prompt_mfa=_prompt_mfa)
    except Exception as exc:  # noqa: BLE001 - any Garmin failure is reported, never raised
        typer.secho(f"Garmin login failed: {exc}", fg=typer.colors.RED, err=True)
        typer.echo(
            "If you saw repeated 429s, STOP and wait a few hours -- retrying compounds"
        )
        typer.echo("Garmin's per-account lockout. Do not loop logins.")
        return StepResult("garmin", "failed", str(exc))

    print_indicator(f"Garmin tokens at {token_dir}", "fresh")
    return StepResult("garmin", "completed", str(token_dir))


def step_telegram(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Telegram bot token + chat id + optional Whisper / voice-retention knobs."""
    print_step_banner("telegram", "Telegram bot credentials (optional - voice + text coach)")

    if state.telegram_configured and (not only or "telegram" not in only):
        print_indicator("Telegram configured", "done")
        return StepResult("telegram", "skipped", "creds present")

    if not only:
        if non_interactive:
            return StepResult("telegram", "skipped", "non-interactive default: declined")
        if not confirm_yn(
            "Do you want to run the Telegram voice/text bot?", default=False
        ):
            return StepResult("telegram", "skipped", "user declined")

    print_block(
        "Telegram bot setup",
        "1. In Telegram, open a chat with @BotFather. Send /newbot, follow prompts.\n"
        "   BotFather replies with a token like 1234567890:AAH... - copy it.\n"
        "2. Open a chat with @userinfobot and send /start to get your numeric chat id.",
    )
    _check_non_interactive(non_interactive, "Telegram bot token + chat id")
    token = prompt_secret("Telegram bot token")
    chat_id = prompt_visible("Your Telegram chat id (numeric)")
    # Note: bare names (NOT RUNOS_-prefixed) -- matches .env.example convention.
    atomic_write_env(
        _ENV_PATH,
        {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_CHAT_ID": chat_id},
    )

    # Optional Whisper knobs.
    if confirm_yn("Change Whisper model defaults (advanced)?", default=False):
        whisper_updates: dict[str, str] = {}
        model = prompt_visible("WHISPER_MODEL_NAME", default="small.en")
        if model != "small.en":
            whisper_updates["WHISPER_MODEL_NAME"] = model
        compute = prompt_visible("WHISPER_COMPUTE_TYPE", default="int8")
        if compute != "int8":
            whisper_updates["WHISPER_COMPUTE_TYPE"] = compute
        device = prompt_visible("WHISPER_DEVICE (blank = auto)", default="")
        if device:
            whisper_updates["WHISPER_DEVICE"] = device
        if whisper_updates:
            atomic_write_env(_ENV_PATH, whisper_updates)

    # Optional voice retention.
    if confirm_yn("Change voice retention from delete-on-success?", default=False):
        days = prompt_int("Retain voice files for N days", default=0)
        if days > 0:
            atomic_write_env(_ENV_PATH, {"VOICE_RETENTION_DAYS": str(days)})

    print_indicator("Telegram configured", "fresh")
    return StepResult("telegram", "completed")


def step_scheduler(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Daily-sync launchd plist (delegates to scheduler.install_plist)."""
    print_step_banner("scheduler", "Daily-sync launchd job (optional)")

    if state.daily_scheduler_installed and (not only or "scheduler" not in only):
        print_indicator("~/Library/LaunchAgents/com.runos.daily.plist", "done")
        return StepResult("scheduler", "skipped", "plist already installed")

    if not only:
        if non_interactive:
            return StepResult(
                "scheduler", "skipped", "non-interactive default: declined"
            )
        if not confirm_yn(
            "Install the daily-sync launchd job to run automatically?", default=True
        ):
            return StepResult("scheduler", "skipped", "user declined")

    _check_non_interactive(non_interactive, "scheduler hour + minute")
    hour = prompt_int("Hour (0-23)", default=5)
    minute = prompt_int("Minute (0-59)", default=30)

    from runos import scheduler

    try:
        result = scheduler.install_plist(
            project_dir=Path.cwd(),
            data_dir=settings.data_dir,
            hour=hour,
            minute=minute,
            to_launch_agents=True,
        )
    except Exception as exc:  # noqa: BLE001 - report any install failure cleanly
        typer.secho(f"Scheduler install failed: {exc}", fg=typer.colors.RED, err=True)
        return StepResult("scheduler", "failed", str(exc))

    print_indicator(f"plist at {result.plist_path}", "fresh")
    typer.echo("To enable:")
    typer.secho(f"  {result.load_command}", fg=typer.colors.CYAN)
    return StepResult("scheduler", "completed", str(result.plist_path))


def step_bot_scheduler(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
    telegram_completed: bool = False,
) -> StepResult:
    """Telegram-bot launchd plist; only offered if Telegram is configured."""
    print_step_banner("bot-scheduler", "Telegram-bot launchd job (optional)")

    # Offer condition (LOCKED, CONTEXT line 105): only if Telegram is configured.
    if not (state.telegram_configured or telegram_completed):
        print_indicator("Telegram not configured - skipping bot-scheduler", "skip")
        return StepResult("bot-scheduler", "skipped", "telegram not configured")

    if state.bot_scheduler_installed and (not only or "bot-scheduler" not in only):
        print_indicator("~/Library/LaunchAgents/com.runos.telegram-bot.plist", "done")
        return StepResult("bot-scheduler", "skipped", "plist already installed")

    if not only:
        if non_interactive:
            return StepResult(
                "bot-scheduler", "skipped", "non-interactive default: declined"
            )
        if not confirm_yn(
            "Install the Telegram-bot launchd job (KeepAlive=true)?", default=True
        ):
            return StepResult("bot-scheduler", "skipped", "user declined")

    from runos import scheduler

    try:
        result = scheduler.install_telegram_bot_plist(
            project_root=Path.cwd(),
            to_launch_agents=True,
            uv_bin=None,
            tz=None,
        )
    except Exception as exc:  # noqa: BLE001 - report any install failure cleanly
        typer.secho(
            f"Bot-scheduler install failed: {exc}", fg=typer.colors.RED, err=True
        )
        return StepResult("bot-scheduler", "failed", str(exc))

    print_indicator(f"plist at {result.plist_path}", "fresh")
    typer.echo("To enable:")
    typer.secho(f"  {result.load_command}", fg=typer.colors.CYAN)
    typer.secho(f"  {result.start_command}", fg=typer.colors.CYAN)
    return StepResult("bot-scheduler", "completed", str(result.plist_path))


def step_smoke(
    settings: Settings,
    state: InstallState,
    *,
    only: frozenset[str],
    non_interactive: bool,
) -> StepResult:
    """Run pipeline.run_full_sync in-process and print per-source status."""
    print_step_banner("smoke", "Smoke test: runos sync")

    from runos import db as _db
    from runos.sync import pipeline

    settings = get_settings()
    settings.ensure_dirs()
    conn = _db.init_db(settings.db_path)
    try:
        try:
            results = pipeline.run_full_sync(conn, settings)
        except Exception as exc:  # noqa: BLE001 - report sync failures cleanly
            typer.secho(f"Smoke test failed: {exc}", fg=typer.colors.RED, err=True)
            return StepResult("smoke", "failed", str(exc))
    finally:
        conn.close()

    strava_terminal_failure = False
    for r in results:
        if r.ok:
            typer.secho(
                f"  {r.source}: OK ({r.rows} raw rows)", fg=typer.colors.GREEN
            )
        else:
            typer.secho(
                f"  {r.source}: skipped -- {r.detail}", fg=typer.colors.YELLOW
            )
            if r.source == "strava":
                strava_terminal_failure = True

    if strava_terminal_failure:
        typer.secho(
            "Strava sync failed terminally. Re-run with `runos setup --only=strava`.",
            fg=typer.colors.RED,
        )
        return StepResult("smoke", "failed", "strava terminal failure")

    return StepResult("smoke", "completed")


def step_finish(
    settings: Settings,
    state_before: InstallState,
    results: list[StepResult],
) -> StepResult:
    """Re-detect install state, print a summary line + next-step hints."""
    print_step_banner("finish", "Setup complete")
    state_after = detect_install_state(settings)
    typer.echo(f"Installed: {_summary_line(state_after)}")
    typer.echo("\nNext steps:")
    typer.echo("  - View today's report: `ls ~/.runos/reports/`")
    if state_after.daily_scheduler_installed:
        typer.echo(
            "  - Enable the daily job: `launchctl bootstrap gui/$UID "
            "~/Library/LaunchAgents/com.runos.daily.plist`"
        )
    if state_after.bot_scheduler_installed:
        typer.echo(
            "  - Enable the bot: `launchctl bootstrap gui/$UID "
            "~/Library/LaunchAgents/com.runos.telegram-bot.plist`"
        )
    typer.echo("  - Manual setup walkthrough: docs/SETUP.md")
    return StepResult("finish", "completed")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_wizard(
    settings: Settings,
    *,
    only: frozenset[str] | set[str] | list[str] | None = None,
    skip_garmin: bool = False,
    skip_telegram: bool = False,
    skip_scheduler: bool = False,
    skip_bot_scheduler: bool = False,
    skip_smoke: bool = False,
    non_interactive: bool = False,
) -> int:
    """Run the first-run setup wizard. Returns the process exit code (0 / 1 / 2).

    Exit codes:
        0 - no non-skipped step failed terminally
        1 - one or more non-skipped steps failed (smoke detected a terminal error, etc.)
        2 - typer.Abort raised (Ctrl-C or --non-interactive hit a required prompt)
    """
    only_set: frozenset[str] = frozenset(only) if only else frozenset()
    # --skip-telegram implies --skip-bot-scheduler (CONTEXT line 44).
    if skip_telegram:
        skip_bot_scheduler = True

    # Reject --only=<step> when <step> is also skipped by a flag: the user
    # asked for the step explicitly AND asked to skip it, which is incoherent.
    # Catching it here means the wizard fails fast with a clear message
    # instead of silently producing a no-op run.
    skipped_by_flag: dict[str, str] = {}
    if skip_garmin:
        skipped_by_flag["garmin"] = "--skip-garmin"
    if skip_telegram:
        skipped_by_flag["telegram"] = "--skip-telegram"
    if skip_scheduler:
        skipped_by_flag["scheduler"] = "--skip-scheduler"
    if skip_bot_scheduler:
        # If --skip-telegram set this, surface the implication so the error
        # is honest about why bot-scheduler is unavailable.
        skipped_by_flag["bot-scheduler"] = (
            "--skip-telegram (implies --skip-bot-scheduler)"
            if skip_telegram
            else "--skip-bot-scheduler"
        )
    if skip_smoke:
        skipped_by_flag["smoke"] = "--skip-smoke"
    conflicts = sorted(only_set & skipped_by_flag.keys())
    if conflicts:
        for step in conflicts:
            typer.secho(
                f"--only={step} conflicts with {skipped_by_flag[step]}: "
                "step is both requested and skipped.",
                fg=typer.colors.RED,
                err=True,
            )
        return 2

    # Explicit dispatch table. Plain list of tuples; no clever registration.
    dispatch: list[tuple[str, object, bool]] = [
        ("welcome", step_welcome, False),
        ("db", step_db, False),
        ("content", step_content, False),
        ("strava", step_strava, False),
        ("garmin", step_garmin, skip_garmin),
        ("telegram", step_telegram, skip_telegram),
        ("scheduler", step_scheduler, skip_scheduler),
        ("bot-scheduler", step_bot_scheduler, skip_bot_scheduler),
        ("smoke", step_smoke, skip_smoke),
        ("finish", step_finish, False),
    ]

    state = detect_install_state(settings)
    results: list[StepResult] = []
    telegram_completed = False

    for step_id, runner, skip_flag in dispatch:
        # --skip-* flags win unconditionally for the non-welcome/finish steps.
        if skip_flag:
            print_indicator(f"step {step_id} skipped by flag", "skip")
            results.append(StepResult(step_id, "skipped", "skip flag"))
            continue
        # --only filter: when set, only the listed steps run (welcome + finish always run).
        if only_set and step_id not in only_set and step_id not in {"welcome", "finish"}:
            continue

        try:
            if step_id == "finish":
                result = step_finish(settings, state, results)
            elif step_id == "bot-scheduler":
                result = step_bot_scheduler(
                    settings,
                    state,
                    only=only_set,
                    non_interactive=non_interactive,
                    telegram_completed=telegram_completed,
                )
            else:
                result = runner(  # type: ignore[operator]
                    settings,
                    state,
                    only=only_set,
                    non_interactive=non_interactive,
                )
        except typer.Abort:
            return 2
        except Exception as exc:  # noqa: BLE001 - never let a step crash the wizard
            typer.secho(
                f"step {step_id} crashed: {exc}", fg=typer.colors.RED, err=True
            )
            results.append(StepResult(step_id, "failed", str(exc)))
            continue

        results.append(result)
        if step_id == "telegram" and result.status == "completed":
            telegram_completed = True
        # Re-detect state after any step that may have changed it. Cheap.
        state = detect_install_state(settings)

    failed = [r for r in results if r.status == "failed"]
    return 1 if failed else 0
