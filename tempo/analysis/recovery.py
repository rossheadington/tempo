"""Multi-signal recovery / overtraining analysis (ANL-03).

A single recovery metric is unreliable; the high-confidence overtraining pattern
is **rising load** (an aggressive CTL ramp / elevated ACWR) coinciding with
**suppressed recovery markers** (HRV / resting HR / sleep diverging from the
user's *personal* baseline). This module combines both, reading load from the
PMC series (:mod:`tempo.analysis.fitness`) and wellness from personal rolling
baselines (:mod:`tempo.analysis.baselines`), and degrades honestly to
"insufficient data" when the baselines lack history (FEATURES: "recovery is
baseline-relative"; PITFALLS: never fabricate a number).

The non-obvious subtlety this module encodes (FEATURES / WHOOP): **HRV is
concerning when it is abnormal in EITHER direction**. A drop below baseline is
the classic suppressed-parasympathetic fatigue signal, but in deep overtraining
HRV can paradoxically *rise* (parasympathetic saturation). So we flag the
*magnitude* of the deviation (|z|), not just a one-sided "low HRV" test. Resting
HR is one-sided (elevated = bad) and sleep is one-sided (short = bad); only HRV
is two-sided.

Everything here is **pure** over already-read inputs plus a couple of read-only
DB helpers, so the multi-signal logic and the insufficient-data paths are
unit-testable against hand-built data with no network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from tempo.analysis import baselines
from tempo.analysis import data as dataread
from tempo.analysis.baselines import BaselinePoint
from tempo.analysis.fitness import FitnessPoint, Guardrail
from tempo.analysis.heat import HeatRollup
from tempo.analysis.heat import heat_rollup as _heat_rollup
from tempo.analysis.heat import parse_heat as _parse_heat

# |z| beyond this is a notable deviation from personal baseline (~2 SD = the
# unusual 5% tail). Configurable so the noteworthy thresholds can be tuned.
SIGNAL_Z_THRESHOLD = 2.0

# A milder deviation worth noting but not alarming (~1.5 SD).
SIGNAL_Z_WATCH = 1.5

# Ramp rate (CTL gain/week) above this counts toward the rising-load half of the
# overtraining pattern (mirrors fitness.RAMP_AGGRESSIVE but kept local so recovery
# thresholds are documented in one place).
RISING_LOAD_RAMP = 8.0

# ACWR above this also counts as rising load (the elevated zone).
RISING_LOAD_ACWR = 1.3


@dataclass(frozen=True, slots=True)
class SignalAssessment:
    """One recovery signal (hrv / resting_hr / sleep) read against its baseline.

    ``status`` is one of: ``insufficient`` (no trustworthy baseline yet),
    ``normal``, ``watch`` (mild deviation), or ``concern`` (strong deviation).
    ``direction`` records which way it deviated (``low`` / ``high`` / ``none``) so
    the HRV-either-direction subtlety is visible. ``z`` / ``value`` / ``mean`` may
    be ``None`` when insufficient.
    """

    metric: str
    status: str
    direction: str
    value: float | None
    mean: float | None
    z: float | None
    n: int
    message: str


# Which direction is *bad* per metric. HRV is two-sided (either extreme), so it
# is handled specially; resting HR high = bad; sleep low (short) = bad.
_ONE_SIDED_BAD: dict[str, str] = {
    "resting_hr": "high",
    "sleep": "low",
}


def assess_signal(metric: str, point: BaselinePoint | None) -> SignalAssessment:
    """Assess one wellness metric's latest reading against its personal baseline.

    For **HRV** the test is two-sided: a large |z| in *either* direction is a
    concern (low = suppressed parasympathetic / fatigue; high = possible
    parasympathetic saturation in deep overtraining). For resting HR and sleep the
    test is one-sided (elevated RHR / short sleep are bad; the opposite is benign).

    Returns ``status='insufficient'`` when there is no point or the baseline has
    too little history to produce a z-score -- the honest cold-start outcome.
    """
    if point is None:
        return SignalAssessment(
            metric=metric,
            status="insufficient",
            direction="none",
            value=None,
            mean=None,
            z=None,
            n=0,
            message=f"{_label(metric)}: no data yet.",
        )
    if point.z is None or point.mean is None:
        # Distinguish "too little history" from "enough history but zero spread"
        # (a flat metric can't be z-scored, but that is not a data shortage).
        if point.mean is not None and point.sd == 0:
            reason = (
                f"baseline is perfectly flat ({_fmt_value(metric, point.mean)}); "
                "no spread to judge a deviation against"
            )
        else:
            reason = (
                f"insufficient baseline history ({point.n} prior day(s)) -- "
                "cannot judge vs personal norm yet"
            )
        return SignalAssessment(
            metric=metric,
            status="insufficient",
            direction="none",
            value=point.value,
            mean=point.mean,
            z=point.z,
            n=point.n,
            message=f"{_label(metric)}: {reason}.",
        )

    z = point.z
    direction = "high" if z > 0 else ("low" if z < 0 else "none")
    magnitude = abs(z)

    if metric == "hrv":
        # Two-sided: magnitude alone decides severity; direction is reported.
        if magnitude >= SIGNAL_Z_THRESHOLD:
            status = "concern"
        elif magnitude >= SIGNAL_Z_WATCH:
            status = "watch"
        else:
            status = "normal"
        message = _hrv_message(point, status, direction, magnitude)
        return SignalAssessment(
            metric=metric,
            status=status,
            direction=direction,
            value=point.value,
            mean=point.mean,
            z=z,
            n=point.n,
            message=message,
        )

    # One-sided metrics: a deviation only matters in the "bad" direction.
    bad_dir = _ONE_SIDED_BAD.get(metric, "low")
    is_bad_way = direction == bad_dir
    if is_bad_way and magnitude >= SIGNAL_Z_THRESHOLD:
        status = "concern"
    elif is_bad_way and magnitude >= SIGNAL_Z_WATCH:
        status = "watch"
    else:
        status = "normal"
    message = _one_sided_message(metric, point, status, direction, magnitude)
    return SignalAssessment(
        metric=metric,
        status=status,
        direction=direction,
        value=point.value,
        mean=point.mean,
        z=z,
        n=point.n,
        message=message,
    )


def _label(metric: str) -> str:
    return {
        "hrv": "HRV (overnight)",
        "resting_hr": "Resting HR",
        "sleep": "Sleep duration",
    }.get(metric, metric)


def _fmt_value(metric: str, value: float | None) -> str:
    if value is None:
        return "?"
    if metric == "sleep":
        return f"{value / 3600.0:.1f} h"
    if metric == "hrv":
        return f"{value:.0f} ms"
    return f"{value:.0f} bpm"


def _hrv_message(point: BaselinePoint, status: str, direction: str, magnitude: float) -> str:
    cur = _fmt_value("hrv", point.value)
    base = _fmt_value("hrv", point.mean)
    if status == "normal":
        return f"HRV {cur} is within your normal range (baseline {base}, z={point.z:+.1f})."
    if direction == "low":
        tail = "suppressed parasympathetic tone -- a classic fatigue / under-recovery sign"
    else:
        tail = (
            "abnormally HIGH -- not automatically good: in deep overtraining HRV can rise "
            "(parasympathetic saturation), so treat an extreme swing in EITHER direction as a flag"
        )
    sev = "strongly" if status == "concern" else "mildly"
    return f"HRV {cur} is {sev} {direction} vs baseline {base} (z={point.z:+.1f}) -- {tail}."


def _one_sided_message(
    metric: str, point: BaselinePoint, status: str, direction: str, magnitude: float
) -> str:
    cur = _fmt_value(metric, point.value)
    base = _fmt_value(metric, point.mean)
    label = _label(metric)
    if status == "normal":
        return f"{label} {cur} is within your normal range (baseline {base}, z={point.z:+.1f})."
    sev = "strongly" if status == "concern" else "mildly"
    if metric == "resting_hr":
        return (
            f"{label} {cur} is {sev} elevated vs baseline {base} (z={point.z:+.1f}) -- "
            "a classic overreaching signal."
        )
    # sleep (short = bad)
    return (
        f"{label} {cur} is {sev} below baseline {base} (z={point.z:+.1f}) -- "
        "recovery debt accumulating."
    )


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """The combined recovery / overtraining verdict for the most recent day.

    ``status`` is one of:

    * ``insufficient`` -- not enough load history AND/OR no trustworthy wellness
      baselines to judge recovery at all.
    * ``ok``           -- nothing notable: load and recovery markers both nominal.
    * ``monitor``      -- a mild deviation OR rising load alone; worth watching.
    * ``elevated``     -- rising load combined with a wellness deviation, OR a
      single strong wellness concern -- the high-confidence overtraining pattern.
    """

    day: str | None
    status: str
    rising_load: bool
    load_reasons: list[str]
    signals: list[SignalAssessment]
    messages: list[str] = field(default_factory=list)
    heat: HeatRollup | None = None
    heat_present: bool = False

    @property
    def concern_signals(self) -> list[SignalAssessment]:
        return [s for s in self.signals if s.status == "concern"]

    @property
    def watch_signals(self) -> list[SignalAssessment]:
        return [s for s in self.signals if s.status == "watch"]

    @property
    def has_any_baseline(self) -> bool:
        return any(s.status != "insufficient" for s in self.signals)


def _load_rising(points: list[FitnessPoint], guardrail: Guardrail) -> tuple[bool, list[str]]:
    """Decide whether load is *rising* (the fatigue-driver half of the pattern)."""
    reasons: list[str] = []
    rising = False
    if guardrail.ramp_rate is not None and guardrail.ramp_rate > RISING_LOAD_RAMP:
        rising = True
        reasons.append(
            f"CTL ramp +{guardrail.ramp_rate:.1f}/week is aggressive (>{RISING_LOAD_RAMP:.0f})."
        )
    if guardrail.acwr is not None and guardrail.acwr > RISING_LOAD_ACWR:
        rising = True
        reasons.append(f"ACWR {guardrail.acwr:.2f} is elevated (>{RISING_LOAD_ACWR}).")
    if not rising and guardrail.acwr is not None:
        reasons.append(f"Load is not spiking (ACWR {guardrail.acwr:.2f}).")
    elif not rising:
        reasons.append("Load trend: insufficient data to judge a ramp.")
    return rising, reasons


def assess_recovery(
    *,
    day: str | None,
    points: list[FitnessPoint],
    guardrail: Guardrail,
    latest_baselines: dict[str, BaselinePoint | None],
) -> RecoveryAssessment:
    """Combine rising load with personal-baseline wellness deviations (ANL-03).

    The verdict is deliberately conservative:

    * If there is no PMC series AND no usable wellness baseline -> ``insufficient``.
    * A wellness ``concern`` (strong |z|, including the HRV either-direction case)
      OR rising-load + any wellness deviation (watch/concern) -> ``elevated``.
    * A single ``watch`` deviation, or rising load on its own -> ``monitor``.
    * Otherwise -> ``ok``.
    """
    signals = [assess_signal(m, latest_baselines.get(m)) for m in baselines.METRIC_COLUMNS]
    rising, load_reasons = _load_rising(points, guardrail)

    have_load = bool(points)
    have_baseline = any(s.status != "insufficient" for s in signals)

    messages: list[str] = []

    if not have_load and not have_baseline:
        return RecoveryAssessment(
            day=day,
            status="insufficient",
            rising_load=False,
            load_reasons=["No load series and no wellness baselines yet."],
            signals=signals,
            messages=[
                "**Insufficient data** for a recovery read: need a continuous load "
                "history (sync + transform Strava) and enough Garmin wellness history "
                "for personal baselines."
            ],
        )

    concerns = [s for s in signals if s.status == "concern"]
    watches = [s for s in signals if s.status == "watch"]

    if concerns or (rising and (concerns or watches)):
        status = "elevated"
        if rising and (concerns or watches):
            messages.append(
                "**Elevated overtraining risk**: load is rising AND recovery markers "
                "are diverging from your personal baseline -- the high-confidence pattern. "
                "Consider an easy day or two."
            )
        else:
            names = ", ".join(_label(s.metric) for s in concerns)
            messages.append(
                f"**Recovery concern**: {names} strongly off your personal baseline. "
                "Watch load and prioritise sleep; reassess in a day or two."
            )
    elif watches or rising:
        status = "monitor"
        if rising and watches:
            messages.append(
                "**Monitor**: load is climbing and a recovery marker is mildly off "
                "baseline. Not alarming yet -- keep an eye on it."
            )
        elif rising:
            messages.append(
                "**Monitor**: load is rising but recovery markers look normal. "
                "Fine for now; don't stack hard days."
            )
        else:
            names = ", ".join(_label(s.metric) for s in watches)
            messages.append(
                f"**Monitor**: {names} mildly off your personal baseline, but load is "
                "not spiking. Likely noise -- recheck tomorrow."
            )
    else:
        status = "ok"
        if not have_baseline:
            messages.append(
                "Load looks controlled. Recovery markers can't be judged yet "
                "(insufficient wellness baseline history)."
            )
        else:
            messages.append(
                "Recovery looks good: load is controlled and HRV / resting HR / sleep "
                "are within your personal norms."
            )

    return RecoveryAssessment(
        day=day,
        status=status,
        rising_load=rising,
        load_reasons=load_reasons,
        signals=signals,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# DB-backed convenience: read the latest baselines + return an assessment
# ---------------------------------------------------------------------------


def assess_recovery_from_db(
    conn: sqlite3.Connection,
    *,
    points: list[FitnessPoint],
    guardrail: Guardrail,
    window: int = baselines.DEFAULT_WINDOW,
    min_points: int = baselines.MIN_POINTS,
    heat_path: Path | None = None,
) -> RecoveryAssessment:
    """Read the latest wellness baselines from the DB and assess recovery.

    A thin wrapper over :func:`assess_recovery` that pulls the latest baseline per
    metric from ``wellness_day`` (read-only). The load ``points`` / ``guardrail``
    are passed in so the caller (the runner) computes them once and shares them.

    If ``heat_path`` is provided, the heat-adaptation log at that path is parsed
    and rolled into 7/14/28-day windows, then attached to the returned assessment
    (``heat`` + ``heat_present`` fields). When omitted, both fields default to the
    "no heat data" state and the renderer omits the section -- back-compat with
    callers from earlier phases.
    """
    latest = baselines.latest_baselines(conn, window=window, min_points=min_points)
    day = points[-1].day if points else None
    assessment = assess_recovery(
        day=day, points=points, guardrail=guardrail, latest_baselines=latest
    )

    if heat_path is None:
        return assessment

    # Align the heat windows with the recovery report's "as of" day so the
    # rolling counts match the recovery verdict's day, not the wall clock.
    if points:
        try:
            today_for_heat = date.fromisoformat(points[-1].day)
        except ValueError:
            today_for_heat = date.today()
    else:
        today_for_heat = date.today()

    heat_ctx = _parse_heat(heat_path)
    rollup = _heat_rollup(heat_ctx.sessions, today_for_heat)
    # Replace the (frozen) assessment with one that carries the heat fields.
    return RecoveryAssessment(
        day=assessment.day,
        status=assessment.status,
        rising_load=assessment.rising_load,
        load_reasons=assessment.load_reasons,
        signals=assessment.signals,
        messages=assessment.messages,
        heat=rollup,
        heat_present=heat_ctx.present,
    )


def _fmt_days_ago(n: int) -> str:
    """Render a 'days ago' phrase the way humans say it (today / 1 day ago / N days ago)."""
    if n == 0:
        return "today"
    if n == 1:
        return "1 day ago"
    return f"{n} days ago"


def _fmt_minutes(value: float) -> str:
    """Render a minutes total without trailing '.0' when it's an integer count."""
    return f"{int(value)} min" if value == int(value) else f"{value:.1f} min"


def _render_heat_section(out: list[str], heat: HeatRollup | None, heat_present: bool) -> None:
    """Append the heat-adaptation section per the A4 3-state degradation rule.

    The renderer never asks the user "do you have a heat.md?" -- the answer rides
    on the two fields the assessment carries. The three states are:

    * ``heat_present is False`` OR ``heat is None`` -- heat.md is absent (or the
      caller did not thread it through). Section omitted entirely.
    * heat.md is present but parsed zero sessions ever -- section omitted.
    * heat.md is present, all 7/14/28-day window counts are zero, but a prior
      session exists in history (``last_session_date is not None``) -- render the
      ``## Heat adaptation`` header plus a single-line *lapsed* nudge. This is
      the A4 override of the researcher's silent-default.
    * heat.md is present and ANY of the 7/14/28-day windows is non-zero -- render
      the full rollup line: counts, minutes, last-session age.
    """
    if not heat_present or heat is None:
        return
    # Present-but-empty: parsed file but no sessions ever.
    if heat.last_session_date is None:
        return

    any_recent = heat.last_7d_count > 0 or heat.last_14d_count > 0 or heat.last_28d_count > 0
    out.append("## Heat adaptation\n")
    if any_recent:
        days_ago = heat.last_session_days_ago or 0
        out.append(
            "- last 7 days: "
            f"{heat.last_7d_count} sessions / {_fmt_minutes(heat.last_7d_minutes)} · "
            f"last 14 days: {heat.last_14d_count} sessions / "
            f"{_fmt_minutes(heat.last_14d_minutes)} · "
            f"last 28 days: {heat.last_28d_count} sessions / "
            f"{_fmt_minutes(heat.last_28d_minutes)} · "
            f"last session: {_fmt_days_ago(days_ago)}"
        )
    else:
        # Lapsed nudge (A4 override): no rollup numbers, one terse line.
        days_ago = heat.last_session_days_ago or 0
        out.append(
            f"_Heat protocol lapsed -- last session {_fmt_days_ago(days_ago)}. "
            "(No sessions in the last 28 days.)_"
        )
    out.append("")


def render_recovery(
    *,
    generated_on: date,
    freshness: list[dataread.SourceFreshness],
    data_range: tuple[str, str] | None,
    assessment: RecoveryAssessment,
) -> str:
    """Render the dated recovery markdown report with the freshness header (ANL-03/05)."""
    # Imported lazily to avoid a circular import (report imports from analysis pkg).
    from tempo.analysis.report import freshness_header

    out = [
        freshness_header(
            report_title="Recovery & Overtraining",
            generated_on=generated_on,
            freshness=freshness,
            data_range=data_range,
        )
    ]

    a = assessment
    out.append("## Verdict\n")
    if a.day:
        out.append(f"_As of {a.day}._\n")
    for msg in a.messages:
        out.append(f"{msg}\n")

    out.append("## Load (fatigue driver)\n")
    for r in a.load_reasons:
        out.append(f"- {r}")
    out.append("")

    out.append("## Recovery markers vs personal baseline\n")
    out.append(
        "_HRV is judged in **both** directions: an abnormal swing either way is a "
        "flag (a drop = suppressed recovery; a spike can signal parasympathetic "
        "saturation in deep overtraining). Resting HR and sleep are one-sided._\n"
    )
    for s in a.signals:
        icon = {
            "concern": ":warning:",
            "watch": ":eyes:",
            "normal": ":white_check_mark:",
            "insufficient": ":grey_question:",
        }.get(s.status, "")
        out.append(f"- {icon} {s.message}")
    out.append("")

    _render_heat_section(out, a.heat, a.heat_present)

    if a.status == "insufficient":
        out.append(
            "_This report will sharpen as more Garmin wellness history accumulates "
            "(personal baselines need enough prior days) and as load history builds._\n"
        )
    return "\n".join(out)
