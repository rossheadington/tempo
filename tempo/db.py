"""SQLite connection management and the migration runner.

No ORM, no Alembic: a plain :mod:`sqlite3` connection with WAL mode and foreign
keys enabled, plus a tiny migration helper keyed on the integer
``PRAGMA user_version``. Ordered ``NNNN_*.sql`` files in :mod:`tempo.migrations`
are applied in filename order; ``user_version`` records how many have run.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

# The expected schema version after all bundled migrations have been applied.
# Bump this (and add a migration file) whenever the schema changes.
SCHEMA_VERSION = 3

# Tables the foundation schema guarantees exist. Used by tests and `tempo` for
# a quick post-init sanity check.
FOUNDATION_TABLES = ("raw_response", "date_spine", "sync_state")

# Structured (silver) tables added in migration 0002 (Phase 3). Re-derivation
# rebuilds these purely from the raw layer.
STRUCTURED_TABLES = ("activity", "activity_stream")

# Journal (subjective) table added in migration 0003 (Phase 5). Written only via
# the validated journal service, never by free-form SQL.
JOURNAL_TABLES = ("journal",)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with Tempo's standard pragmas.

    Enables WAL journal mode (concurrent reader + single writer, durable across
    a crash) and foreign-key enforcement. The parent directory must already
    exist. Rows are returned as :class:`sqlite3.Row` for name-based access.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL persists on the database file itself, but we set it every connect so a
    # freshly-created DB is in WAL mode from its first write.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version;").fetchone()[0])


def _migration_files() -> list[tuple[int, str, str]]:
    """Return ``(version, name, sql)`` tuples for bundled migrations, ordered.

    A migration named ``0001_init.sql`` targets ``user_version`` 1.
    """
    migrations: list[tuple[int, str, str]] = []
    files = resources.files("tempo.migrations")
    for entry in files.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        version = int(name.split("_", 1)[0])
        sql = entry.read_text(encoding="utf-8")
        migrations.append((version, name, sql))
    migrations.sort(key=lambda m: m[0])
    return migrations


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations and return the resulting schema version.

    Each migration runs in its own transaction; on success ``user_version`` is
    advanced to that migration's number. Already-applied migrations are skipped,
    so this is safe to call on every startup.
    """
    current = _user_version(conn)
    for version, _name, sql in _migration_files():
        if version <= current:
            continue
        with conn:  # transaction: commit on success, rollback on error
            conn.executescript(sql)
            # user_version cannot be parameterised; version is a trusted int.
            conn.execute(f"PRAGMA user_version={version};")
        current = version
    return current


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the DB and bring it up to the latest schema.

    Returns an open connection in WAL mode with the foundation tables present.
    The caller owns closing the connection.
    """
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = connect(db_path)
    migrate(conn)
    return conn


def journal_mode(conn: sqlite3.Connection) -> str:
    """Return the active journal mode (e.g. ``'wal'``)."""
    return str(conn.execute("PRAGMA journal_mode;").fetchone()[0])


def table_names(conn: sqlite3.Connection) -> set[str]:
    """Return the set of user table names in the database."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    return {r[0] for r in rows}
