"""Parse the user-maintained ``heat.md`` heat-adaptation log + roll it into windows.

``heat.md`` is an append-only markdown list the user maintains by hand alongside
``races.md``; Tempo reads it for heat-adaptation context in the recovery report
(TRACK-04/05). Parsing is **lenient**: unknown keys are ignored, malformed lines
are skipped, and a missing file degrades gracefully to an empty result rather
than raising -- the recovery analysis still runs with no heat context.

Documented format (see the committed ``heat.md.example``):

One session per markdown list item, ``key: value`` pairs separated by ``|`` (or
commas), in any order. The leading text before the first `` - `` may carry the
session date directly. Recognised keys: ``date`` (ISO ``YYYY-MM-DD``), ``type``
(free-form: ``sauna`` / ``hot-bath`` / ``hot-run`` / ``steam-room`` ...),
``duration_min`` (minutes; integer or decimal), ``temp_c``, ``hr_avg``,
``notes``.

    - 2026-05-26 - type: sauna | duration_min: 20 | temp_c: 85 | hr_avg: 105 | notes: post-run

Unlike ``races.md`` (where an undated race is still kept), an entry without any
parseable date is **dropped** here -- the rolling-window rollup is date-keyed
and an undated session has nowhere to land.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HeatSession:
    """One heat-adaptation session parsed from ``heat.md``.

    ``date`` is required (entries without a parseable date are dropped at parse
    time, not stored): the rolling-window rollup is date-keyed.
    """

    date: date
    type: str | None = None
    duration_min: float | None = None
    temp_c: float | None = None
    hr_avg: float | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class HeatContext:
    """The parsed ``heat.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    sessions: list[HeatSession] = field(default_factory=list)
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class HeatRollup:
    """Rolling-window summary of heat-adaptation sessions for the recovery report.

    All counts are inclusive of ``today`` and look back ``N`` calendar days
    (so a 7-day window is the closed interval ``[today - 6, today]``: today
    itself plus the six preceding days -- seven dates total). Minutes are summed
    from ``session.duration_min`` and skip sessions without a parseable duration
    (they still contribute to ``*_count`` and to ``last_session_*``).
    """

    today: date
    last_7d_count: int
    last_7d_minutes: float
    last_14d_count: int
    last_14d_minutes: float
    last_28d_count: int
    last_28d_minutes: float
    last_session_date: date | None
    last_session_days_ago: int | None


def _parse_kv(segment: str) -> dict[str, str]:
    """Split a ``key: value | key: value`` (or comma-separated) segment into a dict."""
    fields: dict[str, str] = {}
    for part in re.split(r"[|,]", segment):
        if ":" not in part:
            continue
        key, _, val = part.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key and val:
            fields[key] = val
    return fields


_RECOGNISED_KEYS = {"date", "type", "duration_min", "temp_c", "hr_avg", "notes"}


def _parse_heat_line(line: str) -> HeatSession | None:
    """Parse one ``- YYYY-MM-DD - key: value | ...`` session list item, leniently.

    Returns ``None`` when the line does not carry at least one recognised key,
    or when no parseable date is available (neither a ``date:`` field nor a
    leading ISO-date prefix). This is the inverse of ``_parse_race_line`` --
    a race with no date is still kept, but a heat session with no date is
    dropped (it has nowhere to land in the rolling-window rollup).
    """
    body = line.lstrip()
    for bullet in ("- ", "* ", "+ "):
        if body.startswith(bullet):
            body = body[len(bullet) :]
            break
    else:
        return None
    body = body.strip()
    if not body:
        return None

    # The leading text (before the first ` - ` separator) is the optional
    # date prefix, mirroring how races.md takes the race name from this slot.
    name_part, _, rest = body.partition(" - ")
    fields = _parse_kv(rest) if rest else _parse_kv(body)

    if not (_RECOGNISED_KEYS & fields.keys()):
        return None

    # Resolve the session date: prefer the explicit `date:` field; on failure
    # OR if absent, fall back to the leading-date prefix. If neither yields
    # a valid date, drop the session entirely.
    session_date: date | None = None
    if "date" in fields:
        try:
            session_date = date.fromisoformat(fields["date"][:10])
        except ValueError:
            session_date = None
    if session_date is None:
        try:
            session_date = date.fromisoformat(name_part.strip()[:10])
        except ValueError:
            session_date = None
    if session_date is None:
        return None

    duration_min: float | None = None
    if "duration_min" in fields:
        try:
            duration_min = float(fields["duration_min"])
        except ValueError:
            duration_min = None

    temp_c: float | None = None
    if "temp_c" in fields:
        try:
            temp_c = float(fields["temp_c"])
        except ValueError:
            temp_c = None

    hr_avg: float | None = None
    if "hr_avg" in fields:
        try:
            hr_avg = float(fields["hr_avg"])
        except ValueError:
            hr_avg = None

    return HeatSession(
        date=session_date,
        type=fields.get("type"),
        duration_min=duration_min,
        temp_c=temp_c,
        hr_avg=hr_avg,
        notes=fields.get("notes"),
    )


def parse_heat(path: Path) -> HeatContext:
    """Parse ``heat.md`` at ``path``; missing file -> empty, ``present=False``."""
    if not path.exists():
        return HeatContext(present=False, source_path=str(path))
    text = path.read_text(encoding="utf-8")
    sessions: list[HeatSession] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "-*+":
            session = _parse_heat_line(line)
            if session is not None:
                sessions.append(session)
    return HeatContext(present=True, sessions=sessions, source_path=str(path))


def heat_rollup(sessions: list[HeatSession], today: date) -> HeatRollup:
    """Roll ``sessions`` into closed-interval 7/14/28-day count + minutes windows.

    Windows are inclusive of both endpoints (a 7-day window is the 7 dates
    ``[today-6 .. today]``). Future-dated sessions are defensively filtered
    from all windows -- a typo'd year shouldn't poison the rollup. Sessions
    with ``duration_min=None`` still count toward ``*_count`` but contribute 0
    to ``*_minutes``.
    """
    if not sessions:
        return HeatRollup(
            today=today,
            last_7d_count=0,
            last_7d_minutes=0.0,
            last_14d_count=0,
            last_14d_minutes=0.0,
            last_28d_count=0,
            last_28d_minutes=0.0,
            last_session_date=None,
            last_session_days_ago=None,
        )

    cutoff_7 = today - timedelta(days=6)
    cutoff_14 = today - timedelta(days=13)
    cutoff_28 = today - timedelta(days=27)

    in_past = [s for s in sessions if s.date <= today]

    def window(cutoff: date) -> tuple[int, float]:
        n = 0
        mins = 0.0
        for s in in_past:
            if s.date >= cutoff:
                n += 1
                if s.duration_min is not None:
                    mins += s.duration_min
        return n, mins

    c7, m7 = window(cutoff_7)
    c14, m14 = window(cutoff_14)
    c28, m28 = window(cutoff_28)

    last_date = max((s.date for s in in_past), default=None)
    days_ago = (today - last_date).days if last_date is not None else None

    return HeatRollup(
        today=today,
        last_7d_count=c7,
        last_7d_minutes=m7,
        last_14d_count=c14,
        last_14d_minutes=m14,
        last_28d_count=c28,
        last_28d_minutes=m28,
        last_session_date=last_date,
        last_session_days_ago=days_ago,
    )
