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

from runos.analysis import recovery
from runos.analysis.baselines import BaselinePoint
from runos.analysis.fitness import FitnessPoint, Guardrail, evaluate_guardrail


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
    from runos.connectors.base import RawWriter
    from runos.sync import state
    from runos.transforms.runner import run_transform
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
    from runos.analysis.load import LoadConfig
    from runos.analysis.runner import build_load_series

    cfg = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=48, threshold_hr=170)
    series = build_load_series(conn, cfg)
    guardrail = evaluate_guardrail(series.points)
    a = recovery.assess_recovery_from_db(conn, points=series.points, guardrail=guardrail)
    # HRV crashed + RHR rose at the end -> at least one concern.
    assert a.status in ("elevated", "monitor")
    assert any(s.metric == "hrv" and s.status in ("concern", "watch") for s in a.signals)


def test_render_recovery_has_freshness_and_either_direction_note(conn: sqlite3.Connection) -> None:
    _seed_wellness_and_load(conn, 70, crash_hrv_at=68)
    from runos.analysis import data as dataread
    from runos.analysis.load import LoadConfig
    from runos.analysis.runner import build_load_series

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
    weight: object = None,
    weight_present: bool = False,
    nutrition: object = None,
    nutrition_present: bool = False,
    evolab: object = None,
    evolab_present: bool = False,
    evolab_stamina_7d_ago: int | None = None,
) -> recovery.RecoveryAssessment:
    """Hand-built RecoveryAssessment with neutral signals so we exercise ONLY
    the heat / strength / weight / nutrition path."""
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
        weight=weight,  # type: ignore[arg-type]
        weight_present=weight_present,
        nutrition=nutrition,  # type: ignore[arg-type]
        nutrition_present=nutrition_present,
        evolab=evolab,  # type: ignore[arg-type]
        evolab_present=evolab_present,
        evolab_stamina_7d_ago=evolab_stamina_7d_ago,
    )


def _render(
    a: recovery.RecoveryAssessment,
    *,
    generated_on: date = date(2026, 4, 1),
    units: object = None,
) -> str:
    return recovery.render_recovery(
        generated_on=generated_on,
        freshness=[],
        data_range=None,
        assessment=a,
        units=units,  # type: ignore[arg-type]
    )


def test_render_recovery_omits_heat_when_no_heat_file() -> None:
    """heat.md missing -> the section is omitted entirely (no header)."""
    a = _ok_assessment(heat=None, heat_present=False)
    text = _render(a)
    assert "## Heat adaptation" not in text


def test_render_recovery_omits_heat_when_present_but_empty() -> None:
    """heat.md present but no sessions parsed -> section omitted (no header)."""
    from runos.analysis.heat import HeatRollup

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
    from runos.analysis.heat import HeatRollup

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
    from runos.analysis.heat import HeatRollup

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
    from runos.analysis.heat import HeatRollup

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
    from runos.analysis.strength import StrengthRollup

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
    from runos.analysis.strength import StrengthRollup

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
    from runos.analysis.strength import StrengthRollup

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
    from runos.analysis.heat import HeatRollup
    from runos.analysis.strength import StrengthRollup

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


# ---- Weight section ----


def _weight_rollup_current(*, unit_mixed: bool = False) -> object:
    """Helper: build a 'current' WeightRollup (latest weigh-in yesterday)."""
    from runos.analysis.weight import WeightEntry, WeightRollup

    entry = WeightEntry(
        date=date(2026, 5, 27), weight=72.4, unit="kg", notes=None, source_line=1
    )
    return WeightRollup(
        latest_entry=entry,
        latest_kg=72.4,
        days_since_last=1,
        avg_7d=72.6,
        avg_28d=72.9,
        ewma_trend=72.8,
        delta_vs_28d=-0.5,
        unit_mixed=unit_mixed,
    )


def test_recovery_renderer_omits_weight_section_when_absent() -> None:
    """weight.md missing -> the section is omitted entirely (no header)."""
    a = _ok_assessment(weight=None, weight_present=False)
    text = _render(a)
    assert "## Weight" not in text


def test_recovery_renderer_omits_weight_when_present_but_empty() -> None:
    """weight.md present but no entries parsed -> section omitted (no header)."""
    from runos.analysis.weight import WeightRollup

    empty = WeightRollup(
        latest_entry=None,
        latest_kg=None,
        days_since_last=None,
        avg_7d=None,
        avg_28d=None,
        ewma_trend=None,
        delta_vs_28d=None,
        unit_mixed=False,
    )
    a = _ok_assessment(weight=empty, weight_present=True)
    text = _render(a)
    assert "## Weight" not in text


def test_recovery_renderer_emits_stale_nudge_when_last_weigh_in_over_14d() -> None:
    """Latest entry >14d old -> one-line stale nudge (no rollup numbers)."""
    from runos.analysis.weight import WeightEntry, WeightRollup

    entry = WeightEntry(
        date=date(2026, 5, 7), weight=72.4, unit="kg", notes=None, source_line=1
    )
    stale = WeightRollup(
        latest_entry=entry,
        latest_kg=72.4,
        days_since_last=21,
        avg_7d=None,
        avg_28d=72.9,
        ewma_trend=72.8,
        delta_vs_28d=-0.5,
        unit_mixed=False,
    )
    a = _ok_assessment(weight=stale, weight_present=True)
    text = _render(a)
    assert "## Weight" in text
    assert "Last weigh-in 21 days ago" in text
    assert "log a current reading" in text
    # The stale nudge is the ONLY weight line -- no active-rollup markers.
    assert "7d avg" not in text
    assert "trend" not in text
    assert "vs 28d baseline" not in text


def test_recovery_renderer_emits_full_rollup_when_current() -> None:
    """Latest entry within 14d -> full rollup line with latest / 7d / 28d / trend / delta."""
    a = _ok_assessment(weight=_weight_rollup_current(), weight_present=True)
    text = _render(a)
    assert "## Weight" in text
    assert "72.4 kg today" in text
    assert "7d avg 72.6 kg" in text
    assert "28d avg 72.9 kg" in text
    assert "trend 72.8 kg" in text
    assert "−0.5 kg vs 28d baseline" in text  # Unicode minus
    # Mixed-unit caveat NOT present when unit_mixed=False.
    assert "mixed kg/lb" not in text


def test_recovery_renderer_appends_mixed_unit_caveat() -> None:
    """unit_mixed=True appends the normalised-to-kg caveat to the active rollup line."""
    a = _ok_assessment(
        weight=_weight_rollup_current(unit_mixed=True), weight_present=True
    )
    text = _render(a)
    assert "## Weight" in text
    assert "_(mixed kg/lb in log — normalised to kg)_" in text


def test_recovery_renderer_weight_section_follows_strength() -> None:
    """When both strength and weight are active, weight renders AFTER strength."""
    from runos.analysis.strength import StrengthRollup

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
        strength=strength_rollup,
        strength_present=True,
        weight=_weight_rollup_current(),
        weight_present=True,
    )
    text = _render(a)
    assert "## Strength & conditioning" in text
    assert "## Weight" in text
    assert text.index("## Strength & conditioning") < text.index("## Weight")


def test_fmt_weight_delta_signs() -> None:
    """`_fmt_weight_delta` renders +X.X / −X.X / ±0.0 with Unicode glyphs + 1 decimal."""
    assert recovery._fmt_weight_delta(0.3) == "+0.3 kg"
    assert recovery._fmt_weight_delta(-0.5) == "−0.5 kg"  # Unicode minus U+2212
    assert recovery._fmt_weight_delta(0.0) == "±0.0 kg"  # Unicode plus-minus U+00B1
    # Python's banker's rounding: 1.25 -> "1.2" (round-half-to-even).
    assert recovery._fmt_weight_delta(1.25) == "+1.2 kg"


# ---- Nutrition section ----


def _avg_day(
    *,
    protein_g: float = 122.0,
    carbs_g: float = 312.0,
    fat_g: float = 64.0,
    kcal: int = 2310,
    entry_count: int = 21,
) -> object:
    """Helper: build a DailyNutrition for use as latest_day / avg_7d."""
    from runos.analysis.nutrition import DailyNutrition

    return DailyNutrition(
        date=date(2026, 4, 1),
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        kcal=kcal,
        macro_pct_protein=21.1,
        macro_pct_carbs=54.0,
        macro_pct_fat=24.9,
        entry_count=entry_count,
    )


def _nutrition_current(
    *,
    days_since_last: int = 1,
    target_kcal: int | None = None,
    deficit_surplus_7d: int | None = None,
) -> object:
    """Helper: build a 'current' NutritionRollup (latest entry within 3 days)."""
    from runos.analysis.nutrition import NutritionRollup

    avg = _avg_day()
    return NutritionRollup(
        today=date(2026, 4, 1),
        latest_day=avg,
        days_since_last=days_since_last,
        avg_7d=avg,
        days_logged_7d=7,
        avg_28d_kcal=2280,
        target_kcal=target_kcal,
        deficit_surplus_7d=deficit_surplus_7d,
    )


def test_recovery_renderer_omits_nutrition_section_when_absent() -> None:
    """food.md missing -> the section is omitted entirely (no header)."""
    a = _ok_assessment(nutrition=None, nutrition_present=False)
    text = _render(a)
    assert "## Nutrition" not in text


def test_recovery_renderer_omits_nutrition_when_present_but_empty() -> None:
    """food.md present but no entries parsed -> section omitted (no header)."""
    from runos.analysis.nutrition import NutritionRollup

    empty = NutritionRollup(
        today=date(2026, 4, 1),
        latest_day=None,
        days_since_last=None,
        avg_7d=None,
        days_logged_7d=0,
        avg_28d_kcal=None,
        target_kcal=None,
        deficit_surplus_7d=None,
    )
    a = _ok_assessment(nutrition=empty, nutrition_present=True)
    text = _render(a)
    assert "## Nutrition" not in text


def test_recovery_renderer_emits_stale_nudge_when_last_entry_over_3d() -> None:
    """Latest entry >3d old -> one-line stale nudge (no active rollup line)."""
    stale = _nutrition_current(days_since_last=5)
    a = _ok_assessment(nutrition=stale, nutrition_present=True)
    text = _render(a)
    assert "## Nutrition" in text
    assert "Last food entry 5 days ago" in text
    assert "log today's meals" in text
    # The stale nudge is the ONLY nutrition line — active-rollup markers absent.
    assert "7d avg" not in text
    assert "days logged of 7" not in text


def test_recovery_renderer_emits_7d_trailing_rollup_when_current() -> None:
    """Latest entry within 3d -> full active 7-day rollup line."""
    a = _ok_assessment(nutrition=_nutrition_current(), nutrition_present=True)
    text = _render(a)
    assert "## Nutrition" in text
    assert "7d avg" in text
    assert "P:122g" in text
    assert "C:312g" in text
    assert "F:64g" in text
    assert "cal:2310" in text
    assert "7 days logged of 7" in text
    # No goal line when target_kcal is None.
    assert "Target" not in text
    assert "7d Δ" not in text


def test_recovery_renderer_appends_goal_line_when_target_set() -> None:
    """`target_kcal` + `deficit_surplus_7d` -> goal-delta line appended."""
    surplus = _nutrition_current(target_kcal=2200, deficit_surplus_7d=110)
    a = _ok_assessment(nutrition=surplus, nutrition_present=True)
    text = _render(a)
    assert "## Nutrition" in text
    assert "Target 2200 kcal/day" in text
    assert "7d Δ" in text
    assert "+110 kcal/day" in text

    # Deficit case: Unicode minus.
    deficit = _nutrition_current(target_kcal=2200, deficit_surplus_7d=-85)
    a2 = _ok_assessment(nutrition=deficit, nutrition_present=True)
    text2 = _render(a2)
    assert "−85 kcal/day" in text2  # Unicode minus U+2212


def test_recovery_renderer_nutrition_section_follows_weight() -> None:
    """When all four trackers are active, order is Heat → Strength → Weight → Nutrition."""
    from runos.analysis.heat import HeatRollup
    from runos.analysis.strength import StrengthRollup

    heat_rollup = HeatRollup(
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
    strength_rollup = StrengthRollup(
        today=date(2026, 4, 1),
        last_7d_count=2,
        last_7d_tonnage_kg=9835.0,
        last_14d_count=2,
        last_14d_tonnage_kg=9835.0,
        last_28d_count=3,
        last_28d_tonnage_kg=14200.0,
        last_session_date=date(2026, 3, 31),
        last_session_days_ago=1,
        last_session_name="Lower body",
    )
    a = _ok_assessment(
        heat=heat_rollup,
        heat_present=True,
        strength=strength_rollup,
        strength_present=True,
        weight=_weight_rollup_current(),
        weight_present=True,
        nutrition=_nutrition_current(),
        nutrition_present=True,
    )
    text = _render(a)
    assert "## Heat adaptation" in text
    assert "## Strength & conditioning" in text
    assert "## Weight" in text
    assert "## Nutrition" in text
    # Full ordering: Heat → Strength → Weight → Nutrition.
    assert (
        text.index("## Heat adaptation")
        < text.index("## Strength & conditioning")
        < text.index("## Weight")
        < text.index("## Nutrition")
    )


def test_recovery_renderer_handles_mixed_format_food_input(tmp_path) -> None:
    """Mixed inline + block entries on the same day produce a correct rollup
    that surfaces in the recovery section."""
    from runos.analysis.nutrition import nutrition_rollup, parse_food

    food_path = tmp_path / "food.md"
    food_path.write_text(
        "- 2026-03-31 breakfast: oats | p:13 c:54 f:6 cal:300\n"
        "\n"
        "## 2026-03-31 lunch\n"
        "- chicken bowl: p:38 c:22 f:18 cal:404\n"
        "- apple: p:0 c:25 f:0 cal:100\n",
        encoding="utf-8",
    )
    ctx_food = parse_food(food_path)
    rollup = nutrition_rollup(ctx_food.entries, date(2026, 4, 1))
    # Combined daily kcal = 300 + 404 + 100 = 804 (one day logged in 7d window).
    assert rollup.avg_7d is not None
    assert rollup.avg_7d.kcal == 804
    assert rollup.days_logged_7d == 1

    a = _ok_assessment(nutrition=rollup, nutrition_present=True)
    text = _render(a)
    assert "## Nutrition" in text
    assert "cal:804" in text
    assert "1 days logged of 7" in text


def test_fmt_kcal_delta_signs() -> None:
    """`_fmt_kcal_delta` renders +N / −N / ±0 with Unicode glyphs + no decimals."""
    assert recovery._fmt_kcal_delta(110) == "+110 kcal/day"
    assert recovery._fmt_kcal_delta(-85) == "−85 kcal/day"  # Unicode minus U+2212
    assert recovery._fmt_kcal_delta(0) == "±0 kcal/day"  # Unicode plus-minus U+00B1


# ---- Coros (EvoLab) section --------------------------------------------------


def _evolab_day(
    *,
    day: date | None = None,
    vo2max: float | None = 56.4,
    stamina_level: int | None = 62,
    training_load: int | None = 412,
    lthr: int | None = 172,
    ltsp_s_per_km: int | None = 238,
) -> object:
    """Build an EvoLabDay row for the recovery-renderer fixtures."""
    from datetime import datetime

    from runos.analysis.coros_evolab import EvoLabDay

    return EvoLabDay(
        day=day if day is not None else date(2026, 4, 1),
        vo2max=vo2max,
        stamina_level=stamina_level,
        training_load=training_load,
        lthr=lthr,
        ltsp_s_per_km=ltsp_s_per_km,
        fetched_at=datetime(2026, 4, 1, 12, 0, 0),
    )


def test_recovery_omits_evolab_section_when_absent() -> None:
    """No EvoLab data (evolab_present=False) -> section omitted entirely."""
    a = _ok_assessment(evolab=None, evolab_present=False)
    text = _render(a)
    assert "## Coros (EvoLab)" not in text


def test_recovery_emits_stale_evolab_nudge() -> None:
    """Latest EvoLab day >3 days before `today` -> one-line stale nudge."""
    stale = _evolab_day(day=date(2026, 3, 25))  # 7 days before today=2026-04-01
    a = _ok_assessment(evolab=stale, evolab_present=True)
    text = _render(a)
    assert "## Coros (EvoLab)" in text
    assert "Last EvoLab reading 7 days ago" in text
    assert "wear the watch" in text
    # Stale nudge is the ONLY EvoLab line — full-block markers absent.
    assert "VO2max" not in text
    assert "Stamina" not in text
    assert "Training load" not in text
    assert "Threshold HR" not in text
    assert "Threshold pace" not in text


def test_recovery_renders_evolab_block_with_vo2max_stamina_load_lthr_ltsp() -> None:
    """Full current-state block renders all five lines + stamina 7d delta."""
    latest = _evolab_day(
        day=date(2026, 4, 1),
        vo2max=56.4,
        stamina_level=62,
        training_load=412,
        lthr=172,
        ltsp_s_per_km=238,
    )
    a = _ok_assessment(
        evolab=latest, evolab_present=True, evolab_stamina_7d_ago=59
    )
    text = _render(a)
    assert "## Coros (EvoLab)" in text
    assert "VO2max: 56.4 ml/kg/min" in text
    # 62 - 59 = +3 delta (ASCII '+' inside the parens).
    assert "Stamina: 62 (7d Δ +3)" in text
    assert "Training load (today): 412" in text
    assert "Threshold HR (Coros): 172 bpm" in text
    assert "_cross-check vs preferences.md `threshold_hr`_" in text
    # Default units = km -> M:SS /km rendering. ltsp 238 s/km -> 3:58 /km.
    assert "Threshold pace (Coros): 3:58 /km" in text
    assert "_cross-check vs preferences.md `threshold_pace`_" in text
    # No stale nudge.
    assert "wear the watch" not in text


def test_recovery_evolab_renders_pace_in_user_units() -> None:
    """`Units(distance='miles', pace='min_per_mile')` -> pace renders as `M:SS /mi`."""
    from runos.analysis.preferences import Units

    latest = _evolab_day(
        day=date(2026, 4, 1),
        vo2max=56.4,
        stamina_level=62,
        training_load=None,
        lthr=None,
        ltsp_s_per_km=238,  # 238 s/km == ~6:23 /mi (238 * 1.609344 = 383.0 s/mi)
    )
    a = _ok_assessment(evolab=latest, evolab_present=True)
    text = _render(a, units=Units(distance="miles", pace="min_per_mile"))
    assert "## Coros (EvoLab)" in text
    assert "Threshold pace (Coros): 6:23 /mi" in text
    assert "/km" not in text.split("Threshold pace")[1].split("\n")[0]


def test_recovery_evolab_omits_missing_lines() -> None:
    """Only vo2max set -> other metric lines omitted, but the section header stays."""
    sparse = _evolab_day(
        day=date(2026, 4, 1),
        vo2max=58.1,
        stamina_level=None,
        training_load=None,
        lthr=None,
        ltsp_s_per_km=None,
    )
    a = _ok_assessment(evolab=sparse, evolab_present=True)
    text = _render(a)
    assert "## Coros (EvoLab)" in text
    assert "VO2max: 58.1 ml/kg/min" in text
    # Other lines absent.
    assert "Stamina" not in text
    assert "Training load" not in text
    assert "Threshold HR" not in text
    assert "Threshold pace" not in text


def test_recovery_evolab_omits_stamina_delta_when_no_7d_ago_value() -> None:
    """Stamina present but no 7d-ago value -> Stamina line renders WITHOUT a delta."""
    latest = _evolab_day(
        day=date(2026, 4, 1),
        vo2max=None,
        stamina_level=62,
        training_load=None,
        lthr=None,
        ltsp_s_per_km=None,
    )
    a = _ok_assessment(
        evolab=latest, evolab_present=True, evolab_stamina_7d_ago=None
    )
    text = _render(a)
    assert "## Coros (EvoLab)" in text
    assert "- Stamina: 62" in text
    # No delta fragment when 7d-ago is missing.
    assert "7d Δ" not in text


def test_recovery_evolab_falls_through_to_absent_when_all_metrics_none() -> None:
    """Row present but every metric None -> section falls through to absent."""
    empty = _evolab_day(
        day=date(2026, 4, 1),
        vo2max=None,
        stamina_level=None,
        training_load=None,
        lthr=None,
        ltsp_s_per_km=None,
    )
    a = _ok_assessment(evolab=empty, evolab_present=True)
    text = _render(a)
    assert "## Coros (EvoLab)" not in text


def test_recovery_evolab_section_follows_nutrition() -> None:
    """When both nutrition and EvoLab render, EvoLab comes AFTER nutrition."""
    a = _ok_assessment(
        nutrition=_nutrition_current(),
        nutrition_present=True,
        evolab=_evolab_day(),
        evolab_present=True,
    )
    text = _render(a)
    assert "## Nutrition" in text
    assert "## Coros (EvoLab)" in text
    assert text.index("## Nutrition") < text.index("## Coros (EvoLab)")
