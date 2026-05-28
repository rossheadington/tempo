"""The validated journal-entry service -- the only way subjective rows are written.

This module is the trustworthy boundary required by JRNL-01/02 and ARCHITECTURE
Pattern 5: Claude reads freely but mutates the store ONLY through
:func:`add_entry`, which validates every field, resolves the linked activity, and
inserts via parameterised SQL. There is deliberately no free-form-SQL path.

Activity-resolution rule (date + sport)
---------------------------------------
Given a local ``day`` and an optional ``sport``, :func:`resolve_activity` looks at
the structured ``activity`` rows on that day:

* **0 matches** -> no link. The entry is a rest-day / non-activity reflection;
  ``activity_id`` is ``None``. (Valid: rest days get journaled too.)
* **exactly 1 match** -> link to it automatically.
* **many matches** -> ambiguous. The caller must disambiguate by passing an
  explicit ``activity_id`` (which must be one of the day's activities). Without
  one we raise :class:`MultipleActivitiesError` listing the candidates rather
  than silently guessing -- guessing would undermine "trustworthy structured
  signal". (An explicit ``activity_id`` always wins and skips resolution.)

``sport`` matching is case-insensitive and also treats a leading "trail"/prefix
loosely is NOT done -- we match the stored ``activity.sport`` exactly (case-fold)
to keep resolution predictable; pass the sport as Strava reports it (e.g. "Run",
"TrailRun"). If ``sport`` is omitted, all activities on the day are candidates.

sRPE (JRNL-03)
--------------
``sRPE = RPE x duration_minutes``. Duration is taken, in priority order, from:

1. an explicit ``duration_min`` argument (always wins -- e.g. cross-training with
   no Strava activity, or a correction);
2. the resolved activity's ``moving_s`` (else ``elapsed_s``) converted to minutes.

If neither yields a positive duration, ``srpe`` is ``None`` (we never invent a
load), but the entry is still recorded with its RPE/feel/notes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


class JournalError(ValueError):
    """A journal entry failed validation or activity resolution."""


class MultipleActivitiesError(JournalError):
    """Several same-day (same-sport) activities matched and no id was given."""

    def __init__(self, day: str, sport: str | None, candidates: list[ActivityMatch]) -> None:
        self.day = day
        self.sport = sport
        self.candidates = candidates
        ids = ", ".join(f"{c.activity_id} ({c.sport}, {c.label})" for c in candidates)
        sport_part = f" sport={sport!r}" if sport else ""
        super().__init__(
            f"{len(candidates)} activities match day={day}{sport_part}; "
            f"pass an explicit --activity-id to disambiguate. Candidates: {ids}"
        )


@dataclass(frozen=True, slots=True)
class ActivityMatch:
    """A candidate activity for resolution, with the fields the link/sRPE need."""

    activity_id: int
    day: str
    sport: str | None
    moving_s: int | None
    elapsed_s: int | None
    name: str | None

    @property
    def label(self) -> str:
        """A short human label for disambiguation messages."""
        return self.name or "unnamed"

    def duration_min(self) -> float | None:
        """Duration in minutes from moving time (else elapsed time), if positive."""
        for seconds in (self.moving_s, self.elapsed_s):
            if seconds is not None and seconds > 0:
                return float(seconds) / 60.0
        return None


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A persisted journal row (returned by :func:`add_entry` / :func:`list_entries`)."""

    id: int
    day: str
    activity_id: int | None
    rpe: int
    feel: str | None
    notes: str | None
    sport: str | None
    duration_min: float | None
    srpe: float | None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

RPE_MIN = 1
RPE_MAX = 10


def _validate_rpe(rpe: object) -> int:
    """Coerce/validate RPE to an int in 1..10, raising :class:`JournalError`.

    Rejects ``None``, non-integers (including floats with a fractional part and
    non-numeric strings), and out-of-range values (0, 11, negatives).
    """
    if rpe is None:
        raise JournalError("rpe is required (an integer 1-10)")
    if isinstance(rpe, bool):  # bool is an int subclass -- reject explicitly
        raise JournalError("rpe must be an integer 1-10, not a boolean")
    if isinstance(rpe, float):
        if not rpe.is_integer():
            raise JournalError(f"rpe must be a whole number 1-10, got {rpe}")
        rpe = int(rpe)
    if isinstance(rpe, str):
        text = rpe.strip()
        try:
            rpe = int(text)
        except ValueError as exc:
            raise JournalError(f"rpe must be an integer 1-10, got {rpe!r}") from exc
    if not isinstance(rpe, int):
        raise JournalError(f"rpe must be an integer 1-10, got {type(rpe).__name__}")
    if not (RPE_MIN <= rpe <= RPE_MAX):
        raise JournalError(f"rpe must be between {RPE_MIN} and {RPE_MAX}, got {rpe}")
    return rpe


def _validate_duration(duration_min: float | int | None) -> float | None:
    """Validate an explicit duration in minutes: must be positive when given."""
    if duration_min is None:
        return None
    if isinstance(duration_min, bool):
        raise JournalError("duration_min must be a number, not a boolean")
    try:
        value = float(duration_min)
    except (TypeError, ValueError) as exc:
        raise JournalError(f"duration_min must be a number, got {duration_min!r}") from exc
    if value <= 0:
        raise JournalError(f"duration_min must be positive, got {value}")
    return value


def _clean_text(value: str | None) -> str | None:
    """Trim a free-text field; empty/whitespace-only becomes ``None``."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def compute_srpe(rpe: int, duration_min: float | None) -> float | None:
    """sRPE = RPE x duration_minutes, or ``None`` when no positive duration exists."""
    if duration_min is None or duration_min <= 0:
        return None
    return float(rpe) * float(duration_min)


# ---------------------------------------------------------------------------
# Activity resolution (date + sport)
# ---------------------------------------------------------------------------


def _activities_on_day(
    conn: sqlite3.Connection, day: str, sport: str | None
) -> list[ActivityMatch]:
    """Return candidate activities on ``day`` (optionally filtered by sport)."""
    rows = conn.execute(
        """
        SELECT activity_id, day, sport, moving_s, elapsed_s, name
        FROM activity
        WHERE day = ?
        ORDER BY start_local, activity_id
        """,
        (day,),
    ).fetchall()
    matches = [
        ActivityMatch(
            activity_id=int(r["activity_id"]),
            day=str(r["day"]),
            sport=r["sport"],
            moving_s=r["moving_s"],
            elapsed_s=r["elapsed_s"],
            name=r["name"],
        )
        for r in rows
    ]
    if sport is not None:
        wanted = sport.strip().casefold()
        matches = [m for m in matches if (m.sport or "").casefold() == wanted]
    return matches


def _fetch_activity(conn: sqlite3.Connection, activity_id: int) -> ActivityMatch | None:
    row = conn.execute(
        """
        SELECT activity_id, day, sport, moving_s, elapsed_s, name
        FROM activity WHERE activity_id = ?
        """,
        (activity_id,),
    ).fetchone()
    if row is None:
        return None
    return ActivityMatch(
        activity_id=int(row["activity_id"]),
        day=str(row["day"]),
        sport=row["sport"],
        moving_s=row["moving_s"],
        elapsed_s=row["elapsed_s"],
        name=row["name"],
    )


def resolve_activity(
    conn: sqlite3.Connection,
    *,
    day: str,
    sport: str | None = None,
    activity_id: int | None = None,
) -> ActivityMatch | None:
    """Resolve which activity (if any) a journal entry links to.

    Rules (see module docstring): an explicit ``activity_id`` always wins (and must
    exist); otherwise we look at the day's activities filtered by ``sport`` and
    return the sole match, ``None`` for zero matches, or raise
    :class:`MultipleActivitiesError` when several match.
    """
    if activity_id is not None:
        match = _fetch_activity(conn, activity_id)
        if match is None:
            raise JournalError(f"no activity with id {activity_id} exists")
        return match

    candidates = _activities_on_day(conn, day, sport)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    raise MultipleActivitiesError(day, sport, candidates)


# ---------------------------------------------------------------------------
# The validated entrypoint
# ---------------------------------------------------------------------------


def _ensure_spine_day(conn: sqlite3.Connection, day: str) -> None:
    """Ensure the spine has a row for ``day`` so the FK + daily_summary hold.

    A journal entry can land on a day with no activity (a rest-day reflection);
    that day might not yet exist in ``date_spine``. We insert a metadata row for
    it (idempotently) so the FK is satisfied and the entry appears in
    ``daily_summary`` without dropping or duplicating any spine day. Imported
    lazily to avoid a heavier import at module load.
    """
    from tempo.transforms.spine import ensure_days

    ensure_days(conn, [day])


def add_entry(
    conn: sqlite3.Connection,
    *,
    day: str,
    rpe: int,
    feel: str | None = None,
    notes: str | None = None,
    sport: str | None = None,
    activity_id: int | None = None,
    duration_min: float | None = None,
) -> JournalEntry:
    """Validate, resolve, compute sRPE, and insert one journal entry (JRNL-01/02/03).

    This is the single validated boundary. ``day`` is the resolved LOCAL calendar
    date (``YYYY-MM-DD``). RPE is validated to an integer 1..10. The activity is
    resolved by date + sport (or an explicit ``activity_id``); a linked activity
    supplies the duration for sRPE unless an explicit ``duration_min`` overrides
    it. Everything is written via parameterised SQL inside a transaction.

    Returns the persisted :class:`JournalEntry` (with its new id and computed
    sRPE). Raises :class:`JournalError` (or :class:`MultipleActivitiesError`) on
    any validation/resolution failure -- nothing is written in that case.
    """
    day = _validate_day(day)
    valid_rpe = _validate_rpe(rpe)
    explicit_duration = _validate_duration(duration_min)
    feel = _clean_text(feel)
    notes = _clean_text(notes)
    sport = _clean_text(sport)

    match = resolve_activity(conn, day=day, sport=sport, activity_id=activity_id)

    resolved_activity_id = match.activity_id if match is not None else None
    # Sport recorded on the entry: prefer the resolved activity's sport, else the
    # declared sport, so daily context is preserved even for unlinked entries.
    resolved_sport = (match.sport if match is not None else None) or sport

    # Duration for sRPE: explicit arg wins; else the linked activity's duration.
    duration = explicit_duration
    if duration is None and match is not None:
        duration = match.duration_min()

    srpe = compute_srpe(valid_rpe, duration)

    with conn:  # transaction: ensure spine day + insert atomically
        _ensure_spine_day(conn, day)
        cur = conn.execute(
            """
            INSERT INTO journal
                (day, activity_id, rpe, feel, notes, sport, duration_min, srpe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                resolved_activity_id,
                valid_rpe,
                feel,
                notes,
                resolved_sport,
                duration,
                srpe,
            ),
        )
        new_id = int(cur.lastrowid)  # type: ignore[arg-type]

    return JournalEntry(
        id=new_id,
        day=day,
        activity_id=resolved_activity_id,
        rpe=valid_rpe,
        feel=feel,
        notes=notes,
        sport=resolved_sport,
        duration_min=duration,
        srpe=srpe,
    )


def _validate_day(day: str) -> str:
    """Validate ``day`` is an ISO ``YYYY-MM-DD`` local date string."""
    from datetime import date

    if not day or not isinstance(day, str):
        raise JournalError("day is required (an ISO YYYY-MM-DD local date)")
    try:
        return date.fromisoformat(day.strip()).isoformat()
    except ValueError as exc:
        raise JournalError(f"day must be an ISO YYYY-MM-DD date, got {day!r}") from exc


def link_orphan_entries(
    conn: sqlite3.Connection, *, day: str | None = None
) -> int:
    """Link orphan journal entries (``activity_id IS NULL``) to matching activities.

    Walks every journal row where ``activity_id`` is NULL (optionally filtered to
    a single ``day``) and looks for an activity on that day matching the entry's
    sport. Applies the same 0/1/many convention as :func:`resolve_activity`:

    * **0 matches** -- leave the entry orphaned (it really is a rest-day reflection
      or the activity hasn't arrived yet; future sync will retry).
    * **exactly 1 match** -- UPDATE the entry to set ``activity_id``. If the entry
      has no ``duration_min`` set, also recompute ``srpe`` using the now-linked
      activity's ``moving_s`` (else ``elapsed_s``), so the sRPE load track for
      the day reflects the linked activity duration.
    * **many matches** -- skip (caller must disambiguate manually with
      ``--activity-id`` via :func:`add_entry`). Never guesses on ambiguity.

    Designed to run as a post-transform hook: after every ``tempo transform``,
    newly-arrived activities get auto-linked to any journal entries the user
    captured via the Telegram bot before Strava had synced. Idempotent --
    re-running it on a fully-linked DB is a no-op.

    Args:
        conn: SQLite connection (caller owns the transaction; this function
            wraps its writes in ``with conn:`` so a partial failure rolls back).
        day: If given (ISO ``YYYY-MM-DD``), only consider orphans on that day.
            ``None`` (default) sweeps every orphan in the table.

    Returns:
        The number of entries linked (0 if nothing was eligible).
    """
    where = "activity_id IS NULL"
    params: tuple[object, ...] = ()
    if day is not None:
        where += " AND day = ?"
        params = (day,)
    orphans = conn.execute(
        f"SELECT id, day, sport, rpe, duration_min FROM journal WHERE {where}",
        params,
    ).fetchall()

    linked = 0
    with conn:
        for row in orphans:
            entry_id = int(row["id"])
            entry_day = str(row["day"])
            entry_sport = row["sport"]
            entry_rpe = int(row["rpe"])
            entry_dur = row["duration_min"]
            # Only attempt if the entry has a sport hint -- without it the
            # resolution would be "any activity on the day", which we treat as
            # too lossy to auto-apply (mirrors resolve_activity's ambiguity rule).
            if not entry_sport:
                continue
            candidates = _activities_on_day(conn, entry_day, entry_sport)
            if len(candidates) != 1:
                continue
            match = candidates[0]
            # Recompute sRPE if the entry didn't carry an explicit duration --
            # the newly-linked activity's moving_s (or elapsed_s) is now the
            # authoritative duration for this entry's load track (JRNL-03).
            if entry_dur is None:
                derived_min = None
                if match.moving_s and match.moving_s > 0:
                    derived_min = float(match.moving_s) / 60.0
                elif match.elapsed_s and match.elapsed_s > 0:
                    derived_min = float(match.elapsed_s) / 60.0
                new_srpe = compute_srpe(entry_rpe, derived_min)
                conn.execute(
                    "UPDATE journal SET activity_id = ?, srpe = ? WHERE id = ?",
                    (match.activity_id, new_srpe, entry_id),
                )
            else:
                conn.execute(
                    "UPDATE journal SET activity_id = ? WHERE id = ?",
                    (match.activity_id, entry_id),
                )
            linked += 1
    return linked


def list_entries(conn: sqlite3.Connection, *, limit: int | None = None) -> list[JournalEntry]:
    """Return journal entries, most recent first (by day then id)."""
    sql = (
        "SELECT id, day, activity_id, rpe, feel, notes, sport, duration_min, srpe, created_at "
        "FROM journal ORDER BY day DESC, id DESC"
    )
    params: tuple[object, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    return [
        JournalEntry(
            id=int(r["id"]),
            day=str(r["day"]),
            activity_id=int(r["activity_id"]) if r["activity_id"] is not None else None,
            rpe=int(r["rpe"]),
            feel=r["feel"],
            notes=r["notes"],
            sport=r["sport"],
            duration_min=r["duration_min"],
            srpe=r["srpe"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
