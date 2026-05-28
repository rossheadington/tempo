"""Parser tests for ``tempo/analysis/preferences.py``.

Covers the lenient-parsing contract end-to-end: missing file degrades
gracefully; empty file yields defaults; typed sections (Physiology / Units /
Nutrition) parse known keys; unknown keys inside a typed section are silently
ignored; unrecognised H2 sections are captured verbatim as prose; threshold-
pace format variants (``M:SS/mi``, ``M:SS/km``, ``NNN s/km``, bare ``NNN``)
all normalise to seconds-per-km; malformed values leave the field at its
default and the line number lands in ``malformed_lines``; case-insensitive
section headers; lines before the first H2 are discarded; inline ``# comment``
trailing strips. Stdlib + ``tmp_path`` only.
"""

from __future__ import annotations

from pathlib import Path

from tempo.analysis.preferences import (
    Nutrition,
    Physiology,
    PreferencesContext,
    Units,
    parse_preferences,
)


def _write(tmp_path: Path, body: str) -> Path:
    """Helper: write ``body`` to ``tmp_path / "preferences.md"`` and return path."""
    p = tmp_path / "preferences.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---- Lenient contract -----------------------------------------------------


def test_parse_preferences_missing_file_returns_absent_context(tmp_path: Path) -> None:
    ctx = parse_preferences(tmp_path / "nope.md")
    assert isinstance(ctx, PreferencesContext)
    assert ctx.present is False
    assert ctx.physiology == Physiology()
    assert ctx.units == Units()
    assert ctx.nutrition == Nutrition()
    assert ctx.prose_sections == {}
    assert ctx.path is None
    assert ctx.malformed_lines == ()


def test_parse_preferences_empty_file_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    ctx = parse_preferences(p)
    assert ctx.present is True
    assert ctx.path == p
    assert ctx.physiology == Physiology()
    assert ctx.units == Units()  # km, min_per_km defaults preserved
    assert ctx.nutrition == Nutrition()
    assert ctx.prose_sections == {}
    assert ctx.malformed_lines == ()


# ---- Physiology section ---------------------------------------------------


def test_parse_preferences_physiology_section_happy_path(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "# Preferences\n"
        "\n"
        "## Physiology\n"
        "threshold_pace: 4:00/km\n"
        "max_hr: 188\n"
        "resting_hr: 40\n"
        "threshold_hr: 172\n",
    )
    ctx = parse_preferences(p)
    assert ctx.present is True
    assert ctx.physiology.threshold_pace_s_per_km == 240
    assert ctx.physiology.max_hr == 188
    assert ctx.physiology.resting_hr == 40
    assert ctx.physiology.threshold_hr == 172
    assert ctx.malformed_lines == ()


def test_parse_preferences_threshold_pace_format_mi(tmp_path: Path) -> None:
    # 6:26 per mile = 386 s/mi -> 386 / 1.609344 = 239.85... s/km, round -> 240.
    p = _write(tmp_path, "## Physiology\nthreshold_pace: 6:26/mi\n")
    ctx = parse_preferences(p)
    assert ctx.physiology.threshold_pace_s_per_km == 240

    # 4:00 per mile = 240 s/mi -> 240 / 1.609344 = 149.13... s/km, round -> 149.
    # (The other entry in the locked-decision table.)
    p2 = tmp_path / "p2.md"
    p2.write_text("## Physiology\nthreshold_pace: 4:00/mi\n", encoding="utf-8")
    ctx2 = parse_preferences(p2)
    assert ctx2.physiology.threshold_pace_s_per_km == 149


def test_parse_preferences_threshold_pace_format_km(tmp_path: Path) -> None:
    p = _write(tmp_path, "## Physiology\nthreshold_pace: 4:00/km\n")
    ctx = parse_preferences(p)
    assert ctx.physiology.threshold_pace_s_per_km == 240


def test_parse_preferences_threshold_pace_bare_seconds(tmp_path: Path) -> None:
    # Both `240 s/km` and the bare `240` should normalise identically.
    p = _write(tmp_path, "## Physiology\nthreshold_pace: 240 s/km\n")
    ctx = parse_preferences(p)
    assert ctx.physiology.threshold_pace_s_per_km == 240

    p2 = tmp_path / "bare.md"
    p2.write_text("## Physiology\nthreshold_pace: 240\n", encoding="utf-8")
    ctx2 = parse_preferences(p2)
    assert ctx2.physiology.threshold_pace_s_per_km == 240


def test_parse_preferences_threshold_pace_malformed_captured(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "## Physiology\n"
        "threshold_pace: 4 min/mi\n"  # no colon for seconds -> malformed
        "max_hr: 188\n",
    )
    ctx = parse_preferences(p)
    # Malformed pace stays None; valid sibling key still applies.
    assert ctx.physiology.threshold_pace_s_per_km is None
    assert ctx.physiology.max_hr == 188
    # Line 2 carries the bad threshold_pace; the `# Preferences` H1 was absent
    # so the H2 is on line 1.
    assert 2 in ctx.malformed_lines


# ---- Units section --------------------------------------------------------


def test_parse_preferences_units_section_happy_path(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "## Units\n"
        "distance: miles\n"
        "pace: min_per_mile\n",
    )
    ctx = parse_preferences(p)
    assert ctx.units.distance == "miles"
    assert ctx.units.pace == "min_per_mile"
    assert ctx.malformed_lines == ()


def test_parse_preferences_units_aliases_normalised(tmp_path: Path) -> None:
    # `mi` / `mile` / `miles` all map to "miles"; `min/mi` + `mpm` -> min_per_mile;
    # `km` / `kilometres` / `kilometers` all map to "km".
    p = _write(tmp_path, "## Units\ndistance: mi\npace: mpm\n")
    ctx = parse_preferences(p)
    assert ctx.units.distance == "miles"
    assert ctx.units.pace == "min_per_mile"

    p2 = tmp_path / "u2.md"
    p2.write_text(
        "## Units\ndistance: kilometres\npace: min/km\n", encoding="utf-8"
    )
    ctx2 = parse_preferences(p2)
    assert ctx2.units.distance == "km"
    assert ctx2.units.pace == "min_per_km"


def test_parse_preferences_units_default_when_section_absent(tmp_path: Path) -> None:
    p = _write(tmp_path, "## Physiology\nmax_hr: 188\n")
    ctx = parse_preferences(p)
    # No `## Units` section at all -> defaults apply.
    assert ctx.units.distance == "km"
    assert ctx.units.pace == "min_per_km"


# ---- Nutrition section ----------------------------------------------------


def test_parse_preferences_nutrition_section_target_kcal(tmp_path: Path) -> None:
    p = _write(tmp_path, "## Nutrition\ntarget_kcal: 2800\n")
    ctx = parse_preferences(p)
    assert ctx.nutrition.target_kcal == 2800
    assert ctx.malformed_lines == ()


# ---- Lenient extras -------------------------------------------------------


def test_parse_preferences_unknown_keys_in_typed_section_ignored(
    tmp_path: Path,
) -> None:
    # `vo2max` isn't a Physiology key — should be silently dropped, not malformed.
    p = _write(
        tmp_path,
        "## Physiology\n"
        "max_hr: 188\n"
        "vo2max: 62\n"
        "favourite_colour: blue\n",
    )
    ctx = parse_preferences(p)
    assert ctx.physiology.max_hr == 188
    assert ctx.malformed_lines == ()  # unknown keys are NOT malformed


def test_parse_preferences_unknown_section_captured_as_prose(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "## Training week\n"
        "Monday: easy doubles\n"
        "Tuesday: threshold day\n"
        "Wednesday: medium-long\n",
    )
    ctx = parse_preferences(p)
    assert "training week" in ctx.prose_sections
    body = ctx.prose_sections["training week"]
    assert "Monday: easy doubles" in body
    assert "Tuesday: threshold day" in body
    assert "Wednesday: medium-long" in body
    # Typed sections all default.
    assert ctx.physiology == Physiology()


def test_parse_preferences_inline_comments_stripped(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "## Physiology\n"
        "max_hr: 188            # measured at last 5k\n"
        "resting_hr: 40 # morning average\n",
    )
    ctx = parse_preferences(p)
    assert ctx.physiology.max_hr == 188
    assert ctx.physiology.resting_hr == 40
    assert ctx.malformed_lines == ()


def test_parse_preferences_case_insensitive_section_headers(tmp_path: Path) -> None:
    # ## PHYSIOLOGY, ## physiology, ##   Physiology   all map to the same section.
    p = _write(
        tmp_path,
        "##   PHYSIOLOGY   \n"
        "max_hr: 188\n"
        "## units\n"
        "distance: MILES\n",
    )
    ctx = parse_preferences(p)
    assert ctx.physiology.max_hr == 188
    assert ctx.units.distance == "miles"


def test_parse_preferences_lines_before_first_h2_ignored(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "# Preferences\n"
        "\n"
        "Some preamble prose the user wrote that doesn't belong to any section.\n"
        "max_hr: 999   # this is BEFORE the first H2 -> must NOT apply\n"
        "\n"
        "## Physiology\n"
        "max_hr: 188\n",
    )
    ctx = parse_preferences(p)
    assert ctx.physiology.max_hr == 188
    # Preamble lines are discarded entirely, NOT recorded as malformed.
    assert ctx.malformed_lines == ()
    # No prose section captured for the H1 either.
    assert ctx.prose_sections == {}


def test_parse_preferences_prose_section_preserved_verbatim(tmp_path: Path) -> None:
    body = (
        "# Preferences\n"
        "\n"
        "## Physiology\n"
        "max_hr: 188\n"
        "\n"
        "## Goals / current focus\n"
        "- Serpent 100k 4 Jul (A-race)\n"
        "- Berlin Marathon 27 Sep (A-race)\n"
        "- Build aerobic base through July\n"
        "\n"
        "Note: don't double up MP work on Fri AND Sun.\n"
    )
    p = _write(tmp_path, body)
    ctx = parse_preferences(p)
    assert ctx.physiology.max_hr == 188
    assert "goals / current focus" in ctx.prose_sections
    captured = ctx.prose_sections["goals / current focus"]
    # Verbatim preservation: bullets, blank line, and trailing note all there.
    assert "- Serpent 100k 4 Jul (A-race)" in captured
    assert "- Berlin Marathon 27 Sep (A-race)" in captured
    assert "- Build aerobic base through July" in captured
    assert "Note: don't double up MP work on Fri AND Sun." in captured
