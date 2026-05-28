"""Analysis layer: turn structured data into load metrics, fitness/fatigue/form,
race predictions, and dated markdown reports.

Everything here is **read-only over the structured/gold layer** (``activity`` /
``daily_summary``) plus the user's ``races.md`` / ``heat.md`` context, and pure
Python metric math (stdlib only -- no pandas/polars). It never touches the
network: analyses run entirely on already-stored, already-transformed data, which
is why every report header states its own data freshness (PITFALLS: never trust
stale data silently).

Modules:

* :mod:`runos.analysis.load`    -- per-activity load (rTSS / hrTSS) + method flag.
* :mod:`runos.analysis.fitness` -- CTL/ATL/TSB EWMA series, ACWR, ramp rate.
* :mod:`runos.analysis.race`    -- Riegel / VDOT race-time prediction.
* :mod:`runos.analysis.races`   -- parse ``races.md`` (the canonical home).
* :mod:`runos.analysis.heat`    -- parse ``heat.md`` + heat-session rollups (TRACK-04/05).
* :mod:`runos.analysis.strength` -- parse ``strength.md`` + strength-session rollups (SC-01/02).
* :mod:`runos.analysis.weight`  -- parse ``weight.md`` + EWMA rollup (WEIGHT-01/02/03).
* :mod:`runos.analysis.nutrition` -- parse ``food.md`` (two formats) + daily + rollup (NUTR-03/04).
* :mod:`runos.analysis.data`    -- read daily inputs + per-source freshness.
* :mod:`runos.analysis.report`  -- render dated markdown reports.
* :mod:`runos.analysis.runner`  -- orchestrate the analyses and write reports.
"""
