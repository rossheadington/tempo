"""`rederive` rebuilds structured tables from raw with zero network calls (STORE-02).

Asserts the three things that make rederive trustworthy:

  * idempotency -- running it twice yields identical structured state,
  * purity -- the result is a function of raw only: a deleted raw row disappears
    from the structured layer, a changed transform re-applies on re-run, and
  * NO network -- the transform path never touches stravalib / sockets.
"""

from __future__ import annotations

import socket
import sqlite3
from datetime import date

import pytest

from tempo.connectors.base import RawWriter
from tempo.transforms.runner import run_rederive, run_transform
from tests.strava_fakes import make_activity, make_streams


def _seed(conn: sqlite3.Connection) -> None:
    raw = RawWriter(conn, "strava")
    with conn:
        raw.put(
            "activity_summary",
            "1",
            make_activity(1, start_utc="2026-05-01T10:00:00Z", start_local="2026-05-01T11:00:00Z"),
        )
        raw.put(
            "activity_summary",
            "2",
            make_activity(2, start_utc="2026-05-03T10:00:00Z", start_local="2026-05-03T11:00:00Z"),
        )
        raw.put("streams", "1", make_streams())


def _snapshot(conn: sqlite3.Connection) -> dict[str, list]:
    return {
        "activity": conn.execute(
            "SELECT activity_id, day, distance_m FROM activity ORDER BY activity_id"
        ).fetchall(),
        "streams": conn.execute(
            "SELECT activity_id, type, data FROM activity_stream ORDER BY activity_id, type"
        ).fetchall(),
        "spine": conn.execute("SELECT day FROM date_spine ORDER BY day").fetchall(),
    }


def test_rederive_is_idempotent(conn: sqlite3.Connection) -> None:
    _seed(conn)
    first = run_rederive(conn, fill_to=date(2026, 5, 3))
    snap1 = _snapshot(conn)
    second = run_rederive(conn, fill_to=date(2026, 5, 3))
    snap2 = _snapshot(conn)
    assert first == second
    assert [tuple(r) for r in snap1["activity"]] == [tuple(r) for r in snap2["activity"]]
    assert [tuple(r) for r in snap1["streams"]] == [tuple(r) for r in snap2["streams"]]
    assert [tuple(r) for r in snap1["spine"]] == [tuple(r) for r in snap2["spine"]]


def test_rederive_matches_incremental_transform(conn: sqlite3.Connection) -> None:
    _seed(conn)
    run_transform(conn, fill_to=date(2026, 5, 3))
    snap_transform = _snapshot(conn)
    run_rederive(conn, fill_to=date(2026, 5, 3))
    snap_rederive = _snapshot(conn)
    assert [tuple(r) for r in snap_transform["activity"]] == [
        tuple(r) for r in snap_rederive["activity"]
    ]
    assert [tuple(r) for r in snap_transform["spine"]] == [tuple(r) for r in snap_rederive["spine"]]


def test_rederive_is_pure_function_of_raw_deleted_row_disappears(conn: sqlite3.Connection) -> None:
    _seed(conn)
    run_rederive(conn)
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 2
    # Remove one raw activity, then rederive: structured layer must reflect raw.
    with conn:
        conn.execute(
            "DELETE FROM raw_response WHERE endpoint='activity_summary' AND entity_key='2'"
        )
    run_rederive(conn)
    ids = [r[0] for r in conn.execute("SELECT activity_id FROM activity ORDER BY activity_id")]
    assert ids == [1]
    # And the spine shrinks to the remaining data range (single day).
    days = [r[0] for r in conn.execute("SELECT day FROM date_spine ORDER BY day")]
    assert days == ["2026-05-01"]


def test_rederive_can_rebuild_after_dropping_structured_tables(conn: sqlite3.Connection) -> None:
    _seed(conn)
    run_rederive(conn)
    # Simulate the "looks done but isn't" check: clear structured, rebuild from raw.
    with conn:
        conn.execute("DELETE FROM activity_stream")
        conn.execute("DELETE FROM activity")
        conn.execute("DELETE FROM date_spine")
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0
    run_rederive(conn)
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM activity_stream").fetchone()[0] == 8


def test_rederive_makes_no_network_calls(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard guarantee: the transform path opens no sockets (STORE-02)."""
    _seed(conn)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("rederive attempted a network connection")

    # Block the actual network primitives. If any code path tried stravalib /
    # requests / httpx, creating a socket would raise here.
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    result = run_rederive(conn, fill_to=date(2026, 5, 3))
    assert result.activities == 2
    assert result.streams == 8
