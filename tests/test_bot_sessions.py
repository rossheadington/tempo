"""Tests for the per-chat Claude Code session-id store (tempo.bot.sessions).

Pure-stdlib SQLite, no mocks. Tests use the migrated `conn` fixture from
``tests/conftest.py`` (real on-disk DB at user_version=5 after migrate). All
window-boundary assertions use fixed datetime anchors so the 4-hour resume
window is deterministic regardless of when the test runs (VOICE-08).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from tempo.bot import sessions


T0 = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)


def _row(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT chat_id, session_id, last_message_at, started_at "
        "FROM bot_session WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM bot_session;").fetchone()[0])


def test_session_window_hours_constant_is_four() -> None:
    """The locked default 4-hour resume window (per 11-CONTEXT.md)."""
    assert sessions.SESSION_WINDOW_HOURS == 4


def test_get_or_create_session_returns_none_on_empty_table(conn: sqlite3.Connection) -> None:
    """Fresh DB, never-saved chat_id -> None; no row is inserted as a side effect."""
    assert sessions.get_or_create_session(conn, 999, now=T0) is None
    assert _count(conn) == 0


def test_save_then_get_within_window_returns_same_session_id(conn: sqlite3.Connection) -> None:
    """Save at T0, get at T0+3h -> returns the saved session id (inside 4h window)."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    got = sessions.get_or_create_session(conn, 999, now=T0 + timedelta(hours=3))
    assert got == "sess-A"


def test_get_just_inside_window_returns_session_id(conn: sqlite3.Connection) -> None:
    """Save at T0, get at T0 + (4h - 1s) -> still within window, returns id."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    inside = T0 + timedelta(hours=4) - timedelta(seconds=1)
    assert sessions.get_or_create_session(conn, 999, now=inside) == "sess-A"


def test_get_after_window_returns_none(conn: sqlite3.Connection) -> None:
    """Save at T0, get at T0 + (4h + 1s) -> outside window, returns None; row stays."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    outside = T0 + timedelta(hours=4) + timedelta(seconds=1)
    assert sessions.get_or_create_session(conn, 999, now=outside) is None
    # get_or_create_session is read-only; the stale row must still be there.
    assert _count(conn) == 1


def test_save_session_preserves_started_at_on_same_id(conn: sqlite3.Connection) -> None:
    """Two saves with the same session_id: started_at stays at T0, last_message_at moves."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    later = T0 + timedelta(hours=1)
    sessions.save_session(conn, 999, "sess-A", now=later)
    row = _row(conn, 999)
    assert row is not None
    assert row["session_id"] == "sess-A"
    assert datetime.fromisoformat(row["started_at"]) == T0
    assert datetime.fromisoformat(row["last_message_at"]) == later


def test_save_session_resets_started_at_on_new_id(conn: sqlite3.Connection) -> None:
    """A different session_id for the same chat_id resets started_at to the new now."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    later = T0 + timedelta(hours=1)
    sessions.save_session(conn, 999, "sess-B", now=later)
    row = _row(conn, 999)
    assert row is not None
    assert row["session_id"] == "sess-B"
    assert datetime.fromisoformat(row["started_at"]) == later
    assert datetime.fromisoformat(row["last_message_at"]) == later


def test_reset_session_deletes_row_and_subsequent_get_returns_none(
    conn: sqlite3.Connection,
) -> None:
    """reset_session removes the row; the next get_or_create returns None; reset is idempotent."""
    sessions.save_session(conn, 999, "sess-A", now=T0)
    assert _count(conn) == 1
    sessions.reset_session(conn, 999)
    assert _count(conn) == 0
    assert sessions.get_or_create_session(conn, 999, now=T0) is None
    # Idempotent: calling reset again on an absent chat_id does not raise.
    sessions.reset_session(conn, 999)
    assert _count(conn) == 0


def test_two_chat_ids_do_not_interfere(conn: sqlite3.Connection) -> None:
    """Distinct chat_ids hold independent sessions (PK isolates rows)."""
    sessions.save_session(conn, 1, "sess-A", now=T0)
    sessions.save_session(conn, 2, "sess-B", now=T0)
    assert sessions.get_or_create_session(conn, 1, now=T0) == "sess-A"
    assert sessions.get_or_create_session(conn, 2, now=T0) == "sess-B"
    # Resetting chat 1 leaves chat 2 untouched.
    sessions.reset_session(conn, 1)
    assert sessions.get_or_create_session(conn, 1, now=T0) is None
    assert sessions.get_or_create_session(conn, 2, now=T0) == "sess-B"


def test_session_re_exports_from_tempo_bot_package() -> None:
    """The three public functions are importable from `tempo.bot` (matches existing pattern)."""
    from tempo.bot import (
        SESSION_WINDOW_HOURS,
        get_or_create_session,
        reset_session,
        save_session,
    )

    assert SESSION_WINDOW_HOURS == 4
    assert callable(get_or_create_session)
    assert callable(save_session)
    assert callable(reset_session)
