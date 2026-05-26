"""The ``tempo`` command-line interface.

Phase 1 wired the command surface and DB initialisation. Phase 2 fills in the
Strava ingestion path:

* ``tempo strava auth``     -- the one-time OAuth handshake (STRV-01/02).
* ``tempo strava backfill`` -- resumable all-time history pull (STRV-03).
* ``tempo strava streams``  -- lazy fetch of activity streams (STRV-04).
* ``tempo strava sync`` / ``tempo sync`` -- daily incremental sync (STRV-05).

Connectors write only to the raw store (STRV-06). ``transform``/``analyze``/
``journal`` remain stubs for later phases.

Running bare ``tempo`` (the ``init`` command, also the default) creates the
runtime data dir, opens/creates the SQLite DB in WAL mode, and applies
migrations so the foundation tables exist.
"""

from __future__ import annotations

import typer

from tempo import __version__, db
from tempo.config import get_settings
from tempo.connectors.base import RawWriter
from tempo.connectors.factory import build_strava_connector, strava_token_store
from tempo.connectors.strava import SOURCE as STRAVA_SOURCE

app = typer.Typer(
    name="tempo",
    help="Personal, local-first training & health data pipeline.",
    no_args_is_help=False,
    add_completion=False,
)


def _init() -> None:
    """Create data dirs and bring the SQLite DB up to the latest schema."""
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        mode = db.journal_mode(conn)
        tables = sorted(db.table_names(conn))
    finally:
        conn.close()

    typer.echo(f"Tempo {__version__}")
    typer.echo(f"Data dir:   {settings.data_dir}")
    typer.echo(f"Database:    {settings.db_path}")
    typer.echo(f"Journal mode: {mode}")
    typer.echo(f"Schema version: {db.SCHEMA_VERSION}")
    typer.echo(f"Tables: {', '.join(tables)}")
    typer.echo("Foundation initialised.")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run ``tempo`` with no subcommand to initialise the database."""
    if ctx.invoked_subcommand is None:
        _init()


@app.command()
def init() -> None:
    """Initialise the runtime data directory and SQLite database (WAL mode)."""
    _init()


@app.command()
def version() -> None:
    """Print the Tempo version."""
    typer.echo(__version__)


# ---------------------------------------------------------------------------
# Strava ingestion commands
# ---------------------------------------------------------------------------

strava_app = typer.Typer(help="Strava ingestion: auth, backfill, streams, sync.")
app.add_typer(strava_app, name="strava")


def _connected_db():
    """Open the DB (initialising the schema if needed) for a connector run."""
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    return settings, conn


@strava_app.command("auth")
def strava_auth(
    code: str | None = typer.Option(
        None,
        "--code",
        help="The OAuth 'code' from the redirect URL (completes the handshake).",
    ),
) -> None:
    """One-time Strava OAuth handshake (STRV-01).

    Run with no arguments to print the authorization URL: open it, approve, then
    copy the ``code`` query parameter from the (localhost) redirect URL and run
    again with ``--code <CODE>``. Tokens are then stored locally and atomically;
    the rotating refresh token means you never have to do this again.
    """
    settings = get_settings()
    settings.ensure_dirs()
    try:
        connector = build_strava_connector(settings)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if code is None:
        url = connector.authorization_url(settings.strava_redirect_uri)
        typer.echo("Open this URL in your browser, approve access, then copy the")
        typer.echo("'code' parameter from the redirected URL:\n")
        typer.echo(url)
        typer.echo("\nThen run:  tempo strava auth --code <CODE>")
        return

    tokens = connector.exchange_code(code)
    store = strava_token_store(settings)
    typer.secho("Strava authorised. Tokens stored at:", fg=typer.colors.GREEN)
    typer.echo(f"  {store.path}")
    typer.echo(f"Access token expires at (epoch): {tokens.expires_at}")


@strava_app.command("backfill")
def strava_backfill(
    page_budget: int | None = typer.Option(
        None,
        "--page-budget",
        help="Max activity-list pages to fetch this run (spread a big history across days).",
    ),
) -> None:
    """Resumable all-time backfill of Strava activity summaries (STRV-03).

    Safe to interrupt: a rate-limit or crash mid-run leaves a ``backfill_cursor``
    and re-running resumes without re-fetching. Streams are NOT pulled here -- use
    ``tempo strava streams`` to fetch those lazily.
    """
    settings, conn = _connected_db()
    try:
        connector = build_strava_connector(settings, backfill_page_budget=page_budget)
        raw = RawWriter(conn, STRAVA_SOURCE)
        connector.backfill(raw)
        count = conn.execute(
            "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
            (STRAVA_SOURCE,),
        ).fetchone()[0]
        typer.secho(f"Backfill run complete. {count} activity summaries stored.", fg="green")
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@strava_app.command("streams")
def strava_streams(
    activity_id: int | None = typer.Option(
        None, "--activity-id", help="Fetch streams for a single activity id."
    ),
    limit: int | None = typer.Option(
        None, "--limit", help="When fetching for all stored activities, cap how many this run."
    ),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if already stored."),
) -> None:
    """Lazily fetch activity streams (HR, pace, GPS, power, cadence, elevation) (STRV-04).

    With ``--activity-id`` fetches one activity. Otherwise walks stored activity
    summaries that don't yet have streams, fetching up to ``--limit`` of them so
    the rate limit is respected. Already-stored streams are skipped.
    """
    settings, conn = _connected_db()
    try:
        connector = build_strava_connector(settings)
        raw = RawWriter(conn, STRAVA_SOURCE)
        if activity_id is not None:
            fetched = connector.fetch_streams(raw, activity_id, force=force)
            msg = "fetched" if fetched else "already present (skipped)"
            typer.echo(f"Streams for activity {activity_id}: {msg}.")
            return
        ids = connector.stored_activity_ids(conn)
        done = 0
        for aid in ids:
            if limit is not None and done >= limit:
                break
            if connector.fetch_streams(raw, aid, force=force):
                done += 1
        typer.secho(f"Fetched streams for {done} activities.", fg="green")
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@strava_app.command("sync")
def strava_sync() -> None:
    """Incremental Strava sync: only activities newer than the watermark (STRV-05)."""
    _run_strava_sync()


def _run_strava_sync() -> None:
    settings, conn = _connected_db()
    try:
        connector = build_strava_connector(settings)
        raw = RawWriter(conn, STRAVA_SOURCE)
        connector.sync(raw, since=None)
        count = conn.execute(
            "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
            (STRAVA_SOURCE,),
        ).fetchone()[0]
        typer.secho(f"Strava sync complete. {count} activity summaries in raw store.", fg="green")
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command()
def sync() -> None:
    """Pull new data from connected sources into the raw store.

    Phase 2: runs the Strava incremental sync. Garmin is added in Phase 6 behind
    the same connector interface; its failures will be isolated so they cannot
    block Strava.
    """
    _run_strava_sync()


@app.command()
def transform() -> None:
    """Derive structured tables from stored raw responses (no network).

    Reads raw Strava payloads from ``raw_response`` and upserts structured
    ``activity`` / ``activity_stream`` rows, zero-filling the ``date_spine`` so
    every calendar day (rest days included) has a row (STORE-01/03). Incremental
    and idempotent; the spine is extended forward to today.
    """
    from datetime import UTC, datetime

    from tempo.transforms.runner import run_transform

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        result = run_transform(conn, fill_to=datetime.now(UTC).date())
    finally:
        conn.close()
    typer.secho(
        f"Transform complete: {result.activities} activities, "
        f"{result.streams} streams, {result.spine_days} spine days.",
        fg="green",
    )


@app.command()
def rederive() -> None:
    """Rebuild ALL structured tables from raw data with no network calls (STORE-02).

    Clears and fully rebuilds the structured layer from ``raw_response`` so the
    result depends only on stored raw data -- safe after a schema or transform
    change, and used to re-apply a fixed date-bucketing rule without re-fetching.
    """
    from datetime import UTC, datetime

    from tempo.transforms.runner import run_rederive

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        result = run_rederive(conn, fill_to=datetime.now(UTC).date())
    finally:
        conn.close()
    typer.secho(
        f"Rederive complete (no network): {result.activities} activities, "
        f"{result.streams} streams, {result.spine_days} spine days.",
        fg="green",
    )


analyze_app = typer.Typer(
    help="Run analyses and write dated markdown reports (load-trend, race-readiness).",
    no_args_is_help=False,
)
app.add_typer(analyze_app, name="analyze")


def _analyze_setup():
    """Open the DB and build the load config from settings for an analysis run."""
    from tempo.analysis import load as load_mod
    from tempo.analysis.runner import _load_config_from_settings

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    cfg: load_mod.LoadConfig = _load_config_from_settings(settings)
    return settings, conn, cfg


@analyze_app.callback(invoke_without_command=True)
def analyze_main(ctx: typer.Context) -> None:
    """Run BOTH analyses (load-trend + race-readiness) when no subcommand is given.

    Reads already-stored, already-transformed data (no network) and writes dated
    markdown reports into the gitignored reports dir, each with a per-source
    data-freshness header (ANL-01/02/05; DELIV-01).
    """
    if ctx.invoked_subcommand is not None:
        return

    from datetime import UTC, datetime

    from tempo.analysis import runner

    settings, conn, cfg = _analyze_setup()
    today = datetime.now(UTC).date()
    try:
        load_path = runner.generate_load_trend(
            conn, cfg=cfg, reports_dir=settings.reports_dir, generated_on=today
        )
        race_path = runner.generate_race_readiness(
            conn,
            cfg=cfg,
            races_path=settings.races_path,
            plan_path=settings.plan_path,
            reports_dir=settings.reports_dir,
            generated_on=today,
        )
    finally:
        conn.close()
    typer.secho("Analyses complete. Reports written:", fg="green")
    typer.echo(f"  {load_path}")
    typer.echo(f"  {race_path}")


@analyze_app.command("load-trend")
def analyze_load_trend() -> None:
    """Write the training-load & trend report (CTL/ATL/TSB, ACWR, ramp) (ANL-01)."""
    from datetime import UTC, datetime

    from tempo.analysis import runner

    settings, conn, cfg = _analyze_setup()
    try:
        path = runner.generate_load_trend(
            conn,
            cfg=cfg,
            reports_dir=settings.reports_dir,
            generated_on=datetime.now(UTC).date(),
        )
    finally:
        conn.close()
    typer.secho(f"Load-trend report written: {path}", fg="green")


@analyze_app.command("race-readiness")
def analyze_race_readiness() -> None:
    """Write the race-readiness report (Riegel/VDOT + CTL/TSB form check) (ANL-02)."""
    from datetime import UTC, datetime

    from tempo.analysis import runner

    settings, conn, cfg = _analyze_setup()
    try:
        path = runner.generate_race_readiness(
            conn,
            cfg=cfg,
            races_path=settings.races_path,
            plan_path=settings.plan_path,
            reports_dir=settings.reports_dir,
            generated_on=datetime.now(UTC).date(),
        )
    finally:
        conn.close()
    typer.secho(f"Race-readiness report written: {path}", fg="green")


journal_app = typer.Typer(
    help="Capture and manage subjective journal entries (RPE, feel, notes).",
    no_args_is_help=False,
)
app.add_typer(journal_app, name="journal")


@journal_app.callback(invoke_without_command=True)
def journal_main(ctx: typer.Context) -> None:
    """Journal command group; defaults to a short usage hint."""
    if ctx.invoked_subcommand is None:
        typer.echo("tempo journal: capture subjective entries.")
        typer.echo(
            "  tempo journal add  --rpe 7 --feel strong --notes '...' "
            "[--day --sport --activity-id --duration-min]"
        )
        typer.echo("  tempo journal list [--limit N]")


@journal_app.command("add")
def journal_add(
    rpe: int = typer.Option(..., "--rpe", help="Session RPE, an integer 1-10."),
    feel: str | None = typer.Option(
        None, "--feel", help="How it felt (e.g. 'strong', 'flat', 'sore')."
    ),
    notes: str | None = typer.Option(None, "--notes", help="Free-text reflection."),
    day: str | None = typer.Option(
        None, "--day", help="Local date YYYY-MM-DD the entry is for (default: today)."
    ),
    sport: str | None = typer.Option(
        None, "--sport", help="Sport to resolve the activity by (e.g. 'Run', 'TrailRun')."
    ),
    activity_id: int | None = typer.Option(
        None, "--activity-id", help="Explicitly link to this activity id (disambiguates)."
    ),
    duration_min: float | None = typer.Option(
        None,
        "--duration-min",
        help="Minutes for sRPE when no activity is linked (or to override its duration).",
    ),
) -> None:
    """Record a validated post-workout / rest-day journal entry (JRNL-01/02/03).

    Validates RPE (1-10), resolves the activity by date + sport (or an explicit
    ``--activity-id``), and computes an sRPE (RPE x duration) load track. This is
    the boundary Claude uses to capture entries -- structured rows are written
    only here, never via free-form SQL.
    """
    from datetime import UTC, datetime

    from tempo.journal import JournalError, add_entry

    settings = get_settings()
    settings.ensure_dirs()
    resolved_day = day or datetime.now(UTC).date().isoformat()
    conn = db.init_db(settings.db_path)
    try:
        entry = add_entry(
            conn,
            day=resolved_day,
            rpe=rpe,
            feel=feel,
            notes=notes,
            sport=sport,
            activity_id=activity_id,
            duration_min=duration_min,
        )
    except JournalError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    link = f"linked to activity {entry.activity_id}" if entry.activity_id else "no activity linked"
    typer.secho(f"Journal entry #{entry.id} recorded for {entry.day}.", fg="green")
    typer.echo(f"  RPE {entry.rpe}, feel={entry.feel or '-'}, {link}.")
    if entry.srpe is not None:
        typer.echo(f"  sRPE {entry.srpe:.0f} (RPE {entry.rpe} x {entry.duration_min:.0f} min).")
    else:
        typer.echo("  sRPE not computed (no duration available).")


@journal_app.command("list")
def journal_list(
    limit: int | None = typer.Option(None, "--limit", help="Max entries to show."),
) -> None:
    """List recorded journal entries, most recent first."""
    from tempo.journal import list_entries

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.init_db(settings.db_path)
    try:
        entries = list_entries(conn, limit=limit)
    finally:
        conn.close()

    if not entries:
        typer.echo("No journal entries yet.")
        return
    for e in entries:
        link = f"act {e.activity_id}" if e.activity_id else "no-act"
        srpe = f"sRPE {e.srpe:.0f}" if e.srpe is not None else "sRPE -"
        typer.echo(
            f"#{e.id} {e.day} RPE {e.rpe} feel={e.feel or '-'} "
            f"{link} {srpe} {('notes: ' + e.notes) if e.notes else ''}".rstrip()
        )


if __name__ == "__main__":
    app()
