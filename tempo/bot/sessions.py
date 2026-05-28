"""Per-chat Claude Code session-id store backing the Phase 11 agent loop (VOICE-08).

This module is the validated boundary for ``bot_session`` writes -- mirrors the
"thin Python wrapper around parameterised SQL" pattern from
:mod:`tempo.journal.service`, but simpler: there is no field validation beyond
types and no derived columns. Pure stdlib :mod:`sqlite3`, no async, no Telegram
or Claude SDK imports.

Session lifetime
----------------
Sessions persist **indefinitely** -- there is no time-based expiry.
:func:`get_or_create_session` returns whatever session id is stored, so the
conversation stays alive across days, weeks, and restarts. The only way to
start a fresh session is for the user to send ``/clear`` in Telegram, which
calls :func:`reset_session`.

Drift between SQLite and Claude Code on disk
--------------------------------------------
Claude Code's source of truth for what a session actually contains is the
on-disk session log at ``~/.claude/projects/<project-hash>/<session-id>.jsonl``.
If that file is deleted while we still hold the session id in SQLite,
``ClaudeAgentOptions(resume=<id>)`` silently starts a fresh session. We accept
that drift (per 11-CONTEXT.md ``<specifics>``); no reconciliation happens here.

Public surface
--------------
* :func:`get_or_create_session` -- read-only lookup; returns ``str | None``.
* :func:`save_session` -- UPSERT keeping ``started_at`` stable when ``session_id``
  is unchanged; rotates ``started_at`` when ``session_id`` flips.
* :func:`reset_session` -- DELETE the row; idempotent on absent ``chat_id``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def _now(now: datetime | None) -> datetime:
    """Resolve the caller's ``now`` arg, defaulting to ``datetime.now(UTC)``."""
    if now is None:
        return datetime.now(UTC)
    return now


def _load_session(conn: sqlite3.Connection, chat_id: int) -> tuple[str, datetime] | None:
    """Return ``(session_id, last_message_at)`` for ``chat_id`` or ``None``.

    Internal helper: the on-disk timestamp is parsed back to a tz-aware
    :class:`datetime` so callers compare apples-to-apples against ``now``.
    """
    row = conn.execute(
        "SELECT session_id, last_message_at FROM bot_session WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["session_id"]), datetime.fromisoformat(str(row["last_message_at"]))


def get_or_create_session(
    conn: sqlite3.Connection,
    chat_id: int,
) -> str | None:
    """Return the stored session id if any, else ``None``.

    Read-only: never inserts, updates, or deletes. The session persists
    indefinitely until the user explicitly clears it via the ``/clear``
    command (see :func:`reset_session`). There is no time-based expiry --
    the conversation stays alive across days / weeks / restarts.
    """
    loaded = _load_session(conn, chat_id)
    if loaded is None:
        return None
    session_id, _last_at = loaded
    return session_id


def save_session(
    conn: sqlite3.Connection,
    chat_id: int,
    session_id: str,
    now: datetime | None = None,
) -> None:
    """UPSERT the ``(chat_id, session_id, last_message_at, started_at)`` row.

    Semantics:

    * On INSERT: ``started_at`` is set to ``now``.
    * On UPDATE with the SAME ``session_id``: ``started_at`` is preserved (the
      session continues); only ``last_message_at`` advances.
    * On UPDATE with a DIFFERENT ``session_id``: ``started_at`` is reset to
      ``now`` -- this is a brand-new session for the same chat.

    Implemented as a single ``INSERT ... ON CONFLICT(chat_id) DO UPDATE`` with a
    ``CASE`` expression on the conflicting row's ``session_id`` to keep the
    started_at semantics atomic. ``now`` defaults to ``datetime.now(UTC)`` and
    is persisted via :meth:`~datetime.datetime.isoformat`.
    """
    moment = _now(now).isoformat()
    with conn:  # transaction: commit on success, rollback on error
        conn.execute(
            """
            INSERT INTO bot_session (chat_id, session_id, last_message_at, started_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                session_id = excluded.session_id,
                last_message_at = excluded.last_message_at,
                started_at = CASE
                    WHEN bot_session.session_id = excluded.session_id
                        THEN bot_session.started_at
                    ELSE excluded.started_at
                END
            """,
            (chat_id, session_id, moment, moment),
        )


def reset_session(conn: sqlite3.Connection, chat_id: int) -> None:
    """DELETE the row for ``chat_id``; idempotent when the row is absent.

    Used by the ``/clear`` slash command (Plan 11-03) to force a fresh session on
    the next turn.
    """
    with conn:
        conn.execute("DELETE FROM bot_session WHERE chat_id = ?", (chat_id,))
