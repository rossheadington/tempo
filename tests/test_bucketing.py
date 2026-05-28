"""Date-bucketing correctness (STORE-05) -- the four required edge cases.

These tests pin the single most correctness-critical rule in the structured
layer: which LOCAL calendar day a record is attributed to. They cover, per the
Phase-3 success criteria and docs/DATE_BUCKETING.md:

  1. late-night (11pm) activity
  2. timezone travel
  3. DST transition (spring-forward and fall-back)
  4. Strava's fake-``Z`` ``start_date_local`` (NOT UTC)

The pure rule is exercised directly here; the same rule applied through the full
raw->structured transform is verified in test_transforms.py.
"""

from __future__ import annotations

import pytest

from runos.transforms.bucketing import (
    BucketingError,
    local_day_from_calendar_date,
    local_day_from_strava_local,
)

# ---- (4) Strava's fake-Z: start_date_local is wall-clock, NOT UTC ----------


def test_fake_z_is_treated_as_wall_clock_not_utc() -> None:
    # 23:10 local with a fake trailing Z. Parsing the Z as UTC and converting
    # would risk shifting the day; the rule must take the wall-clock date as-is.
    assert local_day_from_strava_local("2026-05-26T23:10:00Z") == "2026-05-26"


def test_fake_z_early_morning_stays_local() -> None:
    assert local_day_from_strava_local("2026-05-26T05:30:00Z") == "2026-05-26"


def test_fake_z_with_explicit_offset_still_uses_local_date() -> None:
    # Even if a payload carried a real offset, we bucket by the local wall-clock
    # date portion -- never re-projecting to UTC.
    assert local_day_from_strava_local("2026-05-26T23:10:00+09:00") == "2026-05-26"


# ---- (1) Late-night 11pm activity: local day != UTC day -------------------


def test_late_night_run_buckets_to_local_day_not_utc_day() -> None:
    # Athlete in UTC+2: ran 23:30 LOCAL on the 26th. True UTC instant is 21:30 on
    # the 26th here, but the point is we must use the LOCAL date regardless.
    local = "2026-05-26T23:30:00Z"  # fake-Z wall clock
    assert local_day_from_strava_local(local) == "2026-05-26"


def test_late_night_run_where_utc_rolls_to_next_day() -> None:
    # Local 23:30 on the 26th in UTC+2 -> true UTC 21:30 26th; but a +2 zone at
    # 23:30 maps some evenings to the NEXT UTC day. The local rule is immune: the
    # wall-clock date is the 26th, full stop. (Bucketing on start_date/UTC would
    # be the bug this guards against.)
    local = "2026-05-26T23:30:00Z"
    utc_that_would_be_wrong = "2026-05-27T00:30:00Z"  # what UTC bucketing might pick
    assert local_day_from_strava_local(local) == "2026-05-26"
    # And to make the contrast explicit: never use the UTC instant for bucketing.
    assert local_day_from_strava_local(local) != utc_that_would_be_wrong[:10]


# ---- (2) Timezone travel -------------------------------------------------


def test_timezone_travel_attributes_to_local_wall_clock_day() -> None:
    # A run started 11pm while in a UTC+9 zone. start_date_local reflects the
    # local wall clock there; we attribute it to that local day.
    far_east_evening = "2026-03-10T23:00:00Z"  # wall-clock in UTC+9
    assert local_day_from_strava_local(far_east_evening) == "2026-03-10"


def test_timezone_travel_pair_lands_on_their_respective_local_days() -> None:
    # Two runs ~near in real time but in very different zones land on the local
    # day each was experienced on -- not collapsed onto one UTC day.
    tokyo_morning = "2026-03-11T07:00:00Z"  # local wall clock UTC+9
    london_late = "2026-03-10T23:30:00Z"  # local wall clock UTC+0, earlier local day
    assert local_day_from_strava_local(tokyo_morning) == "2026-03-11"
    assert local_day_from_strava_local(london_late) == "2026-03-10"


# ---- (3) DST transition --------------------------------------------------


def test_dst_spring_forward_local_date_unaffected() -> None:
    # US spring-forward 2026-03-08 (clocks jump 02:00 -> 03:00). A run that day
    # buckets to that local date; the missing hour doesn't change the date.
    assert local_day_from_strava_local("2026-03-08T06:30:00Z") == "2026-03-08"


def test_dst_fall_back_local_date_unaffected() -> None:
    # US fall-back 2026-11-01 (clocks 02:00 -> 01:00, an hour repeats). The local
    # wall-clock date is still the 1st regardless of which 01:30 it was.
    assert local_day_from_strava_local("2026-11-01T01:30:00Z") == "2026-11-01"


def test_dst_late_night_on_transition_night_stays_on_local_day() -> None:
    # A 23:50 run on a DST-transition night must not slip to the next day.
    assert local_day_from_strava_local("2026-03-08T23:50:00Z") == "2026-03-08"


# ---- Garmin calendarDate parity (one rule, all sources) -------------------


def test_garmin_calendar_date_taken_verbatim() -> None:
    # Overnight wellness is keyed by the source's wake-up calendarDate.
    assert local_day_from_calendar_date("2026-05-26") == "2026-05-26"
    assert local_day_from_calendar_date("2026-05-26T00:00:00") == "2026-05-26"


# ---- Defensive parsing ----------------------------------------------------


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-01T00:00:00Z", "2026-02-30T10:00:00Z"])
def test_invalid_inputs_raise(bad: str) -> None:
    with pytest.raises(BucketingError):
        local_day_from_strava_local(bad)


def test_none_input_raises() -> None:
    with pytest.raises(BucketingError):
        local_day_from_strava_local(None)  # type: ignore[arg-type]
