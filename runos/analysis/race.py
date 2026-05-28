"""Race-time prediction: Riegel and VDOT (Daniels), plus a TSB form check.

These take a recent best effort (a distance + time) and predict an equivalent
time at a target race distance, used by the race-readiness analysis (ANL-02).

* **Riegel**:  ``T2 = T1 * (D2 / D1) ** 1.06``
  Accurate within ~1-3% when the distance ratio is < 4:1; over-optimistic for big
  jumps (5K -> marathon), which we flag (FEATURES / RunnersConnect).

* **VDOT (Daniels)**: map a performance to a VO2max-like fitness number, then map
  that number back to an equivalent time at another distance. We implement
  Daniels' published formulas:

      vo2 = -4.60 + 0.182258 * v + 0.000104 * v^2          (v = m/min)
      pct = 0.8 + 0.1894393*exp(-0.012778*t) + 0.2989558*exp(-0.1932605*t)  (t = min)
      VDOT = vo2 / pct

  To predict a time at a new distance we invert: solve for the time whose
  velocity gives ``vo2 = VDOT * pct(time)`` at that distance (a short numeric
  solve, since ``pct`` depends on the unknown time).

Standard race distances are provided for convenience; any distance in metres works.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RIEGEL_EXPONENT = 1.06
# Above this distance ratio Riegel tends to over-predict; the readiness report
# downgrades confidence when extrapolating across a big jump.
RIEGEL_RELIABLE_RATIO = 4.0

# Common race distances in metres (for parsing "5K"/"marathon" etc).
STANDARD_DISTANCES_M: dict[str, float] = {
    "1500m": 1500.0,
    "1mile": 1609.34,
    "mile": 1609.34,
    "3k": 3000.0,
    "5k": 5000.0,
    "8k": 8000.0,
    "10k": 10000.0,
    "15k": 15000.0,
    "10mile": 16093.4,
    "20k": 20000.0,
    "half": 21097.5,
    "half-marathon": 21097.5,
    "halfmarathon": 21097.5,
    "marathon": 42195.0,
    "full": 42195.0,
    "50k": 50000.0,
}


def riegel_predict(known_distance_m: float, known_time_s: float, target_distance_m: float) -> float:
    """Predict the time (seconds) at ``target_distance_m`` from a known effort."""
    if known_distance_m <= 0 or known_time_s <= 0 or target_distance_m <= 0:
        raise ValueError("distances and time must be positive")
    return known_time_s * (target_distance_m / known_distance_m) ** RIEGEL_EXPONENT


def _daniels_vo2(velocity_m_per_min: float) -> float:
    """Daniels' VO2 cost of running at ``velocity`` (m/min), in ml/kg/min."""
    v = velocity_m_per_min
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def _daniels_pct_max(time_min: float) -> float:
    """Fraction of VO2max sustainable for a race lasting ``time_min`` minutes."""
    t = time_min
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot_from_performance(distance_m: float, time_s: float) -> float:
    """Compute the VDOT (Daniels VO2max-equivalent) for a race performance."""
    if distance_m <= 0 or time_s <= 0:
        raise ValueError("distance and time must be positive")
    time_min = time_s / 60.0
    velocity = distance_m / time_min  # m/min
    return _daniels_vo2(velocity) / _daniels_pct_max(time_min)


def _velocity_for_vdot_at_time(vdot: float, time_min: float) -> float:
    """Invert the Daniels VO2 cost: velocity (m/min) giving the target VO2 demand."""
    target_vo2 = vdot * _daniels_pct_max(time_min)
    # Solve 0.000104 v^2 + 0.182258 v - (4.60 + target_vo2) = 0 for v > 0.
    a = 0.000104
    b = 0.182258
    c = -(4.60 + target_vo2)
    disc = b * b - 4 * a * c
    return (-b + math.sqrt(disc)) / (2 * a)


def vdot_predict(distance_m: float, time_s: float, target_distance_m: float) -> float:
    """Predict the time (seconds) at ``target_distance_m`` using VDOT-equivalence.

    Computes the VDOT for the known effort, then finds the time at the target
    distance that is consistent with the same VDOT. Because the sustainable
    %VO2max depends on the (unknown) finish time, we fixed-point iterate -- it
    converges in a handful of steps.
    """
    vdot = vdot_from_performance(distance_m, time_s)
    # Seed with a Riegel estimate, then refine.
    time_min = riegel_predict(distance_m, time_s, target_distance_m) / 60.0
    for _ in range(50):
        velocity = _velocity_for_vdot_at_time(vdot, time_min)  # m/min
        new_time_min = target_distance_m / velocity
        if abs(new_time_min - time_min) < 1e-6:
            time_min = new_time_min
            break
        time_min = new_time_min
    return time_min * 60.0


@dataclass(frozen=True, slots=True)
class RacePrediction:
    """A predicted finish time for one target race from one known effort."""

    target_distance_m: float
    riegel_s: float
    vdot_s: float
    vdot: float
    reliable: bool  # False when extrapolating across a big distance jump
    note: str | None = None


def predict_race(
    *, known_distance_m: float, known_time_s: float, target_distance_m: float
) -> RacePrediction:
    """Predict a target-race time both ways and flag low-confidence extrapolation."""
    riegel_s = riegel_predict(known_distance_m, known_time_s, target_distance_m)
    vdot_s = vdot_predict(known_distance_m, known_time_s, target_distance_m)
    vdot = vdot_from_performance(known_distance_m, known_time_s)
    ratio = max(target_distance_m, known_distance_m) / min(target_distance_m, known_distance_m)
    reliable = ratio <= RIEGEL_RELIABLE_RATIO
    note = None
    if not reliable:
        note = (
            f"distance ratio {ratio:.1f}:1 exceeds {RIEGEL_RELIABLE_RATIO:.0f}:1 -- "
            "extrapolation is optimistic; treat as a ceiling, not a target."
        )
    return RacePrediction(
        target_distance_m=target_distance_m,
        riegel_s=riegel_s,
        vdot_s=vdot_s,
        vdot=vdot,
        reliable=reliable,
        note=note,
    )


def format_hms(seconds: float) -> str:
    """Format a duration in seconds as ``H:MM:SS`` (or ``MM:SS`` under an hour)."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_pace(seconds_per_km: float) -> str:
    """Format a pace in seconds/km as ``M:SS/km``."""
    total = int(round(seconds_per_km))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}/km"
