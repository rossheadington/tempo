"""Tests for the multi-signal recovery / overtraining analysis (ANL-03).

Covers:
* the multi-signal combination (rising load + baseline-relative wellness);
* the HRV "abnormal in EITHER direction" subtlety (low AND high are concerns);
* insufficient-data honesty (no baselines, or too little history).

All numbers are hand-built so the thresholds are unit-testable; no network.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from tempo.analysis import recovery
from tempo.analysis.baselines import BaselinePoint
from tempo.analysis.fitness import FitnessPoint, Guardrail, evaluate_guardrail


def _bp(
    metric_value: float, mean: float, sd: float, z: float | None, *, n: int = 60
) -> BaselinePoint:
    return BaselinePoint(
        day="2026-04-01", value=metric_value, mean=mean, sd=sd, z=z, ewma=mean, n=n
    )


def _guardrail(*, acwr: float | None, ramp: float | None) -> Guardrail:
    return Guardrail(
        acwr=acwr,
        ramp_rate=ramp,
        acwr_flag="ok",
        ramp_flag="ok",
        messages=[],
    )


def _points(n: int = 30, load: float = 50.0) -> list[FitnessPoint]:
    pts = []
    d0 = date(2026, 3, 1)
    for i in range(n):
        pts.append(
            FitnessPoint(
                day=(d0 + timedelta(days=i)).isoformat(), load=load, ctl=40.0, atl=45.0, tsb=-5.0
            )
        )
    return pts


# ---- HRV either-direction subtlety -----------------------------------------


def test_hrv_low_is_a_concern() -> None:
    """A strongly LOW HRV vs baseline is flagged as a concern (classic fatigue)."""
    s = recovery.assess_signal("hrv", _bp(30.0, 60.0, 8.0, -3.75))
    assert s.status == "concern"
    assert s.direction == "low"
    assert "suppressed parasympathetic" in s.message


def test_hrv_high_is_ALSO_a_concern() -> None:
    """A strongly HIGH HRV is ALSO a concern (parasympathetic saturation in OTS)."""
    s = recovery.assess_signal("hrv", _bp(95.0, 60.0, 8.0, +4.375))
    assert s.status == "concern"
    assert s.direction == "high"
    # The either-direction rationale must be present in the message.
    assert "either direction" in s.message.lower() or "parasympathetic saturation" in s.message


def test_hrv_normal_is_not_flagged() -> None:
    s = recovery.assess_signal("hrv", _bp(62.0, 60.0, 8.0, +0.25))
    assert s.status == "normal"


def test_hrv_mild_deviation_is_watch_not_concern() -> None:
    s = recovery.assess_signal("hrv", _bp(48.0, 60.0, 8.0, -1.6))
    assert s.status == "watch"


# ---- One-sided metrics: resting HR (high=bad), sleep (low=bad) --------------


def test_resting_hr_elevated_is_concern() -> None:
    s = recovery.assess_signal("resting_hr", _bp(60.0, 48.0, 3.0, +4.0))
    assert s.status == "concern"
    assert s.direction == "high"


def test_resting_hr_low_is_NOT_flagged() -> None:
    """A low resting HR is benign (one-sided): not a concern even at large |z|."""
    s = recovery.assess_signal("resting_hr", _bp(38.0, 48.0, 3.0, -3.3))
    assert s.status == "normal"


def test_sleep_short_is_concern_and_long_is_benign() -> None:
    short = recovery.assess_signal("sleep", _bp(14400.0, 27000.0, 3600.0, -3.5))
    assert short.status == "concern"
    long = recovery.assess_signal("sleep", _bp(36000.0, 27000.0, 3600.0, +2.5))
    assert long.status == "normal"


# ---- Insufficient data honesty ---------------------------------------------


def test_assess_signal_none_point_is_insufficient() -> None:
    s = recovery.assess_signal("hrv", None)
    assert s.status == "insufficient"
    assert "no data" in s.message


def test_assess_signal_no_zscore_is_insufficient() -> None:
    """A baseline point that has no z (too few priors) is insufficient, not normal."""
    s = recovery.assess_signal("hrv", _bp(60.0, mean=60.0, sd=0.0, z=None, n=3))
    assert s.status == "insufficient"


# ---- Combined verdict ------------------------------------------------------


def test_rising_load_plus_wellness_deviation_is_elevated() -> None:
    """Rising load + a wellness deviation = the high-confidence overtraining pattern."""
    latest = {
        "hrv": _bp(48.0, 60.0, 8.0, -1.6),  # watch (mild)
        "resting_hr": _bp(49.0, 48.0, 3.0, +0.3),
        "sleep": _bp(26000.0, 27000.0, 1800.0, -0.5),
    }
    a = recovery.assess_recovery(
        day="2026-04-01",
        points=_points(),
        guardrail=_guardrail(acwr=1.4, ramp=10.0),  # rising
        latest_baselines=latest,
    )
    assert a.rising_load is True
    assert a.status == "elevated"


def test_strong_wellness_concern_alone_is_elevated() -> None:
    """A single strong wellness concern is elevated even without rising load."""
    latest = {
        "hrv": _bp(30.0, 60.0, 8.0, -3.75),  # concern
        "resting_hr": _bp(48.0, 48.0, 3.0, 0.0),
        "sleep": _bp(27000.0, 27000.0, 1800.0, 0.0),
    }
    a = recovery.assess_recovery(
        day="2026-04-01",
        points=_points(),
        guardrail=_guardrail(acwr=1.0, ramp=2.0),  # not rising
        latest_baselines=latest,
    )
    assert a.status == "elevated"
    assert a.concern_signals and a.concern_signals[0].metric == "hrv"


def test_rising_load_alone_is_monitor() -> None:
    latest = {
        "hrv": _bp(60.0, 60.0, 8.0, 0.0),
        "resting_hr": _bp(48.0, 48.0, 3.0, 0.0),
        "sleep": _bp(27000.0, 27000.0, 1800.0, 0.0),
    }
    a = recovery.assess_recovery(
        day="2026-04-01",
        points=_points(),
        guardrail=_guardrail(acwr=1.4, ramp=10.0),
        latest_baselines=latest,
    )
    assert a.status == "monitor"
    assert a.rising_load is True


def test_all_nominal_is_ok() -> None:
    latest = {
        "hrv": _bp(61.0, 60.0, 8.0, 0.125),
        "resting_hr": _bp(48.0, 48.0, 3.0, 0.0),
        "sleep": _bp(27000.0, 27000.0, 1800.0, 0.0),
    }
    a = recovery.assess_recovery(
        day="2026-04-01",
        points=_points(),
        guardrail=_guardrail(acwr=1.0, ramp=2.0),
        latest_baselines=latest,
    )
    assert a.status == "ok"


def test_no_load_and_no_baselines_is_insufficient() -> None:
    latest = {"hrv": None, "resting_hr": None, "sleep": None}
    a = recovery.assess_recovery(
        day=None,
        points=[],
        guardrail=_guardrail(acwr=None, ramp=None),
        latest_baselines=latest,
    )
    assert a.status == "insufficient"
    assert "Insufficient data" in a.messages[0]


def test_load_ok_but_no_baselines_degrades_gracefully() -> None:
    """With load but no wellness baselines, recovery is judged on load alone (ok/monitor)."""
    latest = {"hrv": None, "resting_hr": None, "sleep": None}
    a = recovery.assess_recovery(
        day="2026-04-01",
        points=_points(),
        guardrail=_guardrail(acwr=1.0, ramp=2.0),
        latest_baselines=latest,
    )
    assert a.status == "ok"
    assert not a.has_any_baseline
    assert "can't be judged yet" in a.messages[0] or "insufficient" in a.messages[0].lower()


# ---- DB-backed + render ----------------------------------------------------


def _seed_wellness_and_load(conn: sqlite3.Connection, n: int, *, crash_hrv_at: int | None) -> None:
    from tempo.connectors.base import RawWriter
    from tempo.sync import state
    from tempo.transforms.runner import run_transform
    from tests.garmin_fakes import make_hrv, make_sleep, make_stats
    from tests.strava_fakes import make_run

    d0 = date(2026, 1, 1)
    sraw = RawWriter(conn, "strava")
    graw = RawWriter(conn, "garmin")
    with conn:
        last = None
        for i in range(n):
            d = d0 + timedelta(days=i)
            if i % 3 != 2:
                aid = 5000 + i
                sraw.put("activity_summary", str(aid), make_run(aid, day=d.isoformat()))
                last = f"{d.isoformat()}T06:00:00Z"
            hrv = 60.0 + (i % 7)
            rhr = 48 + (i % 3)
            if crash_hrv_at is not None and i >= crash_hrv_at:
                hrv = 28.0
                rhr = 62
            graw.put("sleep", d.isoformat(), make_sleep(d.isoformat()))
            graw.put("hrv", d.isoformat(), make_hrv(d.isoformat(), last_night_avg=hrv))
            graw.put("stats", d.isoformat(), make_stats(d.isoformat(), resting_hr=rhr))
        state.mark_synced(conn, "strava", last_entity_ts=last)
    run_transform(conn, fill_to=(d0 + timedelta(days=n - 1)))


def test_assess_recovery_from_db_flags_a_crash(conn: sqlite3.Connection) -> None:
    _seed_wellness_and_load(conn, 70, crash_hrv_at=68)
    from tempo.analysis.load import LoadConfig
    from tempo.analysis.runner import build_load_series

    cfg = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=48, threshold_hr=170)
    series = build_load_series(conn, cfg)
    guardrail = evaluate_guardrail(series.points)
    a = recovery.assess_recovery_from_db(conn, points=series.points, guardrail=guardrail)
    # HRV crashed + RHR rose at the end -> at least one concern.
    assert a.status in ("elevated", "monitor")
    assert any(s.metric == "hrv" and s.status in ("concern", "watch") for s in a.signals)


def test_render_recovery_has_freshness_and_either_direction_note(conn: sqlite3.Connection) -> None:
    _seed_wellness_and_load(conn, 70, crash_hrv_at=68)
    from tempo.analysis import data as dataread
    from tempo.analysis.load import LoadConfig
    from tempo.analysis.runner import build_load_series

    cfg = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=48, threshold_hr=170)
    series = build_load_series(conn, cfg)
    guardrail = evaluate_guardrail(series.points)
    a = recovery.assess_recovery_from_db(conn, points=series.points, guardrail=guardrail)
    text = recovery.render_recovery(
        generated_on=date(2026, 4, 1),
        freshness=dataread.source_freshness(conn, as_of=date(2026, 4, 1)),
        data_range=dataread.data_date_range(conn),
        assessment=a,
    )
    assert "# Recovery & Overtraining" in text
    assert "Data freshness" in text  # ANL-05 header
    assert "EITHER" in text or "either" in text  # the HRV subtlety is documented
