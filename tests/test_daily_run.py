"""Tests for the daily-run orchestration (SCHED-01/02/03).

Covers:
* sync -> transform -> analyze runs end to end and writes the report suite;
* it is idempotent (running twice is safe + stable);
* Garmin stays isolated (a 429 during the daily sync doesn't break the run);
* catch-up via watermark (a missed day is recovered on the next run);
* noteworthy-only surfacing: a marker file is written ONLY when noteworthy,
  and removed when a later run is quiet.

The Strava + Garmin connectors are faked (no network, no credentials).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from tempo.config import Settings
from tempo.connectors.base import RawWriter
from tempo.connectors.garmin import SOURCE as GARMIN
from tempo.connectors.garmin import GarminConnector
from tempo.connectors.strava import SOURCE as STRAVA
from tempo.sync import daily, pipeline, state
from tests.garmin_fakes import FakeGarminClient, make_day
from tests.strava_fakes import make_run


def _settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        data_dir=str(tmp_path / "data"),
        threshold_pace_s_per_km=240.0,
        max_hr=190,
        resting_hr=48,
        threshold_hr=170,
    )


class _FakeStrava:
    """A fake Strava connector that emits N activities starting from a base day."""

    source = STRAVA

    def __init__(self, days: list[str]) -> None:
        self._days = days

    def sync(self, raw: RawWriter, since=None) -> None:
        last = None
        with raw.conn:
            for day in self._days:
                # A globally-unique activity id derived from the day (real Strava
                # ids are unique, so distinct days never collide in the raw store).
                aid = int(date.fromisoformat(day).strftime("%Y%m%d"))
                raw.put("activity_summary", str(aid), make_run(aid, day=day))
                last = f"{day}T06:00:00Z"
            if last:
                state.mark_synced(raw.conn, STRAVA, last_entity_ts=last)


def _patch_connectors(monkeypatch, strava, garmin) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pipeline, "build_strava_connector", lambda s: strava)
    monkeypatch.setattr(pipeline, "build_garmin_connector", lambda s: garmin)


def _recent_days(n: int = 6) -> list[str]:
    today = GarminConnector._today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n, -1, -1)]


# ---- End-to-end: full report suite written --------------------------------


def test_run_daily_writes_full_suite(conn: sqlite3.Connection, tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    days = _recent_days()
    strava = _FakeStrava(days)
    garmin = GarminConnector("tok", client=FakeGarminClient(days={d: make_day(d) for d in days}))
    _patch_connectors(monkeypatch, strava, garmin)

    gen_on = date.fromisoformat(days[-1])
    result = daily.run_daily(conn, settings, generated_on=gen_on)

    # All four reports written.
    paths = result.reports.paths()
    assert len(paths) == 4
    for p in paths:
        assert p.exists()
    names = {p.name for p in paths}
    assert any("recovery" in n for n in names)
    assert any("correlations" in n for n in names)

    # Per-source sync status present (Strava ok, Garmin ok).
    by_src = {r.source: r for r in result.sync_results}
    assert by_src[STRAVA].ok is True
    assert by_src[GARMIN].ok is True


# ---- Garmin isolation inside the daily run --------------------------------


def test_run_daily_isolated_garmin_429(conn: sqlite3.Connection, tmp_path, monkeypatch) -> None:
    """A Garmin 429 during the daily run is skipped; Strava + analysis still complete."""
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    days = _recent_days()
    strava = _FakeStrava(days)
    garmin = GarminConnector(
        "tok", client=FakeGarminClient(days={d: make_day(d) for d in days}, raise_429_on="sleep")
    )
    _patch_connectors(monkeypatch, strava, garmin)

    gen_on = date.fromisoformat(days[-1])
    result = daily.run_daily(conn, settings, generated_on=gen_on)  # must not raise

    by_src = {r.source: r for r in result.sync_results}
    assert by_src[STRAVA].ok is True
    assert by_src[GARMIN].ok is False
    assert "429" in by_src[GARMIN].detail
    # Reports were still written despite Garmin failing.
    assert len(result.reports.paths()) == 4


# ---- Idempotency -----------------------------------------------------------


def test_run_daily_idempotent(conn: sqlite3.Connection, tmp_path, monkeypatch) -> None:
    """Running the daily loop twice is safe and the raw row count is stable."""
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    days = _recent_days()
    strava = _FakeStrava(days)
    garmin = GarminConnector("tok", client=FakeGarminClient(days={d: make_day(d) for d in days}))
    _patch_connectors(monkeypatch, strava, garmin)
    gen_on = date.fromisoformat(days[-1])

    daily.run_daily(conn, settings, generated_on=gen_on)
    n1 = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
        (STRAVA,),
    ).fetchone()[0]
    daily.run_daily(conn, settings, generated_on=gen_on)
    n2 = conn.execute(
        "SELECT COUNT(*) FROM raw_response WHERE source=? AND endpoint='activity_summary'",
        (STRAVA,),
    ).fetchone()[0]
    assert n1 == n2  # idempotent upserts -- no duplication


# ---- Catch-up via watermark ------------------------------------------------


def test_catch_up_recovers_a_missed_day(conn: sqlite3.Connection, tmp_path, monkeypatch) -> None:
    """A missed run is recovered on the next run: the watermark-driven sync pulls
    everything since the last success, so a gap day's activity lands on catch-up."""
    settings = _settings(tmp_path)
    settings.ensure_dirs()

    base = GarminConnector._today() - timedelta(days=3)
    day0 = base.isoformat()
    day1 = (base + timedelta(days=1)).isoformat()
    day2 = (base + timedelta(days=2)).isoformat()

    # Run 1: only day0 exists (the scheduler fired on day0).
    strava1 = _FakeStrava([day0])
    garmin1 = GarminConnector("tok", client=FakeGarminClient(days={day0: make_day(day0)}))
    _patch_connectors(monkeypatch, strava1, garmin1)
    daily.run_daily(conn, settings, generated_on=base)

    # The Mac was asleep on day1 -> NO run happened that day. On day2 the
    # scheduler wakes and runs once: a single catch-up sync emits BOTH day1 and
    # day2 (everything since the watermark), so the missed day is recovered.
    strava2 = _FakeStrava([day1, day2])
    garmin2 = GarminConnector(
        "tok", client=FakeGarminClient(days={day1: make_day(day1), day2: make_day(day2)})
    )
    _patch_connectors(monkeypatch, strava2, garmin2)
    daily.run_daily(conn, settings, generated_on=base + timedelta(days=2))

    # All three days' activities are present despite the missed day1 run.
    present = {
        str(r["day"])
        for r in conn.execute("SELECT DISTINCT day FROM activity ORDER BY day").fetchall()
    }
    assert {day0, day1, day2} <= present


# ---- Noteworthy-only surfacing (marker file) ------------------------------


def _seed_noteworthy_state(conn: sqlite3.Connection) -> date:
    """Seed data that crashes HRV recently so the recovery verdict surfaces."""
    d0 = date(2026, 1, 1)
    n = 80
    sraw = RawWriter(conn, STRAVA)
    graw = RawWriter(conn, GARMIN)
    with conn:
        last = None
        for i in range(n):
            d = d0 + timedelta(days=i)
            if i % 3 != 2:
                aid = 8000 + i
                sraw.put("activity_summary", str(aid), make_run(aid, day=d.isoformat()))
                last = f"{d.isoformat()}T06:00:00Z"
            hrv = 28.0 if i >= n - 2 else 60.0 + (i % 5)
            rhr = 62 if i >= n - 2 else 48
            graw.put("sleep", d.isoformat(), make_day(d.isoformat())["sleep"])
            from tests.garmin_fakes import make_hrv, make_stats

            graw.put("hrv", d.isoformat(), make_hrv(d.isoformat(), last_night_avg=hrv))
            graw.put("stats", d.isoformat(), make_stats(d.isoformat(), resting_hr=rhr))
        state.mark_synced(conn, STRAVA, last_entity_ts=last)
        state.mark_synced(
            conn, GARMIN, last_entity_ts=f"{(d0 + timedelta(days=n - 1)).isoformat()}T00:00:00Z"
        )
    return d0 + timedelta(days=n - 1)


def test_marker_written_only_when_noteworthy(conn: sqlite3.Connection, tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    gen_on = _seed_noteworthy_state(conn)
    # do_sync=False: analyze the seeded data without touching the network.
    result = daily.run_daily(conn, settings, generated_on=gen_on, do_sync=False)
    assert result.noteworthy.noteworthy is True
    assert result.marker_path is not None and result.marker_path.exists()
    marker_text = result.marker_path.read_text()
    assert "NOTEWORTHY" in marker_text


def test_no_marker_when_not_noteworthy(conn: sqlite3.Connection, tmp_path, monkeypatch) -> None:
    """A quiet day writes reports but NO marker (and removes a stale one)."""
    settings = _settings(tmp_path)
    settings.ensure_dirs()

    # Seed a calm dataset: steady load, steady wellness, no nearby race.
    d0 = date(2026, 1, 1)
    n = 70
    sraw = RawWriter(conn, STRAVA)
    graw = RawWriter(conn, GARMIN)
    from tests.garmin_fakes import make_hrv, make_sleep, make_stats

    with conn:
        last = None
        for i in range(n):
            d = d0 + timedelta(days=i)
            if i % 3 != 2:
                aid = 6000 + i
                sraw.put("activity_summary", str(aid), make_run(aid, day=d.isoformat()))
                last = f"{d.isoformat()}T06:00:00Z"
            graw.put("sleep", d.isoformat(), make_sleep(d.isoformat()))
            graw.put("hrv", d.isoformat(), make_hrv(d.isoformat(), last_night_avg=60.0 + (i % 4)))
            graw.put("stats", d.isoformat(), make_stats(d.isoformat(), resting_hr=48 + (i % 2)))
        # mark BOTH sources synced "as of" the generated day so nothing is stale.
        state.mark_synced(conn, STRAVA, last_entity_ts=last)
        state.mark_synced(
            conn, GARMIN, last_entity_ts=f"{(d0 + timedelta(days=n - 1)).isoformat()}T00:00:00Z"
        )
    gen_on = d0 + timedelta(days=n - 1)

    # Plant a stale marker from a "previous" noteworthy day.
    marker = settings.reports_dir / daily.MARKER_NAME
    marker.write_text("stale\n", encoding="utf-8")

    # Patch source_freshness so the run sees the sources as fresh as-of gen_on
    # (mark_synced stamps wall-clock now, which would otherwise read as stale).
    from tempo.analysis.data import SourceFreshness

    def _fresh(c, *, as_of=None):  # type: ignore[no-untyped-def]
        return [
            SourceFreshness("garmin", f"{gen_on}T05:00:00+00:00", None, 0),
            SourceFreshness("strava", f"{gen_on}T05:00:00+00:00", None, 0),
        ]

    monkeypatch.setattr(daily.dataread, "source_freshness", _fresh)

    result = daily.run_daily(conn, settings, generated_on=gen_on, do_sync=False)
    assert result.noteworthy.noteworthy is False
    assert result.marker_path is None
    # The stale marker was removed.
    assert not marker.exists()
    # Reports were still written.
    assert len(result.reports.paths()) == 4
