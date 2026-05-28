"""Presentation-layer unit conversion + formatting helpers.

Storage everywhere is SI (metres for distance, seconds-per-km for pace, m/s
for speed). The user's preferred display units (kilometres vs. miles,
min/km vs. min/mile) live in their ``preferences.md`` and are parsed into a
``Units`` dataclass in :mod:`tempo.analysis.preferences`. This module turns
those SI values into human-readable strings at the render boundary.

Rules:

- Pace is rounded to the NEAREST second (not floored). After rounding,
  ``MM:SS`` where ``SS`` would equal 60 carries cleanly into the next
  minute (e.g. 269.6 s/km rounds to 270 s -> ``4:30 /km``, not ``4:90``).
- Distance is rounded to ``precision`` decimal places (default 1).
- Bad inputs -- ``None``, ``NaN``, ``<= 0`` for pace, ``< 0`` for
  distance -- render as ``"-"`` (U+2013 EN DASH) rather than raising.
- Conversion constant ``KM_PER_MILE = 1.609344`` is the exact NIST value.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from tempo.analysis.preferences import Units

KM_PER_MILE: Final[float] = 1.609344
"""Exact NIST conversion factor: 1 international mile = 1.609344 km."""

_EN_DASH: Final[str] = "–"


def km_to_miles(km: float) -> float:
    """Convert kilometres to international miles."""
    return km / KM_PER_MILE


def miles_to_km(mi: float) -> float:
    """Convert international miles to kilometres."""
    return mi * KM_PER_MILE


def s_per_km_to_s_per_mile(s_per_km: float) -> float:
    """Convert seconds-per-km pace to seconds-per-mile pace."""
    return s_per_km * KM_PER_MILE


def format_distance(
    metres: float | None,
    units: Units,
    *,
    precision: int = 1,
) -> str:
    """Format ``metres`` for display in the user's preferred units.

    Returns e.g. ``"12.9 km"`` or ``"8.0 mi"``. Returns ``"-"`` (en-dash)
    for ``None`` or negative values.
    """
    if metres is None or metres < 0 or (isinstance(metres, float) and math.isnan(metres)):
        return _EN_DASH

    km = metres / 1000.0
    if units.distance == "miles":
        value = km_to_miles(km)
        suffix = "mi"
    else:
        value = km
        suffix = "km"
    return f"{value:.{precision}f} {suffix}"


def format_pace(s_per_km: float | None, units: Units) -> str:
    """Format pace (canonical seconds-per-km) in the user's preferred units.

    Returns e.g. ``"4:30 /km"`` or ``"7:14 /mi"``. Returns ``"-"`` (en-dash)
    for ``None``, ``<= 0``, or ``NaN`` input.

    Rounding is to the NEAREST second; if rounding produces ``SS == 60``
    the minute carries (e.g. ``4:60 -> 5:00``).
    """
    if (
        s_per_km is None
        or (isinstance(s_per_km, float) and math.isnan(s_per_km))
        or s_per_km <= 0
    ):
        return _EN_DASH

    if units.pace == "min_per_mile":
        total_s = s_per_km_to_s_per_mile(s_per_km)
        suffix = "/mi"
    else:
        total_s = float(s_per_km)
        suffix = "/km"

    rounded = int(round(total_s))
    minutes, seconds = divmod(rounded, 60)
    return f"{minutes}:{seconds:02d} {suffix}"
