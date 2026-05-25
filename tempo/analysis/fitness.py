"""CTL / ATL / TSB (fitness / fatigue / form) and the ACWR / ramp-rate guardrail.

All of these are transforms of the **daily load series built on the zero-filled
date spine** -- rest days contribute 0 load, which is exactly why the spine exists
(LOAD-02/03; FEATURES "PMC model"). Pass loads as a contiguous, chronologically
ordered list of per-day loads (one entry per calendar day, no gaps).

The Performance Management Chart (PMC) model:

* **CTL** ("Fitness") -- EWMA of daily load over 42 days:
      CTL_today = CTL_yest + (load_today - CTL_yest) * (1/42)
* **ATL** ("Fatigue") -- EWMA over 7 days, same recurrence with 1/7.
* **TSB** ("Form/Freshness") = CTL_yesterday - ATL_yesterday.
  Using *yesterday's* CTL/ATL is the TrainingPeaks convention: today's form
  reflects the fitness/fatigue you carried into the day, before today's session.

* **ACWR** (acute:chronic workload ratio) -- coupled rolling-average method:
      ACWR = (7-day total load) / (28-day total load / 4)
  i.e. last week's load vs the rolling 4-week weekly average. Sweet spot ~0.8-1.3;
  > 1.5 is the elevated-risk "danger zone" (FEATURES / Science for Sport).

* **Ramp rate** -- CTL change over the last 7 days (CTL_today - CTL_7d_ago); a
  cleaner, less-contested companion to ACWR. > ~5-8/week is an aggressive ramp.
"""

from __future__ import annotations

from dataclasses import dataclass

CTL_DAYS = 42
ATL_DAYS = 7
ACWR_ACUTE_DAYS = 7
ACWR_CHRONIC_DAYS = 28

# ACWR interpretation thresholds (widely cited; treat as a flag, not gospel).
ACWR_SWEET_LOW = 0.8
ACWR_SWEET_HIGH = 1.3
ACWR_DANGER = 1.5

# Ramp-rate guardrail: CTL gain per week above this is an aggressive ramp.
RAMP_AGGRESSIVE = 8.0


@dataclass(frozen=True, slots=True)
class FitnessPoint:
    """One day's PMC values."""

    day: str
    load: float
    ctl: float
    atl: float
    tsb: float


def _ewma_series(loads: list[float], window_days: int, *, seed: float = 0.0) -> list[float]:
    """Exponentially-weighted moving average with the PMC ``1/N`` smoothing.

    ``ewma_today = ewma_yest + (load_today - ewma_yest) / N``. The series starts
    from ``seed`` (0.0 = cold start, the standard for a fresh athlete with no
    prior-load assumption). Returns one value per input day.
    """
    alpha = 1.0 / window_days
    out: list[float] = []
    prev = seed
    for load in loads:
        cur = prev + (load - prev) * alpha
        out.append(cur)
        prev = cur
    return out


def fitness_series(
    days: list[str],
    loads: list[float],
    *,
    ctl_seed: float = 0.0,
    atl_seed: float = 0.0,
) -> list[FitnessPoint]:
    """Compute the CTL/ATL/TSB series over a contiguous daily load series.

    ``days`` and ``loads`` are parallel, in chronological order, one entry per
    calendar day (the zero-filled spine guarantees no gaps). TSB for a day uses
    the *previous* day's CTL and ATL (TrainingPeaks convention); the first day's
    TSB uses the seeds.
    """
    if len(days) != len(loads):
        raise ValueError("days and loads must be the same length")

    ctl = _ewma_series(loads, CTL_DAYS, seed=ctl_seed)
    atl = _ewma_series(loads, ATL_DAYS, seed=atl_seed)

    points: list[FitnessPoint] = []
    prev_ctl = ctl_seed
    prev_atl = atl_seed
    for i, day in enumerate(days):
        tsb = prev_ctl - prev_atl
        points.append(FitnessPoint(day=day, load=loads[i], ctl=ctl[i], atl=atl[i], tsb=tsb))
        prev_ctl = ctl[i]
        prev_atl = atl[i]
    return points


def acwr(
    loads: list[float], *, acute_days: int = ACWR_ACUTE_DAYS, chronic_days: int = ACWR_CHRONIC_DAYS
) -> float | None:
    """Acute:chronic workload ratio over the most recent days (coupled rolling avg).

    ``(sum of last ``acute_days`` loads) / (sum of last ``chronic_days`` loads /
    (chronic_days / acute_days))``. Returns ``None`` when there is not yet a full
    chronic window of data, or when the chronic load is zero (ratio undefined) --
    never a fabricated number.
    """
    if len(loads) < chronic_days:
        return None
    acute = sum(loads[-acute_days:])
    chronic_total = sum(loads[-chronic_days:])
    if chronic_total <= 0:
        return None
    chronic_avg = chronic_total / (chronic_days / acute_days)
    return acute / chronic_avg


def ramp_rate(points: list[FitnessPoint], *, days: int = 7) -> float | None:
    """CTL change over the last ``days`` (CTL_today - CTL_N_days_ago), per week.

    Returns ``None`` if the series is too short to look back ``days``.
    """
    if len(points) <= days:
        return None
    return points[-1].ctl - points[-1 - days].ctl


@dataclass(frozen=True, slots=True)
class Guardrail:
    """The ACWR / ramp-rate guardrail verdict for the current state."""

    acwr: float | None
    ramp_rate: float | None
    acwr_flag: str  # 'ok' | 'low' | 'high' | 'danger' | 'insufficient'
    ramp_flag: str  # 'ok' | 'aggressive' | 'insufficient'
    messages: list[str]


def evaluate_guardrail(points: list[FitnessPoint]) -> Guardrail:
    """Evaluate the ACWR + ramp-rate guardrail and flag spikes outside safe ranges.

    ACWR sweet spot is 0.8-1.3; below is detraining/undertraining, 1.3-1.5 is
    elevated, > 1.5 is the danger zone. Ramp rate > ~8 CTL/week is aggressive.
    Both degrade to ``insufficient`` (no flag fabricated) when data is too short.
    """
    loads = [p.load for p in points]
    a = acwr(loads)
    r = ramp_rate(points)
    messages: list[str] = []

    if a is None:
        acwr_flag = "insufficient"
        messages.append(
            f"ACWR: insufficient data (need {ACWR_CHRONIC_DAYS} days of continuous load)."
        )
    elif a > ACWR_DANGER:
        acwr_flag = "danger"
        messages.append(f"ACWR {a:.2f} is in the danger zone (>{ACWR_DANGER}) -- spike risk.")
    elif a > ACWR_SWEET_HIGH:
        acwr_flag = "high"
        messages.append(
            f"ACWR {a:.2f} is elevated (>{ACWR_SWEET_HIGH}) -- load is climbing faster than base."
        )
    elif a < ACWR_SWEET_LOW:
        acwr_flag = "low"
        messages.append(f"ACWR {a:.2f} is below the sweet spot (<{ACWR_SWEET_LOW}) -- detraining.")
    else:
        acwr_flag = "ok"
        messages.append(f"ACWR {a:.2f} is in the sweet spot ({ACWR_SWEET_LOW}-{ACWR_SWEET_HIGH}).")

    if r is None:
        ramp_flag = "insufficient"
        messages.append("Ramp rate: insufficient data (need >7 days of continuous load).")
    elif r > RAMP_AGGRESSIVE:
        ramp_flag = "aggressive"
        messages.append(
            f"Ramp rate +{r:.1f} CTL/week is aggressive "
            f"(>{RAMP_AGGRESSIVE:.0f}) -- overreaching risk."
        )
    else:
        ramp_flag = "ok"
        sign = "+" if r >= 0 else ""
        messages.append(f"Ramp rate {sign}{r:.1f} CTL/week is within a sustainable range.")

    return Guardrail(
        acwr=a, ramp_rate=r, acwr_flag=acwr_flag, ramp_flag=ramp_flag, messages=messages
    )
