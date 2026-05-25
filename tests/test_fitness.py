"""CTL/ATL/TSB EWMA correctness, ACWR, ramp rate, and the guardrail thresholds.

Covers LOAD-02/03. The PMC recurrences are hand-verified:

* ewma_today = ewma_yest + (load_today - ewma_yest) / N, seeded at 0.
* CTL uses N=42, ATL uses N=7.
* TSB_today = CTL_yesterday - ATL_yesterday.
"""

from __future__ import annotations

import pytest

from tempo.analysis.fitness import (
    ACWR_CHRONIC_DAYS,
    acwr,
    evaluate_guardrail,
    fitness_series,
    ramp_rate,
)


def _const_points(load: float, n: int):
    days = [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}" for i in range(n)]
    return fitness_series(days, [load] * n)


# ---- CTL/ATL/TSB EWMA correctness -----------------------------------------


def test_first_day_ctl_atl_from_zero_seed() -> None:
    pts = fitness_series(["d1"], [100.0])
    assert pts[0].ctl == pytest.approx(100.0 / 42.0)  # 2.38095...
    assert pts[0].atl == pytest.approx(100.0 / 7.0)  # 14.2857...
    # TSB on day 1 uses yesterday's (seed) values: 0 - 0 = 0.
    assert pts[0].tsb == pytest.approx(0.0)


def test_two_day_recurrence_matches_hand_calc() -> None:
    pts = fitness_series(["d1", "d2"], [100.0, 100.0])
    ctl1 = 100.0 / 42.0
    ctl2 = ctl1 + (100.0 - ctl1) / 42.0
    atl1 = 100.0 / 7.0
    atl2 = atl1 + (100.0 - atl1) / 7.0
    assert pts[1].ctl == pytest.approx(ctl2)
    assert pts[1].atl == pytest.approx(atl2)
    # Day-2 TSB uses day-1 CTL/ATL.
    assert pts[1].tsb == pytest.approx(ctl1 - atl1)


def test_ctl_converges_toward_constant_load() -> None:
    pts = _const_points(50.0, 31)
    # After ~31 days of constant 50, CTL is climbing toward 50 but not there yet;
    # ATL (7-day) is much closer to 50.
    assert pts[-1].ctl < 50.0
    assert pts[-1].atl == pytest.approx(50.0, abs=0.5)
    assert pts[-1].atl > pts[-1].ctl  # fatigue ahead of fitness while building


def test_tsb_negative_while_building_load() -> None:
    pts = _const_points(80.0, 20)
    # Building load: ATL > CTL -> TSB negative (fatigued/productive).
    assert pts[-1].tsb < 0


def test_rest_after_load_makes_tsb_positive() -> None:
    # 20 days of load then 14 days of rest -> fresh (TSB positive).
    days = [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}" for i in range(34)]
    loads = [80.0] * 20 + [0.0] * 14
    pts = fitness_series(days, loads)
    assert pts[-1].tsb > 0


def test_days_loads_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        fitness_series(["d1", "d2"], [10.0])


# ---- ACWR -----------------------------------------------------------------


def test_acwr_constant_load_is_one() -> None:
    assert acwr([100.0] * 28) == pytest.approx(1.0)


def test_acwr_spike_above_one() -> None:
    # 21 days at 50 then 7 days at 100: acute=700, chronic=21*50+7*100=1750,
    # chronic_avg=1750/4=437.5 -> ACWR=1.6
    loads = [50.0] * 21 + [100.0] * 7
    assert acwr(loads) == pytest.approx(700.0 / (1750.0 / 4.0))
    assert acwr(loads) == pytest.approx(1.6)


def test_acwr_none_when_too_few_days() -> None:
    assert acwr([100.0] * (ACWR_CHRONIC_DAYS - 1)) is None


def test_acwr_none_when_chronic_zero() -> None:
    assert acwr([0.0] * 30) is None


# ---- ramp rate ------------------------------------------------------------


def test_ramp_rate_is_ctl_change_over_7_days() -> None:
    pts = _const_points(100.0, 20)
    expected = pts[-1].ctl - pts[-8].ctl
    assert ramp_rate(pts) == pytest.approx(expected)
    assert ramp_rate(pts) > 0  # CTL rising under constant load


def test_ramp_rate_none_when_too_short() -> None:
    pts = _const_points(100.0, 5)
    assert ramp_rate(pts) is None


# ---- guardrail thresholds -------------------------------------------------


def test_guardrail_sweet_spot_ok() -> None:
    g = evaluate_guardrail(_const_points(60.0, 40))
    assert g.acwr_flag == "ok"
    assert g.acwr == pytest.approx(1.0)


def test_guardrail_flags_danger_zone() -> None:
    # Sharp spike at the end -> ACWR well above 1.5.
    days = [f"d{i}" for i in range(35)]
    loads = [20.0] * 28 + [200.0] * 7
    pts = fitness_series(days, loads)
    g = evaluate_guardrail(pts)
    assert g.acwr is not None and g.acwr > 1.5
    assert g.acwr_flag == "danger"
    assert any("danger" in m.lower() for m in g.messages)


def test_guardrail_flags_low_acwr() -> None:
    # Detraining: high base then a quiet last week.
    days = [f"d{i}" for i in range(35)]
    loads = [100.0] * 28 + [10.0] * 7
    g = evaluate_guardrail(fitness_series(days, loads))
    assert g.acwr_flag == "low"


def test_guardrail_aggressive_ramp() -> None:
    # Steep continuous build -> ramp rate > 8 CTL/week.
    days = [f"d{i}" for i in range(40)]
    loads = [300.0] * 40
    g = evaluate_guardrail(fitness_series(days, loads))
    assert g.ramp_rate is not None and g.ramp_rate > 8.0
    assert g.ramp_flag == "aggressive"


def test_guardrail_insufficient_data() -> None:
    g = evaluate_guardrail(_const_points(50.0, 5))
    assert g.acwr_flag == "insufficient"
    assert g.ramp_flag == "insufficient"
