"""Parse the user-maintained ``races.md`` context file.

This is a simple, low-friction markdown file the user maintains by hand; RunOS
reads it for analysis context (PLAN-01) -- it does NOT diff planned-vs-actual
(that is a deliberate anti-feature; the file is read for qualitative context
only).

Parsing is **lenient**: unknown lines are ignored, fields are best-effort, and a
missing file degrades gracefully to an empty result rather than raising, so the
analyses still run with no context.

Documented format (see the committed ``races.md.example``):

``races.md`` -- one race per markdown list item, ``key: value`` pairs separated by
``|`` or commas, in any order. The race name is the text before the first ``-`` or
the ``name:`` field. Recognised keys: ``date`` (ISO ``YYYY-MM-DD``), ``distance``
(``marathon`` / ``half`` / ``10k`` / ``42.195km`` / ``21100m`` ...), ``goal``
(a target time like ``3:30:00`` or free text), ``priority`` (``A``/``B``/``C``),
``result`` (free-form finish-time string for a past race, stored verbatim).

    - Berlin Marathon - date: 2026-09-27 | distance: marathon | goal: 3:15:00 | priority: A
    - Local Half - date: 2026-04-12 | distance: half | goal: 1:32:00 | priority: B | result: 1:31:48
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from runos.analysis.race import STANDARD_DISTANCES_M


@dataclass(frozen=True, slots=True)
class Race:
    """One upcoming (or past) race parsed from ``races.md``."""

    name: str
    race_date: date | None = None
    distance_m: float | None = None
    distance_label: str | None = None
    goal: str | None = None
    goal_time_s: float | None = None
    priority: str | None = None
    result: str | None = None


@dataclass(frozen=True, slots=True)
class RacesContext:
    """The parsed ``races.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    races: list[Race] = field(default_factory=list)
    source_path: str | None = None

    def upcoming(self, today: date) -> list[Race]:
        """Races dated today or later, soonest first; undated races come last."""
        dated = sorted(
            (r for r in self.races if r.race_date is not None and r.race_date >= today),
            key=lambda r: r.race_date,  # type: ignore[arg-type,return-value]
        )
        undated = [r for r in self.races if r.race_date is None]
        return dated + undated

    def completed(self, today: date) -> list[Race]:
        """Past-dated races (``date < today``), most recent first.

        Undated races are excluded entirely (they cannot be known-past). Races
        dated exactly today are also excluded (strict less-than): today's race
        is still "in progress" from the analysis's point of view.
        """
        return sorted(
            (r for r in self.races if r.race_date is not None and r.race_date < today),
            key=lambda r: r.race_date,  # type: ignore[arg-type,return-value]
            reverse=True,
        )


_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_DISTANCE_NUM_RE = re.compile(r"^([\d.]+)\s*(km|k|m|mi|mile|miles)?$", re.IGNORECASE)


def parse_goal_time(value: str) -> float | None:
    """Parse a goal time like ``3:15:00`` or ``42:30`` into seconds, else ``None``."""
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    return float(h * 3600 + minutes * 60 + seconds)


def parse_distance(value: str) -> tuple[float | None, str]:
    """Parse a distance label into ``(metres, label)``; metres is ``None`` if unknown.

    Accepts named distances (``marathon``, ``half``, ``10k``) and numeric forms
    (``42.195km``, ``21100m``, ``13.1mi``).
    """
    raw = value.strip()
    key = raw.lower().replace(" ", "")
    if key in STANDARD_DISTANCES_M:
        return STANDARD_DISTANCES_M[key], raw
    m = _DISTANCE_NUM_RE.match(key)
    if m:
        num = float(m.group(1))
        unit = (m.group(2) or "km").lower()
        if unit in ("km", "k"):
            return num * 1000.0, raw
        if unit == "m":
            return num, raw
        if unit in ("mi", "mile", "miles"):
            return num * 1609.34, raw
    return None, raw


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


def _parse_race_line(line: str) -> Race | None:
    """Parse one ``- Name - key: value | ...`` race list item, leniently."""
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

    # Name is the text before the first " - " (the kv separator), or the name: field.
    name_part, _, rest = body.partition(" - ")
    fields = _parse_kv(rest) if rest else _parse_kv(body)
    name = fields.get("name") or name_part.strip()
    if not name:
        return None

    # A real race entry must carry at least one recognised race field. This keeps
    # the lenient parser from mistaking documentation/prose bullets (which have no
    # `date:`/`distance:`/`goal:`/`priority:`/`result:` pairs) for races.
    recognised = {"date", "distance", "goal", "priority", "name", "result"}
    if not (recognised & fields.keys()):
        return None

    race_date: date | None = None
    if "date" in fields:
        try:
            race_date = date.fromisoformat(fields["date"][:10])
        except ValueError:
            race_date = None

    distance_m: float | None = None
    distance_label: str | None = None
    if "distance" in fields:
        distance_m, distance_label = parse_distance(fields["distance"])

    goal = fields.get("goal")
    goal_time_s = parse_goal_time(goal) if goal else None
    priority = fields.get("priority")
    if priority:
        priority = priority.strip().upper()[:1]
    result_str = fields.get("result")

    return Race(
        name=name,
        race_date=race_date,
        distance_m=distance_m,
        distance_label=distance_label,
        goal=goal,
        goal_time_s=goal_time_s,
        priority=priority,
        result=result_str,
    )


def parse_races(path: Path) -> RacesContext:
    """Parse ``races.md`` at ``path``; missing file -> empty, ``present=False``."""
    if not path.exists():
        return RacesContext(present=False, source_path=str(path))
    text = path.read_text(encoding="utf-8")
    races: list[Race] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped[0] in "-*+":
            race = _parse_race_line(line)
            if race is not None:
                races.append(race)
    return RacesContext(present=True, races=races, source_path=str(path))
