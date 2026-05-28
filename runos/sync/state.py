"""Read/write the per-source ``sync_state`` row.

``sync_state`` holds, per source:

* ``last_sync_at``      -- ISO datetime of the last *successful* sync.
* ``last_entity_ts``    -- the incremental watermark: the latest entity
  timestamp seen, used as Strava's ``after`` on the next sync so only newer
  activities are pulled (STRV-05).
* ``backfill_cursor``   -- a JSON checkpoint for the resumable all-time backfill
  (STRV-03). Opaque to this module; the connector decides its shape.
* ``backfill_complete`` -- 1 once the all-time backfill has finished, so it is
  never needlessly re-run.

The cardinal rule (ARCHITECTURE.md, Anti-Pattern 3): **advance the watermark
only on success**. A partial failure must not skip data; a slight overlap on the
next run is harmless because raw upserts are idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SyncState:
    """A snapshot of one source's ``sync_state`` row."""

    source: str
    last_sync_at: str | None = None
    last_entity_ts: str | None = None
    backfill_cursor: dict[str, Any] | None = None
    backfill_complete: bool = False


def _ensure_row(conn: sqlite3.Connection, source: str) -> None:
    conn.execute(
        "INSERT INTO sync_state (source) VALUES (?) ON CONFLICT (source) DO NOTHING",
        (source,),
    )


def read(conn: sqlite3.Connection, source: str) -> SyncState:
    """Return the current :class:`SyncState` for ``source`` (defaults if absent)."""
    row = conn.execute(
        """
        SELECT source, last_sync_at, last_entity_ts, backfill_cursor, backfill_complete
        FROM sync_state WHERE source = ?
        """,
        (source,),
    ).fetchone()
    if row is None:
        return SyncState(source=source)
    cursor_raw = row["backfill_cursor"]
    cursor = json.loads(cursor_raw) if cursor_raw else None
    return SyncState(
        source=row["source"],
        last_sync_at=row["last_sync_at"],
        last_entity_ts=row["last_entity_ts"],
        backfill_cursor=cursor,
        backfill_complete=bool(row["backfill_complete"]),
    )


def save_backfill_cursor(
    conn: sqlite3.Connection,
    source: str,
    cursor: dict[str, Any] | None,
    *,
    complete: bool = False,
) -> None:
    """Persist the resumable backfill checkpoint for ``source``.

    Called inside the same transaction as the raw writes for a backfill batch,
    so the cursor and the rows it accounts for commit atomically -- a crash can
    never advance the cursor past data that wasn't actually stored.
    """
    _ensure_row(conn, source)
    conn.execute(
        "UPDATE sync_state SET backfill_cursor = ?, backfill_complete = ? WHERE source = ?",
        (json.dumps(cursor) if cursor is not None else None, 1 if complete else 0, source),
    )


def mark_synced(
    conn: sqlite3.Connection,
    source: str,
    *,
    last_entity_ts: str | None = None,
) -> None:
    """Record a successful sync: bump ``last_sync_at`` and the watermark.

    ``last_entity_ts`` advances the watermark only when provided and only
    forward (a smaller value is ignored), so an out-of-order or empty sync can't
    rewind the watermark and re-pull old data forever.
    """
    _ensure_row(conn, source)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    current = read(conn, source).last_entity_ts
    new_watermark = current
    if last_entity_ts is not None and (current is None or last_entity_ts > current):
        new_watermark = last_entity_ts
    conn.execute(
        "UPDATE sync_state SET last_sync_at = ?, last_entity_ts = ? WHERE source = ?",
        (now, new_watermark, source),
    )
