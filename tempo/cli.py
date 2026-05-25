"""The ``tempo`` command-line interface.

Phase 1 wires the command surface and the DB-initialisation entrypoint. The
data-producing subcommands (``sync``, ``transform``/``rederive``, ``analyze``,
``journal``) are deliberate stubs that print "not yet implemented" -- later
phases fill them in behind these stable command names.

Running bare ``tempo`` (the ``init`` command, also the default) creates the
runtime data dir, opens/creates the SQLite DB in WAL mode, and applies
migrations so the foundation tables exist.
"""

from __future__ import annotations

import typer

from tempo import __version__, db
from tempo.config import get_settings

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


@app.command()
def sync() -> None:
    """Pull new data from connected sources into the raw store. [stub]"""
    typer.echo(f"tempo sync: {_NOT_IMPLEMENTED}")


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
