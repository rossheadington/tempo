"""Tests for sync_state read/write: watermark + backfill cursor."""

from __future__ import annotations

import sqlite3

from tempo.sync import state


def test_read_defaults_when_absent(conn: sqlite3.Connection) -> None:
    st = state.read(conn, "strava")
    assert st.source == "strava"
    assert st.last_entity_ts is None
    assert st.backfill_cursor is None
    assert st.backfill_complete is False


def test_save_and_read_backfill_cursor(conn: sqlite3.Connection) -> None:
    state.save_backfill_cursor(conn, "strava", {"next_page": 3})
    conn.commit()
    st = state.read(conn, "strava")
    assert st.backfill_cursor == {"next_page": 3}
    assert st.backfill_complete is False


def test_mark_backfill_complete_clears_cursor(conn: sqlite3.Connection) -> None:
    state.save_backfill_cursor(conn, "strava", {"next_page": 5})
    state.save_backfill_cursor(conn, "strava", None, complete=True)
    conn.commit()
    st = state.read(conn, "strava")
    assert st.backfill_complete is True
    assert st.backfill_cursor is None


def test_mark_synced_advances_watermark_forward_only(conn: sqlite3.Connection) -> None:
    state.mark_synced(conn, "strava", last_entity_ts="2026-05-01T10:00:00Z")
    conn.commit()
    assert state.read(conn, "strava").last_entity_ts == "2026-05-01T10:00:00Z"

    # An older timestamp must NOT rewind the watermark.
    state.mark_synced(conn, "strava", last_entity_ts="2026-04-01T10:00:00Z")
    conn.commit()
    assert state.read(conn, "strava").last_entity_ts == "2026-05-01T10:00:00Z"

    # A newer one advances it.
    state.mark_synced(conn, "strava", last_entity_ts="2026-06-01T10:00:00Z")
    conn.commit()
    assert state.read(conn, "strava").last_entity_ts == "2026-06-01T10:00:00Z"


def test_mark_synced_sets_last_sync_at(conn: sqlite3.Connection) -> None:
    state.mark_synced(conn, "strava")
    conn.commit()
    st = state.read(conn, "strava")
    assert st.last_sync_at is not None


def test_mark_synced_none_watermark_preserves_existing(conn: sqlite3.Connection) -> None:
    state.mark_synced(conn, "strava", last_entity_ts="2026-05-01T10:00:00Z")
    state.mark_synced(conn, "strava", last_entity_ts=None)  # empty sync
    conn.commit()
    assert state.read(conn, "strava").last_entity_ts == "2026-05-01T10:00:00Z"
