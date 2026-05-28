"""Parse the user-maintained ``strength.md`` strength-and-conditioning log +
roll sessions into windows.

``strength.md`` is an append-only markdown log the user maintains by hand
alongside ``races.md`` / ``heat.md``; Tempo reads it for S&C context in the
recovery report (SC-01/02). Parsing is **lenient**: unknown metadata keys are
ignored, malformed set tokens are skipped, sessions with an unparseable date
are dropped, and a missing file degrades gracefully to ``present=False``
rather than raising -- the recovery analysis still runs with no S&C context.

Documented format (see the committed ``strength.md.example``):

One session per ``## ``-header block. The header carries the date (required),
optional start time, and optional name. Metadata lines (``rest: M:SS``,
``notes: ...``) sit between the header and the first exercise bullet.
Exercises are list items: ``- <Name> [(Equipment)] [[GROUP]]: <set>[, ...]``.
Set tokens come in three flavours: weighted (``55x8``), timed hold (``1:00``),
and bodyweight reps (bare integer).

    ## 2026-05-26 18:19 — Lower body
    rest: 1:30
    notes: pogos + SLGB supersetted

    - Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8
    - Pogos [A]: 15, 15, 15
    - Plank: 1:00, 1:00, 1:00, 0:30
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StrengthSet:
    """One set within an exercise. Exactly one of the three flavours applies:
    weighted (``weight_kg`` + ``reps``), timed hold (``duration_s``), or
    bodyweight reps (``reps`` only)."""

    weight_kg: float | None = None
    reps: int | None = None
    duration_s: int | None = None


@dataclass(frozen=True, slots=True)
class StrengthExercise:
    """One exercise within a session. ``sets`` is a tuple for frozen safety."""

    name: str
    equipment: str | None = None
    superset_group: str | None = None
    sets: tuple[StrengthSet, ...] = ()


@dataclass(frozen=True, slots=True)
class StrengthSession:
    """One strength session parsed from ``strength.md``.

    ``date`` is required: sessions whose ``##`` header carries an unparseable
    date are dropped at parse time (the rolling-window rollup is date-keyed).
    """

    date: date
    start_local: str | None = None
    name: str | None = None
    rest_s: int | None = None
    notes: str | None = None
    exercises: tuple[StrengthExercise, ...] = ()


@dataclass(frozen=True, slots=True)
class StrengthContext:
    """The parsed ``strength.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    sessions: list[StrengthSession] = field(default_factory=list)
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class StrengthRollup:
    """Rolling-window summary of strength sessions for the recovery report.

    Windows are inclusive closed intervals mirroring ``heat_rollup``: a 7-day
    window is ``[today-6 .. today]`` (seven dates total). Tonnage is summed
    from weighted sets only (``weight_kg * reps``); bodyweight + timed sets
    contribute 0 to tonnage but DO count toward ``*_count``.
    """

    today: date
    last_7d_count: int
    last_7d_tonnage_kg: float
    last_14d_count: int
    last_14d_tonnage_kg: float
    last_28d_count: int
    last_28d_tonnage_kg: float
    last_session_date: date | None
    last_session_days_ago: int | None
    last_session_name: str | None


# Set-token regexes. Compiled at module load.
_WEIGHTED_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+)\s*$"
)
_TIMED_RE = re.compile(r"^\s*(\d+):(\d{2})\s*$")
_BODYWEIGHT_RE = re.compile(r"^\s*(\d+)\s*$")

# Session header: `## YYYY-MM-DD [HH:MM] [[— | -] Name]`
# Separator (em-dash or hyphen) is optional — `## 2026-05-26 Lower body` is
# accepted as date + name with no separator.
_HEADER_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})"
    r"(?:\s+(\d{1,2}:\d{2}))?"
    r"(?:\s+(?:(?:—|-)\s*)?(.+?))?"
    r"\s*$"
)

# Exercise bullet: `- Name [(Equipment)] [[GROUP]]: sets`
# Match the LHS (everything before the first `:`) for name/equipment/group.
_EQUIPMENT_RE = re.compile(r"\(([^)]+)\)")
_GROUP_RE = re.compile(r"\[([^\]]+)\]")


def _parse_set(token: str) -> StrengthSet | None:
    """Parse one set token. Returns ``None`` for anything that doesn't match
    one of the three documented flavours (weighted ``WxR`` / timed ``M:SS`` /
    bodyweight bare integer)."""
    if not token or not token.strip():
        return None

    m = _WEIGHTED_RE.match(token)
    if m:
        try:
            return StrengthSet(
                weight_kg=float(m.group(1)),
                reps=int(m.group(2)),
                duration_s=None,
            )
        except ValueError:
            return None

    m = _TIMED_RE.match(token)
    if m:
        try:
            mins = int(m.group(1))
            secs = int(m.group(2))
            return StrengthSet(
                weight_kg=None, reps=None, duration_s=mins * 60 + secs
            )
        except ValueError:
            return None

    m = _BODYWEIGHT_RE.match(token)
    if m:
        try:
            return StrengthSet(
                weight_kg=None, reps=int(m.group(1)), duration_s=None
            )
        except ValueError:
            return None

    return None


def _parse_rest(value: str) -> int | None:
    """Parse a ``rest: M:SS`` value into seconds. Returns ``None`` on malformed."""
    m = _TIMED_RE.match(value)
    if not m:
        return None
    try:
        return int(m.group(1)) * 60 + int(m.group(2))
    except ValueError:
        return None


def _parse_header(line: str) -> tuple[date, str | None, str | None] | None:
    """Parse a ``## ...`` session header. Returns ``(date, start_local, name)``
    or ``None`` if the date is unparseable."""
    m = _HEADER_RE.match(line.rstrip())
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    start_local = m.group(2)
    name = m.group(3).strip() if m.group(3) else None
    return d, start_local, name or None


def _parse_exercise_bullet(body: str) -> StrengthExercise | None:
    """Parse a ``- Name [(Equipment)] [[GROUP]]: sets`` bullet body (with the
    leading ``- `` already stripped). Returns ``None`` if there's no ``:``
    separator (lenient -- a bullet with no sets is dropped)."""
    if ":" not in body:
        return None
    lhs, _, rhs = body.partition(":")

    # Extract optional (Equipment) and [GROUP] annotations from the LHS.
    equipment_match = _EQUIPMENT_RE.search(lhs)
    equipment = equipment_match.group(1).strip() if equipment_match else None
    group_match = _GROUP_RE.search(lhs)
    superset_group = group_match.group(1).strip() if group_match else None

    # Strip the annotations off to leave just the name.
    name = _EQUIPMENT_RE.sub("", lhs)
    name = _GROUP_RE.sub("", name)
    name = name.strip()
    if not name:
        return None

    sets: list[StrengthSet] = []
    for token in rhs.split(","):
        parsed = _parse_set(token)
        if parsed is not None:
            sets.append(parsed)

    return StrengthExercise(
        name=name,
        equipment=equipment,
        superset_group=superset_group,
        sets=tuple(sets),
    )


def parse_strength(path: Path) -> StrengthContext:
    """Parse ``strength.md`` at ``path``; missing file -> empty, ``present=False``."""
    if not path.exists():
        return StrengthContext(present=False, source_path=str(path))

    text = path.read_text(encoding="utf-8")
    sessions: list[StrengthSession] = []

    # Per-session accumulators. ``current`` is ``None`` between sessions or
    # while in skip-mode after an unparseable-date header.
    current_date: date | None = None
    current_start: str | None = None
    current_name: str | None = None
    current_rest_s: int | None = None
    current_notes: str | None = None
    current_exercises: list[StrengthExercise] = []
    in_skip_mode = False

    def flush() -> None:
        nonlocal current_date, current_start, current_name
        nonlocal current_rest_s, current_notes, current_exercises
        if current_date is None:
            return
        sessions.append(
            StrengthSession(
                date=current_date,
                start_local=current_start,
                name=current_name,
                rest_s=current_rest_s,
                notes=current_notes,
                exercises=tuple(current_exercises),
            )
        )
        current_date = None
        current_start = None
        current_name = None
        current_rest_s = None
        current_notes = None
        current_exercises = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            continue

        # Session header `## YYYY-MM-DD ...` closes any in-flight session.
        if stripped.startswith("## "):
            flush()
            in_skip_mode = False
            parsed = _parse_header(stripped)
            if parsed is None:
                # Unparseable date — drop the entire session block until the
                # next valid `## ` header.
                in_skip_mode = True
                continue
            current_date, current_start, current_name = parsed
            continue

        # Top-level `# ...` heading (not `##`) — skip outright.
        if stripped.startswith("#"):
            continue

        if in_skip_mode or current_date is None:
            # Stray content outside any session — skip.
            continue

        # Exercise bullet?
        if stripped.startswith("- "):
            exercise = _parse_exercise_bullet(stripped[2:])
            if exercise is not None:
                current_exercises.append(exercise)
            continue

        # Metadata `key: value`?
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "rest":
                current_rest_s = _parse_rest(value)
            elif key == "notes":
                current_notes = value or None
            # Unknown keys silently ignored.
            continue

        # Prose / anything else — skip.

    flush()
    return StrengthContext(
        present=True, sessions=sessions, source_path=str(path)
    )


def _session_tonnage(session: StrengthSession) -> float:
    """Sum of ``weight_kg * reps`` over every weighted set in a session.

    Bodyweight + timed sets contribute 0 (their ``weight_kg`` is None)."""
    total = 0.0
    for ex in session.exercises:
        for st in ex.sets:
            if st.weight_kg is not None and st.reps is not None:
                total += st.weight_kg * st.reps
    return total


def strength_rollup(
    sessions: list[StrengthSession], today: date
) -> StrengthRollup:
    """Roll ``sessions`` into closed-interval 7/14/28-day count + tonnage windows.

    Windows are inclusive of both endpoints (7-day = ``[today-6 .. today]``).
    Future-dated sessions are defensively filtered. Tonnage = sum of
    ``weight_kg * reps`` over weighted sets only; bodyweight + timed sets
    contribute 0 to tonnage but DO count toward ``*_count``.
    """
    if not sessions:
        return StrengthRollup(
            today=today,
            last_7d_count=0,
            last_7d_tonnage_kg=0.0,
            last_14d_count=0,
            last_14d_tonnage_kg=0.0,
            last_28d_count=0,
            last_28d_tonnage_kg=0.0,
            last_session_date=None,
            last_session_days_ago=None,
            last_session_name=None,
        )

    cutoff_7 = today - timedelta(days=6)
    cutoff_14 = today - timedelta(days=13)
    cutoff_28 = today - timedelta(days=27)

    in_past = [s for s in sessions if s.date <= today]

    def window(cutoff: date) -> tuple[int, float]:
        n = 0
        tonnage = 0.0
        for s in in_past:
            if s.date >= cutoff:
                n += 1
                tonnage += _session_tonnage(s)
        return n, tonnage

    c7, t7 = window(cutoff_7)
    c14, t14 = window(cutoff_14)
    c28, t28 = window(cutoff_28)

    if in_past:
        last_session = max(in_past, key=lambda s: s.date)
        last_date: date | None = last_session.date
        days_ago: int | None = (today - last_date).days
        last_name: str | None = last_session.name or "unnamed"
    else:
        last_date = None
        days_ago = None
        last_name = None

    return StrengthRollup(
        today=today,
        last_7d_count=c7,
        last_7d_tonnage_kg=t7,
        last_14d_count=c14,
        last_14d_tonnage_kg=t14,
        last_28d_count=c28,
        last_28d_tonnage_kg=t28,
        last_session_date=last_date,
        last_session_days_ago=days_ago,
        last_session_name=last_name,
    )
