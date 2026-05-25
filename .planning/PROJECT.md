# Tempo

## What This Is

Tempo is a personal, local-first training and health system for a runner. It
pulls running and wellness data from Strava and Garmin into a structured,
queryable SQLite database, lets the user plan (races + training plan) and reflect
(post-workout journaling via Claude), and runs scheduled Claude analyses that
write markdown reports on recovery, training load, race readiness, and
correlations. It's a single-user tool for the project owner, not a product.

## Core Value

Turn scattered training and health data into trustworthy, structured signal that
tells the user when to push, when to back off, and whether they're on track for
their goals — combining objective data (Strava/Garmin) with their own plan and
reflections.

## Requirements

### Validated

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. -->

**Ingestion**
- [ ] Pull all-time Strava activity history (activities + detailed streams: HR, pace, GPS, power, cadence, elevation)
- [ ] Incrementally sync new Strava activities on a daily schedule
- [ ] Pull Garmin wellness data (sleep, HRV, body battery, resting HR, stress, steps) via the unofficial garminconnect library
- [ ] Store every raw API response verbatim, then normalise into structured tables (two-layer raw → structured)

**Storage & modelling**
- [ ] Structured SQLite schema with a shared date spine joining all sources
- [ ] A unified daily-summary view/table that joins activities, wellness, and journal per day
- [ ] Re-derive structured tables from stored raw data without re-fetching

**Plan & reflect**
- [ ] Maintain upcoming races (date, distance, goal) in a simple markdown file Tempo reads for context
- [ ] Maintain a training plan in a simple markdown file Tempo reads for context
- [ ] Capture structured post-workout journal entries (RPE, how it felt, notes) by telling Claude, written into the DB and linked to the activity

**Analysis & delivery**
- [ ] Daily scheduled sync followed by a daily analysis check
- [ ] Analyses written as markdown reports into a reports/ folder in the repo
- [ ] Recovery / overtraining analysis (rising load vs HRV / sleep / resting HR)
- [ ] Training load & trend analysis (volume, intensity, fitness/fatigue over time)
- [ ] Race-readiness analysis (progress toward goal race / target pace)
- [ ] Correlation insight (sleep / HRV / how runs felt vs performance)

**Foundation**
- [ ] Secure local credential/token handling (Strava OAuth tokens, Garmin login) — never committed
- [ ] CLI entrypoint to run pulls and analyses (`tempo ...`)

### Out of Scope

- **MyFitnessPal / food & nutrition** — no official API; deferred. May return later via CSV-drop ingest.
- **Multi-user / accounts / hosting** — single-user local tool; no server, no auth beyond personal API tokens.
- **Mobile or web UI** — interaction is via CLI, markdown files, and Claude; no front end.
- **Real-time / live tracking** — batch daily sync is sufficient.
- **Selling or sharing data externally** — personal use only; code-only public repo, data stays local.

## Context

- **Owner/user:** a runner who currently has training data scattered across
  Strava (activities) and Garmin (wellness), with no unified store and no
  structured way to capture subjective post-workout reflection or compare against
  a plan.
- **Goals span four areas:** recovery/overtraining, training load & trends, race
  readiness, and correlations — plus planning (races, training plan) and
  journaling.
- **Repo:** https://github.com/rossheadington/tempo (public, code-only; all
  secrets and health data gitignored from the first commit).
- **Source realities:**
  - *Strava* — official OAuth2 REST API; clean. One-time auth, refresh token,
    paged history pulls within rate limits.
  - *Garmin* — no individual official Health API; the community `garminconnect`
    library logs in with Connect credentials. Robust enough for personal use but
    can break on site changes / MFA.
  - *MyFitnessPal* — API removed in 2020; deferred deliberately.
- **Journaling model:** the user prefers to "tell Claude," so Claude is the
  capture interface and writes structured journal rows — no separate input UI.
- **Plan model:** training plan and race calendar are simple markdown files the
  user maintains; Tempo reads them for analysis context rather than diffing
  planned-vs-actual (a structured plan engine can come later).

## Constraints

- **Tech stack**: Python 3.14, `uv` for packaging/deps, SQLite for storage — chosen for best health-data library support and a zero-infrastructure local tool.
- **Privacy**: Public repo holds code only. Credentials, tokens, and all health data must stay local and gitignored — non-negotiable.
- **Dependencies**: Garmin access relies on an unofficial library that may break; design connectors to fail gracefully and isolate that risk.
- **Rate limits**: Strava API limits require paged, resumable history backfill for the all-time pull.
- **Local-first**: No servers, no hosted database; everything runs on the user's machine, analyses run on a schedule via Claude.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Name: Tempo | Running term (tempo runs) + the steady rhythm of scheduled data collection | — Pending |
| Python + uv + SQLite | Best library support (garminconnect, strava), zero-infra local store | — Pending |
| Strava-first milestone | Easiest, cleanest source; proves pull → store → analyse end-to-end before Garmin | — Pending |
| Two-layer raw → structured storage | Keep raw verbatim so new metrics can be derived later without re-fetching | — Pending |
| Defer MyFitnessPal / food | No official API; scraping is fragile — not worth blocking on | — Pending |
| Journaling via Claude | User prefers telling Claude; avoids building an input UI | — Pending |
| Plan/races as simple markdown | Low friction; read for context now, structured plan-vs-actual later if needed | — Pending |
| Public, code-only repo | Share code; keep all health data and secrets gitignored and local | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-26 after initialization*
