"""The local-date attribution (date-bucketing) rule, implemented in one place.

This is the single most correctness-critical function in the structured layer.
Every join, the ``date_spine``, and the ``daily_summary`` view depend on every
record being attributed to the *same* notion of "which local calendar day did the
athlete experience this on". If that is computed inconsistently, every downstream
analysis (load vs recovery, trends, correlations) is subtly and confidently wrong
(see ``.planning/research/PITFALLS.md`` Pitfall 6).

The authoritative rule is ``docs/DATE_BUCKETING.md``. The headline trap, and the
reason this module exists:

    Strava serialises BOTH ``start_date`` and ``start_date_local`` with a trailing
    ``Z``. ``start_date`` is a true UTC instant. ``start_date_local`` is the
    athlete's **wall-clock local time with a FAKE ``Z``** -- it is NOT UTC.

So the local day is simply the first 10 characters of ``start_date_local``. We
must NEVER parse ``start_date_local`` as UTC, and NEVER bucket on ``start_date``
(true UTC) -- either choice can shove a late-night or early-morning run onto the
wrong calendar day. Because the wall-clock value already encodes the athlete's
local time, this is also automatically correct across timezone travel and DST
transitions: the clock change does not alter the wall-clock date string.

Bucketing happens here in the transform layer (not the connector), so the rule is
pure and can be re-applied via ``tempo rederive`` with zero network calls.
"""

from __future__ import annotations

import re

# A date-only or datetime ISO string we are willing to take the first 10 chars of.
# Accepts 'YYYY-MM-DD' optionally followed by 'T'/' ' and a time, with or without
# the (possibly fake) trailing 'Z' / offset. We only ever USE the date portion.
_ISO_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class BucketingError(ValueError):
    """Raised when a value cannot be attributed to a local calendar day."""


def local_day_from_strava_local(start_date_local: str) -> str:
    """Return the athlete's local calendar day for a Strava ``start_date_local``.

    ``start_date_local`` is wall-clock local time with a *fake* trailing ``Z``.
    The local day is therefore just its date portion -- the first 10 characters.
    We deliberately do NOT parse it as a UTC instant; doing so is the classic bug
    that shifts a 23:10 run into the next day.

    >>> local_day_from_strava_local("2026-05-26T23:10:00Z")
    '2026-05-26'
    >>> local_day_from_strava_local("2026-05-26T05:30:00Z")
    '2026-05-26'

    Raises :class:`BucketingError` if the value is empty or not an ISO date.
    """
    if not start_date_local or not isinstance(start_date_local, str):
        raise BucketingError(f"empty/invalid start_date_local: {start_date_local!r}")
    match = _ISO_DATE_PREFIX.match(start_date_local.strip())
    if match is None:
        raise BucketingError(f"unparseable start_date_local: {start_date_local!r}")
    # Validate the components form a real calendar date (e.g. reject month 13).
    year, month, day = (int(g) for g in match.groups())
    _validate_calendar_date(year, month, day)
    return f"{year:04d}-{month:02d}-{day:02d}"


def local_day_from_calendar_date(calendar_date: str) -> str:
    """Return the local day for a source-provided ``calendarDate`` (e.g. Garmin).

    Garmin attributes overnight metrics (sleep/HRV) to a single ``calendarDate``
    -- the wake-up day -- specifically to remove the cross-midnight ambiguity. We
    take it verbatim. Used by Phase 6; defined here so all bucketing lives in one
    module and one rule governs every source (DATE_BUCKETING invariant 1).
    """
    if not calendar_date or not isinstance(calendar_date, str):
        raise BucketingError(f"empty/invalid calendarDate: {calendar_date!r}")
    match = _ISO_DATE_PREFIX.match(calendar_date.strip())
    if match is None:
        raise BucketingError(f"unparseable calendarDate: {calendar_date!r}")
    year, month, day = (int(g) for g in match.groups())
    _validate_calendar_date(year, month, day)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _validate_calendar_date(year: int, month: int, day: int) -> None:
    # Cheap structural validation: reject impossible months/days (e.g. 2026-13-01,
    # 2026-02-30) so a malformed payload fails loudly rather than corrupting the
    # spine. Constructing a date() is the simplest correct calendar check.
    from datetime import date

    try:
        date(year, month, day)
    except ValueError as exc:  # e.g. 2026-13-01 or 2026-02-30
        raise BucketingError(f"not a real calendar date: {year:04d}-{month:02d}-{day:02d}") from exc
