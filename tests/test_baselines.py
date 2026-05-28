"""Tests for personal rolling baselines (GRMN-05) -- numeric, hand-verified."""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

from runos.analysis import baselines
from runos.connectors.base import RawWriter
from runos.connectors.garmin import SOURCE as GARMIN
from runos.transforms.runner import run_transform
from tests.garmin_fakes import make_hrv, make_sleep, make_stats


def _series(values: list[float], start: str = "2026-01-01") -> list[tuple[str, float]]:
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]


# ---- Pure math (hand-calculated) ------------------------------------------


def test_rolling_baseline_mean_sd_and_zscore_hand_checked() -> None:
    """Mean/SD/z are computed over PRIOR points only and match hand calculation."""
    # 7 priors all = 50 (so the 8th point's baseline mean = 50, sd = 0 -> z None),
    # then a 9th point of 60 against priors [50]*7 + [60]? No: priors for the 9th
    # are the first 8 values. Build a clean case:
    vals = [50, 52, 48, 51, 49, 50, 50, 60]  # 7 priors before the 8th (=60)
    pts = baselines.rolling_baseline(_series(vals), min_points=7)
    last = pts[-1]
    priors = vals[:7]  # the 7 values before the 8th
    mean = sum(priors) / 7
    var = sum((v - mean) ** 2 for v in priors) / 6
    sd = math.sqrt(var)
    assert last.n == 7
    assert last.mean is not None and abs(last.mean - mean) < 1e-9
    assert last.sd is not None and abs(last.sd - sd) < 1e-9
    assert last.z is not None and abs(last.z - (60 - mean) / sd) < 1e-9


def test_rolling_baseline_insufficient_data_returns_none() -> None:
    """Fewer than min_points priors -> None baseline (honest 'insufficient data')."""
    pts = baselines.rolling_baseline(_series([50, 51, 52]), min_points=7)
    assert all(p.mean is None and p.z is None for p in pts)
    assert pts[0].n == 0  # first point has zero priors


def test_rolling_baseline_window_is_trailing_and_bounded() -> None:
    """Only the last `window` priors feed the baseline."""
    vals = list(range(1, 101))  # 1..100
    pts = baselines.rolling_baseline(_series([float(v) for v in vals]), window=10, min_points=5)
    last = pts[-1]  # value 100, priors are 90..99 (the 10-day window)
    assert last.n == 10
    assert last.mean is not None and abs(last.mean - (sum(range(90, 100)) / 10)) < 1e-9


def test_rolling_baseline_zero_variance_gives_none_z() -> None:
    pts = baselines.rolling_baseline(_series([50.0] * 10), min_points=5)
    last = pts[-1]
    assert last.sd == 0.0
    assert last.z is None  # can't z-score against zero spread


def test_ewma_is_exclusive_of_today_and_smooths() -> None:
    pts = baselines.rolling_baseline(_series([10, 20, 30, 40]), min_points=1, ewma_span=3)
    # First point has no prior EWMA.
    assert pts[0].ewma is None
    # Second point's EWMA = first value (10).
    assert pts[1].ewma == 10.0
    # Third point's EWMA = alpha*20 + (1-alpha)*10, alpha = 2/(3+1) = 0.5 -> 15.
    assert abs(pts[2].ewma - 15.0) < 1e-9


def test_latest_baseline_returns_last_point() -> None:
    pts = baselines.rolling_baseline(_series([50.0] * 10), min_points=5)
    assert baselines.latest_baseline(_series([50.0] * 10), min_points=5) == pts[-1]


def test_latest_baseline_empty_series_is_none() -> None:
    assert baselines.latest_baseline([]) is None


# ---- DB integration over wellness_day -------------------------------------


def _seed_wellness(conn: sqlite3.Connection, n_days: int) -> None:
    raw = RawWriter(conn, GARMIN)
    d0 = date(2026, 1, 1)
    with conn:
        for i in range(n_days):
            day = (d0 + timedelta(days=i)).isoformat()
            raw.put("sleep", day, make_sleep(day))
            raw.put("hrv", day, make_hrv(day, last_night_avg=60.0 + (i % 5)))
            raw.put("stats", day, make_stats(day, resting_hr=48 + (i % 3)))
    run_transform(conn, fill_to=(d0 + timedelta(days=n_days - 1)))


def test_compute_baselines_over_wellness_day(conn: sqlite3.Connection) -> None:
    _seed_wellness(conn, 20)
    result = baselines.compute_baselines(conn, min_points=7)
    assert set(result) == {"hrv", "resting_hr", "sleep"}
    assert len(result["hrv"]) == 20
    # The last HRV point has a real baseline (>=7 priors).
    assert result["hrv"][-1].mean is not None


def test_read_metric_series_skips_nulls(conn: sqlite3.Connection) -> None:
    """Days missing a metric are absent from the series (no None gaps)."""
    raw = RawWriter(conn, GARMIN)
    with conn:
        # Day 1: sleep only (no HRV). Day 2: full.
        raw.put("sleep", "2026-02-01", make_sleep("2026-02-01"))
        raw.put("sleep", "2026-02-02", make_sleep("2026-02-02"))
        raw.put("hrv", "2026-02-02", make_hrv("2026-02-02", last_night_avg=70.0))
    run_transform(conn, fill_to=date(2026, 2, 2))

    hrv_series = baselines.read_metric_series(conn, "hrv")
    # Only the day with HRV appears.
    assert hrv_series == [("2026-02-02", 70.0)]


def test_read_metric_series_empty_when_no_wellness_table() -> None:
    """On a bare DB with no wellness rows, the series is empty (not an error)."""
    import tempfile
    from pathlib import Path

    from runos import db

    with tempfile.TemporaryDirectory() as tmp:
        c = db.init_db(Path(tmp) / "t.db")
        try:
            assert baselines.read_metric_series(c, "hrv") == []
        finally:
            c.close()


def test_latest_baselines_returns_none_on_sparse_data(conn: sqlite3.Connection) -> None:
    _seed_wellness(conn, 3)  # fewer than min_points
    latest = baselines.latest_baselines(conn, min_points=7)
    assert latest["hrv"] is not None  # a point exists
    assert latest["hrv"].mean is None  # but no trustworthy baseline yet
