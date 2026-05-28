"""Parse the user-maintained ``preferences.md`` profile file.

Unlike the append-only trackers (``races.md`` / ``heat.md`` / ``strength.md`` /
``weight.md`` / ``food.md``), ``preferences.md`` is an **edit-in-place profile**:
a short human-edited Markdown document carrying the owner's physiology numbers,
display-unit preferences, a nutrition kcal target, and free-form prose sections
the coach reads when planning (weekly training shape, current focus, etc.).

Typed sections (``Physiology`` / ``Units`` / ``Nutrition``) are parsed as
structured config; every other H2 section is captured verbatim as a
prose section keyed by the lowercased header text so the coach can read
the file as-is via ``Read training/preferences.md`` (the parser does NOT
interpret prose sections — that is intentional, not a TODO).

Parsing is **lenient**: missing file -> ``present=False``; malformed values
inside a typed section leave the field at its default and the 1-indexed line
number lands in ``PreferencesContext.malformed_lines``; unknown keys inside
a typed section are silently ignored; unrecognised section headers are
captured as prose. The parser NEVER raises and NEVER logs the parsed VALUES
(line numbers only) so personal physiology numbers can never leak via stderr.

Documented format (see the committed ``preferences.md.example``)::

    # Preferences

    ## Physiology
    threshold_pace: 4:00/mi          # M:SS/mi, M:SS/km, NNN s/km, or bare NNN
    max_hr: 188
    resting_hr: 40
    threshold_hr: 172                # optional; if absent load.py's ~0.92*max_hr kicks in

    ## Units
    distance: miles                  # km | miles
    pace: min_per_mile               # min_per_km | min_per_mile

    ## Nutrition
    target_kcal: 2800                # optional; enables goal tracking in nutrition reports

    ## Training week
    <free-form prose — coach reads, parser ignores>

    ## Goals / current focus
    <free-form prose>
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Canonical km <-> mile conversion (NIST).
KM_PER_MILE = 1.609344


@dataclass(frozen=True, slots=True)
class Physiology:
    """Typed ``## Physiology`` section fields.

    Every field optional; missing section or unparseable value -> ``None``.
    ``threshold_pace_s_per_km`` is normalised to seconds-per-km regardless of
    the source format (``4:00/mi``, ``4:00/km``, ``240 s/km``, bare ``240``).
    """

    threshold_pace_s_per_km: float | None = None
    max_hr: int | None = None
    resting_hr: int | None = None
    threshold_hr: int | None = None


@dataclass(frozen=True, slots=True)
class Units:
    """Typed ``## Units`` section fields with safe defaults.

    Defaults preserve pre-Phase-17 behaviour (km + min/km) so a user with no
    ``## Units`` section sees no rendering change.
    """

    distance: Literal["km", "miles"] = "km"
    pace: Literal["min_per_km", "min_per_mile"] = "min_per_km"


@dataclass(frozen=True, slots=True)
class Nutrition:
    """Typed ``## Nutrition`` section fields.

    ``target_kcal`` is optional. When ``None`` (missing section or missing
    key), the nutrition report and recovery report goal lines silently omit
    — same behaviour as the old ``RUNOS_TARGET_KCAL`` being unset.
    """

    target_kcal: int | None = None


@dataclass(frozen=True, slots=True)
class PreferencesContext:
    """The parsed ``preferences.md`` result.

    ``present`` is ``False`` when the file is missing on disk; in that case
    every typed dataclass holds its defaults, ``prose_sections`` is empty,
    and ``path`` is ``None``. ``prose_sections`` is keyed by the lowercased
    + stripped H2 header text (e.g. ``"training week"``,
    ``"goals / current focus"``) and stores the raw section body verbatim
    (including blank lines, with trailing newlines stripped).
    """

    present: bool
    physiology: Physiology
    units: Units
    nutrition: Nutrition
    prose_sections: dict[str, str] = field(default_factory=dict)
    path: Path | None = None
    malformed_lines: tuple[int, ...] = ()


# Canonical typed-section names (lowercased). Anything else -> prose section.
_TYPED_SECTIONS = frozenset({"physiology", "units", "nutrition"})

# Recognised keys per typed section. Unknown keys inside a typed section are
# silently ignored (forward-compat by design).
_PHYSIOLOGY_KEYS = frozenset({"threshold_pace", "max_hr", "resting_hr", "threshold_hr"})
_UNITS_KEYS = frozenset({"distance", "pace"})
_NUTRITION_KEYS = frozenset({"target_kcal"})

# H2 header: `## <name>` (case-insensitive, surrounding whitespace stripped).
# We match `##` followed by at least one space and then the header text.
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")

# Threshold pace forms (compiled once):
#   M:SS/mi   ->  6:26/mi
#   M:SS/km   ->  4:00/km
#   NNN s/km  ->  240 s/km   (also `240s/km` no-space)
#   NNN       ->  240        (bare int, treated as s/km)
_PACE_MMSS_UNIT_RE = re.compile(
    r"^\s*(?P<m>\d{1,2}):(?P<s>\d{2})\s*/\s*(?P<unit>mi|km)\s*$",
    re.IGNORECASE,
)
_PACE_SECONDS_UNIT_RE = re.compile(
    r"^\s*(?P<sec>\d+(?:\.\d+)?)\s*s\s*/\s*km\s*$",
    re.IGNORECASE,
)
_PACE_BARE_INT_RE = re.compile(r"^\s*(?P<sec>\d+(?:\.\d+)?)\s*$")

# Units enum normalisation tables.
_DISTANCE_ALIASES: dict[str, Literal["km", "miles"]] = {
    "km": "km",
    "kilometre": "km",
    "kilometres": "km",
    "kilometer": "km",
    "kilometers": "km",
    "mi": "miles",
    "mile": "miles",
    "miles": "miles",
}
_PACE_ALIASES: dict[str, Literal["min_per_km", "min_per_mile"]] = {
    "min_per_km": "min_per_km",
    "min/km": "min_per_km",
    "mpk": "min_per_km",
    "min_per_mile": "min_per_mile",
    "min/mi": "min_per_mile",
    "mpm": "min_per_mile",
}


def _parse_threshold_pace(value: str) -> int | None:
    """Parse a threshold-pace string into seconds-per-km (int), else ``None``.

    Accepted forms:

    ===============  =============  ==========================================
    Input            Parsed (s/km)  Notes
    ===============  =============  ==========================================
    ``4:00/mi``      149            convert from min/mi via 1 mi = 1.609344 km
    ``6:26/mi``      240            M:SS/mi
    ``4:00/km``      240            M:SS/km direct
    ``240 s/km``     240            bare seconds with unit
    ``240``          240            bare integer assumed s/km
    ===============  =============  ==========================================

    ``"4 min/mi"`` (no colon for the seconds component) and any other shape
    return ``None``; the caller records the malformed line.
    """
    if not value:
        return None
    # Strip trailing inline comment defensively (the section parser already
    # does this, but the helper should be safe to call standalone).
    raw = value.split("#", 1)[0].strip()
    if not raw:
        return None

    m = _PACE_MMSS_UNIT_RE.match(raw)
    if m is not None:
        minutes = int(m.group("m"))
        seconds = int(m.group("s"))
        if seconds >= 60:
            # Defensive: M:SS where SS >= 60 is malformed input, not data we
            # want to silently roll over.
            return None
        total_s = minutes * 60 + seconds
        unit = m.group("unit").lower()
        if unit == "km":
            return int(round(total_s))
        # min/mi -> s/km : divide by km-per-mile.
        s_per_km = total_s / KM_PER_MILE
        return int(round(s_per_km))

    m = _PACE_SECONDS_UNIT_RE.match(raw)
    if m is not None:
        try:
            return int(round(float(m.group("sec"))))
        except (ValueError, TypeError):
            return None

    m = _PACE_BARE_INT_RE.match(raw)
    if m is not None:
        try:
            return int(round(float(m.group("sec"))))
        except (ValueError, TypeError):
            return None

    return None


def _strip_inline_comment(value: str) -> str:
    """Strip a trailing `` # comment`` from a value, preserving leading hashes.

    Only an ``#`` preceded by whitespace OR appearing at the start of the
    trailing-side is treated as a comment delimiter, so a value of
    ``#abc`` (no space) stays intact. In practice typed-section values are
    plain numbers / words / pace strings, so the simple "first whitespace +
    hash" rule is sufficient.
    """
    # Look for ` #` (whitespace then hash) and cut from there.
    idx = value.find(" #")
    if idx >= 0:
        return value[:idx].rstrip()
    # Also handle a value that is *just* a comment (`# something`).
    if value.lstrip().startswith("#"):
        return ""
    return value.rstrip()


def _parse_key_value(line: str) -> tuple[str, str] | None:
    """Split a `key: value` typed-section line, returning ``(key, value)``.

    Returns ``None`` if the line has no ``:`` separator (treated as noise
    inside a typed section, silently ignored). Both key and value are
    stripped; key is lowercased. Inline ``# comment`` trail is stripped from
    the value via :func:`_strip_inline_comment`.
    """
    if ":" not in line:
        return None
    key, _, value = line.partition(":")
    key = key.strip().lower()
    value = _strip_inline_comment(value.strip())
    if not key:
        return None
    return key, value


def _apply_physiology_kv(
    state: dict[str, float | int | None], key: str, value: str
) -> bool:
    """Apply one ``key: value`` to a Physiology field-state dict.

    Returns ``True`` on a successful parse, ``False`` if the value is
    unparseable (caller records the malformed line). Unknown keys return
    ``True`` (silently ignored — not malformed).
    """
    if key not in _PHYSIOLOGY_KEYS:
        return True

    if key == "threshold_pace":
        parsed = _parse_threshold_pace(value)
        if parsed is None:
            return False
        state["threshold_pace_s_per_km"] = parsed
        return True

    # max_hr / resting_hr / threshold_hr — int parse.
    try:
        state[key] = int(round(float(value)))
    except (ValueError, TypeError):
        return False
    return True


def _apply_units_kv(state: dict[str, str], key: str, value: str) -> bool:
    """Apply one ``key: value`` to a Units field-state dict.

    Returns ``True`` on success (incl. unknown keys, which are ignored),
    ``False`` if the value isn't in the recognised alias set.
    """
    if key not in _UNITS_KEYS:
        return True
    lowered = value.strip().lower()
    if key == "distance":
        if lowered not in _DISTANCE_ALIASES:
            return False
        state["distance"] = _DISTANCE_ALIASES[lowered]
        return True
    # pace
    if lowered not in _PACE_ALIASES:
        return False
    state["pace"] = _PACE_ALIASES[lowered]
    return True


def _apply_nutrition_kv(
    state: dict[str, int | None], key: str, value: str
) -> bool:
    """Apply one ``key: value`` to a Nutrition field-state dict.

    Returns ``True`` on success (or unknown-key ignored), ``False`` on a
    bad ``target_kcal`` value.
    """
    if key not in _NUTRITION_KEYS:
        return True
    try:
        state["target_kcal"] = int(round(float(value)))
    except (ValueError, TypeError):
        return False
    return True


def parse_preferences(path: Path) -> PreferencesContext:
    """Parse ``preferences.md`` at ``path``; missing file -> ``present=False``.

    Lenient: malformed values inside a typed section leave the field at its
    default and the 1-indexed line number lands in ``malformed_lines``;
    unknown keys inside a typed section are silently ignored; any H2 section
    that isn't ``Physiology`` / ``Units`` / ``Nutrition`` is captured
    verbatim into ``prose_sections`` keyed by the lowercased header. Lines
    before the first H2 are discarded (covers the ``# Preferences`` H1 and
    any preamble). Never raises.
    """
    if not path.exists():
        return PreferencesContext(
            present=False,
            physiology=Physiology(),
            units=Units(),
            nutrition=Nutrition(),
            prose_sections={},
            path=None,
            malformed_lines=(),
        )

    # ``utf-8-sig`` transparently strips a leading BOM if present.
    text = path.read_text(encoding="utf-8-sig")

    physiology_state: dict[str, float | int | None] = {
        "threshold_pace_s_per_km": None,
        "max_hr": None,
        "resting_hr": None,
        "threshold_hr": None,
    }
    units_state: dict[str, str] = {"distance": "km", "pace": "min_per_km"}
    nutrition_state: dict[str, int | None] = {"target_kcal": None}
    prose_sections: dict[str, str] = {}
    malformed: list[int] = []

    # Section tracking. ``current_section_header`` is the lowercased H2 name
    # for the active section; ``None`` means "before the first H2".
    current_section_header: str | None = None
    prose_buffer: list[str] = []

    def flush_prose() -> None:
        """Stash the buffered prose lines into ``prose_sections`` and reset."""
        if current_section_header is None:
            return
        if current_section_header in _TYPED_SECTIONS:
            return
        body = "\n".join(prose_buffer).rstrip("\n")
        # Replace (latest-wins on duplicate headers); blank prose still counts
        # as a section the coach may want to see (empty string body).
        prose_sections[current_section_header] = body

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        # Match an H2 header first — `##` on its own opens a new section.
        header_match = _H2_RE.match(raw_line.rstrip())
        if header_match is not None:
            # Close out the previous prose section (if any) before switching.
            flush_prose()
            prose_buffer = []
            current_section_header = header_match.group(1).strip().lower()
            continue

        # Before any H2: discard (covers H1 + preamble + blank lines).
        if current_section_header is None:
            continue

        # Inside a prose section: buffer verbatim (preserve indentation).
        if current_section_header not in _TYPED_SECTIONS:
            prose_buffer.append(raw_line)
            continue

        # Inside a typed section.
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Whole-line comment.
        if stripped.startswith("#"):
            continue

        kv = _parse_key_value(stripped)
        if kv is None:
            # No `:` — silently ignored (prose noise inside a typed section).
            continue
        key, value = kv
        if value == "":
            # Empty value after comment strip — treat as malformed so the
            # user spots the typo on their preferences edit.
            malformed.append(line_no)
            continue

        if current_section_header == "physiology":
            ok = _apply_physiology_kv(physiology_state, key, value)
        elif current_section_header == "units":
            ok = _apply_units_kv(units_state, key, value)
        else:
            # Must be nutrition (only remaining typed section).
            ok = _apply_nutrition_kv(nutrition_state, key, value)

        if not ok:
            malformed.append(line_no)

    # EOF — close out the trailing prose section.
    flush_prose()

    physiology = Physiology(
        threshold_pace_s_per_km=physiology_state["threshold_pace_s_per_km"],
        max_hr=physiology_state["max_hr"],  # type: ignore[arg-type]
        resting_hr=physiology_state["resting_hr"],  # type: ignore[arg-type]
        threshold_hr=physiology_state["threshold_hr"],  # type: ignore[arg-type]
    )
    units = Units(
        distance=units_state["distance"],  # type: ignore[arg-type]
        pace=units_state["pace"],  # type: ignore[arg-type]
    )
    nutrition = Nutrition(target_kcal=nutrition_state["target_kcal"])

    return PreferencesContext(
        present=True,
        physiology=physiology,
        units=units,
        nutrition=nutrition,
        prose_sections=prose_sections,
        path=path,
        malformed_lines=tuple(malformed),
    )
