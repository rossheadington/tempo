"""Tests for the multi-signal recovery / overtraining analysis (ANL-03).

Covers:
* the multi-signal combination (rising load + baseline-relative wellness);
* the HRV "abnormal in EITHER direction" subtlety (low AND high are concerns);
* insufficient-data honesty (no baselines, or too little history);
* the heat-adaptation section rendering with the A4 3-state degradation rule.

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


# ---- Heat-adaptation section rendering (A4 override: 3 degradation states) --


def _ok_assessment(
    *,
    heat: object = None,
    heat_present: bool = False,
    strength: object = None,
    strength_present: bool = False,
) -> recovery.RecoveryAssessment:
    """Hand-built RecoveryAssessment with neutral signals so we exercise ONLY
    the heat / strength path."""
    signals = [
        recovery.SignalAssessment(
            metric="hrv",
            status="normal",
            direction="none",
            value=60.0,
            mean=60.0,
            z=0.0,
            n=60,
            message="HRV nominal.",
        ),
        recovery.SignalAssessment(
            metric="resting_hr",
            status="normal",
            direction="none",
            value=48.0,
            mean=48.0,
            z=0.0,
            n=60,
            message="Resting HR nominal.",
        ),
        recovery.SignalAssessment(
            metric="sleep",
            status="normal",
            direction="none",
            value=27000.0,
            mean=27000.0,
            z=0.0,
            n=60,
            message="Sleep nominal.",
        ),
    ]
    return recovery.RecoveryAssessment(
        day="2026-04-01",
        status="ok",
        rising_load=False,
        load_reasons=["Load is not spiking (ACWR 1.00)."],
        signals=signals,
        messages=["Recovery looks good."],
        heat=heat,  # type: ignore[arg-type]
        heat_present=heat_present,
        strength=strength,  # type: ignore[arg-type]
        strength_present=strength_present,
    )


def _render(a: recovery.RecoveryAssessment) -> str:
    return recovery.render_recovery(
        generated_on=date(2026, 4, 1),
        freshness=[],
        data_range=None,
        assessment=a,
    )


def test_render_recovery_omits_heat_when_no_heat_file() -> None:
    """heat.md missing -> the section is omitted entirely (no header)."""
    a = _ok_assessment(heat=None, heat_present=False)
    text = _render(a)
    assert "## Heat adaptation" not in text


def test_render_recovery_omits_heat_when_present_but_empty() -> None:
    """heat.md present but no sessions parsed -> section omitted (no header)."""
    from tempo.analysis.heat import HeatRollup

    empty = HeatRollup(
        today=date(2026, 4, 1),
        last_7d_count=0,
        last_7d_minutes=0.0,
        last_14d_count=0,
        last_14d_minutes=0.0,
        last_28d_count=0,
        last_28d_minutes=0.0,
        last_session_date=None,
        last_session_days_ago=None,
    )
    a = _ok_assessment(heat=empty, heat_present=True)
    text = _render(a)
    assert "## Heat adaptation" not in text


def test_render_recovery_renders_heat_when_recent_sessions() -> None:
    """Sessions in the 7/14/28-day windows -> full rollup line."""
    from tempo.analysis.heat import HeatRollup

    rollup = HeatRollup(
        today=date(2026, 4, 1),
        last_7d_count=3,
        last_7d_minutes=78.0,
        last_14d_count=6,
        last_14d_minutes=154.0,
        last_28d_count=10,
        last_28d_minutes=260.0,
        last_session_date=date(2026, 3, 30),
        last_session_days_ago=2,
    )
    a = _ok_assessment(heat=rollup, heat_present=True)
    text = _render(a)
    assert "## Heat adaptation" in text
    assert "3 sessions" in text
    assert "78 min" in text
    assert "last session" in text
    assert "2 days ago" in text
    # The full-rollup line must NOT be a lapsed-nudge.
    assert "lapsed" not in text.lower()


def test_render_recovery_renders_heat_lapsed_nudge() -> None:
    """Sessions exist in history but ALL >28 days old -> one-line lapsed nudge (A4 override)."""
    from tempo.analysis.heat import HeatRollup

    lapsed = HeatRollup(
        today=date(2026, 4, 1),
        last_7d_count=0,
        last_7d_minutes=0.0,
        last_14d_count=0,
        last_14d_minutes=0.0,
        last_28d_count=0,
        last_28d_minutes=0.0,
        last_session_date=date(2026, 2, 13),
        last_session_days_ago=47,
    )
    a = _ok_assessment(heat=lapsed, heat_present=True)
    text = _render(a)
    assert "## Heat adaptation" in text
    assert "lapsed" in text.lower()
    assert "47 days ago" in text
    # The lapsed nudge is the ONLY heat line -- no rollup numbers.
    assert "sessions /" not in text
    assert "last 7 days" not in text


def test_render_recovery_heat_today_phrase() -> None:
    """A session today renders 'last session: today' (NOT '0 days ago')."""
    from tempo.analysis.heat import HeatRollup

    today_rollup = HeatRollup(
        today=date(2026, 4, 1),
        last_7d_count=1,
        last_7d_minutes=20.0,
        last_14d_count=1,
        last_14d_minutes=20.0,
        last_28d_count=1,
        last_28d_minutes=20.0,
        last_session_date=date(2026, 4, 1),
        last_session_days_ago=0,
    )
    a = _ok_assessment(heat=today_rollup, heat_present=True)
    text = _render(a)
    assert "## Heat adaptation" in text
    assert "last session: today" in text
    assert "0 days ago" not in text


# ---- Strength & conditioning section ----


def test_render_recovery_omits_strength_when_no_strength_file() -> None:
    """strength.md missing -> the section is omitted entirely (no header)."""
    a = _ok_assessment(strength=None, strength_present=False)
    text = _render(a)
    assert "## Strength & conditioning" not in text


def test_render_recovery_omits_strength_when_present_but_empty() -> None:
    """strength.md present but no sessions parsed -> section omitted (no header)."""
    from tempo.analysis.strength import StrengthRollup

    empty = StrengthRollup(
        today=date(2026, 4, 1),
        last_7d_count=0,
        last_7d_tonnage_kg=0.0,
        last_14d_count=0,
        last_14d_tonnage_kg=0.0,
        last_28d_count=0,
        last_28d_tonnage_kg=0.0,
        last_session_date=None,
        last_session_days_ago=None,
        last_session_name=None,
    )
    a = _ok_assessment(strength=empty, strength_present=True)
    text = _render(a)
    assert "## Strength & conditioning" not in text


def test_render_recovery_renders_strength_when_recent_sessions() -> None:
    """Sessions in the 7/14/28-day windows -> full rollup line with tonnage + name."""
    from tempo.analysis.strength import StrengthRollup

    rollup = StrengthRollup(
        today=date(2026, 5, 27),
        last_7d_count=2,
        last_7d_tonnage_kg=9835.0,
        last_14d_count=2,
        last_14d_tonnage_kg=9835.0,
        last_28d_count=3,
        last_28d_tonnage_kg=14200.0,
        last_session_date=date(2026, 5, 26),
        last_session_days_ago=1,
        last_session_name="Lower body",
    )
    a = _ok_assessment(strength=rollup, strength_present=True)
    text = _render(a)
    assert "## Strength & conditioning" in text
    assert "last 7 days: 2 sessions" in text
    assert "9,835 kg" in text
    assert "last 28 days: 3 sessions" in text
    assert "14.2 t" in text
    assert "Lower body" in text
    assert "1 day ago" in text
    # The full-rollup line must NOT be a lapsed-nudge.
    assert "lapsed" not in text.lower()


def test_render_recovery_renders_strength_lapsed_nudge() -> None:
    """Sessions exist in history but ALL >28 days old -> one-line lapsed nudge."""
    from tempo.analysis.strength import StrengthRollup

    lapsed = StrengthRollup(
        today=date(2026, 5, 27),
        last_7d_count=0,
        last_7d_tonnage_kg=0.0,
        last_14d_count=0,
        last_14d_tonnage_kg=0.0,
        last_28d_count=0,
        last_28d_tonnage_kg=0.0,
        last_session_date=date(2026, 4, 1),
        last_session_days_ago=56,
        last_session_name="Upper body",
    )
    a = _ok_assessment(strength=lapsed, strength_present=True)
    text = _render(a)
    assert "## Strength & conditioning" in text
    assert "S&C protocol lapsed" in text
    assert "56 days ago" in text
    assert "No sessions in the last 28 days" in text
    # The lapsed nudge is the ONLY strength line -- no rollup numbers.
    assert "last 7 days:" not in text
    assert "tonnage" not in text


def test_render_recovery_strength_section_follows_heat() -> None:
    """When both heat and strength are active, strength renders AFTER heat."""
    from tempo.analysis.heat import HeatRollup
    from tempo.analysis.strength import StrengthRollup

    heat_rollup = HeatRollup(
        today=date(2026, 5, 27),
        last_7d_count=3,
        last_7d_minutes=78.0,
        last_14d_count=6,
        last_14d_minutes=154.0,
        last_28d_count=10,
        last_28d_minutes=260.0,
        last_session_date=date(2026, 5, 25),
        last_session_days_ago=2,
    )
    strength_rollup = StrengthRollup(
        today=date(2026, 5, 27),
        last_7d_count=2,
        last_7d_tonnage_kg=9835.0,
        last_14d_count=2,
        last_14d_tonnage_kg=9835.0,
        last_28d_count=3,
        last_28d_tonnage_kg=14200.0,
        last_session_date=date(2026, 5, 26),
        last_session_days_ago=1,
        last_session_name="Lower body",
    )
    a = _ok_assessment(
        heat=heat_rollup,
        heat_present=True,
        strength=strength_rollup,
        strength_present=True,
    )
    text = _render(a)
    assert "## Heat adaptation" in text
    assert "## Strength & conditioning" in text
    assert text.index("## Heat adaptation") < text.index("## Strength & conditioning")


def test_fmt_tonnage_kg_vs_tonnes() -> None:
    """`_fmt_tonnage` boundary cases: 0 kg / comma-grouped kg / tonnes with 1 decimal."""
    assert recovery._fmt_tonnage(0.0) == "0 kg"
    assert recovery._fmt_tonnage(9835.0) == "9,835 kg"
    assert recovery._fmt_tonnage(9999.0) == "9,999 kg"
    assert recovery._fmt_tonnage(10000.0) == "10.0 t"
    assert recovery._fmt_tonnage(12400.0) == "12.4 t"
