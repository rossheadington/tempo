"""The connector interface and the raw-store writer.

Two pieces, both source-agnostic:

* :class:`RawWriter` -- the *only* way data enters the database from the
  network. It performs an idempotent upsert into ``raw_response`` keyed on
  ``(source, endpoint, entity_key)``. Re-fetching an already-stored entity is
  therefore harmless, which is what makes the Strava backfill resumable and the
  daily sync safe to overlap (see ``.planning/research/ARCHITECTURE.md`` Pattern
  2 & 4).
* :class:`Connector` -- the thin protocol every source implements. The Strava
  connector implements it now; the Garmin connector will implement the *same*
  protocol in Phase 6, so the sync pipeline talks to the protocol and never to a
  specific API.

A connector knows the API; it knows **nothing** about structured tables. It
writes verbatim payloads via :class:`RawWriter` and stops there.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RawWriteResult:
    """Outcome of a single :meth:`RawWriter.put`.

    ``inserted`` is ``True`` if the row was newly created, ``False`` if an
    existing ``(source, endpoint, entity_key)`` row was refreshed. Backfill uses
    this to count genuinely-new fetches vs idempotent re-stores.
    """

    inserted: bool


class RawWriter:
    """Idempotent writer into the ``raw_response`` (bronze) table.

    Holds an open connection and a fixed ``source``. ``put`` upserts a single
    verbatim payload. The connection's transaction is managed by the caller
    (the connector commits in sensible batches), so a writer never commits on
    its own -- this keeps a backfill batch atomic with its cursor advance.
    """

    def __init__(self, conn: sqlite3.Connection, source: str) -> None:
        self._conn = conn
        self._source = source

    @property
    def source(self) -> str:
        """The source these writes are attributed to (e.g. ``'strava'``)."""
        return self._source

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying connection.

        Exposed so a connector can own the *transaction* boundary -- committing a
        batch of raw rows together with its backfill cursor in one atomic unit,
        which is what makes the backfill resumable without ever advancing the
        cursor past un-stored data. The connection's transaction is otherwise
        unmanaged by the writer.
        """
        return self._conn

    def put(self, endpoint: str, key: str, payload: Any) -> RawWriteResult:
        """Upsert one verbatim payload into ``raw_response``.

        ``payload`` is serialised to canonical JSON (sorted keys) and stored in
        the ``payload`` TEXT column. On a repeat of the same
        ``(source, endpoint, entity_key)`` the stored payload and ``fetched_at``
        are refreshed rather than duplicated, so the unique index is never
        violated and re-runs are safe.
        """
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        # Check existence first so we can report insert-vs-refresh reliably;
        # SQLite's rowcount is 1 for both the INSERT and the ON CONFLICT UPDATE
        # path, so it can't distinguish them on its own.
        existed = self._row_existed(endpoint, key)
        self._conn.execute(
            """
            INSERT INTO raw_response (source, endpoint, entity_key, payload, fetched_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT (source, endpoint, entity_key)
            DO UPDATE SET payload = excluded.payload,
                          fetched_at = excluded.fetched_at
            """,
            (self._source, endpoint, key, body),
        )
        return RawWriteResult(inserted=not existed)

    def has(self, endpoint: str, key: str) -> bool:
        """Return ``True`` if a raw row already exists for this entity.

        Lets a connector skip work it has already done (e.g. streams already
        fetched for an activity) without a network call.
        """
        return self._row_existed(endpoint, key)

    def _row_existed(self, endpoint: str, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM raw_response WHERE source=? AND endpoint=? AND entity_key=? LIMIT 1",
            (self._source, endpoint, key),
        ).fetchone()
        return row is not None


@runtime_checkable
class Connector(Protocol):
    """The interface every source connector implements.

    Deliberately thin: ``backfill`` for the one-time all-time history pull and
    ``sync`` for the daily incremental pull. Both write only through a
    :class:`RawWriter`. The shape of returned data is intentionally *not* part
    of the contract -- Strava is per-activity and Garmin is per-day, and those
    differences are normalised later in transforms, not here.
    """

    source: str

    def backfill(self, raw: RawWriter) -> None:
        """Pull all-time history into the raw store, resumably and idempotently."""
        ...

    def sync(self, raw: RawWriter, since: date | None) -> None:
        """Pull only data newer than ``since`` (the last watermark) into raw."""
        ...
