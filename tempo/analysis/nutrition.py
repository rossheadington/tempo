"""Parse the user-maintained ``food.md`` nutrition log + roll entries into
windows with optional kcal-goal tracking.

``food.md`` is an append-only markdown log the user maintains by hand
alongside ``races.md`` / ``heat.md`` / ``strength.md`` / ``weight.md``;
Tempo reads it for nutrition context in the recovery report (NUTR-03/04).
Parsing is **lenient**: malformed lines are skipped (their 1-indexed line
numbers land in ``FoodContext.malformed_lines``), missing required macro
keys → entry skipped, missing file degrades to ``present=False`` rather
than raising -- the recovery analysis still runs with no nutrition
context.

Two interchangeable input formats share ONE parser. Both produce identical
``FoodEntry`` records (modulo ``date`` / ``source_line`` / ``source_format``),
so the choice between them is purely ergonomic.

**Format A — Inline** (one entry per top-level bullet):

    - 2026-05-28 breakfast: 80g rolled oats | p:13 c:54 f:6 cal:303
    - 2026-05-28 lunch: chicken salad bowl | p:38 c:22 f:18 cal:404

**Format B — Block-per-meal** (header + nested bullets inherit date + meal):

    ## 2026-05-28 breakfast
    - 80g rolled oats: p:13 c:54 f:6 cal:303
    - 1 banana: p:1.3 c:27 f:0.4 cal:105

Macro keys (``p:`` / ``c:`` / ``f:`` / ``cal:``) are unordered and
case-insensitive on parse; unknown keys (``fibre:5``, ``sodium:120``) are
silently ignored. ``cal:`` accepts rounding tolerance (``cal:303.4`` →
``303``). Missing ANY of the four required keys → entry skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FoodEntry:
    """One food log entry parsed from ``food.md``.

    ``meal_name`` is lowercased + stripped (or ``None`` if absent / empty).
    ``food_label`` is the verbatim opaque string ("80g rolled oats",
    "1 banana") — the parser does NOT split quantity from food name.
    ``source_format`` is ``"inline"`` or ``"block"``; ``source_line`` is
    1-indexed and (for Format B) points at the bullet line, NOT the header.
    """

    date: date
    meal_name: str | None
    food_label: str
    protein_g: float
    carbs_g: float
    fat_g: float
    kcal: int
    source_line: int
    source_format: str


@dataclass(frozen=True, slots=True)
class MealBlock:
    """All entries from one ``## YYYY-MM-DD <meal>`` block (Format B only).

    Format-A entries do NOT produce a ``MealBlock``.
    """

    date: date
    meal_name: str | None
    entries: tuple[FoodEntry, ...]


@dataclass(frozen=True, slots=True)
class FoodContext:
    """The parsed ``food.md`` result.

    ``entries`` is dedup'd by ``(date, meal_name, food_label)`` (latest-wins
    on file order) and sorted by ``(date, source_line)``. ``blocks``
    contains only Format-B blocks. ``path`` is ``None`` when the file was
    missing.
    """

    present: bool
    entries: tuple[FoodEntry, ...]
    blocks: tuple[MealBlock, ...]
    path: Path | None
    malformed_lines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DailyNutrition:
    """Per-day sum + macro-percentage view of ``FoodEntry``s on one date.

    Macro percentages use the kcal-share formula, computed AFTER summation:
    ``(protein_g * 4) / kcal * 100``, etc. If ``kcal == 0`` (degenerate)
    all three percentages are ``0.0`` (no ``ZeroDivisionError``).
    """

    date: date
    protein_g: float
    carbs_g: float
    fat_g: float
    kcal: int
    macro_pct_protein: float
    macro_pct_carbs: float
    macro_pct_fat: float
    entry_count: int


@dataclass(frozen=True, slots=True)
class NutritionRollup:
    """Rolling-window summary of nutrition entries for the recovery report.

    Windows are **left-open right-closed** intervals ``(today - N, today]``
    so a same-day log always counts. Averages are computed across days
    WITH entries only (a day without entries is NOT in the denominator);
    ``days_logged_7d`` exposes the day-count for transparency. ``avg_7d``
    is a ``DailyNutrition`` whose ``date`` is set to ``today`` and whose
    ``macro_pct_*`` fields are recomputed from the averaged grams (NOT
    averaged from per-day percentages). ``avg_28d_kcal`` is a scalar int;
    deeper 28-day trend lives in a future version.

    ``target_kcal`` is sourced from the caller (Phase 17: the CLI loads it
    from ``preferences.md`` via ``PreferencesContext.nutrition.target_kcal``);
    ``deficit_surplus_7d`` is ``avg_7d.kcal - target_kcal`` when both are
    available, else ``None``.
    """

    today: date
    latest_day: DailyNutrition | None
    days_since_last: int | None
    avg_7d: DailyNutrition | None
    days_logged_7d: int
    avg_28d_kcal: int | None
    target_kcal: int | None
    deficit_surplus_7d: int | None


# Macro key/value extractor. Matches `<key>:<num>` with optional whitespace
# around the colon; case-insensitive on the key (`P:13` works). Numeric value
# is `\d+(\.\d+)?` so floats parse cleanly; we coerce later.
_MACRO_RE = re.compile(
    r"(?P<key>[A-Za-z]+)\s*:\s*(?P<val>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Required macro keys (lowercased canonical form).
_REQUIRED_MACRO_KEYS = ("p", "c", "f", "cal")

# Format-A inline bullet:
#   - YYYY-MM-DD <meal>: <food> | <macros>
# `meal` is non-greedy up to the first `:`; `food` is non-greedy up to the
# first `|`. Trailing whitespace tolerated.
_INLINE_RE = re.compile(
    r"^-\s+(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s+(?P<meal>[^:]+?)"
    r"\s*:\s*"
    r"(?P<food>.+?)"
    r"\s*\|\s*"
    r"(?P<macros>.+?)"
    r"\s*$"
)

# Format-B block header: `## YYYY-MM-DD [<meal>]`.
_BLOCK_HEADER_RE = re.compile(
    r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:\s+(?P<meal>.+?))?"
    r"\s*$"
)

# Format-B nested bullet: `- <food>: <macros>`.
_BLOCK_BULLET_RE = re.compile(
    r"^-\s+(?P<food>.+?)"
    r"\s*:\s*"
    r"(?P<macros>.+?)"
    r"\s*$"
)


def _parse_macros(macros_text: str) -> tuple[float, float, float, int] | None:
    """Extract p / c / f / cal from a free-form macro fragment.

    Order is FREE; keys are case-insensitive; unknown keys (``fibre:5``)
    silently ignored. Returns ``None`` if ANY of p/c/f/cal is missing or
    any float / int parse fails. ``cal:`` is rounded via
    ``int(round(float(...)))`` so ``cal:303.4`` → ``303``.
    """
    found: dict[str, str] = {}
    for m in _MACRO_RE.finditer(macros_text):
        key = m.group("key").lower()
        if key in _REQUIRED_MACRO_KEYS:
            found[key] = m.group("val")
        # Unknown keys silently ignored.

    if not all(k in found for k in _REQUIRED_MACRO_KEYS):
        return None

    try:
        protein = float(found["p"])
        carbs = float(found["c"])
        fat = float(found["f"])
        kcal = int(round(float(found["cal"])))
    except (ValueError, TypeError):
        return None

    return protein, carbs, fat, kcal


def _parse_inline_entry(line: str, line_no: int) -> FoodEntry | None:
    """Parse one Format-A bullet. Returns ``None`` on malformed input."""
    m = _INLINE_RE.match(line.strip())
    if m is None:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    meal_raw = m.group("meal").strip().lower()
    meal_name: str | None = meal_raw if meal_raw else None
    food_label = m.group("food").strip()
    if not food_label:
        return None
    macros = _parse_macros(m.group("macros"))
    if macros is None:
        return None
    protein, carbs, fat, kcal = macros
    return FoodEntry(
        date=d,
        meal_name=meal_name,
        food_label=food_label,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        kcal=kcal,
        source_line=line_no,
        source_format="inline",
    )


def _parse_block_header(line: str) -> tuple[date, str | None] | None:
    """Parse a ``## YYYY-MM-DD [<meal>]`` header.

    Returns ``(date, meal_name_or_None)`` on success or ``None`` if the
    date doesn't parse (the whole block then gets skipped by the caller).
    """
    m = _BLOCK_HEADER_RE.match(line.strip())
    if m is None:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    meal_raw = m.group("meal")
    if meal_raw is None:
        return d, None
    meal_clean = meal_raw.strip().lower()
    return d, (meal_clean if meal_clean else None)


def _parse_block_bullet(
    line: str, line_no: int, block_date: date, block_meal: str | None
) -> FoodEntry | None:
    """Parse a Format-B nested bullet (inherits date + meal from block header)."""
    m = _BLOCK_BULLET_RE.match(line.strip())
    if m is None:
        return None
    food_label = m.group("food").strip()
    if not food_label:
        return None
    macros = _parse_macros(m.group("macros"))
    if macros is None:
        return None
    protein, carbs, fat, kcal = macros
    return FoodEntry(
        date=block_date,
        meal_name=block_meal,
        food_label=food_label,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        kcal=kcal,
        source_line=line_no,
        source_format="block",
    )


def parse_food(path: Path) -> FoodContext:
    """Parse ``food.md`` at ``path``; missing file -> ``present=False``.

    Lenient: malformed bullets are recorded in ``malformed_lines``;
    blank lines, ``#``-prefixed comment headers, and non-bullet prose are
    silently ignored; a block with a malformed ``##`` header is skipped
    entirely (header + nested bullets all land in ``malformed_lines``).
    Never raises.
    """
    if not path.exists():
        return FoodContext(
            present=False, entries=(), blocks=(), path=None, malformed_lines=()
        )

    text = path.read_text(encoding="utf-8-sig")

    # Combined entries (inline + block) in file order; we dedup at the end.
    collected: list[FoodEntry] = []
    blocks: list[MealBlock] = []
    malformed: list[int] = []

    # Active block state.
    current_block_date: date | None = None
    current_block_meal: str | None = None
    current_block_entries: list[FoodEntry] = []
    in_dead_block = False  # set when a `##` header had a bad date

    def close_block() -> None:
        """Finalise the active block, if any, into ``blocks``."""
        nonlocal current_block_date, current_block_meal
        nonlocal current_block_entries, in_dead_block
        if current_block_date is not None and current_block_entries:
            blocks.append(
                MealBlock(
                    date=current_block_date,
                    meal_name=current_block_meal,
                    entries=tuple(current_block_entries),
                )
            )
        current_block_date = None
        current_block_meal = None
        current_block_entries = []
        in_dead_block = False

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()

        # Blank lines don't close the active block (per spec — a block may
        # span blank-line separators between bullets).
        if not stripped:
            continue

        # `## ` block header (close any in-flight block first).
        if stripped.startswith("## "):
            close_block()
            parsed_header = _parse_block_header(stripped)
            if parsed_header is None:
                # Dead block: header AND subsequent bullets all malformed
                # until the next `##` header.
                malformed.append(line_no)
                in_dead_block = True
                continue
            current_block_date, current_block_meal = parsed_header
            in_dead_block = False
            continue

        # Single `#` (NOT `##`) — comment header, ignore.
        if stripped.startswith("#") and not stripped.startswith("## "):
            continue

        # Bullet line.
        if stripped.startswith("- "):
            if in_dead_block:
                malformed.append(line_no)
                continue
            if current_block_date is not None:
                # Even inside a block, the bullet may still be an inline-format
                # entry (carries its own ``YYYY-MM-DD <meal>:`` prefix AND
                # a ``|`` macro separator). Try inline first so an inline
                # entry appended after a block doesn't get misattributed to
                # the active block's meal_name. The block stays open for
                # subsequent block-shaped bullets.
                inline_entry = _parse_inline_entry(raw_line, line_no)
                if inline_entry is not None:
                    collected.append(inline_entry)
                    continue
                entry = _parse_block_bullet(
                    raw_line, line_no, current_block_date, current_block_meal
                )
                if entry is None:
                    malformed.append(line_no)
                    continue
                current_block_entries.append(entry)
                collected.append(entry)
                continue
            # No active block — try inline.
            inline_entry = _parse_inline_entry(raw_line, line_no)
            if inline_entry is None:
                malformed.append(line_no)
                continue
            collected.append(inline_entry)
            continue

        # Any other prose line — silently ignored.

    # Close any still-open block at EOF.
    close_block()

    # Latest-wins dedup on (date, meal_name, food_label). Iterate in
    # file/source order so later entries overwrite earlier.
    collected.sort(key=lambda e: e.source_line)
    by_key: dict[tuple[date, str | None, str], FoodEntry] = {}
    for e in collected:
        by_key[(e.date, e.meal_name, e.food_label)] = e

    deduped = tuple(
        sorted(by_key.values(), key=lambda e: (e.date, e.source_line))
    )

    # Apply the same dedup to MealBlock.entries so `blocks` stays a true view
    # of `entries` (every block entry is also in `entries`; nothing surviving
    # in `entries` is dropped from its source block). Without this, a block
    # that repeated a (date, meal_name, food_label) triple kept both copies
    # in MealBlock.entries while FoodContext.entries kept only the latest.
    surviving_lines = {e.source_line for e in deduped}
    filtered_blocks: list[MealBlock] = []
    for block in blocks:
        kept = tuple(e for e in block.entries if e.source_line in surviving_lines)
        if kept:
            filtered_blocks.append(
                MealBlock(date=block.date, meal_name=block.meal_name, entries=kept)
            )

    return FoodContext(
        present=True,
        entries=deduped,
        blocks=tuple(filtered_blocks),
        path=path,
        malformed_lines=tuple(sorted(set(malformed))),
    )


def daily_nutrition(
    entries: tuple[FoodEntry, ...] | list[FoodEntry], day: date
) -> DailyNutrition:
    """Sum P/C/F/kcal across all entries with ``entry.date == day``.

    Macro percentages computed AFTER summation, from kcal-share. When
    ``kcal == 0`` (degenerate sum), all three percentages return ``0.0``
    rather than crashing. An empty filter result returns a zero-everything
    ``DailyNutrition`` with ``entry_count == 0`` — the rollup is
    responsible for skipping days with no entries.
    """
    day_entries = [e for e in entries if e.date == day]

    protein = sum(e.protein_g for e in day_entries)
    carbs = sum(e.carbs_g for e in day_entries)
    fat = sum(e.fat_g for e in day_entries)
    kcal = sum(e.kcal for e in day_entries)

    if kcal > 0:
        pct_p = (protein * 4) / kcal * 100
        pct_c = (carbs * 4) / kcal * 100
        pct_f = (fat * 9) / kcal * 100
    else:
        pct_p = 0.0
        pct_c = 0.0
        pct_f = 0.0

    return DailyNutrition(
        date=day,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        kcal=kcal,
        macro_pct_protein=pct_p,
        macro_pct_carbs=pct_c,
        macro_pct_fat=pct_f,
        entry_count=len(day_entries),
    )


def nutrition_rollup(
    entries: tuple[FoodEntry, ...] | list[FoodEntry],
    today: date,
    *,
    target_kcal: int | None = None,
) -> NutritionRollup:
    """Roll ``entries`` into 7d/28d trailing averages + optional kcal goal.

    Windows are left-open right-closed ``(today - N, today]`` so a same-day
    log always counts. Averages are over days WITH entries only; a day
    without entries is NOT in the denominator. ``avg_28d_kcal`` is a
    single scalar int (the deeper 28d trend lives in a future version).

    Empty / all-future ``entries`` -> all-None / zero-counts rollup.
    """
    # Defensive: accept both tuple and list; filter out future-dated rows.
    filtered = [e for e in entries if e.date <= today]

    if not filtered:
        return NutritionRollup(
            today=today,
            latest_day=None,
            days_since_last=None,
            avg_7d=None,
            days_logged_7d=0,
            avg_28d_kcal=None,
            target_kcal=target_kcal,
            deficit_surplus_7d=None,
        )

    # Group entries by date.
    by_day: dict[date, list[FoodEntry]] = {}
    for e in filtered:
        by_day.setdefault(e.date, []).append(e)

    # Per-day DailyNutrition for each day that has entries.
    per_day: dict[date, DailyNutrition] = {
        d: daily_nutrition(grp, d) for d, grp in by_day.items()
    }

    latest_date = max(per_day.keys())
    latest_day = per_day[latest_date]
    days_since_last = (today - latest_date).days

    # 7-day window: left-open right-closed (today - 7, today].
    cutoff_7 = today - timedelta(days=7)
    seven_d_days = [
        per_day[d] for d in per_day if d > cutoff_7 and d <= today
    ]
    days_logged_7d = len(seven_d_days)

    if days_logged_7d == 0:
        avg_7d: DailyNutrition | None = None
    else:
        n = float(days_logged_7d)
        mean_p = sum(d.protein_g for d in seven_d_days) / n
        mean_c = sum(d.carbs_g for d in seven_d_days) / n
        mean_f = sum(d.fat_g for d in seven_d_days) / n
        mean_kcal_f = sum(d.kcal for d in seven_d_days) / n
        mean_kcal_i = int(round(mean_kcal_f))
        # Macro percentages from the means.
        if mean_kcal_i > 0:
            pct_p = (mean_p * 4) / mean_kcal_i * 100
            pct_c = (mean_c * 4) / mean_kcal_i * 100
            pct_f = (mean_f * 9) / mean_kcal_i * 100
        else:
            pct_p = 0.0
            pct_c = 0.0
            pct_f = 0.0
        total_entries = sum(d.entry_count for d in seven_d_days)
        avg_7d = DailyNutrition(
            date=today,
            protein_g=mean_p,
            carbs_g=mean_c,
            fat_g=mean_f,
            kcal=mean_kcal_i,
            macro_pct_protein=pct_p,
            macro_pct_carbs=pct_c,
            macro_pct_fat=pct_f,
            entry_count=total_entries,
        )

    # 28-day window (scalar kcal only).
    cutoff_28 = today - timedelta(days=28)
    twentyeight_d_days = [
        per_day[d] for d in per_day if d > cutoff_28 and d <= today
    ]
    if not twentyeight_d_days:
        avg_28d_kcal: int | None = None
    else:
        mean_28_kcal = sum(d.kcal for d in twentyeight_d_days) / len(
            twentyeight_d_days
        )
        avg_28d_kcal = int(round(mean_28_kcal))

    if target_kcal is not None and avg_7d is not None:
        deficit_surplus_7d: int | None = avg_7d.kcal - target_kcal
    else:
        deficit_surplus_7d = None

    return NutritionRollup(
        today=today,
        latest_day=latest_day,
        days_since_last=days_since_last,
        avg_7d=avg_7d,
        days_logged_7d=days_logged_7d,
        avg_28d_kcal=avg_28d_kcal,
        target_kcal=target_kcal,
        deficit_surplus_7d=deficit_surplus_7d,
    )
