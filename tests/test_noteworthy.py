"""Tests for the noteworthy-only threshold logic (SCHED-03).

The scheduled run only surfaces output when a threshold is crossed; otherwise it
stays quiet. These tests pin each threshold (ACWR, ramp, recovery status, baseline
z, race proximity, source staleness) and the all-nominal "not noteworthy" case.
"""

from __future__ import annotations

from datetime import date

from tempo.analysis import noteworthy as nw
from tempo.analysis.data import SourceFreshness
from tempo.analysis.fitness import Guardrail
from tempo.analysis.recovery import RecoveryAssessment, SignalAssessment


def _guardrail(**kw) -> Guardrail:  # type: ignore[no-untyped-def]
    base = dict(acwr=1.0, ramp_rate=2.0, acwr_flag="ok", ramp_flag="ok", messages=[])
    base.update(kw)
    return Guardrail(**base)


def _recovery(
    status: str = "ok", signals: list[SignalAssessment] | None = None
) -> RecoveryAssessment:
    return RecoveryAssessment(
        day="2026-04-01",
        status=status,
        rising_load=False,
        load_reasons=[],
        signals=signals or [],
        messages=[],
    )


def _fresh(source: str, days_stale: int | None, *, synced: bool = True) -> SourceFreshness:
    return SourceFreshness(
        source=source,
        last_sync_at="2026-04-01T05:00:00+00:00" if synced else None,
        last_entity_ts=None,
        days_stale=days_stale,
    )


AS_OF = date(2026, 4, 1)
NOMINAL_FRESH = [_fresh("strava", 0), _fresh("garmin", 0)]


def test_all_nominal_is_not_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is False
    assert r.reasons == []


def test_acwr_danger_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(acwr=1.7, acwr_flag="danger"),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is True
    assert any("ACWR" in reason for reason in r.reasons)


def test_aggressive_ramp_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(ramp_rate=12.0, ramp_flag="aggressive"),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is True
    assert any("ramp" in reason.lower() for reason in r.reasons)


def test_recovery_elevated_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("elevated"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is True
    assert any("Recovery status: elevated" in reason for reason in r.reasons)


def test_recovery_monitor_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("monitor"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is True


def test_recovery_insufficient_is_NOT_noteworthy() -> None:
    """An insufficient-data recovery verdict is not noise to surface daily."""
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("insufficient"),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is False


def test_strong_baseline_z_is_noteworthy() -> None:
    sig = SignalAssessment(
        metric="hrv",
        status="concern",
        direction="high",
        value=95.0,
        mean=60.0,
        z=4.3,
        n=60,
        message="",
    )
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok", signals=[sig]),
        freshness=NOMINAL_FRESH,
        next_race_days=None,
    )
    assert r.noteworthy is True
    assert any("baseline" in reason for reason in r.reasons)


def test_race_within_window_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=10,
    )
    assert r.noteworthy is True
    assert any("race" in reason.lower() for reason in r.reasons)


def test_race_far_away_is_not_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=60,
    )
    assert r.noteworthy is False


def test_stale_source_is_noteworthy() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=[_fresh("strava", 5), _fresh("garmin", 0)],
        next_race_days=None,
    )
    assert r.noteworthy is True
    assert any("stale" in reason.lower() for reason in r.reasons)


def test_never_synced_source_is_not_a_daily_gap() -> None:
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=[_fresh("garmin", None, synced=False), _fresh("strava", 0)],
        next_race_days=None,
    )
    assert r.noteworthy is False


def test_marker_text_lists_reasons() -> None:
    r = nw.NoteworthyResult(noteworthy=True, reasons=["ACWR 1.70 (danger).", "Race in 5 day(s)."])
    text = r.as_marker_text(AS_OF)
    assert "NOTEWORTHY 2026-04-01" in text
    assert "ACWR 1.70 (danger)." in text
    assert "Race in 5 day(s)." in text


def test_marker_text_quiet_when_not_noteworthy() -> None:
    r = nw.NoteworthyResult(noteworthy=False, reasons=[])
    assert "nothing noteworthy" in r.as_marker_text(AS_OF)


def test_thresholds_are_configurable() -> None:
    """A custom threshold changes the verdict (documented + tunable, SCHED-03)."""
    strict = nw.NoteworthyThresholds(race_within_days=3)
    r = nw.evaluate_noteworthy(
        as_of=AS_OF,
        guardrail=_guardrail(),
        recovery=_recovery("ok"),
        freshness=NOMINAL_FRESH,
        next_race_days=10,
        thresholds=strict,
    )
    assert r.noteworthy is False  # 10 days > the strict 3-day window


# ---- next_race_within_days helper ------------------------------------------


def test_next_race_within_days() -> None:
    from tempo.analysis.races import Race, RacesContext

    ctx = RacesContext(
        present=True,
        races=[
            Race(name="A", race_date=date(2026, 4, 20)),
            Race(name="B", race_date=date(2026, 4, 10)),
        ],
    )
    assert nw.next_race_within_days(ctx, AS_OF) == 9  # soonest is Apr 10


def test_next_race_within_days_none_when_absent() -> None:
    from tempo.analysis.races import RacesContext

    assert nw.next_race_within_days(RacesContext(present=False), AS_OF) is None
