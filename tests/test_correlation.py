"""Tests for the honest correlation insight (ANL-04).

Covers:
* Pearson r computed correctly on a known dataset (hand-verified);
* the minimum-n gate: insufficient data below the floor, reported above it;
* the explicit "insufficient data -- N paired days, need M" message;
* DB-backed pairing from daily_summary + the load series.

stdlib only; no network.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from tempo.analysis import correlation as corr

# ---- Pearson math (hand-verified) ------------------------------------------


def test_pearson_perfect_positive() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]
    assert corr.pearson(xs, ys) == 1.0


def test_pearson_perfect_negative() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [8.0, 6.0, 4.0, 2.0]
    assert corr.pearson(xs, ys) == -1.0


def test_pearson_known_value() -> None:
    """A known small dataset: r computed by hand."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [2.0, 1.0, 4.0, 3.0, 5.0]
    # mean_x=3, mean_y=3; cov numerator = sum(dx*dy):
    # dx=[-2,-1,0,1,2], dy=[-1,-2,1,0,2] -> 2+2+0+0+4 = 8
    # sum dx^2 = 10, sum dy^2 = 10 -> r = 8 / sqrt(100) = 0.8
    r = corr.pearson(xs, ys)
    assert r is not None and abs(r - 0.8) < 1e-12


def test_pearson_zero_variance_is_none() -> None:
    assert corr.pearson([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None


def test_pearson_too_few_points_is_none() -> None:
    assert corr.pearson([1.0], [2.0]) is None


# ---- The minimum-n gate (the whole point of ANL-04) ------------------------


def _pairs(n: int, *, r_target: str = "pos") -> list[tuple[float, float]]:
    """n paired points with a clear relationship so r is well-defined."""
    out = []
    for i in range(n):
        x = float(i)
        y = float(i) if r_target == "pos" else float(n - i)
        out.append((x, y))
    return out


def test_below_min_n_is_insufficient_with_explicit_message() -> None:
    result = corr.correlate("sleep", "load", _pairs(5), min_n=20)
    assert result.reported is False
    assert result.r is None
    assert result.strength == "insufficient"
    assert "insufficient data" in result.message.lower()
    assert "5 paired" in result.message
    assert "need 20" in result.message


def test_exactly_min_n_is_reported() -> None:
    result = corr.correlate("sleep", "load", _pairs(20), min_n=20)
    assert result.reported is True
    assert result.r is not None
    assert result.n == 20


def test_reported_correlation_direction_and_strength() -> None:
    result = corr.correlate("hrv", "load", _pairs(30, r_target="neg"), min_n=20)
    assert result.reported is True
    assert result.r is not None and result.r < 0
    assert result.direction == "negative"
    assert result.strength == "strong"


def test_reported_with_zero_variance_is_not_computable() -> None:
    """Enough days but a flat predictor -> reported=False, 'not computable'."""
    pairs = [(5.0, float(i)) for i in range(25)]  # x is constant
    result = corr.correlate("sleep", "load", pairs, min_n=20)
    assert result.reported is False
    assert "not computable" in result.message


def test_default_min_n_is_documented_constant() -> None:
    assert corr.MIN_PAIRED_DAYS == 20


# ---- p-value sanity --------------------------------------------------------


def test_pvalue_small_for_strong_correlation_many_points() -> None:
    # A strong-but-imperfect relationship (r<1 so the t-stat is defined): a small
    # jitter on y keeps r high without making it exactly 1.0.
    pairs = [(float(i), float(i) + (1.0 if i % 2 else -1.0)) for i in range(40)]
    result = corr.correlate("hrv", "load", pairs, min_n=20)
    assert result.reported is True
    assert result.p is not None and result.p < 0.05


def test_norm_cdf_sanity() -> None:
    assert abs(corr._norm_cdf(0.0) - 0.5) < 1e-9
    assert corr._norm_cdf(5.0) > 0.999


# ---- DB-backed pairing -----------------------------------------------------


def _seed(conn: sqlite3.Connection, n: int) -> dict[str, float]:
    """Seed n days of activities (with HR) + wellness with VARYING sleep/HRV."""
    from tempo.connectors.base import RawWriter
    from tempo.transforms.runner import run_transform
    from tests.garmin_fakes import make_hrv, make_sleep, make_stats
    from tests.strava_fakes import make_run

    d0 = date(2026, 1, 1)
    sraw = RawWriter(conn, "strava")
    graw = RawWriter(conn, "garmin")
    with conn:
        for i in range(n):
            d = d0 + timedelta(days=i)
            if i % 3 != 2:
                aid = 7000 + i
                # vary speed so load varies
                speed = 3.0 + (i % 5) * 0.2
                sraw.put(
                    "activity_summary",
                    str(aid),
                    make_run(aid, day=d.isoformat(), average_speed=speed),
                )
            graw.put("sleep", d.isoformat(), make_sleep(d.isoformat()))
            # vary HRV so it has variance
            graw.put("hrv", d.isoformat(), make_hrv(d.isoformat(), last_night_avg=55.0 + (i % 9)))
            graw.put("stats", d.isoformat(), make_stats(d.isoformat()))
    run_transform(conn, fill_to=(d0 + timedelta(days=n - 1)))
    return {}


def test_read_observations_and_build(conn: sqlite3.Connection) -> None:
    from tempo.analysis.load import LoadConfig
    from tempo.analysis.runner import build_load_series

    _seed(conn, 40)
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    series = build_load_series(conn, cfg)
    load_by_day = {dl.day: dl.load for dl in series.day_loads}
    obs = corr.read_observations(conn, load_by_day)
    # One observation per spine day.
    assert len(obs) >= 40
    results = corr.build_correlations(obs)
    # hrv->load should have enough paired training days to be reported.
    hrv_load = next(r for r in results if r.predictor == "hrv" and r.outcome == "load")
    assert hrv_load.n >= 20
    assert hrv_load.reported is True


def test_build_correlations_excludes_rest_days_from_load(conn: sqlite3.Connection) -> None:
    """Zero-load rest days are excluded from *->load correlations."""
    from tempo.analysis.load import LoadConfig
    from tempo.analysis.runner import build_load_series

    _seed(conn, 30)
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    series = build_load_series(conn, cfg)
    load_by_day = {dl.day: dl.load for dl in series.day_loads}
    obs = corr.read_observations(conn, load_by_day)
    n_training = sum(1 for o in obs if o.load and o.load > 0)
    results = corr.build_correlations(obs)
    hrv_load = next(r for r in results if r.predictor == "hrv" and r.outcome == "load")
    assert hrv_load.n == n_training


def test_render_correlations_reports_insufficient_when_sparse(conn: sqlite3.Connection) -> None:
    from tempo.analysis import data as dataread
    from tempo.analysis.load import LoadConfig
    from tempo.analysis.runner import build_load_series

    _seed(conn, 10)  # too few days for the 20-floor
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    series = build_load_series(conn, cfg)
    load_by_day = {dl.day: dl.load for dl in series.day_loads}
    obs = corr.read_observations(conn, load_by_day)
    results = corr.build_correlations(obs)
    text = corr.render_correlations(
        generated_on=date(2026, 2, 1),
        freshness=dataread.source_freshness(conn, as_of=date(2026, 2, 1)),
        data_range=dataread.data_date_range(conn),
        results=results,
    )
    assert "# Correlation Insight" in text
    assert "insufficient data" in text.lower()
    assert "Data freshness" in text  # ANL-05
