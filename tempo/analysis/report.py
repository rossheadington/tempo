"""Render the dated markdown reports (load-trend + race-readiness).

Both reports are plain markdown written into the gitignored ``reports/`` dir
(DELIV-01). Every report opens with a **freshness header** stating, per source,
the last successful sync and how stale it is, plus the data date range -- so stale
data is never trusted silently (ANL-05; PITFALLS). When the inputs are too thin,
sections degrade to an explicit "insufficient data" note rather than fabricating
numbers.

These functions are pure string builders: they take already-computed findings and
return markdown text. The :mod:`tempo.analysis.runner` gathers the findings and
writes the files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tempo.analysis.data import SourceFreshness
from tempo.analysis.fitness import FitnessPoint, Guardrail
from tempo.analysis.load import DayLoad
from tempo.analysis.preferences import Units
from tempo.analysis.race import RacePrediction, format_hms
from tempo.analysis.race_link import RaceLink
from tempo.analysis.races import Race, RacesContext
from tempo.units import format_distance

# A source synced longer ago than this is flagged stale in the header.
STALE_AFTER_DAYS = 2


@dataclass(frozen=True, slots=True)
class WeeklyRollup:
    """Per-week training volume / load summary for the load-trend report.

    ``distance_m`` is raw metres (SI), to keep the analysis layer unit-agnostic;
    the renderer converts to the user's preferred unit via
    :func:`tempo.units.format_distance` at the display boundary (Phase 17).
    """

    week_label: str
    n_activities: int
    distance_m: float
    duration_h: float
    load: float


def freshness_header(
    *,
    report_title: str,
    generated_on: date,
    freshness: list[SourceFreshness],
    data_range: tuple[str, str] | None,
) -> str:
    """Build the per-source freshness / data-range header for any report (ANL-05)."""
    lines = [f"# {report_title}", "", f"_Generated: {generated_on.isoformat()}_", ""]
    lines.append("## Data freshness")
    lines.append("")
    if not freshness:
        lines.append("- No sources have synced yet -- this report has no source data.")
    else:
        for f in freshness:
            if f.last_sync_at is None:
                lines.append(f"- **{f.source}**: never synced -- no data from this source.")
                continue
            if f.days_stale is None:
                staleness = ""
            elif f.days_stale <= 0:
                staleness = " (synced today)"
            elif f.days_stale == 1:
                staleness = " (1 day ago)"
            else:
                staleness = f" ({f.days_stale} days ago)"
            flag = ""
            if f.days_stale is not None and f.days_stale > STALE_AFTER_DAYS:
                flag = "  :warning: **STALE -- data may be out of date**"
            lines.append(
                f"- **{f.source}**: last successful sync {f.last_sync_at}{staleness}{flag}"
            )
    lines.append("")
    if data_range is not None:
        lines.append(f"_Activity data spans **{data_range[0]} -> {data_range[1]}**._")
    else:
        lines.append("_No activity data is present yet._")
    lines.append("")
    return "\n".join(lines)


def _load_method_summary(day_loads: list[DayLoad]) -> str:
    """One line summarising how each day's load was derived (the method flag)."""
    counts: dict[str, int] = {}
    insufficient_days = 0
    for dl in day_loads:
        counts[dl.method] = counts.get(dl.method, 0) + 1
        if dl.n_insufficient:
            insufficient_days += 1
    parts = [f"{n} {method}" for method, n in sorted(counts.items())]
    line = "Load method per day: " + ", ".join(parts) + "."
    if insufficient_days:
        line += (
            f" :warning: {insufficient_days} day(s) had activities that could not be"
            " scored (insufficient pace/HR inputs); those contribute 0 load."
        )
    return line


def render_load_trend(
    *,
    generated_on: date,
    freshness: list[SourceFreshness],
    data_range: tuple[str, str] | None,
    day_loads: list[DayLoad],
    points: list[FitnessPoint],
    guardrail: Guardrail,
    weeks: list[WeeklyRollup],
    has_load_config: bool,
    units: Units | None = None,
) -> str:
    """Render the training-load & trend report (ANL-01, LOAD-02/03).

    ``units`` controls the weekly-distance column label + value. When ``None``
    (the default) the renderer uses :class:`Units` defaults (km + min/km) so
    pre-Phase-17 callers see no change.
    """
    units = units if units is not None else Units()
    out = [
        freshness_header(
            report_title="Training Load & Trend",
            generated_on=generated_on,
            freshness=freshness,
            data_range=data_range,
        )
    ]

    if not points:
        out.append("## Summary\n")
        out.append(
            "**Insufficient data**: no daily load series could be built. Sync and "
            "transform Strava activities first (`tempo sync && tempo transform`).\n"
        )
        return "\n".join(out)

    if not has_load_config:
        out.append(
            "> :warning: No threshold pace or HR config set -- per-activity load may be "
            "**insufficient** for many days. Fill in `## Physiology` in `preferences.md` "
            "(threshold_pace / max_hr / resting_hr) for accurate rTSS/hrTSS load.\n"
        )

    latest = points[-1]
    out.append("## Current fitness, fatigue & form (PMC)\n")
    out.append(f"- **CTL (fitness)**: {latest.ctl:.1f}")
    out.append(f"- **ATL (fatigue)**: {latest.atl:.1f}")
    out.append(f"- **TSB (form)**: {latest.tsb:+.1f}  ({_tsb_phrase(latest.tsb)})")
    out.append("")
    out.append(_load_method_summary(day_loads))
    out.append("")

    out.append("## Load guardrail (ACWR + ramp rate)\n")
    for msg in guardrail.messages:
        out.append(f"- {msg}")
    out.append("")

    out.append("## Weekly volume & load\n")
    if weeks:
        distance_label = "Distance (mi)" if units.distance == "miles" else "Distance (km)"
        out.append(f"| Week | Runs | {distance_label} | Time (h) | Load |")
        out.append("|------|------|---------------|----------|------|")
        for w in weeks[-12:]:
            # ``format_distance`` returns "12.9 km" / "8.0 mi"; strip the unit
            # suffix because the column header already states the unit.
            dist_str = format_distance(w.distance_m, units, precision=1)
            dist_value = dist_str.rsplit(" ", 1)[0] if " " in dist_str else dist_str
            out.append(
                f"| {w.week_label} | {w.n_activities} | {dist_value} | "
                f"{w.duration_h:.1f} | {w.load:.0f} |"
            )
    else:
        out.append("_No weekly data._")
    out.append("")
    return "\n".join(out)


def _tsb_phrase(tsb: float) -> str:
    if tsb > 15:
        return "very fresh / detraining risk if sustained"
    if tsb >= 5:
        return "fresh / tapered"
    if tsb >= -10:
        return "productive training zone"
    if tsb >= -30:
        return "fatigued"
    return "deeply fatigued -- recovery needed"


@dataclass(frozen=True, slots=True)
class RaceReadiness:
    """A race-readiness finding for one target race."""

    race: Race
    prediction: RacePrediction | None
    goal_gap_s: float | None  # predicted - goal (negative = ahead of goal)
    weeks_out: int | None
    form_note: str


def _link_line(link: RaceLink | None) -> str | None:
    """Render the per-race auto-link line (TRACK-03), or None to emit nothing.

    The four ``link_status`` values map to four phrasings:

    * ``linked`` -- if ``race.result`` is populated, surface the result (the race
      already happened and the user logged a time) plus the activity id for the
      audit trail; otherwise just note an activity was recorded on the day.
    * ``unlinked_no_match`` -- explicit "no activity" marker (the date passed and
      nothing showed up; the user might not have worn the watch).
    * ``unlinked_ambiguous`` -- multiple activities on the day; the linker
      refuses to guess and surfaces the ambiguity so the user can resolve it.
    * ``unlinked_no_date`` -- the race has no parseable date; the missing date
      is already obvious from the heading not carrying one, so we emit nothing.
    """
    if link is None:
        return None
    status = link.link_status
    race = link.race
    if status == "linked":
        if race.result is not None:
            return f"- **Result**: {race.result} (activity id: {link.activity_id})"
        return f"- Activity recorded on race day (id: {link.activity_id})."
    if status == "unlinked_no_match":
        return "- _No activity recorded for race date._"
    if status == "unlinked_ambiguous":
        return "- _Multiple activities on race day; cannot auto-link._"
    # unlinked_no_date -- emit nothing; the heading already tells the story.
    return None


def render_race_readiness(
    *,
    generated_on: date,
    freshness: list[SourceFreshness],
    data_range: tuple[str, str] | None,
    races_ctx: RacesContext,
    readiness: list[RaceReadiness],
    best_effort_label: str | None,
    latest_point: FitnessPoint | None,
    race_links: list[RaceLink] | None = None,
) -> str:
    """Render the race-readiness report (ANL-02 + CTL/TSB form check + TRACK-03 links)."""
    out = [
        freshness_header(
            report_title="Race Readiness",
            generated_on=generated_on,
            freshness=freshness,
            data_range=data_range,
        )
    ]

    if not races_ctx.present:
        out.append("## Races\n")
        out.append(
            "**No `races.md` found** -- create one (see `races.md.example`) so Tempo "
            "knows your target races. Without it, readiness cannot be assessed.\n"
        )
    elif not races_ctx.races:
        out.append("## Races\n")
        out.append("`races.md` is present but lists no parseable races.\n")

    if latest_point is not None:
        out.append("## Current form (CTL/TSB)\n")
        out.append(f"- **CTL (fitness)**: {latest_point.ctl:.1f}")
        out.append(f"- **TSB (form)**: {latest_point.tsb:+.1f}  ({_tsb_phrase(latest_point.tsb)})")
        out.append("")

    if best_effort_label:
        out.append(f"_Predictions are based on your best recent effort: {best_effort_label}._\n")

    if not readiness:
        out.append("## Readiness\n")
        if races_ctx.present and races_ctx.races:
            out.append(
                "**Insufficient data**: have races but no usable recent effort to "
                "predict from. Sync/transform some runs with pace or distance+time.\n"
            )
        return "\n".join(out)

    out.append("## Readiness by race\n")
    for r in readiness:
        race = r.race
        title = race.name
        if race.race_date:
            title += f" -- {race.race_date.isoformat()}"
        out.append(f"### {title}\n")
        if race.distance_label:
            out.append(f"- **Distance**: {race.distance_label}")
        if race.priority:
            out.append(f"- **Priority**: {race.priority}")
        if r.weeks_out is not None:
            out.append(f"- **Weeks out**: {r.weeks_out}")
        if r.prediction is not None:
            p = r.prediction
            out.append(
                f"- **Predicted time**: {format_hms(p.vdot_s)} (VDOT {p.vdot:.1f}) / "
                f"{format_hms(p.riegel_s)} (Riegel)"
            )
            if not p.reliable and p.note:
                out.append(f"  - :warning: {p.note}")
            if race.goal_time_s is not None and r.goal_gap_s is not None:
                if r.goal_gap_s <= 0:
                    out.append(
                        f"- **Goal {format_hms(race.goal_time_s)}**: on track "
                        f"(~{format_hms(abs(r.goal_gap_s))} ahead of goal). :white_check_mark:"
                    )
                else:
                    out.append(
                        f"- **Goal {format_hms(race.goal_time_s)}**: behind by "
                        f"~{format_hms(r.goal_gap_s)} at current fitness."
                    )
        else:
            out.append("- **Predicted time**: insufficient data to predict.")
        out.append(f"- **Form check**: {r.form_note}")
        # Identity-match the race against the parallel race_links list (when
        # provided) and append the auto-link line in the relevant phrasing.
        if race_links:
            matching = next((lk for lk in race_links if lk.race is race), None)
            line = _link_line(matching)
            if line is not None:
                out.append(line)
        out.append("")
    return "\n".join(out)
