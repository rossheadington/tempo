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

_NOT_IMPLEMENTED = "not yet implemented"


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
    """Derive structured tables from stored raw responses. [stub]"""
    typer.echo(f"tempo transform: {_NOT_IMPLEMENTED}")


@app.command()
def rederive() -> None:
    """Rebuild all structured tables from raw data with no network calls. [stub]"""
    typer.echo(f"tempo rederive: {_NOT_IMPLEMENTED}")


@app.command()
def analyze() -> None:
    """Run analyses and write dated markdown reports. [stub]"""
    typer.echo(f"tempo analyze: {_NOT_IMPLEMENTED}")


journal_app = typer.Typer(help="Capture and manage post-workout journal entries.")
app.add_typer(journal_app, name="journal")


@journal_app.callback(invoke_without_command=True)
def journal_main(ctx: typer.Context) -> None:
    """Journal command group; defaults to a usage stub."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"tempo journal: {_NOT_IMPLEMENTED}")


@journal_app.command("add")
def journal_add() -> None:
    """Record a structured post-workout journal entry. [stub]"""
    typer.echo(f"tempo journal add: {_NOT_IMPLEMENTED}")


if __name__ == "__main__":
    app()
