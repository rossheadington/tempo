"""Orchestrate the Phase-4 analyses and write the dated markdown reports.

This wires the read layer (:mod:`tempo.analysis.data`), the pure metrics
(:mod:`load`, :mod:`fitness`, :mod:`race`), and the races/heat parsers
(:mod:`races`, :mod:`heat`) into the two reports the milestone ships:

* ``tempo analyze load-trend``      -> ``reports/YYYY-MM-DD-load-trend.md``
* ``tempo analyze race-readiness``  -> ``reports/YYYY-MM-DD-race-readiness.md``
* ``tempo analyze`` (both)

Everything runs on already-stored, already-transformed data with **no network**.
The daily load series is built on the zero-filled spine (rest days = 0), which is
what makes the CTL/ATL/TSB EWMAs and the ACWR/ramp guardrail correct.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tempo.analysis import correlation as corr_mod
from tempo.analysis import data as dataread
from tempo.analysis import fitness, load, race
from tempo.analysis import races as ctx
from tempo.analysis import recovery as recovery_mod
from tempo.analysis import report as report_mod
from tempo.analysis.preferences import PreferencesContext, Units
from tempo.analysis.race_link import link_races_to_activities
from tempo.analysis.races import Race


@dataclass(frozen=True, slots=True)
class LoadSeries:
    """The daily load series + derived PMC points, built on the spine."""

    days: list[str]
    day_loads: list[load.DayLoad]
    points: list[fitness.FitnessPoint]


def build_load_series(conn: sqlite3.Connection, cfg: load.LoadConfig) -> LoadSeries:
    """Build the continuous daily load series (rest days = 0) and the PMC series.

    Iterates the zero-filled ``date_spine`` so every calendar day has an entry;
    days with no activity are rest days (load 0). Each activity is scored with
    rTSS (or hrTSS fallback, or insufficient) and summed per day. The resulting
    contiguous series feeds the CTL/ATL/TSB EWMAs.
    """
    spine_days = dataread.read_spine_days(conn)
    by_day = dataread.activities_by_day(conn)
    srpe_by_day = dataread.srpe_by_day(conn)

    day_loads: list[load.DayLoad] = []
    for day in spine_days:
        records = by_day.get(day, [])
        activity_loads = [
            load.compute_activity_load(
                duration_s=rec.moving_s,
                avg_pace_s_per_km=rec.avg_pace_s_km,
                avg_hr=rec.avg_hr,
                config=cfg,
            )
            for rec in records
        ]
        day_load = load.aggregate_day_load(day, activity_loads)
        # sRPE fallback: when pace/HR load is insufficient (or a journaled rest-day
        # cross-training session exists), use the day's sRPE as the load (JRNL-03).
        day_load = load.apply_srpe_fallback(day_load, srpe_by_day.get(day))
        day_loads.append(day_load)

    loads = [dl.load for dl in day_loads]
    points = fitness.fitness_series(spine_days, loads)
    return LoadSeries(days=spine_days, day_loads=day_loads, points=points)


def weekly_rollups(conn: sqlite3.Connection, series: LoadSeries) -> list[report_mod.WeeklyRollup]:
    """Aggregate activities + load into ISO-week rollups for the load report."""
    activities = dataread.read_activities(conn)
    load_by_day = {dl.day: dl.load for dl in series.day_loads}

    # Map day -> ISO (year, week) label using the spine's own calendar math.
    week_of_day: dict[str, str] = {}
    for day in series.days:
        iso = date.fromisoformat(day).isocalendar()
        week_of_day[day] = f"{iso.year}-W{iso.week:02d}"

    rollup: dict[str, dict[str, float]] = {}
    for label in week_of_day.values():
        rollup.setdefault(label, {"n": 0.0, "dist": 0.0, "dur": 0.0, "load": 0.0})

    for rec in activities:
        label = week_of_day.get(rec.day)
        if label is None:
            continue
        agg = rollup[label]
        agg["n"] += 1
        agg["dist"] += rec.distance_m or 0.0
        agg["dur"] += rec.moving_s or 0.0

    for day, label in week_of_day.items():
        rollup[label]["load"] += load_by_day.get(day, 0.0)

    weeks: list[report_mod.WeeklyRollup] = []
    for label in sorted(rollup):
        agg = rollup[label]
        if agg["n"] == 0 and agg["load"] == 0:
            continue
        weeks.append(
            report_mod.WeeklyRollup(
                week_label=label,
                n_activities=int(agg["n"]),
                distance_m=agg["dist"],
                duration_h=agg["dur"] / 3600.0,
                load=agg["load"],
            )
        )
    return weeks


def best_recent_effort(
    conn: sqlite3.Connection, *, as_of: date, within_days: int = 120
) -> tuple[float, float, str] | None:
    """Find the best recent effort to predict races from: ``(distance_m, time_s, label)``.

    "Best" = highest VDOT among runs in the last ``within_days`` with both a usable
    distance and moving time. Returns ``None`` when no qualifying effort exists
    (the readiness report then degrades to "insufficient data").
    """
    activities = dataread.read_activities(conn)
    cutoff = as_of.toordinal() - within_days
    best: tuple[float, float, str] | None = None
    best_vdot = -1.0
    for rec in activities:
        if rec.distance_m is None or rec.moving_s is None:
            continue
        if rec.distance_m <= 0 or rec.moving_s <= 0:
            continue
        try:
            day_ord = date.fromisoformat(rec.day).toordinal()
        except ValueError:
            continue
        if day_ord < cutoff:
            continue
        try:
            v = race.vdot_from_performance(rec.distance_m, rec.moving_s)
        except ValueError:
            continue
        if v > best_vdot:
            best_vdot = v
            label = f"{rec.distance_m / 1000:.1f}km in {race.format_hms(rec.moving_s)} on {rec.day}"
            best = (rec.distance_m, float(rec.moving_s), label)
    return best


def _form_note(point: fitness.FitnessPoint | None, weeks_out: int | None) -> str:
    """A qualitative CTL/TSB form note for a race (the form check half of ANL-02)."""
    if point is None:
        return "no fitness data yet -- build a load history first."
    if weeks_out is None:
        return f"current CTL {point.ctl:.0f}, TSB {point.tsb:+.0f}."
    if weeks_out <= 1:
        if point.tsb >= 0:
            return f"race week: TSB {point.tsb:+.0f} -- well tapered and fresh."
        return f"race week but TSB {point.tsb:+.0f} -- still carrying fatigue; ease off."
    if weeks_out <= 3:
        return f"{weeks_out} weeks out: begin sharpening; current TSB {point.tsb:+.0f}."
    return f"{weeks_out} weeks out: keep building fitness (CTL {point.ctl:.0f})."


def build_race_readiness(
    conn: sqlite3.Connection,
    races_ctx: ctx.RacesContext,
    series: LoadSeries,
    *,
    as_of: date,
) -> tuple[list[report_mod.RaceReadiness], str | None]:
    """Build readiness findings for upcoming races + the best-effort label used."""
    effort = best_recent_effort(conn, as_of=as_of)
    latest = series.points[-1] if series.points else None
    findings: list[report_mod.RaceReadiness] = []

    upcoming = races_ctx.upcoming(as_of) if races_ctx.present else []
    for r in upcoming:
        weeks_out = None
        if r.race_date is not None:
            weeks_out = max(0, (r.race_date - as_of).days // 7)
        prediction = None
        goal_gap = None
        if effort is not None and r.distance_m is not None:
            dist_m, time_s, _label = effort
            prediction = race.predict_race(
                known_distance_m=dist_m, known_time_s=time_s, target_distance_m=r.distance_m
            )
            if r.goal_time_s is not None:
                goal_gap = prediction.vdot_s - r.goal_time_s
        findings.append(
            report_mod.RaceReadiness(
                race=r,
                prediction=prediction,
                goal_gap_s=goal_gap,
                weeks_out=weeks_out,
                form_note=_form_note(latest, weeks_out),
            )
        )
    label = effort[2] if effort is not None else None
    return findings, label


# ---------------------------------------------------------------------------
# Top-level report generation (writes files)
# ---------------------------------------------------------------------------


def _load_config_from_prefs(prefs: PreferencesContext) -> load.LoadConfig:
    """Build a :class:`load.LoadConfig` from a parsed preferences context.

    Pure shim — the four physiology knobs (threshold pace + max/resting/threshold
    HR) used to live on :class:`Settings`; Phase 17 moved them into
    ``preferences.md`` so :class:`Physiology` is now the authoritative source.
    """
    return load.LoadConfig(
        threshold_pace_s_per_km=prefs.physiology.threshold_pace_s_per_km,
        max_hr=prefs.physiology.max_hr,
        resting_hr=prefs.physiology.resting_hr,
        threshold_hr=prefs.physiology.threshold_hr,
    )


def _write_report(reports_dir: Path, name: str, generated_on: date, text: str) -> Path:
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = reports_dir / f"{generated_on.isoformat()}-{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def generate_load_trend(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    reports_dir: Path,
    generated_on: date,
    units: Units | None = None,
) -> Path:
    """Compute the load-trend findings and write the dated markdown report.

    ``units`` controls the display unit for the weekly-distance column; defaults
    to the Phase 17 ``Units()`` (km + min/km) for back-compat. Callers may pass
    ``prefs.units`` to honour ``preferences.md`` (see :func:`tempo.cli`).
    """
    series = build_load_series(conn, cfg)
    guardrail = fitness.evaluate_guardrail(series.points)
    weeks = weekly_rollups(conn, series)
    freshness = dataread.source_freshness(conn, as_of=generated_on)
    data_range = dataread.data_date_range(conn)
    has_config = cfg.threshold_pace_s_per_km is not None or cfg.max_hr is not None

    text = report_mod.render_load_trend(
        generated_on=generated_on,
        freshness=freshness,
        data_range=data_range,
        day_loads=series.day_loads,
        points=series.points,
        guardrail=guardrail,
        weeks=weeks,
        has_load_config=has_config,
        units=units if units is not None else Units(),
    )
    return _write_report(reports_dir, "load-trend", generated_on, text)


def generate_race_readiness(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    races_path: Path,
    reports_dir: Path,
    generated_on: date,
) -> Path:
    """Compute the race-readiness findings and write the dated markdown report.

    Also computes the race-to-activity auto-link (TRACK-03) from the parsed
    races, threading the parallel ``RaceLink`` list into the renderer so each
    race surfaces its linked activity / unlinked-status marker.
    """
    series = build_load_series(conn, cfg)
    races_ctx = ctx.parse_races(races_path)
    race_links = link_races_to_activities(races_ctx.races, conn)
    findings, best_label = build_race_readiness(conn, races_ctx, series, as_of=generated_on)
    freshness = dataread.source_freshness(conn, as_of=generated_on)
    data_range = dataread.data_date_range(conn)
    latest = series.points[-1] if series.points else None

    text = report_mod.render_race_readiness(
        generated_on=generated_on,
        freshness=freshness,
        data_range=data_range,
        races_ctx=races_ctx,
        readiness=findings,
        best_effort_label=best_label,
        latest_point=latest,
        race_links=race_links,
    )
    return _write_report(reports_dir, "race-readiness", generated_on, text)


def generate_recovery(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    heat_path: Path,
    strength_path: Path | None = None,
    weight_path: Path | None = None,
    food_path: Path | None = None,
    target_kcal: int | None = None,
    reports_dir: Path,
    generated_on: date,
) -> Path:
    """Compute the multi-signal recovery findings and write the dated report (ANL-03).

    ``heat_path`` points at the user's optional ``heat.md`` heat-adaptation log;
    a missing file is fine -- the recovery report degrades to omitting the heat
    section rather than failing. When present, the parsed rollup is rendered
    into a ``## Heat adaptation`` section (per the A4 3-state degradation rule
    implemented in :func:`tempo.analysis.recovery._render_heat_section`).

    If ``strength_path`` is provided, the parsed rollup is rendered into a
    ``## Strength & conditioning`` section using the same 3-state degradation
    rule as heat.

    If ``weight_path`` is provided, the parsed rollup is rendered into a
    ``## Weight`` section using the same 3-state degradation rule as heat and
    strength.

    If ``food_path`` is provided, the parsed nutrition rollup is rendered into
    a ``## Nutrition`` section using the same 3-state degradation rule as the
    other trackers (staleness threshold tightens to >3 days because food is
    logged daily). ``target_kcal`` enables the optional goal-delta line.
    """
    series = build_load_series(conn, cfg)
    guardrail = fitness.evaluate_guardrail(series.points)
    assessment = recovery_mod.assess_recovery_from_db(
        conn,
        points=series.points,
        guardrail=guardrail,
        heat_path=heat_path,
        strength_path=strength_path,
        weight_path=weight_path,
        food_path=food_path,
        target_kcal=target_kcal,
    )
    freshness = dataread.source_freshness(conn, as_of=generated_on)
    data_range = dataread.data_date_range(conn)

    text = recovery_mod.render_recovery(
        generated_on=generated_on,
        freshness=freshness,
        data_range=data_range,
        assessment=assessment,
    )
    return _write_report(reports_dir, "recovery", generated_on, text)


def generate_nutrition(
    conn: sqlite3.Connection | None,
    *,
    cfg: load.LoadConfig,
    reports_dir: Path,
    generated_on: date,
    food_path: Path,
    target_kcal: int | None = None,
) -> Path:
    """Write the dated nutrition report (NUTR-05).

    Parses ``food.md``, builds a ``NutritionRollup`` for ``generated_on``, and
    renders the standalone nutrition report into
    ``reports/<YYYY-MM-DD>-nutrition.md``. ``conn`` and ``cfg`` are accepted
    for runner-signature uniformity (the daily pipeline opens one connection
    for the whole analysis suite); this report does NOT read from the DB --
    it's a pure file parse + render.
    """
    from tempo.analysis import nutrition as nutrition_mod
    from tempo.analysis import nutrition_report as nutrition_report_mod

    context = nutrition_mod.parse_food(food_path)
    today_breakdown = nutrition_mod.daily_nutrition(context.entries, generated_on)
    rollup = nutrition_mod.nutrition_rollup(
        context.entries, generated_on, target_kcal=target_kcal
    )
    blocks_today = tuple(b for b in context.blocks if b.date == generated_on)
    text = nutrition_report_mod.render_nutrition(
        generated_on, rollup, today_breakdown, blocks_today, context
    )
    return _write_report(reports_dir, "nutrition", generated_on, text)


def generate_correlations(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    reports_dir: Path,
    generated_on: date,
) -> Path:
    """Compute the n-gated correlation insight and write the dated report (ANL-04)."""
    series = build_load_series(conn, cfg)
    load_by_day = {dl.day: dl.load for dl in series.day_loads}
    observations = corr_mod.read_observations(conn, load_by_day)
    results = corr_mod.build_correlations(observations)
    freshness = dataread.source_freshness(conn, as_of=generated_on)
    data_range = dataread.data_date_range(conn)

    text = corr_mod.render_correlations(
        generated_on=generated_on,
        freshness=freshness,
        data_range=data_range,
        results=results,
    )
    return _write_report(reports_dir, "correlations", generated_on, text)


@dataclass(frozen=True, slots=True)
class AnalyzeResult:
    """Paths of the reports written by an ``analyze`` run (full suite)."""

    load_trend: Path | None = None
    race_readiness: Path | None = None
    recovery: Path | None = None
    correlations: Path | None = None
    nutrition: Path | None = None

    def paths(self) -> list[Path]:
        return [
            p
            for p in (
                self.load_trend,
                self.race_readiness,
                self.recovery,
                self.correlations,
                self.nutrition,
            )
            if p
        ]


def generate_all(
    conn: sqlite3.Connection,
    *,
    cfg: load.LoadConfig,
    races_path: Path,
    heat_path: Path,
    strength_path: Path | None = None,
    weight_path: Path | None = None,
    food_path: Path | None = None,
    target_kcal: int | None = None,
    reports_dir: Path,
    generated_on: date,
    units: Units | None = None,
) -> AnalyzeResult:
    """Run the FULL analysis suite (load-trend, race-readiness, recovery, correlations, nutrition).

    This is what the bare ``tempo analyze`` and the daily scheduled run invoke. The
    five reports are written to the gitignored reports dir, each with its own
    per-source freshness header. No network. ``food_path`` is optional for
    back-compat: callers that don't pass it skip the nutrition report entirely.
    ``units`` controls the display unit for the weekly-distance column
    (Phase 17); defaults to km when ``None``.
    """
    return AnalyzeResult(
        load_trend=generate_load_trend(
            conn,
            cfg=cfg,
            reports_dir=reports_dir,
            generated_on=generated_on,
            units=units,
        ),
        race_readiness=generate_race_readiness(
            conn,
            cfg=cfg,
            races_path=races_path,
            reports_dir=reports_dir,
            generated_on=generated_on,
        ),
        recovery=generate_recovery(
            conn,
            cfg=cfg,
            heat_path=heat_path,
            strength_path=strength_path,
            weight_path=weight_path,
            food_path=food_path,
            target_kcal=target_kcal,
            reports_dir=reports_dir,
            generated_on=generated_on,
        ),
        correlations=generate_correlations(
            conn, cfg=cfg, reports_dir=reports_dir, generated_on=generated_on
        ),
        nutrition=(
            generate_nutrition(
                conn,
                cfg=cfg,
                reports_dir=reports_dir,
                generated_on=generated_on,
                food_path=food_path,
                target_kcal=target_kcal,
            )
            if food_path is not None
            else None
        ),
    )


# Re-exported so tests / callers don't need to know about Race internals.
__all__ = [
    "AnalyzeResult",
    "LoadSeries",
    "Race",
    "build_load_series",
    "generate_all",
    "generate_correlations",
    "generate_load_trend",
    "generate_nutrition",
    "generate_race_readiness",
    "generate_recovery",
]
