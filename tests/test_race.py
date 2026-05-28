"""Riegel / VDOT race-time prediction (numeric) + formatting helpers.

Covers the prediction half of ANL-02. Reference values:

* Riegel T2 = T1 * (D2/D1)^1.06.
* VDOT for a 5k in 19:57 is ~50 (Daniels' published value).
* Riegel and VDOT agree closely for nearby distances (ratio < 4:1).
"""

from __future__ import annotations

import pytest

from runos.analysis.race import (
    RIEGEL_RELIABLE_RATIO,
    format_hms,
    format_pace,
    predict_race,
    riegel_predict,
    vdot_from_performance,
    vdot_predict,
)

# ---- Riegel ---------------------------------------------------------------


def test_riegel_5k_to_10k() -> None:
    # 5k in 20:00 (1200s) -> 10k = 1200 * 2^1.06 = 2502.2s
    pred = riegel_predict(5000, 1200, 10000)
    assert pred == pytest.approx(1200 * 2**1.06)
    assert pred == pytest.approx(2502.2, abs=0.5)


def test_riegel_identity_distance() -> None:
    assert riegel_predict(5000, 1200, 5000) == pytest.approx(1200.0)


def test_riegel_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        riegel_predict(0, 1200, 10000)


# ---- VDOT -----------------------------------------------------------------


def test_vdot_5k_1957_is_about_50() -> None:
    # Daniels: a 5k in 19:57 corresponds to VDOT ~50.
    vdot = vdot_from_performance(5000, 19 * 60 + 57)
    assert vdot == pytest.approx(50.0, abs=0.3)


def test_vdot_faster_runner_has_higher_vdot() -> None:
    fast = vdot_from_performance(5000, 16 * 60)
    slow = vdot_from_performance(5000, 25 * 60)
    assert fast > slow


def test_vdot_predict_round_trip_same_distance() -> None:
    # Predicting the same distance should reproduce the input time.
    t = 20 * 60.0
    out = vdot_predict(5000, t, 5000)
    assert out == pytest.approx(t, abs=1.0)


def test_vdot_and_riegel_agree_for_nearby_distances() -> None:
    # 10k -> half (ratio ~2.1:1): the two methods should be within a few percent.
    known_d, known_t = 10000, 40 * 60.0
    target = 21097.5
    r = riegel_predict(known_d, known_t, target)
    v = vdot_predict(known_d, known_t, target)
    assert abs(r - v) / r < 0.05


# ---- predict_race wrapper + reliability flag ------------------------------


def test_predict_race_marks_big_jump_unreliable() -> None:
    pred = predict_race(known_distance_m=5000, known_time_s=1200, target_distance_m=42195)
    assert not pred.reliable  # 8.4:1 ratio exceeds 4:1
    assert pred.note is not None
    assert pred.riegel_s > 0 and pred.vdot_s > 0


def test_predict_race_marks_nearby_reliable() -> None:
    pred = predict_race(known_distance_m=10000, known_time_s=2400, target_distance_m=21097.5)
    assert pred.reliable
    assert pred.note is None


def test_reliable_ratio_boundary() -> None:
    # Exactly at the boundary ratio is treated as reliable.
    pred = predict_race(
        known_distance_m=5000, known_time_s=1200, target_distance_m=5000 * RIEGEL_RELIABLE_RATIO
    )
    assert pred.reliable


# ---- formatting -----------------------------------------------------------


def test_format_hms_under_and_over_hour() -> None:
    assert format_hms(2502.2) == "41:42"
    assert format_hms(3 * 3600 + 15 * 60) == "3:15:00"


def test_format_pace() -> None:
    assert format_pace(240) == "4:00/km"
    assert format_pace(270.5) == "4:30/km"
