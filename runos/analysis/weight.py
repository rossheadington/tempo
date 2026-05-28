"""Parse the user-maintained ``weight.md`` body-weight log + roll entries into
windows with an EWMA trend.

``weight.md`` is an append-only markdown log the user maintains by hand
alongside ``races.md`` / ``heat.md`` / ``strength.md``; RunOS reads it for
weight context in the recovery report (WEIGHT-01/02/03). Parsing is
**lenient**: malformed lines are skipped (their 1-indexed line numbers land
in ``WeightContext.malformed_lines``), out-of-range weights are rejected as a
typo guard, and a missing file degrades gracefully to ``present=False``
rather than raising -- the recovery analysis still runs with no weight
context.

Documented format (see the committed ``weight.md.example``):

One entry per `` - `` bullet line. The leading ``# Weight log`` header and
any other heading / blank line / prose line is silently ignored. The
``lbs`` unit normalises to ``lb``; missing unit defaults to ``kg``. Notes
are everything after the first ``| notes:`` on the line, verbatim.

    # Weight log

    - 2026-05-28: 72.4 kg | notes: post-run, pre-breakfast
    - 2026-05-27: 72.8 kg
    - 2026-05-26: 73.1 kg | notes: post-strength session
    - 2026-05-25: 72.9 kg
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WeightEntry:
    """One weigh-in entry parsed from ``weight.md``.

    ``weight`` is the verbatim numeric value -- NOT pre-converted. ``unit``
    is the lowercase canonical form (``"kg"`` or ``"lb"``; source ``lbs`` is
    normalised to ``"lb"``). Conversion to kg happens at rollup time via
    :func:`_to_kg`.
    """

    date: date
    weight: float
    unit: str
    notes: str | None
    source_line: int


@dataclass(frozen=True, slots=True)
class WeightContext:
    """The parsed ``weight.md`` result.

    ``entries`` is deduplicated by date (latest-wins on duplicates) and
    sorted ascending by date. ``path`` is ``None`` when the file was missing.
    """

    present: bool
    entries: tuple[WeightEntry, ...]
    path: Path | None
    malformed_lines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WeightRollup:
    """Rolling-window summary of body-weight entries for the recovery report.

    Windows are **left-open right-closed** intervals ``(today - N, today]``
    so a same-day weigh-in always counts. ``today - N`` itself is excluded.
    Every numeric output is kg-normalised (any ``lb`` entry is converted via
    ``* 0.453592``); ``latest_entry`` preserves the original unit.
    """

    latest_entry: WeightEntry | None
    latest_kg: float | None
    days_since_last: int | None
    avg_7d: float | None
    avg_28d: float | None
    ewma_trend: float | None
    delta_vs_28d: float | None
    unit_mixed: bool


# Entry line: `- YYYY-MM-DD: <weight> [<unit>] [| notes: <free text>]`
# Notes capture is greedy from the first `| notes:` substring; subsequent
# `|` pipes are part of the notes verbatim.
_ENTRY_RE = re.compile(
    r"^-\s+(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s*:\s*"
    r"(?P<weight>\d+(?:\.\d+)?)"
    r"(?:\s+(?P<unit>[A-Za-z]+))?"
    r"(?:\s*\|\s*notes:\s*(?P<notes>.*?))?"
    r"\s*$"
)

_LB_TO_KG = 0.453592
_MIN_KG = 20.0
_MAX_KG = 500.0


def _to_kg(weight: float, unit: str) -> float:
    """Convert a weight + unit to kilograms.

    ``"kg"`` returns the value unchanged; ``"lb"`` multiplies by
    ``0.453592``; anything else returns the value unchanged (defensive --
    :func:`_parse_entry_line` should never produce other units, but the
    rollup MUST NOT crash on a stray value)."""
    if unit == "lb":
        return weight * _LB_TO_KG
    return weight


def _parse_entry_line(line: str, line_no: int) -> WeightEntry | None:
    """Parse one ``- YYYY-MM-DD: <weight> [<unit>] [| notes: ...]`` bullet.

    Returns ``None`` on malformed input (caller records ``line_no`` in
    ``WeightContext.malformed_lines``). Never raises.

    Rejections:
      * date that fails :meth:`date.fromisoformat`
      * weight that fails :func:`float`
      * unit that isn't ``kg`` / ``lb`` / ``lbs`` (case-insensitive)
      * weight whose kg-equivalent is <20 or >500 (typo guard)
    """
    m = _ENTRY_RE.match(line.strip())
    if m is None:
        return None

    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None

    try:
        weight = float(m.group("weight"))
    except ValueError:
        return None

    raw_unit = m.group("unit")
    if raw_unit is None:
        unit = "kg"
    else:
        lowered = raw_unit.lower()
        if lowered == "kg":
            unit = "kg"
        elif lowered in ("lb", "lbs"):
            unit = "lb"
        else:
            return None

    kg_equiv = _to_kg(weight, unit)
    if not (_MIN_KG < kg_equiv < _MAX_KG):
        return None

    notes_raw = m.group("notes")
    notes = notes_raw.rstrip() if notes_raw is not None else None
    if notes == "":
        notes = None

    return WeightEntry(
        date=d, weight=weight, unit=unit, notes=notes, source_line=line_no
    )


def parse_weight(path: Path) -> WeightContext:
    """Parse ``weight.md`` at ``path``; missing file -> ``present=False``.

    Lenient: malformed entry bullets are recorded in ``malformed_lines``;
    blank lines and headers (``#``-prefixed) are silently ignored;
    non-bullet prose lines are silently ignored (NOT recorded as malformed).
    Never raises.
    """
    if not path.exists():
        return WeightContext(
            present=False, entries=(), path=None, malformed_lines=()
        )

    # Strip BOM if present; tolerate trailing whitespace per-line.
    text = path.read_text(encoding="utf-8-sig")

    # Latest-wins dedup: dict keyed by date, later entries overwrite earlier.
    by_date: dict[date, WeightEntry] = {}
    malformed: list[int] = []

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            # Non-bullet prose -- silently ignored.
            continue

        entry = _parse_entry_line(raw_line, line_no)
        if entry is None:
            malformed.append(line_no)
            continue
        by_date[entry.date] = entry

    deduped_sorted = tuple(
        sorted(by_date.values(), key=lambda e: e.date)
    )
    return WeightContext(
        present=True,
        entries=deduped_sorted,
        path=path,
        malformed_lines=tuple(malformed),
    )


def weight_rollup(
    entries: tuple[WeightEntry, ...] | list[WeightEntry], today: date
) -> WeightRollup:
    """Roll ``entries`` into 7d/28d averages + an EWMA trend.

    Windows are left-open right-closed ``(today - N, today]`` so a same-day
    weigh-in always counts. Future-dated entries are defensively filtered
    out (a typo'd year shouldn't poison the rollup).

    The EWMA trend uses ``alpha=0.1``, seeded from the FIRST entry's
    kg-converted weight, iterated forward through every filtered entry in
    date order.

    Empty input -> all-None / ``unit_mixed=False`` rollup.
    """
    if not entries:
        return WeightRollup(
            latest_entry=None,
            latest_kg=None,
            days_since_last=None,
            avg_7d=None,
            avg_28d=None,
            ewma_trend=None,
            delta_vs_28d=None,
            unit_mixed=False,
        )

    # Defensive: filter out future-dated entries and sort by date ascending
    # (the parser already returns sorted, but we accept lists too).
    filtered = sorted(
        (e for e in entries if e.date <= today), key=lambda e: e.date
    )
    if not filtered:
        return WeightRollup(
            latest_entry=None,
            latest_kg=None,
            days_since_last=None,
            avg_7d=None,
            avg_28d=None,
            ewma_trend=None,
            delta_vs_28d=None,
            unit_mixed=False,
        )

    latest_entry = filtered[-1]
    latest_kg = _to_kg(latest_entry.weight, latest_entry.unit)
    days_since_last = (today - latest_entry.date).days
    unit_mixed = len({e.unit for e in filtered}) > 1

    cutoff_7 = today - timedelta(days=7)
    cutoff_28 = today - timedelta(days=28)

    window_7 = [
        _to_kg(e.weight, e.unit)
        for e in filtered
        if cutoff_7 < e.date <= today
    ]
    window_28 = [
        _to_kg(e.weight, e.unit)
        for e in filtered
        if cutoff_28 < e.date <= today
    ]

    avg_7d = sum(window_7) / len(window_7) if window_7 else None
    avg_28d = sum(window_28) / len(window_28) if window_28 else None

    # EWMA: seed from the first entry, iterate forward through every entry.
    alpha = 0.1
    trend = _to_kg(filtered[0].weight, filtered[0].unit)
    for e in filtered[1:]:
        trend = alpha * _to_kg(e.weight, e.unit) + (1 - alpha) * trend
    ewma_trend = trend

    delta_vs_28d = (
        latest_kg - avg_28d
        if (latest_kg is not None and avg_28d is not None)
        else None
    )

    return WeightRollup(
        latest_entry=latest_entry,
        latest_kg=latest_kg,
        days_since_last=days_since_last,
        avg_7d=avg_7d,
        avg_28d=avg_28d,
        ewma_trend=ewma_trend,
        delta_vs_28d=delta_vs_28d,
        unit_mixed=unit_mixed,
    )
