"""Tests for the transition shim ``tempo.analysis.context``.

After Phase 8 the canonical races parser lives in :mod:`tempo.analysis.races`;
``context`` is a re-export shim plus the still-inline ``plan.md`` parser. The
shim assertions below ensure existing import sites keep resolving until Plan 04
migrates them; the plan-portion tests stay until Plan 05 deletes the parser.
"""

from __future__ import annotations

from pathlib import Path

from tempo.analysis import context as ctx_mod
from tempo.analysis.context import (
    PlanContext,
    Race,
    RacesContext,
    parse_plan,
    parse_races,
)

# ---- shim re-export assertions --------------------------------------------


def test_context_shim_reexports_races() -> None:
    """The shim re-exports the canonical races names from tempo.analysis.races."""
    from tempo.analysis import races as races_mod

    # Same object identity proves it is a re-export, not a duplicate.
    assert ctx_mod.parse_races is races_mod.parse_races
    assert ctx_mod.Race is races_mod.Race
    assert ctx_mod.RacesContext is races_mod.RacesContext


def test_context_shim_still_exports_parse_plan_for_now() -> None:
    """parse_plan stays inline on the shim until Plan 05 deletes plan.md support."""
    assert callable(parse_plan)
    assert PlanContext.__name__ == "PlanContext"
    # And the parse_races / Race / RacesContext re-exports remain importable.
    assert callable(parse_races)
    assert Race.__name__ == "Race"
    assert RacesContext.__name__ == "RacesContext"


# ---- plan.md (kept until Plan 05 deletes it) ------------------------------


def test_parse_plan_missing_file(tmp_path: Path) -> None:
    ctx = parse_plan(tmp_path / "nope.md")
    assert ctx.present is False
    assert ctx.text == ""
    assert ctx.fields == {}


def test_parse_plan_captures_headings_and_fields(tmp_path: Path) -> None:
    p = tmp_path / "plan.md"
    p.write_text(
        "# Training Plan\n\n"
        "Phase: Base building\n"
        "Week: 6 of 16\n"
        "Focus: aerobic volume\n\n"
        "## This week\n\n"
        "- Mon: rest\n",
        encoding="utf-8",
    )
    ctx = parse_plan(p)
    assert ctx.present is True
    assert "Training Plan" in ctx.headings
    assert "This week" in ctx.headings
    assert ctx.fields["phase"] == "Base building"
    assert ctx.fields["week"] == "6 of 16"
    assert ctx.fields["focus"] == "aerobic volume"
    assert "Mon: rest" in ctx.text


def test_parse_plan_first_field_wins(tmp_path: Path) -> None:
    p = tmp_path / "plan.md"
    p.write_text("Phase: Base\nPhase: Peak\n", encoding="utf-8")
    ctx = parse_plan(p)
    assert ctx.fields["phase"] == "Base"
