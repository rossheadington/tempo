"""Transition shim: re-exports races-parser names plus the legacy plan.md parser.

Race-parser names now live in :mod:`tempo.analysis.races`. The legacy plan.md
parser is kept inline because Plan 05 deletes it (and this whole shim) along
with plan.md itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tempo.analysis.races import (
    Race,
    RacesContext,
    _parse_kv,
    _parse_race_line,
    parse_distance,
    parse_goal_time,
    parse_races,
)


@dataclass(frozen=True, slots=True)
class PlanContext:
    """The parsed ``plan.md`` result (empty + ``present=False`` when missing)."""

    present: bool
    text: str = ""
    headings: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None


_PLAN_FIELD_RE = re.compile(
    r"^\s*(phase|week|focus|mileage|target|block)\s*:\s*(.+)$", re.IGNORECASE
)


def parse_plan(path: Path) -> PlanContext:
    """Parse ``plan.md`` at ``path``; missing file -> empty, ``present=False``."""
    if not path.exists():
        return PlanContext(present=False, source_path=str(path))
    text = path.read_text(encoding="utf-8")
    headings: list[str] = []
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append(stripped.lstrip("#").strip())
            continue
        m = _PLAN_FIELD_RE.match(line)
        if m:
            key = m.group(1).strip().lower()
            fields.setdefault(key, m.group(2).strip())  # first occurrence wins
    return PlanContext(
        present=True, text=text, headings=headings, fields=fields, source_path=str(path)
    )


__all__ = [
    "PlanContext",
    "Race",
    "RacesContext",
    "_parse_kv",
    "_parse_race_line",
    "parse_distance",
    "parse_goal_time",
    "parse_plan",
    "parse_races",
]
