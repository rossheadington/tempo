"""Tests for the presentation-layer units formatter (``runos.units``).

The ``Units`` dataclass lives in ``runos.analysis.preferences`` (Wave 17-01).
This test module declares a local ``Units`` stub matching the locked shape
so the formatter's tests don't depend on 17-01 landing first; the formatter
only reads ``units.distance`` and ``units.pace``, so a shape-compatible stub
is sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from runos.units import (
    KM_PER_MILE,
    format_distance,
    format_pace,
    km_to_miles,
    miles_to_km,
)


@dataclass(frozen=True, slots=True)
class Units:
    distance: Literal["km", "miles"] = "km"
    pace: Literal["min_per_km", "min_per_mile"] = "min_per_km"


def test_format_distance_km_default() -> None:
    assert format_distance(12_900, Units()) == "12.9 km"


def test_format_distance_miles_converts() -> None:
    # 12.9 km / 1.609344 = 8.0156... mi -> 8.0 at precision=1
    assert format_distance(12_900, Units(distance="miles")) == "8.0 mi"


def test_format_distance_precision_argument() -> None:
    assert format_distance(12_880, Units(), precision=2) == "12.88 km"
    # 12880 m -> 12.88 km -> 8.0033... mi -> 8.00 at precision=2
    assert format_distance(12_880, Units(distance="miles"), precision=2) == "8.00 mi"


def test_format_distance_none_returns_dash() -> None:
    assert format_distance(None, Units()) == "–"
    assert format_distance(-5, Units()) == "–"


def test_format_pace_km_default() -> None:
    assert format_pace(270, Units()) == "4:30 /km"


def test_format_pace_mile_converts_correctly_at_known_pivot() -> None:
    # The locked pivot from CONTEXT.md: 240 s/km == 4:00 /km AND
    # 240 * 1.609344 = 386.24 s/mi -> rounds to 386 s -> 6:26 /mi.
    assert format_pace(240, Units()) == "4:00 /km"
    assert format_pace(240, Units(pace="min_per_mile")) == "6:26 /mi"


def test_format_pace_seconds_rollover_handled() -> None:
    # 269.6 s/km rounds to 270 s -> must render as 4:30, never 4:90.
    assert format_pace(269.6, Units()) == "4:30 /km"
    # Exactly 300 s/km -> 5:00, not 4:60.
    assert format_pace(300, Units()) == "5:00 /km"


def test_format_pace_none_or_zero_returns_dash() -> None:
    assert format_pace(None, Units()) == "–"
    assert format_pace(0, Units()) == "–"
    assert format_pace(-1, Units()) == "–"
    assert format_pace(float("nan"), Units()) == "–"


def test_km_to_miles_roundtrip() -> None:
    for km in (1.0, 5.0, 10.0, 21.0975, 42.195):
        assert miles_to_km(km_to_miles(km)) == km
    # Conversion constant sanity check.
    assert KM_PER_MILE == 1.609344
