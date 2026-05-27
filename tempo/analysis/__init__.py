"""Analysis layer: turn structured data into load metrics, fitness/fatigue/form,
race predictions, and dated markdown reports.

Everything here is **read-only over the structured/gold layer** (``activity`` /
``daily_summary``) plus the user's ``races.md`` / ``plan.md`` context, and pure
Python metric math (stdlib only -- no pandas/polars). It never touches the
network: analyses run entirely on already-stored, already-transformed data, which
is why every report header states its own data freshness (PITFALLS: never trust
stale data silently).

Modules:

* :mod:`tempo.analysis.load`    -- per-activity load (rTSS / hrTSS) + method flag.
* :mod:`tempo.analysis.fitness` -- CTL/ATL/TSB EWMA series, ACWR, ramp rate.
* :mod:`tempo.analysis.race`    -- Riegel / VDOT race-time prediction.
* :mod:`tempo.analysis.context` -- parse ``races.md`` / ``plan.md``.
* :mod:`tempo.analysis.heat`    -- parse ``heat.md`` + heat-session rollups (TRACK-04/05).
* :mod:`tempo.analysis.data`    -- read daily inputs + per-source freshness.
* :mod:`tempo.analysis.report`  -- render dated markdown reports.
* :mod:`tempo.analysis.runner`  -- orchestrate the analyses and write reports.
"""
