# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** Phase 1 — Foundation

## Current Position

Phase: 1 of 7 (Foundation)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-05-26 — Roadmap created (7 phases, 39 v1 requirements mapped)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: — min
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

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
Stopped at: Roadmap and STATE initialised; REQUIREMENTS.md traceability populated
Resume file: None
