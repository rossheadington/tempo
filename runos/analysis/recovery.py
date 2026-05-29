"""Multi-signal recovery / overtraining analysis (ANL-03).

A single recovery metric is unreliable; the high-confidence overtraining pattern
is **rising load** (an aggressive CTL ramp / elevated ACWR) coinciding with
**suppressed recovery markers** (HRV / resting HR / sleep diverging from the
user's *personal* baseline). This module combines both, reading load from the
PMC series (:mod:`runos.analysis.fitness`) and wellness from personal rolling
baselines (:mod:`runos.analysis.baselines`), and degrades honestly to
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
from datetime import date, timedelta
from pathlib import Path

from runos.analysis import baselines
from runos.analysis import data as dataread
from runos.analysis.baselines import BaselinePoint
from runos.analysis.coros_evolab import EvoLabContext, EvoLabDay
from runos.analysis.fitness import FitnessPoint, Guardrail
from runos.analysis.heat import HeatRollup
from runos.analysis.heat import heat_rollup as _heat_rollup
from runos.analysis.heat import parse_heat as _parse_heat
from runos.analysis.nutrition import NutritionRollup
from runos.analysis.nutrition import nutrition_rollup as _nutrition_rollup
from runos.analysis.nutrition import parse_food as _parse_food
from runos.analysis.preferences import Units
from runos.analysis.strength import StrengthRollup
from runos.analysis.strength import parse_strength as _parse_strength
from runos.analysis.strength import strength_rollup as _strength_rollup
from runos.analysis.weight import WeightRollup
from runos.analysis.weight import parse_weight as _parse_weight
from runos.analysis.weight import weight_rollup as _weight_rollup
from runos.units import format_pace

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
    strength: StrengthRollup | None = None
    strength_present: bool = False
    weight: WeightRollup | None = None
    weight_present: bool = False
    nutrition: NutritionRollup | None = None
    nutrition_present: bool = False
    evolab: EvoLabDay | None = None
    evolab_present: bool = False
    evolab_stamina_7d_ago: int | None = None

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
    strength_path: Path | None = None,
    weight_path: Path | None = None,
    food_path: Path | None = None,
    target_kcal: int | None = None,
    evolab_ctx: EvoLabContext | None = None,
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

    If ``strength_path`` is provided, the strength-and-conditioning log at that
    path is parsed and rolled into matching 7/14/28-day windows, then attached to
    the assessment (``strength`` + ``strength_present`` fields). The same alignment
    day used for heat is reused so both tracker windows match the recovery
    report's "as of" day. Omitted ``strength_path`` preserves back-compat with
    callers from earlier phases.

    If ``weight_path`` is provided, the weight log at that path is parsed and
    rolled into 7d/28d averages + an EWMA trend, then attached to the assessment
    (``weight`` + ``weight_present`` fields). The same alignment day reused for
    heat + strength applies here too. Omitted ``weight_path`` preserves
    back-compat.

    If ``food_path`` is provided, the food log at that path is parsed and
    rolled into 7d/28d nutrition windows + optional kcal-goal delta, then
    attached to the assessment (``nutrition`` + ``nutrition_present`` fields).
    ``target_kcal`` flows into the rollup so the recovery report can surface a
    signed deficit/surplus alongside the 7d average. The same alignment day
    used for heat + strength + weight applies here too. Omitted ``food_path``
    preserves back-compat.

    If ``evolab_ctx`` is provided, the Coros EvoLab context (read via
    :func:`runos.analysis.coros_evolab.read_evolab`) is attached to the
    assessment as ``evolab`` (latest day with metrics) and ``evolab_present``.
    The 7-day-prior stamina value is looked up from the context's full days
    list and stored as ``evolab_stamina_7d_ago`` so the renderer can compute a
    Δ without re-reading the DB. Omitted ``evolab_ctx`` preserves back-compat
    (section omitted entirely).
    """
    latest = baselines.latest_baselines(conn, window=window, min_points=min_points)
    day = points[-1].day if points else None
    assessment = assess_recovery(
        day=day, points=points, guardrail=guardrail, latest_baselines=latest
    )

    if (
        heat_path is None
        and strength_path is None
        and weight_path is None
        and food_path is None
        and evolab_ctx is None
    ):
        return assessment

    # Align both tracker windows with the recovery report's "as of" day so the
    # rolling counts match the recovery verdict's day, not the wall clock.
    if points:
        try:
            today_for_tracker = date.fromisoformat(points[-1].day)
        except ValueError:
            today_for_tracker = date.today()
    else:
        today_for_tracker = date.today()

    heat_rollup_obj: HeatRollup | None = None
    heat_present = False
    if heat_path is not None:
        heat_ctx = _parse_heat(heat_path)
        heat_rollup_obj = _heat_rollup(heat_ctx.sessions, today_for_tracker)
        heat_present = heat_ctx.present

    strength_rollup_obj: StrengthRollup | None = None
    strength_present = False
    if strength_path is not None:
        strength_ctx = _parse_strength(strength_path)
        strength_rollup_obj = _strength_rollup(strength_ctx.sessions, today_for_tracker)
        strength_present = strength_ctx.present

    weight_rollup_obj: WeightRollup | None = None
    weight_present = False
    if weight_path is not None:
        weight_ctx = _parse_weight(weight_path)
        weight_rollup_obj = _weight_rollup(weight_ctx.entries, today_for_tracker)
        weight_present = weight_ctx.present

    nutrition_rollup_obj: NutritionRollup | None = None
    nutrition_present = False
    if food_path is not None:
        food_ctx = _parse_food(food_path)
        nutrition_rollup_obj = _nutrition_rollup(
            food_ctx.entries, today_for_tracker, target_kcal=target_kcal
        )
        nutrition_present = food_ctx.present

    evolab_latest: EvoLabDay | None = None
    evolab_present = False
    evolab_stamina_7d_ago: int | None = None
    if evolab_ctx is not None:
        evolab_latest = evolab_ctx.latest
        evolab_present = evolab_ctx.present
        if evolab_latest is not None:
            evolab_stamina_7d_ago = _lookup_stamina_n_days_ago(
                evolab_ctx, evolab_latest.day, 7
            )

    # Replace the (frozen) assessment with one that carries all four tracker fields.
    return RecoveryAssessment(
        day=assessment.day,
        status=assessment.status,
        rising_load=assessment.rising_load,
        load_reasons=assessment.load_reasons,
        signals=assessment.signals,
        messages=assessment.messages,
        heat=heat_rollup_obj,
        heat_present=heat_present,
        strength=strength_rollup_obj,
        strength_present=strength_present,
        weight=weight_rollup_obj,
        weight_present=weight_present,
        nutrition=nutrition_rollup_obj,
        nutrition_present=nutrition_present,
        evolab=evolab_latest,
        evolab_present=evolab_present,
        evolab_stamina_7d_ago=evolab_stamina_7d_ago,
    )


def _lookup_stamina_n_days_ago(
    ctx: EvoLabContext, anchor: date, n: int
) -> int | None:
    """Return the stamina value from the row ``n`` days before ``anchor``.

    Returns ``None`` when no such row exists, or the matching row's
    ``stamina_level`` is itself None (no delta to compute).
    """
    target = anchor - timedelta(days=n)
    for row in ctx.days:
        if row.day == target:
            return row.stamina_level
    return None


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


def _fmt_tonnage(kg: float) -> str:
    """Render a strength tonnage total in kg for sub-10t loads and in tonnes
    (1 decimal) for ≥ 10t loads.

    Examples: ``0.0 -> "0 kg"``, ``9_835.0 -> "9,835 kg"``,
    ``10_000.0 -> "10.0 t"``, ``12_400.0 -> "12.4 t"``.
    """
    if kg < 10_000.0:
        return f"{int(round(kg)):,} kg"
    return f"{kg / 1000.0:.1f} t"


def _fmt_weight_delta(delta_kg: float) -> str:
    """Render a kg delta with a sign glyph + one decimal place.

    Examples: ``0.3 -> "+0.3 kg"``, ``-0.5 -> "−0.5 kg"`` (Unicode minus
    U+2212), ``0.0 -> "±0.0 kg"`` (Unicode plus-minus U+00B1). A near-zero
    value (``|delta| < 0.05``) rounds to ``±0.0 kg`` so a tiny positive
    shift doesn't render as an ambiguous ``+0.0 kg``.
    """
    if abs(delta_kg) < 0.05:
        return "±0.0 kg"
    if delta_kg > 0:
        return f"+{delta_kg:.1f} kg"
    return f"−{abs(delta_kg):.1f} kg"


def _fmt_kcal_delta(delta_kcal: int) -> str:
    """Render a kcal/day delta with a sign glyph + no decimals.

    Examples: ``110 -> "+110 kcal/day"``, ``-85 -> "−85 kcal/day"`` (Unicode
    minus U+2212), ``0 -> "±0 kcal/day"`` (Unicode plus-minus U+00B1). Mirrors
    :func:`_fmt_weight_delta` so the recovery report's tracker sections share
    one signed-delta convention.
    """
    if delta_kcal == 0:
        return "±0 kcal/day"
    if delta_kcal > 0:
        return f"+{delta_kcal} kcal/day"
    return f"−{abs(delta_kcal)} kcal/day"


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


def _render_strength_section(
    out: list[str], strength: StrengthRollup | None, strength_present: bool
) -> None:
    """Append the strength & conditioning section per the same 3-state degradation
    rule the heat section uses.

    The three states (mirroring :func:`_render_heat_section`):

    * ``strength_present is False`` OR ``strength is None`` -- strength.md is
      absent (or the caller did not thread it through). Section omitted entirely.
    * strength.md is present but parsed zero sessions ever
      (``last_session_date is None``) -- section omitted.
    * strength.md is present, all 7/14/28-day window counts are zero, but a prior
      session exists in history -- render the ``## Strength & conditioning``
      header plus a single-line lapsed nudge.
    * strength.md is present and ANY of the 7/14/28-day window counts is non-zero
      -- render the full rollup line: counts, tonnage, last-session name + age.
    """
    if not strength_present or strength is None:
        return
    # Present-but-empty: parsed file but no sessions ever.
    if strength.last_session_date is None:
        return

    any_recent = (
        strength.last_7d_count > 0
        or strength.last_14d_count > 0
        or strength.last_28d_count > 0
    )
    out.append("## Strength & conditioning\n")
    if any_recent:
        days_ago = strength.last_session_days_ago or 0
        name = strength.last_session_name or "unnamed"
        out.append(
            "- last 7 days: "
            f"{strength.last_7d_count} sessions / "
            f"{_fmt_tonnage(strength.last_7d_tonnage_kg)} tonnage · "
            f"last 14 days: {strength.last_14d_count} sessions / "
            f"{_fmt_tonnage(strength.last_14d_tonnage_kg)} tonnage · "
            f"last 28 days: {strength.last_28d_count} sessions / "
            f"{_fmt_tonnage(strength.last_28d_tonnage_kg)} tonnage · "
            f"last session: {name} ({_fmt_days_ago(days_ago)})"
        )
    else:
        # Lapsed nudge: no rollup numbers, one terse line.
        days_ago = strength.last_session_days_ago or 0
        out.append(
            f"_S&C protocol lapsed -- last session {_fmt_days_ago(days_ago)}. "
            "(No sessions in the last 28 days.)_"
        )
    out.append("")


def _render_weight_section(
    out: list[str], weight: WeightRollup | None, weight_present: bool
) -> None:
    """Append the body-weight section per the same 3-state degradation rule
    the heat + strength sections use.

    The three states (mirroring :func:`_render_strength_section`):

    * ``weight_present is False`` OR ``weight is None`` -- weight.md is absent
      (or the caller did not thread it through). Section omitted entirely.
    * weight.md is present but parsed zero entries ever
      (``latest_entry is None``) -- section omitted.
    * weight.md is present and the latest weigh-in is >14 days old
      (``days_since_last > 14``) -- render the ``## Weight`` header plus a
      one-line stale nudge.
    * weight.md is present and the latest weigh-in is within 14 days -- render
      the active rollup line: latest kg, 7d avg, 28d avg, EWMA trend, delta
      vs 28d baseline. When ``unit_mixed=True`` a trailing caveat is appended.
    """
    if not weight_present or weight is None:
        return
    if weight.latest_entry is None:
        return

    days_since = weight.days_since_last or 0
    out.append("## Weight\n")
    if days_since > 14:
        out.append(
            f"_Last weigh-in {days_since} days ago — log a current reading "
            "to keep the rollup live._"
        )
        out.append("")
        return

    # Current state: full rollup line. Be defensive about None-valued fields
    # (shouldn't happen when days_since_last <= 14, but the rollup contract
    # exposes Optionals so substitute "n/a" rather than crash).
    def _fmt_kg(v: float | None) -> str:
        return f"{v:.1f} kg" if v is not None else "n/a"

    latest_kg = _fmt_kg(weight.latest_kg)
    avg_7d = _fmt_kg(weight.avg_7d)
    avg_28d = _fmt_kg(weight.avg_28d)
    trend = _fmt_kg(weight.ewma_trend)
    delta_fmt = (
        _fmt_weight_delta(weight.delta_vs_28d)
        if weight.delta_vs_28d is not None
        else "n/a"
    )

    line = (
        f"{latest_kg} today · 7d avg {avg_7d} · 28d avg {avg_28d} · "
        f"trend {trend} · {delta_fmt} vs 28d baseline"
    )
    if weight.unit_mixed:
        line += " _(mixed kg/lb in log — normalised to kg)_"
    out.append(line)
    out.append("")


def _render_nutrition_section(
    out: list[str], nutrition: NutritionRollup | None, nutrition_present: bool
) -> None:
    """Append the nutrition section per the same 3-state degradation rule the
    heat / strength / weight sections use.

    The three states (mirroring :func:`_render_weight_section`):

    * ``nutrition_present is False`` OR ``nutrition is None`` -- food.md is
      absent (or the caller did not thread it through). Section omitted.
    * food.md is present but parsed zero entries ever (``latest_day is None``)
      -- section omitted.
    * food.md is present and the latest entry is >3 days old
      (``days_since_last > 3``) -- render the ``## Nutrition`` header plus a
      one-line stale nudge. (3 days is tighter than weight's 14 because food
      is logged daily; a 3-day gap makes the 7-day rollup unreliable.)
    * food.md is present and the latest entry is within 3 days -- render the
      active 7-day trailing rollup line: P/C/F grams + kcal + days-logged.
      When ``target_kcal`` is set AND ``deficit_surplus_7d is not None``, a
      second line appends the goal-delta.
    """
    if not nutrition_present or nutrition is None:
        return
    if nutrition.latest_day is None:
        return

    days_since = nutrition.days_since_last or 0
    out.append("## Nutrition\n")
    if days_since > 3:
        out.append(
            f"_Last food entry {days_since} days ago — log today's meals "
            "to keep the rollup live._"
        )
        out.append("")
        return

    # Current state: full 7-day rollup line.
    if nutrition.avg_7d is None:
        # Defensive: shouldn't happen when days_since_last <= 3 (an entry on
        # or after today-3 always lands inside the (today-7, today] window),
        # but the rollup contract exposes Optional so substitute a one-liner.
        out.append("_Insufficient 7-day history._")
        out.append("")
        return

    avg = nutrition.avg_7d
    line = (
        f"7d avg P:{avg.protein_g:.0f}g · C:{avg.carbs_g:.0f}g · "
        f"F:{avg.fat_g:.0f}g · cal:{avg.kcal} "
        f"({nutrition.days_logged_7d} days logged of 7)"
    )
    out.append(line)

    if nutrition.target_kcal is not None and nutrition.deficit_surplus_7d is not None:
        goal_line = (
            f"Target {nutrition.target_kcal} kcal/day · "
            f"7d Δ {_fmt_kcal_delta(nutrition.deficit_surplus_7d)}"
        )
        out.append(goal_line)

    out.append("")


def _render_evolab_section(
    out: list[str],
    evolab: EvoLabDay | None,
    evolab_present: bool,
    *,
    units: Units,
    today: date,
    stamina_7d_ago: int | None,
) -> None:
    """Append the Coros (EvoLab) section per the same 3-state degradation rule.

    The three states (mirroring :func:`_render_nutrition_section`):

    * ``evolab_present is False`` OR ``evolab is None`` -- the Coros EvoLab
      table is empty (or the caller did not thread the context through).
      Section omitted entirely.
    * ``evolab.day < today - 3 days`` -- the latest reading is stale (the user
      hasn't worn the watch). Render the ``## Coros (EvoLab)`` header plus a
      one-line nudge.
    * Current (within 3 days) -- render the block with VO2max, Stamina (+ 7d
      delta when computable), training load, threshold HR (lthr), and
      threshold pace (ltsp). Each line is omitted independently when its
      value is ``None``; if EVERY metric is None, the section falls through
      to the absent state (no section).

    Pace formatting uses :func:`runos.units.format_pace` so the user's
    ``preferences.md`` unit choice is honoured.
    """
    if not evolab_present or evolab is None:
        return

    days_since = (today - evolab.day).days
    if days_since > 3:
        out.append("## Coros (EvoLab)\n")
        out.append(
            f"_Last EvoLab reading {days_since} days ago — wear the watch to "
            "refresh the dashboard._"
        )
        out.append("")
        return

    # Current state: assemble per-metric lines, each independently omittable.
    lines: list[str] = []
    if evolab.vo2max is not None:
        lines.append(f"- VO2max: {evolab.vo2max:.1f} ml/kg/min")
    if evolab.stamina_level is not None:
        delta_fragment = ""
        if stamina_7d_ago is not None:
            delta = evolab.stamina_level - stamina_7d_ago
            if delta > 0:
                delta_fragment = f" (7d Δ +{delta})"
            elif delta < 0:
                # ASCII minus is fine here -- this is a small integer delta on
                # a count-style metric, distinct from the weight/kcal deltas
                # that use Unicode glyphs for visual parity with their headers.
                delta_fragment = f" (7d Δ -{abs(delta)})"
            else:
                delta_fragment = " (7d Δ 0)"
        lines.append(f"- Stamina: {evolab.stamina_level}{delta_fragment}")
    if evolab.training_load is not None:
        lines.append(f"- Training load (today): {evolab.training_load}")
    if evolab.lthr is not None:
        lines.append(
            f"- Threshold HR (Coros): {evolab.lthr} bpm — "
            "_cross-check vs preferences.md `threshold_hr`_"
        )
    if evolab.ltsp_s_per_km is not None:
        pace_str = format_pace(float(evolab.ltsp_s_per_km), units)
        lines.append(
            f"- Threshold pace (Coros): {pace_str} — "
            "_cross-check vs preferences.md `threshold_pace`_"
        )

    if not lines:
        # All metrics are None -- fall through to absent (per per-line rule).
        return

    out.append("## Coros (EvoLab)\n")
    out.extend(lines)
    out.append("")


def render_recovery(
    *,
    generated_on: date,
    freshness: list[dataread.SourceFreshness],
    data_range: tuple[str, str] | None,
    assessment: RecoveryAssessment,
    units: Units | None = None,
) -> str:
    """Render the dated recovery markdown report with the freshness header (ANL-03/05)."""
    # Imported lazily to avoid a circular import (report imports from analysis pkg).
    from runos.analysis.report import freshness_header

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
    _render_strength_section(out, a.strength, a.strength_present)
    _render_weight_section(out, a.weight, a.weight_present)
    _render_nutrition_section(out, a.nutrition, a.nutrition_present)
    _render_evolab_section(
        out,
        a.evolab,
        a.evolab_present,
        units=units if units is not None else Units(),
        today=generated_on,
        stamina_7d_ago=a.evolab_stamina_7d_ago,
    )

    if a.status == "insufficient":
        out.append(
            "_This report will sharpen as more Garmin wellness history accumulates "
            "(personal baselines need enough prior days) and as load history builds._\n"
        )
    return "\n".join(out)
