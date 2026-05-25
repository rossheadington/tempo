# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** Phase 2 — Strava Ingestion (next)

## Current Position

Phase: 1 of 7 (Foundation) — COMPLETE
Plan: 1 of 1 in Phase 1 complete
Status: Phase 1 done; ready to plan Phase 2 (Strava Ingestion)
Last activity: 2026-05-26 — Phase 1 (Foundation) implemented, tested, committed, and pushed

Progress: [█░░░░░░░░░] ~14% (1 of 7 phases)

## What's Done (Phase 1: Foundation)

- uv project scaffold: `pyproject.toml` (`[project.scripts] tempo = "tempo.cli:app"`),
  `tempo/` package, `uv.lock` committed. Python 3.14. Runtime deps: typer,
  pydantic-settings. Dev deps: pytest, ruff.
- `tempo/config.py` — typed pydantic-settings reading a gitignored `.env`; runtime
  data dir defaults to `~/.tempo/` (OUTSIDE the repo tree); derived db/tokens/reports
  paths; 0700 dir perms. (FND-02, FND-04)
- `tempo/db.py` + `tempo/migrations/0001_init.sql` — raw `sqlite3` connection (WAL +
  foreign keys), integer `user_version` migration runner; tables `raw_response`,
  `date_spine`, `sync_state`. (FND-01)
- `tempo/cli.py` — typer app; bare `tempo` / `tempo init` initialises the DB;
  `sync`/`transform`/`rederive`/`analyze`/`journal[ add]` wired as "not yet
  implemented" stubs. (FND-05)
- `.githooks/pre-commit` — gitleaks scan of staged changes (`gitleaks git --staged`);
  fails loudly if gitleaks absent. Enabled via `core.hooksPath=.githooks`. Also
  `.pre-commit-config.yaml` for pre-commit-framework users. (FND-03)
- `.env.example` committed; `.gitignore` hardened (`.tempo/`). (FND-02)
- `docs/DATE_BUCKETING.md` — authoritative local-date attribution rule (Strava fake-Z,
  Garmin calendarDate, edge cases). (FND-06)
- `tests/` — 25 pytest tests, all green; `ruff check` + `ruff format` clean.

All Phase-1 success criteria verified (incl. gitleaks blocking a deliberately-staged
fake credential). Commits pushed to origin/main.

### Conventions established this phase
- Flat `tempo/` package layout (not `src/`).
- No ORM / no Alembic: raw sqlite3 + hand-written SQL + integer `user_version`
  migrations applied from `tempo/migrations/NNNN_*.sql`.
- All runtime data (DB, tokens, reports) lives under `~/.tempo/` by default,
  configurable via `TEMPO_DATA_DIR`; never inside the repo tree.
- Settings env prefix is `TEMPO_`.

## Performance Metrics

**Velocity:**
- Total plans completed: 1
- Average duration: — min
- Total execution time: — hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation | 1 | — | — |

**Recent Trend:**
- Last 5 plans: —
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Strava-first milestone: prove pull → store → analyse end-to-end on the clean source before the fragile Garmin connector
- Two-layer raw → structured storage: connectors write only to `raw_response`; transforms read raw and write structured, enabling `tempo rederive` with no network
- Date spine in Phase 3 (not later): CTL/ATL EWMAs and ACWR windows are silently wrong without a zero-filled spine
- Journaling early (Phase 5): correlation analysis is data-hungry, so paired subjective history must start accumulating before Garmin

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 2] Strava API Agreement conflict (7-day cache limit; no feeding data to AI models) must be documented as a known accepted conflict before backfill proceeds (private self-data, never shared)
- [Phase 4] rTSS/NGP implementation details (grade-adjusted pace, GPS dropout, threshold-pace estimation) need a brief planning-time research pass; hrTSS-only is a valid v1 fallback
- [Phase 6] `garminconnect` is the single fragile dependency (garth deprecated 2026-03-27); pin version, monitor upstream, budget for a version bump
- [Phase 7] HRV baseline cold-start and multi-signal recovery weighting may need a brief planning-time research pass; first weeks of Garmin data will be low-quality and must be flagged honestly

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-26
Stopped at: Phase 1 (Foundation) complete — project skeleton, secure DB schema (WAL),
secrets outside the tree, gitleaks hook, typer CLI shell, and date-bucketing rule all
shipped, tested, and pushed. Next: plan Phase 2 (Strava Ingestion).
Resume file: None
