"""Parsing of strength.md plus the rolling-window strength_rollup.

Lenient parser (SC-01); inclusive closed-interval windows (SC-02). All tests use
only stdlib + pytest's tmp_path fixture; the rollup tests pin a fixed reference
``today`` so the windows are deterministic. Mirrors tests/test_heat.py shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo.analysis.strength import (
    StrengthContext,
    StrengthExercise,
    StrengthRollup,
    StrengthSession,
    StrengthSet,
    _parse_set,
    parse_strength,
    strength_rollup,
)

# Fixed reference date for all rollup tests so the closed-interval windows
# (today-6 .. today, today-13 .. today, today-27 .. today) are deterministic.
TODAY = date(2026, 5, 27)


def _session(
    d: date,
    *,
    name: str | None = "Lower body",
    weighted: tuple[tuple[float, int], ...] = (),
    bodyweight: tuple[int, ...] = (),
    timed: tuple[int, ...] = (),
) -> StrengthSession:
    """Build a minimal StrengthSession with a single exercise containing the
    requested mix of weighted / bodyweight / timed sets."""
    sets: list[StrengthSet] = []
    for w, r in weighted:
        sets.append(StrengthSet(weight_kg=w, reps=r, duration_s=None))
    for r in bodyweight:
        sets.append(StrengthSet(weight_kg=None, reps=r, duration_s=None))
    for s in timed:
        sets.append(StrengthSet(weight_kg=None, reps=None, duration_s=s))
    exercises: tuple[StrengthExercise, ...] = ()
    if sets:
        exercises = (StrengthExercise(name="Test", sets=tuple(sets)),)
    return StrengthSession(date=d, name=name, exercises=exercises)


# ---- _parse_set ----------------------------------------------------------


def test_parse_set_weighted_basic() -> None:
    assert _parse_set("55x8") == StrengthSet(weight_kg=55.0, reps=8, duration_s=None)
    assert _parse_set("52.5x10") == StrengthSet(
        weight_kg=52.5, reps=10, duration_s=None
    )


def test_parse_set_weighted_separators() -> None:
    expected = StrengthSet(weight_kg=55.0, reps=8, duration_s=None)
    assert _parse_set("55X8") == expected
    assert _parse_set("55×8") == expected  # unicode ×
    assert _parse_set("55 x 8") == expected


def test_parse_set_timed_hold() -> None:
    assert _parse_set("1:00") == StrengthSet(
        weight_kg=None, reps=None, duration_s=60
    )
    assert _parse_set("0:30") == StrengthSet(
        weight_kg=None, reps=None, duration_s=30
    )
    assert _parse_set("2:15") == StrengthSet(
        weight_kg=None, reps=None, duration_s=135
    )


def test_parse_set_bodyweight() -> None:
    assert _parse_set("15") == StrengthSet(
        weight_kg=None, reps=15, duration_s=None
    )
    assert _parse_set("8") == StrengthSet(
        weight_kg=None, reps=8, duration_s=None
    )


def test_parse_set_malformed_returns_none() -> None:
    assert _parse_set("bogus") is None
    assert _parse_set("1:2:3") is None
    assert _parse_set("abc x 8") is None
    assert _parse_set("") is None


# ---- parse_strength ------------------------------------------------------


def test_parse_strength_missing_file(tmp_path: Path) -> None:
    ctx = parse_strength(tmp_path / "nope.md")
    assert isinstance(ctx, StrengthContext)
    assert ctx.present is False
    assert ctx.sessions == []


def test_parse_strength_header_only(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text("## 2026-05-26\n", encoding="utf-8")
    ctx = parse_strength(p)
    assert ctx.present is True
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.date == date(2026, 5, 26)
    assert s.start_local is None
    assert s.name is None
    assert s.rest_s is None
    assert s.notes is None
    assert s.exercises == ()


def test_parse_strength_header_with_time_and_name(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26 18:19 — Lower body\n"
        "## 2026-05-25 09:00 - Upper body\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 2
    first, second = ctx.sessions
    assert first.start_local == "18:19"
    assert first.name == "Lower body"
    assert second.start_local == "09:00"
    assert second.name == "Upper body"


def test_parse_strength_header_name_without_separator(tmp_path: Path) -> None:
    """A header like `## 2026-05-26 Lower body` (no em-dash / hyphen) is accepted.

    Regression: the previous _HEADER_RE required a `—` or `-` separator before
    the name, so any header that just put the name after the date silently
    failed to match and dropped the entire session into skip mode. The
    separator is now optional.
    """
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26 Lower body\n"
        "- Squats: 60x5\n"
        "\n"
        "## 2026-05-27 18:00 Push day\n"
        "- Bench Press: 60x5\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 2
    first, second = ctx.sessions
    assert first.date == date(2026, 5, 26)
    assert first.start_local is None
    assert first.name == "Lower body"
    assert len(first.exercises) == 1
    assert second.date == date(2026, 5, 27)
    assert second.start_local == "18:00"
    assert second.name == "Push day"
    assert len(second.exercises) == 1


def test_parse_strength_metadata_rest_and_notes(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26 18:19 — Lower body\n"
        "rest: 1:30\n"
        "notes: pogos + SLGB supersetted\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.rest_s == 90
    assert s.notes == "pogos + SLGB supersetted"


def test_parse_strength_metadata_malformed_rest_is_none(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\nrest: bogus\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    assert ctx.sessions[0].rest_s is None


def test_parse_strength_metadata_unknown_key_ignored(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\nweight: 80\nnotes: real notes\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.notes == "real notes"
    # `weight:` is unknown — silently dropped, no exception, no side-effect.


def test_parse_strength_exercise_bullet_basic(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\n"
        "- Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    exs = ctx.sessions[0].exercises
    assert len(exs) == 1
    ex = exs[0]
    assert ex.name == "Romanian Deadlift"
    assert ex.equipment == "Barbell"
    assert ex.superset_group is None
    assert len(ex.sets) == 4
    assert ex.sets[0] == StrengthSet(weight_kg=40.0, reps=8)
    assert ex.sets[1] == StrengthSet(weight_kg=50.0, reps=8)
    assert ex.sets[2] == StrengthSet(weight_kg=55.0, reps=7)
    assert ex.sets[3] == StrengthSet(weight_kg=55.0, reps=8)


def test_parse_strength_exercise_bullet_with_superset_group(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\n- Pogos [A]: 15, 15, 15\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    ex = ctx.sessions[0].exercises[0]
    assert ex.name == "Pogos"
    assert ex.equipment is None
    assert ex.superset_group == "A"
    assert len(ex.sets) == 3
    for s in ex.sets:
        assert s == StrengthSet(weight_kg=None, reps=15, duration_s=None)


def test_parse_strength_exercise_bullet_timed_holds(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\n- Plank: 1:00, 1:00, 1:00, 0:30\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    ex = ctx.sessions[0].exercises[0]
    assert ex.name == "Plank"
    durations = [s.duration_s for s in ex.sets]
    assert durations == [60, 60, 60, 30]
    for s in ex.sets:
        assert s.weight_kg is None
        assert s.reps is None


def test_parse_strength_exercise_with_malformed_set_skipped(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## 2026-05-26\n- Squat: 100x5, bogus, 100x5\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    ex = ctx.sessions[0].exercises[0]
    assert ex.name == "Squat"
    assert len(ex.sets) == 2
    assert ex.sets[0] == StrengthSet(weight_kg=100.0, reps=5)
    assert ex.sets[1] == StrengthSet(weight_kg=100.0, reps=5)


def test_parse_strength_session_with_unparseable_date_skipped(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "## not-a-date\n"
        "- Squat: 100x5\n"
        "## 2026-05-26\n"
        "- Bench: 60x8\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.date == date(2026, 5, 26)
    assert len(s.exercises) == 1
    assert s.exercises[0].name == "Bench"


def test_parse_strength_ignores_top_level_heading(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(
        "# Strength sessions\n\n## 2026-05-26\n",
        encoding="utf-8",
    )
    ctx = parse_strength(p)
    assert len(ctx.sessions) == 1
    assert ctx.sessions[0].date == date(2026, 5, 26)


# ---- Integration: owner's Tuesday session --------------------------------


OWNERS_TUESDAY = (
    "# Strength sessions\n"
    "\n"
    "## 2026-05-26 18:19 — Lower body\n"
    "rest: 1:30\n"
    "notes: pogos + SLGB supersetted\n"
    "\n"
    "- Romanian Deadlift (Barbell): 40x8, 50x8, 55x7, 55x8\n"
    "- Hip Thrust (Barbell): 50x10, 55x10, 55x10, 55x10\n"
    "- Seated Leg Curl (Machine): 25x12, 30x12, 30x12\n"
    "- Calf Press (Leg Press): 80x16, 80x16, 80x16, 80x16\n"
    "- Pogos [A]: 15, 15, 15\n"
    "- Single Leg Glute Bridge [A]: 8, 8, 8\n"
    "- Plank: 1:00, 1:00, 1:00, 0:30\n"
)


def test_parse_owners_tuesday_session_full_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(OWNERS_TUESDAY, encoding="utf-8")
    ctx = parse_strength(p)
    assert ctx.present is True
    assert len(ctx.sessions) == 1
    s = ctx.sessions[0]
    assert s.date == date(2026, 5, 26)
    assert s.start_local == "18:19"
    assert s.name == "Lower body"
    assert s.rest_s == 90
    assert s.notes == "pogos + SLGB supersetted"

    names = [e.name for e in s.exercises]
    assert names == [
        "Romanian Deadlift",
        "Hip Thrust",
        "Seated Leg Curl",
        "Calf Press",
        "Pogos",
        "Single Leg Glute Bridge",
        "Plank",
    ]

    by_name = {e.name: e for e in s.exercises}
    assert by_name["Pogos"].superset_group == "A"
    assert by_name["Single Leg Glute Bridge"].superset_group == "A"
    for other in (
        "Romanian Deadlift",
        "Hip Thrust",
        "Seated Leg Curl",
        "Calf Press",
        "Plank",
    ):
        assert by_name[other].superset_group is None

    assert by_name["Romanian Deadlift"].equipment == "Barbell"
    assert by_name["Hip Thrust"].equipment == "Barbell"
    assert by_name["Seated Leg Curl"].equipment == "Machine"
    assert by_name["Calf Press"].equipment == "Leg Press"

    plank_sets = by_name["Plank"].sets
    assert [st.duration_s for st in plank_sets] == [60, 60, 60, 30]
    for st in plank_sets:
        assert st.weight_kg is None
        assert st.reps is None

    calf_sets = by_name["Calf Press"].sets
    assert len(calf_sets) == 4
    for st in calf_sets:
        assert st == StrengthSet(weight_kg=80.0, reps=16, duration_s=None)


# ---- strength_rollup -----------------------------------------------------


def test_strength_rollup_empty_sessions() -> None:
    roll = strength_rollup([], TODAY)
    assert isinstance(roll, StrengthRollup)
    assert roll.today == TODAY
    assert roll.last_7d_count == 0
    assert roll.last_7d_tonnage_kg == 0.0
    assert roll.last_14d_count == 0
    assert roll.last_14d_tonnage_kg == 0.0
    assert roll.last_28d_count == 0
    assert roll.last_28d_tonnage_kg == 0.0
    assert roll.last_session_date is None
    assert roll.last_session_days_ago is None
    assert roll.last_session_name is None


def test_strength_rollup_window_edges_inclusive() -> None:
    sessions = [
        _session(TODAY),
        _session(TODAY - timedelta(days=6)),
        _session(TODAY - timedelta(days=7)),
        _session(TODAY - timedelta(days=13)),
        _session(TODAY - timedelta(days=14)),
        _session(TODAY - timedelta(days=27)),
        _session(TODAY - timedelta(days=28)),
    ]
    roll = strength_rollup(sessions, TODAY)
    assert roll.last_7d_count == 2
    assert roll.last_14d_count == 4
    assert roll.last_28d_count == 6


def test_strength_rollup_tonnage_weighted_sets_only() -> None:
    sessions = [
        _session(
            TODAY,
            weighted=((50.0, 10),),
            bodyweight=(15,),
            timed=(60,),
        )
    ]
    roll = strength_rollup(sessions, TODAY)
    assert roll.last_7d_count == 1
    assert roll.last_7d_tonnage_kg == pytest.approx(500.0)


def test_strength_rollup_tonnage_owners_tuesday(tmp_path: Path) -> None:
    p = tmp_path / "strength.md"
    p.write_text(OWNERS_TUESDAY, encoding="utf-8")
    ctx = parse_strength(p)
    roll = strength_rollup(ctx.sessions, date(2026, 5, 26))
    assert roll.last_7d_count == 1
    assert roll.last_7d_tonnage_kg == pytest.approx(9835.0)
    assert roll.last_session_date == date(2026, 5, 26)
    assert roll.last_session_days_ago == 0
    assert roll.last_session_name == "Lower body"


def test_strength_rollup_filters_future_dated_sessions() -> None:
    sessions = [_session(TODAY + timedelta(days=5))]
    roll = strength_rollup(sessions, TODAY)
    assert roll.last_7d_count == 0
    assert roll.last_14d_count == 0
    assert roll.last_28d_count == 0
    assert roll.last_session_date is None
    assert roll.last_session_days_ago is None
    assert roll.last_session_name is None


def test_strength_rollup_last_session_name_unnamed_fallback() -> None:
    sessions = [_session(TODAY, name=None)]
    roll = strength_rollup(sessions, TODAY)
    assert roll.last_session_date == TODAY
    assert roll.last_session_name == "unnamed"


def test_strength_rollup_last_session_today_is_zero_days_ago() -> None:
    roll = strength_rollup([_session(TODAY)], TODAY)
    assert roll.last_session_date == TODAY
    assert roll.last_session_days_ago == 0


def test_strength_rollup_counts_session_with_zero_weighted_sets() -> None:
    sessions = [_session(TODAY, bodyweight=(15, 15), timed=(60,))]
    roll = strength_rollup(sessions, TODAY)
    assert roll.last_7d_count == 1
    assert roll.last_7d_tonnage_kg == 0.0


def test_example_file_parses_cleanly() -> None:
    """The committed strength.md.example must parse cleanly through
    parse_strength -- it's both the documentation of the format AND a parser
    fixture that guards against drift between the template and the parser.

    Asserts: ≥ 3 sessions parse; the owner's Tuesday 2026-05-26 session is
    present with the expected shape (7 exercises, the [A] superset pair on
    Pogos + Single Leg Glute Bridge, plank durations [60,60,60,30], rest_s=90).
    """
    repo_root = Path(__file__).resolve().parent.parent
    example_path = repo_root / "strength.md.example"
    assert example_path.exists(), example_path

    ctx = parse_strength(example_path)
    assert ctx.present
    assert len(ctx.sessions) >= 3, [s.date for s in ctx.sessions]

    tuesday = next(
        (s for s in ctx.sessions if s.date == date(2026, 5, 26)), None
    )
    assert tuesday is not None, [s.date for s in ctx.sessions]
    assert tuesday.start_local == "18:19"
    assert tuesday.name == "Lower body"
    assert tuesday.rest_s == 90
    assert tuesday.notes == "pogos + SLGB supersetted"
    assert len(tuesday.exercises) == 7, [e.name for e in tuesday.exercises]

    supersets = [e for e in tuesday.exercises if e.superset_group == "A"]
    assert len(supersets) == 2
    superset_names = {e.name for e in supersets}
    assert superset_names == {"Pogos", "Single Leg Glute Bridge"}, superset_names

    plank = next(e for e in tuesday.exercises if e.name == "Plank")
    durations = [st.duration_s for st in plank.sets]
    assert durations == [60, 60, 60, 30], durations
