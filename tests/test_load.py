"""Per-activity load: rTSS primary, hrTSS fallback, method flagging, insufficient.

Covers LOAD-01. Numbers are hand-computed:

* rTSS = duration_s * IF^2 / 3600 * 100, IF = threshold_pace / activity_pace.
  One hour exactly at threshold pace => IF=1 => rTSS=100.
* hrTSS uses HR-reserve anchored on threshold HR; one hour at threshold HR => ~100.
"""

from __future__ import annotations

import pytest

from runos.analysis.load import (
    REST,
    ActivityLoad,
    DayLoad,
    LoadConfig,
    LoadMethod,
    aggregate_day_load,
    compute_activity_load,
)

# ---- rTSS (primary, pace-based) -------------------------------------------


def test_rtss_one_hour_at_threshold_is_100() -> None:
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=240.0, avg_hr=None, config=cfg)
    assert out.method is LoadMethod.RTSS
    assert out.load == pytest.approx(100.0)
    assert out.intensity_factor == pytest.approx(1.0)


def test_rtss_faster_than_threshold_scores_above_100() -> None:
    # 10% faster pace -> IF = 240/216 = 1.1111 -> 100 * IF^2 = 123.457
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=216.0, avg_hr=None, config=cfg)
    assert out.method is LoadMethod.RTSS
    assert out.load == pytest.approx(100.0 * (240.0 / 216.0) ** 2)
    assert out.load == pytest.approx(123.4568, abs=1e-3)


def test_rtss_half_hour_easy_scores_low() -> None:
    # 30 min at pace 300 (threshold 240) -> IF=0.8 -> 1800*0.64/3600*100 = 32
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    out = compute_activity_load(duration_s=1800, avg_pace_s_per_km=300.0, avg_hr=None, config=cfg)
    assert out.method is LoadMethod.RTSS
    assert out.load == pytest.approx(32.0)


def test_rtss_preferred_over_hr_when_both_available() -> None:
    cfg = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=50, threshold_hr=170)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=240.0, avg_hr=170.0, config=cfg)
    assert out.method is LoadMethod.RTSS  # pace wins


# ---- hrTSS (fallback) -----------------------------------------------------


def test_hrtss_one_hour_at_threshold_hr_is_100() -> None:
    cfg = LoadConfig(max_hr=190, resting_hr=50, threshold_hr=170)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=None, avg_hr=170.0, config=cfg)
    assert out.method is LoadMethod.HRTSS
    assert out.load == pytest.approx(100.0)
    assert out.intensity_factor == pytest.approx(1.0)


def test_hrtss_used_when_no_threshold_pace_even_if_pace_present() -> None:
    # No threshold pace configured -> rTSS impossible -> hrTSS from HR.
    cfg = LoadConfig(max_hr=190, resting_hr=50, threshold_hr=170)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=240.0, avg_hr=170.0, config=cfg)
    assert out.method is LoadMethod.HRTSS


def test_hrtss_threshold_hr_estimated_from_max_when_unset() -> None:
    # threshold_hr defaults to 0.92*max = 0.92*200 = 184.
    cfg = LoadConfig(max_hr=200, resting_hr=40)
    assert cfg.effective_threshold_hr() == pytest.approx(184.0)
    # avg_hr at the estimated threshold -> IF=1 -> 100.
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=None, avg_hr=184.0, config=cfg)
    assert out.method is LoadMethod.HRTSS
    assert out.load == pytest.approx(100.0)


def test_hrtss_below_threshold_scores_below_100() -> None:
    # HRR: avg 110, rest 50, max 190 -> hrr=60/140=0.4286; thr=120/140=0.8571
    # IF = 0.5 -> 100*0.25 = 25
    cfg = LoadConfig(max_hr=190, resting_hr=50, threshold_hr=170)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=None, avg_hr=110.0, config=cfg)
    assert out.load == pytest.approx(25.0)


# ---- insufficient data (never invent load) --------------------------------


def test_insufficient_when_no_config_and_no_data() -> None:
    out = compute_activity_load(
        duration_s=3600, avg_pace_s_per_km=None, avg_hr=None, config=LoadConfig()
    )
    assert out.method is LoadMethod.INSUFFICIENT
    assert out.load is None
    assert out.reason


def test_insufficient_when_config_present_but_activity_lacks_inputs() -> None:
    cfg = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=50)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=None, avg_hr=None, config=cfg)
    assert out.method is LoadMethod.INSUFFICIENT
    assert out.load is None


def test_insufficient_without_duration() -> None:
    cfg = LoadConfig(threshold_pace_s_per_km=240.0)
    out = compute_activity_load(duration_s=0, avg_pace_s_per_km=240.0, avg_hr=None, config=cfg)
    assert out.method is LoadMethod.INSUFFICIENT


def test_insufficient_when_hr_config_incomplete() -> None:
    # max_hr but no resting_hr -> HRR undefined -> cannot hrTSS.
    cfg = LoadConfig(max_hr=190)
    out = compute_activity_load(duration_s=3600, avg_pace_s_per_km=None, avg_hr=170.0, config=cfg)
    assert out.method is LoadMethod.INSUFFICIENT


# ---- day aggregation + method flag ----------------------------------------


def test_rest_day_is_zero_load() -> None:
    dl = aggregate_day_load("2026-05-01", [])
    assert dl == DayLoad(day="2026-05-01", load=0.0, method=REST, n_activities=0, n_insufficient=0)


def test_day_sums_loads_and_flags_dominant_method() -> None:
    loads = [
        ActivityLoad(load=50.0, method=LoadMethod.RTSS),
        ActivityLoad(load=30.0, method=LoadMethod.HRTSS),
    ]
    dl = aggregate_day_load("2026-05-02", loads)
    assert dl.load == pytest.approx(80.0)
    assert dl.method == LoadMethod.RTSS.value  # rTSS dominates when any present
    assert dl.n_activities == 2
    assert dl.n_insufficient == 0


def test_day_with_only_insufficient_activities_flags_insufficient() -> None:
    loads = [ActivityLoad(load=None, method=LoadMethod.INSUFFICIENT)]
    dl = aggregate_day_load("2026-05-03", loads)
    assert dl.load == pytest.approx(0.0)
    assert dl.method == LoadMethod.INSUFFICIENT.value
    assert dl.n_insufficient == 1


def test_day_hr_only_flags_hrtss() -> None:
    loads = [ActivityLoad(load=40.0, method=LoadMethod.HRTSS)]
    dl = aggregate_day_load("2026-05-04", loads)
    assert dl.method == LoadMethod.HRTSS.value
