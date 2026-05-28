"""Parsing of weight.md plus the rolling-window weight_rollup.

Lenient parser (WEIGHT-01); left-open right-closed windows + EWMA trend
(WEIGHT-02); kg-normalised mixed-unit logs (WEIGHT-03). All tests use only
stdlib + pytest's tmp_path fixture; the rollup tests pin a fixed reference
``today`` so the windows are deterministic. Mirrors tests/test_strength.py /
tests/test_heat.py shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo.analysis.weight import (
    WeightContext,
    WeightEntry,
    _parse_entry_line,
    _to_kg,
    parse_weight,
    weight_rollup,
)

# Fixed reference date for all rollup tests so the left-open right-closed
# windows (today-7, today] and (today-28, today] are deterministic.
TODAY = date(2026, 5, 28)


def _write_weight(tmp_path: Path, body: str) -> Path:
    """Helper: write ``body`` to ``tmp_path / "weight.md"`` and return the path."""
    p = tmp_path / "weight.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---- _to_kg ----------------------------------------------------------------


def test_to_kg_kg_unchanged() -> None:
    assert _to_kg(72.4, "kg") == 72.4


def test_to_kg_lb_converted() -> None:
    assert abs(_to_kg(160.0, "lb") - 72.5747) < 0.01


# ---- _parse_entry_line -----------------------------------------------------


def test_parse_entry_line_kg_with_notes() -> None:
    entry = _parse_entry_line("- 2026-05-28: 72.4 kg | notes: post-run", 1)
    assert entry is not None
    assert entry.date == date(2026, 5, 28)
    assert entry.weight == pytest.approx(72.4)
    assert entry.unit == "kg"
    assert entry.notes == "post-run"
    assert entry.source_line == 1


def test_parse_entry_line_lbs_normalised_to_lb() -> None:
    entry = _parse_entry_line("- 2026-05-25: 160.2 lbs", 4)
    assert entry is not None
    assert entry.unit == "lb"
    assert entry.weight == pytest.approx(160.2)


def test_parse_entry_line_missing_unit_defaults_kg() -> None:
    entry = _parse_entry_line("- 2026-05-27: 72.8", 1)
    assert entry is not None
    assert entry.unit == "kg"
    assert entry.weight == pytest.approx(72.8)
    assert entry.notes is None


def test_parse_entry_line_malformed_returns_none() -> None:
    assert _parse_entry_line("- not-a-date: 72.0 kg", 1) is None
    assert _parse_entry_line("- 2026-05-28: bogus kg", 1) is None
    assert _parse_entry_line("- 2026-05-28: 72.4 stone", 1) is None


# ---- parse_weight ----------------------------------------------------------


def test_parse_weight_missing_file_returns_absent_context(tmp_path: Path) -> None:
    ctx = parse_weight(tmp_path / "nope.md")
    assert isinstance(ctx, WeightContext)
    assert ctx.present is False
    assert ctx.entries == ()
    assert ctx.path is None
    assert ctx.malformed_lines == ()


def test_parse_weight_happy_path_kg_and_lb(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "# Weight log\n"
        "\n"
        "- 2026-05-28: 72.4 kg | notes: post-run\n"
        "- 2026-05-27: 159.6 lb\n"
        "- 2026-05-26: 73.1 kg\n"
        "- 2026-05-25: 160.2 lbs\n",
    )
    ctx = parse_weight(p)
    assert ctx.present is True
    assert ctx.path == p
    assert ctx.malformed_lines == ()
    assert len(ctx.entries) == 4

    # Sorted ascending by date.
    dates = [e.date for e in ctx.entries]
    assert dates == sorted(dates)
    assert dates == [
        date(2026, 5, 25),
        date(2026, 5, 26),
        date(2026, 5, 27),
        date(2026, 5, 28),
    ]

    # Last (newest) entry: kg + notes preserved.
    newest = ctx.entries[-1]
    assert newest.unit == "kg"
    assert newest.weight == pytest.approx(72.4)
    assert newest.notes == "post-run"

    # The `lbs` entry from line 7 normalised to `lb`.
    earliest = ctx.entries[0]
    assert earliest.unit == "lb"
    assert earliest.weight == pytest.approx(160.2)
    assert earliest.notes is None

    # Check the 2026-05-26 kg entry specifically (middle kg entry, no notes).
    by_date = {e.date: e for e in ctx.entries}
    assert by_date[date(2026, 5, 26)].unit == "kg"
    assert by_date[date(2026, 5, 26)].notes is None


def test_parse_weight_skips_malformed_dates_and_floats(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "- 2026-05-28: 72.4 kg\n"
        "- not-a-date: 72.0 kg\n"
        "- 2026-05-27: bogus kg\n"
        "- 2026-05-26: 72.1 kg\n",
    )
    ctx = parse_weight(p)
    assert len(ctx.entries) == 2
    valid_dates = {e.date for e in ctx.entries}
    assert valid_dates == {date(2026, 5, 28), date(2026, 5, 26)}
    # Lines 2 and 3 are the malformed ones (1-indexed).
    assert ctx.malformed_lines == (2, 3)


def test_parse_weight_latest_wins_on_duplicate_date(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "- 2026-05-28: 72.4 kg | notes: morning\n"
        "- 2026-05-28: 72.6 kg | notes: evening\n",
    )
    ctx = parse_weight(p)
    assert len(ctx.entries) == 1
    survivor = ctx.entries[0]
    assert survivor.weight == pytest.approx(72.6)
    assert survivor.notes == "evening"


def test_parse_weight_handles_optional_notes(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "- 2026-05-28: 72.4 kg | notes: post-run | feels great | maybe dehydrated\n"
        "- 2026-05-27: 72.8 kg\n",
    )
    ctx = parse_weight(p)
    assert len(ctx.entries) == 2
    with_notes = next(e for e in ctx.entries if e.date == date(2026, 5, 28))
    # Only the FIRST `| notes:` is the split point; subsequent `|` pipes are
    # part of the notes verbatim.
    assert with_notes.notes == "post-run | feels great | maybe dehydrated"
    no_notes = next(e for e in ctx.entries if e.date == date(2026, 5, 27))
    assert no_notes.notes is None


def test_parse_weight_rejects_out_of_range_weights(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "- 2026-05-28: 7.24 kg\n"   # decimal slip — too low
        "- 2026-05-27: 724 kg\n"    # unit slip — too high
        "- 2026-05-26: 1600 lb\n"   # ~726 kg — too high
        "- 2026-05-25: 72.4 kg\n",  # valid
    )
    ctx = parse_weight(p)
    assert len(ctx.entries) == 1
    assert ctx.entries[0].date == date(2026, 5, 25)
    assert ctx.entries[0].weight == pytest.approx(72.4)
    assert ctx.malformed_lines == (1, 2, 3)


def test_parse_weight_ignores_headers_and_blanks(tmp_path: Path) -> None:
    p = _write_weight(
        tmp_path,
        "# Weight log\n"
        "\n"
        "Some prose paragraph explaining things.\n"
        "\n"
        "## A sub-heading\n"
        "- 2026-05-28: 72.4 kg\n"
        "\n"
        "- 2026-05-27: 72.8 kg\n",
    )
    ctx = parse_weight(p)
    assert len(ctx.entries) == 2
    # Headers, blanks, and prose lines should NOT appear in malformed_lines.
    assert ctx.malformed_lines == ()


# ---- weight_rollup ---------------------------------------------------------


def test_weight_rollup_empty_returns_all_none() -> None:
    rollup = weight_rollup((), TODAY)
    assert rollup.latest_entry is None
    assert rollup.latest_kg is None
    assert rollup.days_since_last is None
    assert rollup.avg_7d is None
    assert rollup.avg_28d is None
    assert rollup.ewma_trend is None
    assert rollup.delta_vs_28d is None
    assert rollup.unit_mixed is False


def test_weight_rollup_single_entry_today() -> None:
    entry = WeightEntry(
        date=TODAY, weight=72.4, unit="kg", notes=None, source_line=1
    )
    rollup = weight_rollup((entry,), TODAY)
    assert rollup.latest_entry is entry
    assert rollup.latest_kg == pytest.approx(72.4)
    assert rollup.days_since_last == 0
    assert rollup.avg_7d == pytest.approx(72.4)
    assert rollup.avg_28d == pytest.approx(72.4)
    assert rollup.ewma_trend == pytest.approx(72.4)  # seed-only
    assert rollup.delta_vs_28d == pytest.approx(0.0)
    assert rollup.unit_mixed is False


def test_weight_rollup_7d_28d_windows_left_open() -> None:
    # Entries at TODAY, TODAY-7, TODAY-8, TODAY-28, TODAY-29.
    # 7d window is (TODAY-7, TODAY]  -> TODAY entry ONLY (TODAY-7 excluded).
    # 28d window is (TODAY-28, TODAY] -> TODAY, TODAY-7, TODAY-8 (TODAY-28 excluded).
    entries = (
        WeightEntry(date=TODAY - timedelta(days=29), weight=80.0, unit="kg",
                    notes=None, source_line=1),
        WeightEntry(date=TODAY - timedelta(days=28), weight=79.0, unit="kg",
                    notes=None, source_line=2),
        WeightEntry(date=TODAY - timedelta(days=8), weight=78.0, unit="kg",
                    notes=None, source_line=3),
        WeightEntry(date=TODAY - timedelta(days=7), weight=77.0, unit="kg",
                    notes=None, source_line=4),
        WeightEntry(date=TODAY, weight=70.0, unit="kg", notes=None, source_line=5),
    )
    rollup = weight_rollup(entries, TODAY)
    # 7d window: only TODAY -> avg = 70.0.
    assert rollup.avg_7d == pytest.approx(70.0)
    # 28d window: TODAY (70) + TODAY-7 (77) + TODAY-8 (78) -> mean = 75.0.
    assert rollup.avg_28d == pytest.approx(75.0)
    # latest = TODAY entry.
    assert rollup.latest_kg == pytest.approx(70.0)
    assert rollup.days_since_last == 0
    # delta vs 28d baseline.
    assert rollup.delta_vs_28d == pytest.approx(70.0 - 75.0)
    assert rollup.unit_mixed is False


def test_weight_rollup_ewma_seeded_from_first_entry() -> None:
    # 3 entries weighted 70, 80, 90 in date-ascending order.
    # trend_0 = 70.0
    # trend_1 = 0.1 * 80 + 0.9 * 70 = 71.0
    # trend_2 = 0.1 * 90 + 0.9 * 71 = 72.9
    entries = (
        WeightEntry(date=TODAY - timedelta(days=4), weight=70.0, unit="kg",
                    notes=None, source_line=1),
        WeightEntry(date=TODAY - timedelta(days=2), weight=80.0, unit="kg",
                    notes=None, source_line=2),
        WeightEntry(date=TODAY, weight=90.0, unit="kg", notes=None, source_line=3),
    )
    rollup = weight_rollup(entries, TODAY)
    assert rollup.ewma_trend is not None
    assert abs(rollup.ewma_trend - 72.9) < 1e-9


def test_weight_rollup_unit_mixed_flag_normalises_to_kg() -> None:
    # 72.0 kg + 160.0 lb (~72.5747 kg), both within the 28d window.
    entries = (
        WeightEntry(date=TODAY - timedelta(days=3), weight=72.0, unit="kg",
                    notes=None, source_line=1),
        WeightEntry(date=TODAY, weight=160.0, unit="lb", notes=None, source_line=2),
    )
    rollup = weight_rollup(entries, TODAY)
    assert rollup.unit_mixed is True
    expected_avg = (72.0 + 160.0 * 0.453592) / 2
    assert rollup.avg_28d == pytest.approx(expected_avg)
    assert rollup.latest_kg == pytest.approx(160.0 * 0.453592)
    # latest_entry preserves the original unit.
    assert rollup.latest_entry is not None
    assert rollup.latest_entry.unit == "lb"


def test_weight_rollup_days_since_last_computed() -> None:
    entry = WeightEntry(
        date=TODAY - timedelta(days=3), weight=72.4, unit="kg",
        notes=None, source_line=1,
    )
    rollup = weight_rollup((entry,), TODAY)
    assert rollup.days_since_last == 3
