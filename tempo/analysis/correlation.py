"""Honest correlation insight (ANL-04): predictors vs outcomes, n-gated.

Links candidate *predictors* (prior-night sleep, prior-night HRV, the day's
subjective RPE / feel) to *outcomes* (the day's training load = performance proxy,
and the day's subjective RPE). The whole point is **honesty about small n**:
correlation is data-hungry, and on a handful of paired days a Pearson r is noise
dressed up as signal. So a relationship is reported ONLY when there are at least
:data:`MIN_PAIRED_DAYS` paired observations; below that the pair emits an explicit
"insufficient data -- N paired days, need M" message rather than asserting a weak
correlation (FEATURES: "report correlations honestly incl. not-enough-data";
PITFALLS: degrade to insufficient-data rather than fabricate).

Pure stdlib only -- ``statistics`` / ``math``; no pandas / polars. Correlation is
Pearson's r over the paired, non-missing observations. We also report a coarse
two-sided significance flag using a t-statistic so a small-but-real r on enough
days reads differently from a large r on few days.

Pairing rule for "prior-night" predictors: HRV / sleep recorded for day D (Garmin
keys overnight data to the wake-up ``calendarDate`` = D, per docs/DATE_BUCKETING.md)
are paired with the SAME day's load/RPE -- i.e. "how I slept last night vs how
today went". This is the standard subjective-monitoring pairing (FEATURES sources).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

# Minimum number of paired (predictor, outcome) days before a correlation is
# reported at all. ~20-30 is the commonly-cited floor for a personal-monitoring
# correlation to be more than noise; we use 20 and document it. Configurable.
MIN_PAIRED_DAYS = 20


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    """One predictor->outcome relationship, or an insufficient-data verdict.

    ``reported`` is True only when ``n >= min_n``. When False, ``r`` / ``p`` are
    ``None`` and ``message`` explains how many more paired days are needed.
    """

    predictor: str
    outcome: str
    n: int
    min_n: int
    reported: bool
    r: float | None
    p: float | None
    strength: str  # 'insufficient' | 'negligible' | 'weak' | 'moderate' | 'strong'
    direction: str  # 'insufficient' | 'positive' | 'negative' | 'none'
    message: str


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient over paired samples.

    Returns ``None`` when there are fewer than 2 points or either variable has zero
    variance (r is undefined) -- never a fabricated value.
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    num = sum(a * b for a, b in zip(dx, dy, strict=True))
    den = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
    if den == 0:
        return None
    return num / den


def _t_pvalue(r: float, n: int) -> float | None:
    """Two-sided p-value for Pearson r via the t-distribution approximation.

    Uses ``t = r * sqrt((n-2)/(1-r^2))`` and a normal approximation to the
    two-sided tail (good enough for a coarse significance flag on n>=20). Returns
    ``None`` for |r| == 1 (degenerate) or n < 3.
    """
    if n < 3 or abs(r) >= 1.0:
        return None
    t = r * math.sqrt((n - 2) / (1.0 - r * r))
    # Normal-approximation two-sided tail via the error function.
    z = abs(t)
    p = 2.0 * (1.0 - _norm_cdf(z))
    return max(0.0, min(1.0, p))


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _strength(r: float) -> str:
    a = abs(r)
    if a < 0.1:
        return "negligible"
    if a < 0.3:
        return "weak"
    if a < 0.5:
        return "moderate"
    return "strong"


def correlate(
    predictor: str,
    outcome: str,
    pairs: list[tuple[float, float]],
    *,
    min_n: int = MIN_PAIRED_DAYS,
) -> CorrelationResult:
    """Correlate a predictor against an outcome over paired observations, n-gated.

    ``pairs`` is ``[(predictor_value, outcome_value), ...]`` already filtered to
    days where BOTH are present. If ``len(pairs) < min_n`` the result is
    ``reported=False`` with an explicit "need M, have N" message (ANL-04). Above the
    floor, Pearson r + a coarse two-sided p-value are reported with a strength /
    direction label.
    """
    n = len(pairs)
    if n < min_n:
        return CorrelationResult(
            predictor=predictor,
            outcome=outcome,
            n=n,
            min_n=min_n,
            reported=False,
            r=None,
            p=None,
            strength="insufficient",
            direction="insufficient",
            message=(
                f"{_plabel(predictor)} -> {_olabel(outcome)}: **insufficient data** -- "
                f"{n} paired day(s), need {min_n}. Keep logging; this will report once "
                "enough history accumulates."
            ),
        )

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    r = pearson(xs, ys)
    if r is None:
        return CorrelationResult(
            predictor=predictor,
            outcome=outcome,
            n=n,
            min_n=min_n,
            reported=False,
            r=None,
            p=None,
            strength="insufficient",
            direction="insufficient",
            message=(
                f"{_plabel(predictor)} -> {_olabel(outcome)}: not computable "
                f"({n} paired days but one variable has no variance)."
            ),
        )

    p = _t_pvalue(r, n)
    strength = _strength(r)
    direction = "positive" if r > 0 else ("negative" if r < 0 else "none")
    sig = ""
    if p is not None:
        sig = " (significant)" if p < 0.05 else " (not significant)"
    message = (
        f"{_plabel(predictor)} -> {_olabel(outcome)}: r = {r:+.2f} "
        f"({strength} {direction}) over {n} paired days{sig}."
    )
    return CorrelationResult(
        predictor=predictor,
        outcome=outcome,
        n=n,
        min_n=min_n,
        reported=True,
        r=r,
        p=p,
        strength=strength,
        direction=direction,
        message=message,
    )


def _plabel(predictor: str) -> str:
    return {
        "sleep": "Prior-night sleep duration",
        "hrv": "Prior-night HRV",
        "rpe": "Subjective effort (RPE)",
    }.get(predictor, predictor)


def _olabel(outcome: str) -> str:
    return {
        "load": "training load (performance proxy)",
        "rpe": "subjective effort (RPE)",
    }.get(outcome, outcome)


# ---------------------------------------------------------------------------
# DB reader: assemble paired series from daily_summary + the load series
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DailyObservation:
    """Per-day predictor/outcome values for correlation, from daily_summary + load."""

    day: str
    sleep_seconds: float | None
    hrv: float | None
    rpe: float | None
    load: float | None  # the day's computed training load (performance proxy)


def read_observations(
    conn: sqlite3.Connection, load_by_day: dict[str, float]
) -> list[DailyObservation]:
    """Read per-day predictor/outcome values from ``daily_summary`` + a load map.

    ``load_by_day`` is the computed daily load series (from the runner's PMC build),
    keyed by day -- correlation uses *computed load* as the performance proxy rather
    than re-deriving it. Days are returned in chronological order. Missing values
    are ``None`` (the pairing step drops days where a needed pair is absent).
    """
    rows = conn.execute(
        """
        SELECT day, sleep_seconds, hrv_last_night, rpe
        FROM daily_summary
        ORDER BY day
        """
    ).fetchall()
    out: list[DailyObservation] = []
    for r in rows:
        day = str(r["day"])
        out.append(
            DailyObservation(
                day=day,
                sleep_seconds=_as_float(r["sleep_seconds"]),
                hrv=_as_float(r["hrv_last_night"]),
                rpe=_as_float(r["rpe"]),
                load=load_by_day.get(day),
            )
        )
    return out


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


# The candidate (predictor, outcome) pairs we test. Documented + fixed so the
# report is stable; the n-gate decides which actually get reported.
CANDIDATE_PAIRS: list[tuple[str, str]] = [
    ("sleep", "load"),
    ("hrv", "load"),
    ("sleep", "rpe"),
    ("hrv", "rpe"),
    ("rpe", "load"),
]


def _value_for(obs: DailyObservation, key: str) -> float | None:
    if key == "sleep":
        return obs.sleep_seconds
    if key == "hrv":
        return obs.hrv
    if key == "rpe":
        return obs.rpe
    if key == "load":
        return obs.load
    return None


def build_correlations(
    observations: list[DailyObservation], *, min_n: int = MIN_PAIRED_DAYS
) -> list[CorrelationResult]:
    """Build n-gated correlation results for every candidate predictor/outcome pair.

    For each pair, keep only days where BOTH values are present (and, for the
    RPE->load and *->load pairs, only days with a positive load so true rest days
    don't dominate the relationship), then correlate with the n-gate applied.
    """
    results: list[CorrelationResult] = []
    for predictor, outcome in CANDIDATE_PAIRS:
        pairs: list[tuple[float, float]] = []
        for obs in observations:
            pv = _value_for(obs, predictor)
            ov = _value_for(obs, outcome)
            if pv is None or ov is None:
                continue
            # For load outcomes, exclude zero-load rest days: correlation should be
            # over training days (a wall of zeros would swamp the signal).
            if outcome == "load" and ov <= 0:
                continue
            pairs.append((pv, ov))
        results.append(correlate(predictor, outcome, pairs, min_n=min_n))
    return results


def render_correlations(
    *,
    generated_on,  # type: ignore[no-untyped-def]
    freshness,  # type: ignore[no-untyped-def]
    data_range: tuple[str, str] | None,
    results: list[CorrelationResult],
    min_n: int = MIN_PAIRED_DAYS,
) -> str:
    """Render the dated correlation-insight report with the freshness header (ANL-04/05)."""
    from tempo.analysis.report import freshness_header

    out = [
        freshness_header(
            report_title="Correlation Insight",
            generated_on=generated_on,
            freshness=freshness,
            data_range=data_range,
        )
    ]
    out.append("## How to read this\n")
    out.append(
        f"Relationships are only reported with at least **{min_n} paired days** of data. "
        "Below that floor a pair shows an explicit *insufficient data* note rather than "
        "asserting a weak correlation from too little history. Correlation is not "
        "causation -- treat any relationship as a prompt to investigate, not a rule.\n"
    )

    reported = [r for r in results if r.reported]
    insufficient = [r for r in results if not r.reported]

    out.append("## Reported relationships\n")
    if reported:
        for r in reported:
            flag = ""
            if r.p is not None and r.p < 0.05 and r.strength in ("moderate", "strong"):
                flag = " :sparkles:"
            out.append(f"- {r.message}{flag}")
    else:
        out.append(
            "_None yet -- not enough paired history for any predictor/outcome pair to "
            "clear the minimum-n bar. This is expected early on; keep syncing wellness "
            "and journaling._"
        )
    out.append("")

    if insufficient:
        out.append("## Not enough data yet\n")
        for r in insufficient:
            out.append(f"- {r.message}")
        out.append("")
    return "\n".join(out)
