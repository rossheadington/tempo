"""The daily loop: sync -> transform -> analyze, idempotent + catch-up-aware.

This is what the launchd LaunchAgent invokes once a day (``tempo run-daily``). It
is the heartbeat of the product, so it is built to the scheduled-job pitfalls
(PITFALLS 7):

* **Idempotent & catch-up-aware.** Sync is watermark-driven (Strava ``after`` /
  Garmin recent-days) and raw upserts are idempotent, so running it twice is
  harmless and a *missed* day is recovered on the next run automatically -- a run
  syncs everything since the last successful watermark, never just "today". The
  launchd ``StartCalendarInterval`` job itself fires a missed run on wake; this
  function is what makes that catch-up actually fill the gap rather than skip it.
* **Garmin stays isolated.** It reuses :func:`tempo.sync.pipeline.run_full_sync`,
  so a Garmin 429 / breakage is caught and skipped while Strava + transform +
  analyze still complete (GRMN-01/03).
* **Surface staleness, don't fail silently.** Per-source freshness is computed and
  fed into the noteworthy check; a stale source is itself a reason to surface.
* **Noteworthy-only.** All four reports are always written, but the run only
  *surfaces* (prints a NOTEWORTHY block to the launchd log and writes a marker
  file) when a threshold is crossed (SCHED-03).

It returns a structured result the CLI prints and tests assert against; it never
performs system-level side effects beyond writing into the data dir.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from tempo.analysis import context as ctx
from tempo.analysis import data as dataread
from tempo.analysis import fitness
from tempo.analysis import noteworthy as nw
from tempo.analysis import recovery as recovery_mod
from tempo.analysis import runner as analysis_runner
from tempo.analysis.load import LoadConfig
from tempo.analysis.runner import AnalyzeResult, build_load_series
from tempo.config import Settings
from tempo.sync import pipeline
from tempo.sync.pipeline import SourceResult

logger = logging.getLogger(__name__)

MARKER_NAME = "NOTEWORTHY.md"


@dataclass(frozen=True, slots=True)
class DailyRunResult:
    """The outcome of one ``tempo run-daily`` invocation."""

    generated_on: date
    sync_results: list[SourceResult]
    transform_summary: str
    reports: AnalyzeResult
    noteworthy: nw.NoteworthyResult
    marker_path: Path | None = None
    stale_sources: list[str] = field(default_factory=list)


def _load_config_from_settings(settings: Settings) -> LoadConfig:
    return LoadConfig(
        threshold_pace_s_per_km=settings.threshold_pace_s_per_km,
        max_hr=settings.max_hr,
        resting_hr=settings.resting_hr,
        threshold_hr=settings.threshold_hr,
    )


def run_daily(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    generated_on: date,
    do_sync: bool = True,
) -> DailyRunResult:
    """Run the full daily loop on an open connection. Safe to run repeatedly.

    ``do_sync=False`` skips the network sync step (used by tests and by an
    "analysis-only" re-run) but still transforms + analyzes existing raw data, so
    the orchestration is exercised without any connector. With ``do_sync=True`` the
    isolated full sync runs first (Strava authoritative, Garmin best-effort), then
    the transform rebuilds the structured layer from raw, then the full analysis
    suite writes its four reports, and finally the noteworthy check decides what to
    surface.
    """
    # ---- 1. Sync (watermark-driven; Garmin isolated) -> catch-up happens here ----
    sync_results: list[SourceResult] = []
    if do_sync:
        sync_results = pipeline.run_full_sync(conn, settings)

    # ---- 2. Transform raw -> structured (no network, idempotent) ----
    from tempo.transforms.runner import run_transform

    tr = run_transform(conn, fill_to=generated_on)
    transform_summary = (
        f"{tr.activities} activities, {tr.streams} streams, "
        f"{tr.wellness_days} wellness days, {tr.spine_days} spine days"
    )

    # ---- 3. Analyze: write the full report suite ----
    cfg = _load_config_from_settings(settings)
    reports = analysis_runner.generate_all(
        conn,
        cfg=cfg,
        races_path=settings.races_path,
        plan_path=settings.plan_path,
        heat_path=settings.heat_path,
        reports_dir=settings.reports_dir,
        generated_on=generated_on,
    )

    # ---- 4. Noteworthy-only surfacing (recompute the shared findings once) ----
    series = build_load_series(conn, cfg)
    guardrail = fitness.evaluate_guardrail(series.points)
    recovery = recovery_mod.assess_recovery_from_db(conn, points=series.points, guardrail=guardrail)
    freshness = dataread.source_freshness(conn, as_of=generated_on)
    races_ctx = ctx.parse_races(settings.races_path)
    next_race_days = nw.next_race_within_days(races_ctx, generated_on)

    result = nw.evaluate_noteworthy(
        as_of=generated_on,
        guardrail=guardrail,
        recovery=recovery,
        freshness=freshness,
        next_race_days=next_race_days,
    )

    stale = [
        f.source
        for f in freshness
        if f.last_sync_at is not None
        and f.days_stale is not None
        and f.days_stale > nw.NoteworthyThresholds().stale_after_days
    ]

    # ---- 5. Write the marker file ONLY when noteworthy (SCHED-03 surfacing) ----
    marker_path: Path | None = None
    if result.noteworthy:
        settings.reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker_path = settings.reports_dir / MARKER_NAME
        marker_path.write_text(result.as_marker_text(generated_on) + "\n", encoding="utf-8")
        logger.warning("%s", result.as_marker_text(generated_on))
    else:
        # Remove a stale marker from a previous noteworthy day so it doesn't linger.
        existing = settings.reports_dir / MARKER_NAME
        if existing.exists():
            existing.unlink()
        logger.info("daily run %s: nothing noteworthy", generated_on.isoformat())

    return DailyRunResult(
        generated_on=generated_on,
        sync_results=sync_results,
        transform_summary=transform_summary,
        reports=reports,
        noteworthy=result,
        marker_path=marker_path,
        stale_sources=stale,
    )
