# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** Phase 5 — Journaling via Claude (next)

## Current Position

Phase: 4 of 7 (Load Metrics + First Analysis) — COMPLETE
Plan: 1 of 1 in Phase 4 complete
Status: Phase 4 done — **the Strava end-to-end milestone is shippable**: pull → store → transform → analyze → report works end-to-end on stored Strava data. Ready to plan Phase 5 (Journaling via Claude).
Last activity: 2026-05-26 — Phase 4 (Load Metrics + First Analysis) implemented, tested, committed, and pushed

Progress: [██████░░░░] ~57% (4 of 7 phases)

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

## What's Done (Phase 2: Strava Ingestion)

- `tempo/connectors/base.py` — `Connector` Protocol (`backfill(raw)` / `sync(raw, since)`)
  that Garmin will share in Phase 6, plus `RawWriter`: idempotent upsert into
  `raw_response` keyed on `(source, endpoint, entity_key)`. Connectors write ONLY
  here. (STRV-06)
- `tempo/connectors/tokens.py` — atomic rotating-token store: temp-write → fsync →
  `os.replace`, mode 0600, dir fsync. A crash mid-write can never strand the user
  back in the OAuth browser flow. (STRV-01/02; PITFALLS 4)
- `tempo/connectors/strava.py` — `StravaConnector`: OAuth handshake (authorize URL +
  code exchange), refresh-only-near-expiry with atomic persistence of the rotated
  refresh token, resumable all-time backfill via `backfill_cursor` (batch raw rows +
  cursor committed together so an interrupt resumes without re-fetch), incremental
  `sync` via the `last_entity_ts` watermark (Strava `after`), and lazy idempotent
  `fetch_streams` / `fetch_detail`. Verbatim raw via `client.protocol.get` (no model
  round-trip). tenacity backoff on 429, then checkpoint-and-exit (never hammer).
  (STRV-01..06; PITFALLS 5)
- `tempo/sync/state.py` — `sync_state` read/write: watermark advances forward-only and
  only on success; backfill cursor + complete flag. (ARCHITECTURE Anti-Pattern 3)
- `tempo/connectors/factory.py` — wires config → token store → connector; clean
  "credentials missing" error.
- `tempo/cli.py` — `tempo strava auth|backfill|streams|sync`; top-level `tempo sync`
  runs the Strava incremental sync. `transform`/`rederive`/`analyze`/`journal` remain
  stubs.
- `tempo/config.py` / `.env.example` — added `strava_redirect_uri`; documented setup.
- README — "Strava setup (one-time)" section + accepted-API-terms note.
- Deps: `stravalib` 2.4, `tenacity` 9.1; dev `responses`.
- `tests/` — 73 pytest tests (was 25), all green; ruff check + format clean. Mocked
  stravalib client + Strava-shaped JSON fixtures. Proven: token-rotation atomicity
  (incl. simulated crash-during-rename leaving old file intact), backfill resume after
  a simulated mid-run 429 without re-fetch, idempotent raw upsert, watermark-only
  incremental sync, lazy streams skip-when-present, and raw-only writes.

Criteria 1–4 proven via mocks; **live execution pending the user's real Strava API
client ID/secret** (create app + `tempo strava auth`). Strava API Agreement conflict
recorded as accepted (README + REQUIREMENTS Known Accepted Conflicts).

## What's Done (Phase 3: Strava Transforms + Date Spine)

- `tempo/transforms/bucketing.py` — the one local-date attribution rule, in one place
  (DATE_BUCKETING invariant). `local_day_from_strava_local` takes `start_date_local[:10]`
  (wall-clock; the trailing `Z` is FAKE, NOT UTC) and never re-projects to UTC, so
  late-night / DST / timezone-travel runs all land on the correct local day.
  `local_day_from_calendar_date` (Garmin parity, Phase 6) takes a `calendarDate` verbatim.
  Defensive: rejects empty / malformed / impossible dates with `BucketingError`. (STORE-05)
- `tempo/transforms/strava.py` — pure raw→structured projection. `transform_activity`
  (payload → typed `ActivityRow`, deriving `avg_pace_s_km` from `average_speed`) and
  `transform_streams` (key_by_type payload → one `StreamRow` per type). `rebuild_activities`
  ensures each activity's spine day exists before insert (FK-safe), preferring the richer
  `activity` detail over `activity_summary`; `rebuild_streams` skips orphans. (STORE-01)
- `tempo/transforms/spine.py` — zero-fills `date_spine`: a CONTINUOUS run of days across
  `[min data day, max data day]` (rest days + gap days included), optionally extended
  forward to `fill_to` (today). Missing spine days would silently corrupt Phase-4 EWMA /
  ACWR windows, so continuity is enforced. Spine metadata (dow/ISO-week/month/year)
  recomputed deterministically. (STORE-03)
- `tempo/transforms/coerce.py` — defensive optional-value coercion (absent/empty → None).
- `tempo/transforms/runner.py` — orchestrates `run_transform` (incremental upsert) and
  `run_rederive` (clear + full rebuild) as ONE atomic, ZERO-NETWORK transaction; ordering
  respects the spine→activity→stream foreign keys. Both produce identical state for a
  given raw layer. (STORE-02)
- `tempo/migrations/0002_structured.sql` — `activity`, `activity_stream` tables (FK to
  `date_spine`/`activity`) + `daily_summary` VIEW: a LEFT JOIN from `date_spine` rolling up
  activities per day (n_activities, totals, max HR, sports), one row per calendar day, rest
  days first-class — shaped for Phase-6 wellness and Phase-5 journal to LEFT JOIN in later.
  (STORE-04). `db.SCHEMA_VERSION` bumped to 2; `STRUCTURED_TABLES` added.
- `tempo/cli.py` — `tempo transform` and `tempo rederive` wired to the runner (spine filled
  forward to today); report activity/stream/spine-day counts.
- `tests/` — 113 pytest tests (was 73, +40), all green; ruff check + format clean. New:
  `test_bucketing.py` (all four edge cases — 11pm, tz-travel, DST spring/fall, fake-Z — plus
  Garmin calendarDate parity and defensive parsing), `test_transforms.py` (pure transform,
  zero-filled spine incl. rest days, daily_summary one-row-per-day LEFT JOIN + rollup, edge
  cases applied through the FULL transform), `test_rederive.py` (idempotency, purity =
  function-of-raw, rebuild-after-drop, and a hard NO-NETWORK guard that blocks `socket`),
  `test_transform_cli.py` (end-to-end CLI). `strava_fakes.make_activity_tz` added.

All four Phase-3 success criteria verified, including a live CLI run on a seeded DB proving
late-night→local-day, DST-night, and tz-travel bucketing and `daily_summary` rows == spine
days. Re-derivation confirmed no-network (socket-blocked test + CLI rerun reproducing counts).

## What's Done (Phase 4: Load Metrics + First Analysis — Strava end-to-end milestone)

- `tempo/analysis/load.py` — per-activity load: **rTSS** (pace-based, primary) with an
  **hrTSS** fallback (HR-reserve / Karvonen, anchored on threshold HR), and a per-day
  **method flag** (`rTSS` / `hrTSS` / `insufficient` / `rest`). When neither pace nor HR
  inputs exist, the activity is `insufficient` — load is never invented (LOAD-01; PITFALLS).
  Numerically verified: 1 h at threshold pace/HR ⇒ 100. (LOAD-01)
- `tempo/analysis/fitness.py` — **CTL/ATL/TSB** EWMA series (42 / 7 day, PMC `1/N`
  recurrence; TSB uses yesterday's CTL−ATL), **ACWR** (coupled rolling avg; sweet spot
  0.8–1.3, danger >1.5), **ramp rate** (CTL change/week; aggressive >8), and a
  `evaluate_guardrail` verdict. All built on the zero-filled spine (rest days = 0).
  Degrades to `insufficient` rather than fabricating. (LOAD-02/03)
- `tempo/analysis/race.py` — **Riegel** (`T2=T1·(D2/D1)^1.06`) + **VDOT** (Daniels'
  published vo2/pct formulas, inverted by fixed-point iteration) race prediction, with a
  reliability flag when the distance ratio exceeds 4:1. Verified: VDOT(5k 19:57)=50.0.
- `tempo/analysis/context.py` — lenient **races.md / plan.md** parsers; missing file →
  empty result (analyses degrade). Race lines need a recognised field so prose/doc bullets
  aren't mistaken for races. (PLAN-01/02)
- `tempo/analysis/data.py` — read-only DB access: activities, the zero-filled spine days,
  and **per-source `sync_state` freshness** (days-stale vs as-of) for the report header.
- `tempo/analysis/report.py` — markdown renderers; every report opens with a **per-source
  last-successful-sync + staleness freshness header** and the data date range (ANL-05).
- `tempo/analysis/runner.py` — orchestrates raw inputs → load series (on the spine) →
  PMC + guardrail + weekly rollups + race readiness → writes
  `reports/YYYY-MM-DD-load-trend.md` and `…-race-readiness.md`. Zero network. (ANL-01/02; DELIV-01)
- `tempo/config.py` / `.env.example` — `TEMPO_THRESHOLD_PACE_S_PER_KM`, `TEMPO_MAX_HR`,
  `TEMPO_RESTING_HR`, `TEMPO_THRESHOLD_HR` settings; `races_path` / `plan_path` derived paths.
- `tempo/cli.py` — `tempo analyze` (both reports) + `tempo analyze load-trend` /
  `tempo analyze race-readiness`.
- `races.md.example` / `plan.md.example` — committed templates documenting the format;
  real files live in the gitignored data dir (also gitignored in repo root defensively).
- `tests/` — 183 pytest tests (was 113, +70), all green; ruff check + format clean. New:
  `test_load.py` (rTSS/hrTSS/method/insufficient, numeric), `test_fitness.py` (CTL/ATL/TSB
  EWMA numeric, ACWR/ramp/guardrail thresholds), `test_race.py` (Riegel/VDOT numeric +
  reliability), `test_context.py` (races/plan parsing incl. missing-file + prose-not-a-race),
  `test_analysis_reports.py` + `test_analyze_cli.py` (end-to-end: seed temp DB → run analyses
  → assert dated reports with freshness headers + insufficient-data degradation).
  `strava_fakes.make_run` added.

All five Phase-4 success criteria verified, including a live `tempo analyze` run against a
seeded throwaway DB producing real markdown reports with freshness headers (and the STALE
flag) — confirmed in a temp data dir so nothing real was touched. **This completes the Strava
end-to-end milestone: pull → store → transform → analyze → report works end-to-end. The only
remaining user step for live data is the one-time `tempo strava auth` + a backfill with the
user's own Strava API app.**

### Conventions established this phase
- Analysis layer (`tempo/analysis/`) is **read-only over the structured/gold layer + the
  user's races.md/plan.md context**, pure-Python metric math (stdlib only — no pandas/polars),
  and **never touches the network**. Reports are dated markdown into the gitignored reports
  dir; every report states per-source data freshness; thin data degrades to "insufficient".
- Threshold pace / HR are configurable pydantic settings (`TEMPO_*`), documented in `.env.example`.

### Conventions established earlier
- Flat `tempo/` package layout (not `src/`).
- No ORM / no Alembic: raw sqlite3 + hand-written SQL + integer `user_version`
  migrations applied from `tempo/migrations/NNNN_*.sql`.
- All runtime data (DB, tokens, reports) lives under `~/.tempo/` by default,
  configurable via `TEMPO_DATA_DIR`; never inside the repo tree.
- Settings env prefix is `TEMPO_`.
- (Phase 3) Transforms live in `tempo/transforms/`, are PURE functions of the raw layer
  (no network), and bucket via the single rule in `tempo/transforms/bucketing.py`. The
  `daily_summary` gold layer is a VIEW (always fresh) LEFT-JOINed from `date_spine`; future
  sources join through `day`. `rederive` = clear + rebuild in one txn; `transform` = upsert.

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

- [Phase 2 — RESOLVED] Strava API Agreement conflict documented as accepted (README + REQUIREMENTS Known Accepted Conflicts); private self-data, never shared.
- [Phase 2 — pending user] Live Strava pull needs the user's own API app: create at https://www.strava.com/settings/api, set TEMPO_STRAVA_CLIENT_ID/SECRET in .env, run `tempo strava auth`, then `tempo strava backfill`. All machinery (incl. Phase-4 analysis) proven against mocks/seeded data; this is the only remaining step before live reports.
- [Phase 4 — RESOLVED] rTSS uses `avg_pace_s_km` directly (no grade-adjusted/normalised pace in v1; NGP/GAP is a documented future refinement). hrTSS fallback uses HR-reserve anchored on threshold HR. Threshold pace is a configurable pydantic setting. Insufficient days are flagged, not invented.
- [Phase 6] `garminconnect` is the single fragile dependency (garth deprecated 2026-03-27); pin version, monitor upstream, budget for a version bump
- [Phase 7] HRV baseline cold-start and multi-signal recovery weighting may need a brief planning-time research pass; first weeks of Garmin data will be low-quality and must be flagged honestly

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-26
Stopped at: Phase 4 (Load Metrics + First Analysis) complete — the **Strava end-to-end
milestone is shippable**. Per-activity load (rTSS + hrTSS fallback + method flag),
CTL/ATL/TSB EWMA series, ACWR/ramp guardrail, Riegel/VDOT race prediction, races.md/plan.md
context parsing, and dated load-trend + race-readiness markdown reports with per-source
freshness headers — all pure-Python, no-network, reading the structured/gold layer. 183 tests
green, ruff clean, shipped and pushed. `tempo analyze [load-trend|race-readiness]` wired and
verified live against a seeded DB. Next: plan Phase 5 (Journaling via Claude).
Resume file: None
