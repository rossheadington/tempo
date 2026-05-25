"""Per-activity training load: rTSS (pace-based) primary, hrTSS (HR) fallback.

Load is the atom every trend metric aggregates (CTL/ATL/TSB, ACWR, ramp rate).
We compute one load number per activity, **flag which method produced it**, and
when neither pace nor HR inputs are available we refuse to invent a number --
the activity is marked ``insufficient`` (LOAD-01; PITFALLS: degrade to
"insufficient data" rather than fabricate).

Formulas (see ``.planning/research/FEATURES.md``):

* **rTSS** -- running Training Stress Score, the running standard:

      IF   = threshold_pace / activity_pace          (intensity factor)
      rTSS = (duration_s * IF^2) / 3600 * 100

  Conceptually 100 = one hour at threshold pace. We express the pace-based form
  using the identity ``duration_s * NGP / FTP_pace = duration_s * IF`` and a
  second IF from ``IF = NGP / FTP_pace``; with NGP == activity pace (no
  grade-adjustment in v1) this reduces to ``duration * IF^2 / 3600 * 100``.
  Faster-than-threshold pace -> IF > 1 -> more than 100/hour, as intended.

  v1 uses ``avg_pace_s_km`` (no grade-adjusted/normalised pace yet); NGP/GAP is a
  documented future refinement (REQUIREMENTS ADV-* / research "build-vs-borrow").

* **hrTSS** -- HR-based fallback when pace/threshold is unavailable but HR is.
  Anchored on lactate-threshold HR so it stays comparable to rTSS (1 hour at
  threshold HR ~= 100):

      hrr_frac = (avg_hr - resting_hr) / (max_hr - resting_hr)   (HR reserve)
      thr_frac = (threshold_hr - resting_hr) / (max_hr - resting_hr)
      IF       = hrr_frac / thr_frac
      hrTSS    = (duration_s * IF^2) / 3600 * 100

  Using HR-reserve (Karvonen) rather than %max makes the intensity factor honest
  across athletes. ``threshold_hr`` defaults to ~0.92 * max_hr when not configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LoadMethod(StrEnum):
    """Which method produced an activity's load value (the per-day flag)."""

    RTSS = "rTSS"
    HRTSS = "hrTSS"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """The athlete-specific inputs the load formulas need.

    All optional: missing inputs simply narrow which methods are available. With
    no usable inputs at all, every activity becomes ``insufficient``.
    """

    threshold_pace_s_per_km: float | None = None
    max_hr: int | None = None
    resting_hr: int | None = None
    threshold_hr: int | None = None

    def effective_threshold_hr(self) -> float | None:
        """Threshold HR, falling back to ~0.92 * max_hr when not configured."""
        if self.threshold_hr is not None:
            return float(self.threshold_hr)
        if self.max_hr is not None:
            return 0.92 * float(self.max_hr)
        return None


@dataclass(frozen=True, slots=True)
class ActivityLoad:
    """The computed load for one activity, with the method that produced it."""

    load: float | None
    method: LoadMethod
    intensity_factor: float | None = None
    reason: str | None = None  # why it was insufficient, when applicable


def _rtss(duration_s: float, pace_s_per_km: float, threshold_pace_s_per_km: float) -> ActivityLoad:
    """rTSS from average pace and threshold pace. Pace is seconds/km (lower = faster)."""
    # IF = threshold_pace / activity_pace: a faster (smaller) pace -> IF > 1.
    intensity = threshold_pace_s_per_km / pace_s_per_km
    load = (duration_s * intensity * intensity) / 3600.0 * 100.0
    return ActivityLoad(load=load, method=LoadMethod.RTSS, intensity_factor=intensity)


def _hrtss(
    duration_s: float,
    avg_hr: float,
    *,
    max_hr: float,
    resting_hr: float,
    threshold_hr: float,
) -> ActivityLoad:
    """hrTSS from average HR using HR-reserve, anchored on threshold HR."""
    reserve = max_hr - resting_hr
    hrr_frac = (avg_hr - resting_hr) / reserve
    thr_frac = (threshold_hr - resting_hr) / reserve
    intensity = hrr_frac / thr_frac
    load = (duration_s * intensity * intensity) / 3600.0 * 100.0
    return ActivityLoad(load=load, method=LoadMethod.HRTSS, intensity_factor=intensity)


def compute_activity_load(
    *,
    duration_s: int | float | None,
    avg_pace_s_per_km: float | None,
    avg_hr: float | None,
    config: LoadConfig,
) -> ActivityLoad:
    """Compute one activity's load, preferring rTSS, falling back to hrTSS.

    Decision order (LOAD-01):

    1. **rTSS** if a configured threshold pace and a usable activity pace exist.
    2. **hrTSS** if HR data and the HR config (max + resting, threshold HR or a
       max to estimate it from) exist.
    3. Otherwise **insufficient** -- ``load`` is ``None`` and a reason is given;
       analyses treat the day as zero-load-with-low-confidence, never inventing a
       value.

    Duration must be positive; without it no method can produce a load.
    """
    if duration_s is None or duration_s <= 0:
        return ActivityLoad(
            load=None, method=LoadMethod.INSUFFICIENT, reason="no positive duration"
        )

    duration = float(duration_s)

    # --- Primary: rTSS (pace-based) ---
    if (
        config.threshold_pace_s_per_km is not None
        and config.threshold_pace_s_per_km > 0
        and avg_pace_s_per_km is not None
        and avg_pace_s_per_km > 0
    ):
        return _rtss(duration, avg_pace_s_per_km, config.threshold_pace_s_per_km)

    # --- Fallback: hrTSS (HR-based) ---
    threshold_hr = config.effective_threshold_hr()
    if (
        avg_hr is not None
        and avg_hr > 0
        and config.max_hr is not None
        and config.resting_hr is not None
        and threshold_hr is not None
        and config.max_hr > config.resting_hr
        and threshold_hr > config.resting_hr
    ):
        return _hrtss(
            duration,
            float(avg_hr),
            max_hr=float(config.max_hr),
            resting_hr=float(config.resting_hr),
            threshold_hr=threshold_hr,
        )

    # --- Neither available: do not fabricate load ---
    if config.threshold_pace_s_per_km is None and config.max_hr is None:
        reason = "no threshold pace or HR config"
    elif avg_pace_s_per_km is None and avg_hr is None:
        reason = "no pace or HR data on activity"
    else:
        reason = "insufficient inputs for rTSS or hrTSS"
    return ActivityLoad(load=None, method=LoadMethod.INSUFFICIENT, reason=reason)


@dataclass(frozen=True, slots=True)
class DayLoad:
    """Aggregated load for one calendar day across its activities.

    ``load`` is the sum of per-activity loads (rest days = 0.0). ``method`` is the
    dominant flag for the day: ``rTSS`` if any activity used pace, else ``hrTSS``
    if any used HR, else ``insufficient`` if activities existed but none could be
    scored, else ``rest`` for a true rest day with no activities.
    """

    day: str
    load: float
    method: str
    n_activities: int
    n_insufficient: int


REST = "rest"


def aggregate_day_load(day: str, activity_loads: list[ActivityLoad]) -> DayLoad:
    """Roll a day's per-activity loads into a single :class:`DayLoad`.

    Insufficient activities contribute 0 to the load total but are counted so a
    report can flag low-confidence days. A day with no activities is a rest day
    (load 0, method ``rest``) -- exactly the zero-fill the EWMA series needs.
    """
    if not activity_loads:
        return DayLoad(day=day, load=0.0, method=REST, n_activities=0, n_insufficient=0)

    total = sum(al.load for al in activity_loads if al.load is not None)
    n_insufficient = sum(1 for al in activity_loads if al.method is LoadMethod.INSUFFICIENT)
    methods = {al.method for al in activity_loads}

    if LoadMethod.RTSS in methods:
        method = LoadMethod.RTSS.value
    elif LoadMethod.HRTSS in methods:
        method = LoadMethod.HRTSS.value
    else:
        method = LoadMethod.INSUFFICIENT.value

    return DayLoad(
        day=day,
        load=total,
        method=method,
        n_activities=len(activity_loads),
        n_insufficient=n_insufficient,
    )
