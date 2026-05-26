"""Tests for tempo.db (WAL mode, migrations, foundation tables)."""

from __future__ import annotations

from pathlib import Path

from tempo import db


def test_init_db_creates_foundation_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "tempo.db"
    conn = db.init_db(db_path)
    try:
        tables = db.table_names(conn)
        for expected in db.FOUNDATION_TABLES:
            assert expected in tables, f"missing table {expected}"
    finally:
        conn.close()
    assert db_path.exists()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        assert db.journal_mode(conn) == "wal"
    finally:
        conn.close()


def test_foreign_keys_enabled(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
    finally:
        conn.close()


def test_schema_version_set(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert version == db.SCHEMA_VERSION
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "tempo.db"
    conn = db.init_db(db_path)
    try:
        # Running migrate again should be a no-op and leave the version unchanged.
        result = db.migrate(conn)
        assert result == db.SCHEMA_VERSION
        assert db.table_names(conn) >= set(db.FOUNDATION_TABLES)
    finally:
        conn.close()


def test_init_db_creates_parent_dir(tmp_path: Path) -> None:
    # Parent dir does not exist yet; init_db must create it.
    db_path = tmp_path / "nested" / "deeper" / "tempo.db"
    conn = db.init_db(db_path)
    try:
        assert db_path.exists()
    finally:
        conn.close()


def test_raw_response_unique_constraint(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        conn.execute(
            "INSERT INTO raw_response (source, endpoint, entity_key, payload) "
            "VALUES ('strava', 'activity', '1', '{}');"
        )
        conn.commit()
        # Same (source, endpoint, entity_key) should violate the UNIQUE index.
        import sqlite3

        try:
            conn.execute(
                "INSERT INTO raw_response (source, endpoint, entity_key, payload) "
                "VALUES ('strava', 'activity', '1', '{}');"
            )
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "expected UNIQUE constraint violation"
    finally:
        conn.close()


def test_sync_state_and_date_spine_columns(tmp_path: Path) -> None:
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        sync_cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_state);")}
        assert {"source", "backfill_cursor", "backfill_complete"} <= sync_cols
        spine_cols = {r[1] for r in conn.execute("PRAGMA table_info(date_spine);")}
        assert {"day", "dow", "week", "month", "year"} <= spine_cols
    finally:
        conn.close()


def test_wellness_day_table_and_columns(tmp_path: Path) -> None:
    """Migration 0004 creates wellness_day keyed by `day` with the Phase-6 metrics."""
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        assert "wellness_day" in db.table_names(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(wellness_day);")}
        assert {
            "day",
            "resting_hr",
            "hrv_last_night",
            "hrv_status",
            "sleep_score",
            "sleep_seconds",
            "deep_s",
            "rem_s",
            "light_s",
            "awake_s",
            "body_battery_high",
            "body_battery_low",
            "stress_avg",
            "steps",
        } <= cols
        # `day` is the primary key (one row per calendar day).
        pk = [r[1] for r in conn.execute("PRAGMA table_info(wellness_day);") if r[5]]
        assert pk == ["day"]
    finally:
        conn.close()


def test_daily_summary_exposes_wellness_columns(tmp_path: Path) -> None:
    """The gold daily_summary view now surfaces wellness fields (STORE-04; GRMN-04)."""
    conn = db.init_db(tmp_path / "tempo.db")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_summary);")}
        assert {"hrv_last_night", "resting_hr", "sleep_score", "steps", "has_wellness"} <= cols
    finally:
        conn.close()
