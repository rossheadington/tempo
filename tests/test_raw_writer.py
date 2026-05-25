"""Tests for RawWriter idempotent upsert and the Connector protocol (STRV-06)."""

from __future__ import annotations

import json
import sqlite3

from tempo.connectors.base import Connector, RawWriter


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM raw_response").fetchone()[0]


def test_put_inserts_verbatim_json(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    payload = {"id": 1, "name": "Run", "nested": {"hr": [120, 130]}}
    result = raw.put("activity_summary", "1", payload)
    conn.commit()
    assert result.inserted is True
    stored = conn.execute(
        "SELECT payload FROM raw_response "
        "WHERE source='strava' AND endpoint='activity_summary' AND entity_key='1'"
    ).fetchone()[0]
    assert json.loads(stored) == payload


def test_put_is_idempotent_on_repeat(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    raw.put("activity_summary", "1", {"id": 1, "v": 1})
    first = raw.put("activity_summary", "1", {"id": 1, "v": 2})  # same key, new body
    conn.commit()
    assert first.inserted is False  # second write is a refresh, not an insert
    assert _count(conn) == 1  # no duplicate row
    stored = json.loads(
        conn.execute("SELECT payload FROM raw_response WHERE entity_key='1'").fetchone()[0]
    )
    assert stored["v"] == 2  # payload refreshed in place


def test_put_distinguishes_endpoints_and_keys(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    raw.put("activity_summary", "1", {"id": 1})
    raw.put("streams", "1", {"hr": []})
    raw.put("activity_summary", "2", {"id": 2})
    conn.commit()
    assert _count(conn) == 3


def test_has_reports_existence(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    assert raw.has("streams", "99") is False
    raw.put("streams", "99", {"hr": []})
    conn.commit()
    assert raw.has("streams", "99") is True


def test_raw_writer_only_touches_raw_response(conn: sqlite3.Connection) -> None:
    """A connector writing via RawWriter must not mutate any structured table.

    Phase 2 has no structured tables yet, but the date_spine / sync_state tables
    exist; assert the writer leaves everything except raw_response untouched.
    """
    before_spine = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
    raw = RawWriter(conn, "strava")
    raw.put("activity_summary", "1", {"id": 1})
    conn.commit()
    after_spine = conn.execute("SELECT COUNT(*) FROM date_spine").fetchone()[0]
    assert before_spine == after_spine == 0


def test_conn_property_exposes_connection(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    assert raw.conn is conn
    assert raw.source == "strava"


def test_connector_protocol_is_runtime_checkable() -> None:
    class Dummy:
        source = "x"

        def backfill(self, raw: RawWriter) -> None: ...

        def sync(self, raw: RawWriter, since) -> None: ...

    assert isinstance(Dummy(), Connector)
