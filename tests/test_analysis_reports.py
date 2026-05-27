"""End-to-end analysis: seed a temp DB, run the analyses, assert the reports.

Covers ANL-01, ANL-02, ANL-05, DELIV-01 and the data->load->report integration.
We seed Strava-shaped raw rows across a date range, run the real transform, then
run the analysis runner and assert:

* dated markdown report files are written into the reports dir;
* each report header states per-source last-successful-sync + data freshness;
* the load series is built on the zero-filled spine (rest days included);
* race readiness uses races.md/plan.md context and degrades when absent.

No network, no credentials.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from tempo.analysis import runner
from tempo.analysis.fitness import evaluate_guardrail
from tempo.analysis.load import LoadConfig, LoadMethod
from tempo.connectors.base import RawWriter
from tempo.sync import state
from tempo.transforms.runner import run_transform
from tests.strava_fakes import make_run

START = date(2026, 1, 1)
GEN_ON = date(2026, 3, 15)
CFG = LoadConfig(threshold_pace_s_per_km=240.0, max_hr=190, resting_hr=50, threshold_hr=170)


def _seed(conn: sqlite3.Connection, *, n_days: int = 70, synced: bool = True) -> None:
    """Seed n_days of synthetic activities (rest every 3rd day) + a sync watermark."""
    raw = RawWriter(conn, "strava")
    last_ts = None
    with conn:
        for i in range(n_days):
            d = START + timedelta(days=i)
            if i % 3 == 2:  # rest day
                continue
            aid = 2000 + i
            speed = 4.0 if i % 6 == 0 else 3.0  # some quality, mostly easy
            raw.put(
                "activity_summary",
                str(aid),
                make_run(aid, day=d.isoformat(), average_speed=speed),
            )
            last_ts = f"{d.isoformat()}T06:00:00Z"
        if synced and last_ts:
            state.mark_synced(conn, "strava", last_entity_ts=last_ts)
    run_transform(conn, fill_to=GEN_ON)


def _write_context(tmp_path: Path) -> tuple[Path, Path]:
    races = tmp_path / "races.md"
    plan = tmp_path / "plan.md"
    races.write_text(
        "- Spring 10k - date: 2026-03-22 | distance: 10k | goal: 38:00 | priority: A\n"
        "- Autumn Marathon - date: 2026-10-04 | distance: marathon | goal: 3:00:00 | priority: A\n",
        encoding="utf-8",
    )
    plan.write_text("Phase: Base\nFocus: build aerobic base\n", encoding="utf-8")
    return races, plan


# ---- load series built on the spine ---------------------------------------


def test_load_series_covers_every_spine_day(conn: sqlite3.Connection) -> None:
    _seed(conn)
    series = runner.build_load_series(conn, CFG)
    spine_days = conn.execute("SELECT day FROM date_spine ORDER BY day").fetchall()
    assert len(series.day_loads) == len(spine_days)
    assert len(series.points) == len(spine_days)
    # Rest days exist and carry zero load.
    rest = [dl for dl in series.day_loads if dl.method == "rest"]
    assert rest and all(dl.load == 0.0 for dl in rest)
    # Active days used rTSS (pace configured).
    active = [dl for dl in series.day_loads if dl.n_activities > 0]
    assert active and all(dl.method == LoadMethod.RTSS.value for dl in active)


def test_load_series_zero_filled_continuous(conn: sqlite3.Connection) -> None:
    _seed(conn)
    series = runner.build_load_series(conn, CFG)
    days = [date.fromisoformat(d) for d in series.days]
    # No gaps: consecutive days differ by exactly 1.
    for a, b in zip(days, days[1:], strict=False):
        assert (b - a).days == 1


def test_guardrail_computed_from_series(conn: sqlite3.Connection) -> None:
    _seed(conn)
    series = runner.build_load_series(conn, CFG)
    g = evaluate_guardrail(series.points)
    # 70 days of continuous load -> ACWR and ramp are both computable (not insufficient).
    assert g.acwr is not None
    assert g.ramp_rate is not None


# ---- report generation + freshness header ---------------------------------


def test_load_trend_report_written_with_freshness(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _seed(conn)
    reports = tmp_path / "reports"
    path = runner.generate_load_trend(conn, cfg=CFG, reports_dir=reports, generated_on=GEN_ON)
    assert path == reports / "2026-03-15-load-trend.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# Training Load & Trend" in text
    assert "## Data freshness" in text
    assert "**strava**: last successful sync" in text  # ANL-05
    assert "Activity data spans" in text
    assert "CTL (fitness)" in text and "ATL (fatigue)" in text and "TSB (form)" in text
    assert "ACWR" in text
    assert "Load method per day" in text  # method flag visible (LOAD-01)


def test_race_readiness_report_written_with_predictions(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed(conn)
    races, plan = _write_context(tmp_path)
    reports = tmp_path / "reports"
    path = runner.generate_race_readiness(
        conn, cfg=CFG, races_path=races, plan_path=plan, reports_dir=reports, generated_on=GEN_ON
    )
    assert path == reports / "2026-03-15-race-readiness.md"
    text = path.read_text(encoding="utf-8")
    assert "# Race Readiness" in text
    assert "## Data freshness" in text  # ANL-05
    assert "Spring 10k" in text  # upcoming race from races.md
    assert "Autumn Marathon" in text
    assert "Predicted time" in text  # Riegel/VDOT (ANL-02)
    assert "VDOT" in text and "Riegel" in text
    assert "Form check" in text  # CTL/TSB form half
    assert "**phase**: Base" in text  # plan.md context


def test_freshness_header_flags_stale_source(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _seed(conn)
    # Force the watermark to be old relative to the generated_on date.
    with conn:
        conn.execute(
            "UPDATE sync_state SET last_sync_at = ? WHERE source = 'strava'",
            ("2026-03-01T08:00:00+00:00",),
        )
    reports = tmp_path / "reports"
    path = runner.generate_load_trend(conn, cfg=CFG, reports_dir=reports, generated_on=GEN_ON)
    text = path.read_text(encoding="utf-8")
    assert "STALE" in text  # 14 days old > threshold


def test_reports_degrade_when_no_data(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # Empty DB (only spine-less): both reports should say insufficient, not crash.
    reports = tmp_path / "reports"
    lt = runner.generate_load_trend(
        conn, cfg=LoadConfig(), reports_dir=reports, generated_on=GEN_ON
    )
    rr = runner.generate_race_readiness(
        conn,
        cfg=LoadConfig(),
        races_path=tmp_path / "missing-races.md",
        plan_path=tmp_path / "missing-plan.md",
        reports_dir=reports,
        generated_on=GEN_ON,
    )
    assert "Insufficient data" in lt.read_text(encoding="utf-8")
    rr_text = rr.read_text(encoding="utf-8")
    assert "No `races.md` found" in rr_text


def test_race_readiness_no_races_file_degrades(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _seed(conn)
    reports = tmp_path / "reports"
    rr = runner.generate_race_readiness(
        conn,
        cfg=CFG,
        races_path=tmp_path / "absent.md",
        plan_path=tmp_path / "absent-plan.md",
        reports_dir=reports,
        generated_on=GEN_ON,
    )
    text = rr.read_text(encoding="utf-8")
    assert "No `races.md` found" in text


def test_goal_gap_marks_ahead_or_behind(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _seed(conn)
    races = tmp_path / "races.md"
    # An easy goal (5:00:00 marathon) should be flagged on-track; a hard goal behind.
    races.write_text(
        "- Easy Goal - date: 2026-04-05 | distance: marathon | goal: 5:00:00\n"
        "- Hard Goal - date: 2026-04-05 | distance: marathon | goal: 2:30:00\n",
        encoding="utf-8",
    )
    plan = tmp_path / "plan.md"
    plan.write_text("Phase: peak\n", encoding="utf-8")
    reports = tmp_path / "reports"
    rr = runner.generate_race_readiness(
        conn, cfg=CFG, races_path=races, plan_path=plan, reports_dir=reports, generated_on=GEN_ON
    )
    text = rr.read_text(encoding="utf-8")
    assert "on track" in text
    assert "behind by" in text


def test_best_recent_effort_picks_highest_vdot(conn: sqlite3.Connection) -> None:
    _seed(conn)
    effort = runner.best_recent_effort(conn, as_of=GEN_ON)
    assert effort is not None
    dist_m, time_s, label = effort
    # The quality (4.0 m/s) runs have the best VDOT; pick one of those.
    assert dist_m == pytest.approx(4.0 * 3600)
    assert "km in" in label


# ---- Race-link surfacing in render_race_readiness (TRACK-03 wiring) --------


def _render_with_links(race, link) -> str:
    """Render a one-race readiness section through render_race_readiness directly."""
    from tempo.analysis.context import PlanContext, RacesContext
    from tempo.analysis.report import RaceReadiness, render_race_readiness

    races_ctx = RacesContext(present=True, races=[race])
    plan_ctx = PlanContext(present=False, fields={})
    readiness = [
        RaceReadiness(
            race=race,
            prediction=None,
            goal_gap_s=None,
            weeks_out=None,
            form_note="no fitness data yet.",
        )
    ]
    return render_race_readiness(
        generated_on=date(2026, 4, 1),
        freshness=[],
        data_range=None,
        races_ctx=races_ctx,
        plan_ctx=plan_ctx,
        readiness=readiness,
        best_effort_label=None,
        latest_point=None,
        race_links=[link],
    )


def test_race_readiness_renders_result_when_linked() -> None:
    """A linked race with a result string surfaces the result + activity id."""
    from tempo.analysis.race_link import RaceLink
    from tempo.analysis.races import Race

    race = Race(
        name="Local Half",
        race_date=date(2026, 3, 22),
        distance_label="half",
        result="1:31:48",
    )
    link = RaceLink(race=race, activity_id=987654321, link_status="linked")
    text = _render_with_links(race, link)
    assert "1:31:48" in text
    assert "987654321" in text


def test_race_readiness_renders_no_activity_when_unlinked_no_match() -> None:
    """An unlinked-no-match race surfaces the explicit 'no activity' marker."""
    from tempo.analysis.race_link import RaceLink
    from tempo.analysis.races import Race

    race = Race(name="Past 10k", race_date=date(2026, 3, 22), distance_label="10k")
    link = RaceLink(race=race, activity_id=None, link_status="unlinked_no_match")
    text = _render_with_links(race, link)
    assert "No activity recorded for race date" in text


def test_race_readiness_renders_ambiguous_when_multiple() -> None:
    """A race with 2+ activities on day surfaces the 'multiple activities' marker."""
    from tempo.analysis.race_link import RaceLink
    from tempo.analysis.races import Race

    race = Race(name="Crit", race_date=date(2026, 3, 22), distance_label="40km")
    link = RaceLink(race=race, activity_id=None, link_status="unlinked_ambiguous")
    text = _render_with_links(race, link)
    assert "Multiple activities on race day" in text


def test_race_readiness_renders_nothing_when_unlinked_no_date() -> None:
    """A race without a date emits no link line (the missing date is its own marker)."""
    from tempo.analysis.race_link import RaceLink
    from tempo.analysis.races import Race

    race = Race(name="Someday Marathon", race_date=None, distance_label="marathon")
    link = RaceLink(race=race, activity_id=None, link_status="unlinked_no_date")
    text = _render_with_links(race, link)
    assert "No activity recorded for race date" not in text
    assert "Multiple activities on race day" not in text
    assert "cannot auto-link" not in text
