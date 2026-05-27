---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: telegram-voice-coach
status: "Roadmap defined. v1.1 spans Phases 9–12 (Telegram bot foundation → voice intake + transcription → Claude Code agent loop → lifecycle/hardening/privacy). 15 VOICE-* requirements all mapped. Next: `/gsd:plan-phase 9`."
stopped_at: Phase 5 (Journaling via Claude) complete. Validated `tempo journal add` entrypoint
last_updated: "2026-05-27T23:35:00.000Z"
last_activity: 2026-05-27 — Plan 11-01 complete: bot_session table at schema v5, tempo.bot.sessions store (4h resume window), docs prerequisites for Claude Code agent loop. 437 tests green.
progress:
  total_phases: 4
  completed_phases: 2
  total_plans: 6
  completed_plans: 5
  percent: 50
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-26)

**Core value:** Turn scattered training and health data into trustworthy, structured signal that tells the user when to push, when to back off, and whether they're on track — combining objective data (Strava/Garmin) with their own plan and reflections.
**Current focus:** Milestone v1.1 — Telegram Voice Coach (Mac). Defining requirements + roadmap.

## Current Position

Phase: Not started (roadmap complete; ready to plan Phase 9)
Plan: —
Status: Roadmap defined. v1.1 spans Phases 9–12 (Telegram bot foundation → voice intake + transcription → Claude Code agent loop → lifecycle/hardening/privacy). 15 VOICE-* requirements all mapped. Next: `/gsd:plan-phase 9`.
Last activity: 2026-05-27 — v1.1 ROADMAP.md written, REQUIREMENTS traceability extended (Phase 8 TRACK-* + Phases 9–12 VOICE-*)

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

## What's Done (Phase 5: Journaling via Claude)

- `tempo/migrations/0003_journal.sql` (SCHEMA_VERSION → 3) — `journal` table:
  `id, day (FK date_spine), activity_id (FK activity, nullable), rpe (CHECK 1–10),
  feel, notes, sport, duration_min, srpe, created_at` + indexes on day/activity. The
  `daily_summary` VIEW is dropped+recreated to LEFT-JOIN a per-day journal rollup
  (latest-entry rpe/feel, SUM(srpe), has_journal, has_notes) while preserving the
  one-row-per-spine-day invariant (no day dropped). (JRNL-01/03; STORE-04)

- `tempo/journal/service.py` — the **validated boundary** (`add_entry`): validates
  RPE to an integer 1–10 (rejects 0/11/fractional/non-numeric/bool), validates an
  optional positive `duration_min`, resolves the activity by **local date + sport**
  (0 → unlinked rest-day reflection; 1 → auto-link; many → `MultipleActivitiesError`
  unless an explicit `--activity-id` disambiguates; explicit id always wins), computes
  **sRPE = RPE × duration_min** (explicit duration wins, else linked activity's
  moving/elapsed time), and inserts via parameterised SQL in a transaction. Also
  `resolve_activity`, `compute_srpe`, `list_entries`, and `ensure_days` of the spine
  for rest-day entries. NO free-form-SQL path. (JRNL-01/02)

- `tempo/analysis/load.py` — new `LoadMethod.SRPE` + `apply_srpe_fallback(day_load, srpe)`:
  uses the day's sRPE as the load **only** when objective load is `insufficient` or it's a
  `rest` day with a journaled (e.g. cross-training) session; rTSS/hrTSS always win when
  present; flagged `method='sRPE'`. (JRNL-03)

- `tempo/analysis/data.py` — `srpe_by_day()` (SUM of journal sRPE per day; empty/safe when
  the journal table is absent), wired into `runner.build_load_series` so the daily load
  series fills insufficient days from sRPE.

- `tempo/cli.py` — `tempo journal add` (thin wrapper over `add_entry`; `--rpe` required,
  `--feel/--notes/--day/--sport/--activity-id/--duration-min`; day defaults to today) and
  `tempo journal list`. Validation failures exit 1 with the error.

- `docs/JOURNALING.md` — the "Claude in the loop" contract: Claude captures entries ONLY by
  calling `tempo journal add`, never SQL; documents the date+sport resolution rule, sRPE, and
  that journal content stays in the gitignored `~/.tempo/` DB.

- `tests/` — 235 pytest tests (was 183), all green; ruff check + format clean. New:
  `test_journal_service.py` (RPE validation incl. 0/11/non-int/bool; resolution none/one/many

  + disambiguation + case-insensitive sport; sRPE linked-vs-explicit; persistence; failed
  validation writes nothing; rest-day spine creation), `test_journal_summary.py` (journal in
  daily_summary, one-row-per-spine-day invariant, null journal cols, multi-entry rollup; sRPE
  fallback unit + integration through `build_load_series`), `test_journal_cli.py` (end-to-end
  CLI: link+sRPE, reject bad RPE, ambiguous-needs-id, cross-training, list).

All three Phase-5 success criteria verified live against a seeded throwaway DB (temp data
dir): `tempo journal add --rpe 7 --feel strong --day … --sport Run` created entry #1, linked
to the day's activity, computed sRPE 420, and appeared in `daily_summary`; an out-of-range RPE
was rejected (exit 1); and on an activity-with-no-pace/HR day the analysis load series flagged
the day `method='sRPE'` with the sRPE value as the load.

### Conventions established this phase

- Subjective rows are written ONLY through the validated `tempo.journal.service.add_entry`
  boundary (CLI is a thin wrapper); Claude never writes SQL (ARCHITECTURE Pattern 5 / Anti-
  Pattern 4). Activity resolution by date+sport refuses to guess on ambiguity.

- sRPE is a **fallback** load track flagged `sRPE`; objective rTSS/hrTSS always wins when
  available. Journal content is personal data — lives only in the gitignored `~/.tempo/` DB.

### Conventions established earlier (Phase 4)

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

## What's Done (Phase 6: Garmin Ingestion)

- `tempo/migrations/0004_wellness.sql` (SCHEMA_VERSION → 4) — `wellness_day` table:
  `day (PK, FK date_spine), resting_hr, hrv_last_night, hrv_status, sleep_score,
  sleep_seconds, deep_s/rem_s/light_s/awake_s, body_battery_high/low, stress_avg,
  steps, updated_at`. `daily_summary` VIEW dropped+recreated to LEFT-JOIN wellness
  (one row per spine day preserved; `has_wellness` flag; wellness-only rest days kept).
  (GRMN-04; STORE-04)

- `tempo/connectors/garmin.py` — `GarminConnector` implements the SAME `Connector`
  protocol as Strava (`backfill`/`sync`). Behind a narrow `GarminClient` Protocol seam
  (so the fragile library is decoupled + fakeable). Token-reuse only: `_authenticated_client`
  builds a credential-less client and logs in via the token store — it CANNOT fall through
  to an SSO credential login (GRMN-02). **No-retry-on-429**: a 429 (typed-name OR string
  match) on login or any data call raises `GarminSyncError` immediately — never a backoff
  loop (PITFALLS 2). Per-day verbatim fetch of `sleep`/`hrv`/`stats`, stored under
  `(garmin, <endpoint>, <ISO date>)`; the whole pull + watermark advance is ONE transaction
  so a 429 mid-pull rolls back cleanly and never advances the watermark (Anti-Pattern 3).
  `GarminAuthError` (no tokens → run `tempo garmin login`) vs `GarminSyncError` (runtime).
  (GRMN-01/02/03/04)

- `tempo/sync/pipeline.py` — the ISOLATION seam. `run_garmin_sync` wraps the connector in
  try/except catching `GarminSyncError`/`GarminAuthError`/`Exception` → returns a
  `SourceResult(ok=False)`, NEVER raising. `run_full_sync` runs Strava (authoritative,
  not isolated) THEN attempts Garmin isolated, so a Garmin failure can't block Strava or
  analysis (GRMN-01/03; Anti-Pattern 5). Per-source status reported honestly (no silent
  partial-success).

- `tempo/connectors/factory.py` — `build_garmin_connector` (token-only client, NEVER
  credentials), `garmin_login` (the ONLY credential path: submits email/password + MFA
  via `prompt_mfa`, library dumps DI tokens to `~/.tempo/tokens/garmin`), `garmin_token_dir`.

- `tempo/transforms/wellness.py` — pure no-network `rebuild_wellness`: groups raw
  sleep/hrv/stats by ISO key and collapses each day into ONE `wellness_day` row keyed by
  Garmin's `calendarDate` (via shared `local_day_from_calendar_date`); tolerates missing
  endpoints / corrupt payloads; idempotent upsert. Wired into `transform`/`rederive` runner
  (rebuilds wellness, clears it on rederive). Spine `data_day_bounds` now unions wellness +
  journal days so wellness-only days extend the spine. (GRMN-04; STORE-02)

- `tempo/analysis/baselines.py` — personal rolling baselines (GRMN-05): trailing-window
  mean + sample-SD z-score (exclusive of today) + EWMA per metric (HRV/resting HR/sleep)
  over `wellness_day`; `<MIN_POINTS` priors or zero variance → `None` ("insufficient data",
  honest). Read helpers skip NULL days. Exposed (`compute_baselines`/`latest_baselines`) for
  Phase 7's recovery analysis.

- `tempo/cli.py` — `tempo garmin login` (interactive, one-time, MFA prompt; warns NOT to
  retry on 429), `tempo garmin backfill --days N`, `tempo garmin sync` (token reuse, reports
  skip not crash), and `tempo sync` now runs Strava then isolated Garmin with per-source
  status. transform/rederive echo wellness counts.

- `tests/garmin_fakes.py` — fake garminconnect client (no network/credentials): realistic
  sleep/hrv/stats payloads, a 429 exception named `GarminConnectTooManyRequestsError`,
  token-reuse vs credential-login distinction (counts credential logins), and per-call
  429/error/missing-token scripting.

- `tests/` — 288 pytest tests (was 235), all green; ruff check + format clean. New:
  `test_garmin_connector.py` (interface conformance, verbatim raw keyed by date, watermark,
  token-reuse no-credential-login, 429 no-retry + rollback, idempotency, backfill),
  `test_wellness_transforms.py` (parse fns, collapse-to-one-day, calendarDate-not-key,
  missing endpoints, rederive zero-network, corrupt-payload skip), `test_wellness_summary.py`
  (daily_summary wellness columns + one-row-per-spine-day invariant + wellness-only/activity-
  only days + spine extension), `test_baselines.py` (hand-checked mean/SD/z/EWMA, insufficient-
  data None, window bounding, DB integration), `test_garmin_isolation.py` (429/auth/unexpected
  all caught; full pipeline Strava survives Garmin 429; analysis runs after), `test_garmin_cli.py`
  (login persists via credential client, sync reuses tokens 0-credential-logins, 429 reports
  skip, `tempo sync` both-sources isolated), plus wellness schema tests in `test_db.py`.

All five Phase-6 success criteria verified live against a throwaway temp data dir with the
fake client: (1) `GarminConnector` is a `Connector` and a 429 was isolated; (2) a 40-day
backfill made **0 credential logins** (token reuse); (3) an end-to-end `tempo sync` with a
simulated Garmin 429 exited 0 with `strava: ok` and Garmin skipped, then `tempo transform`
and `tempo analyze` both succeeded; (4) 120 raw rows → 40 `wellness_day` rows keyed by
calendarDate, surfaced in `daily_summary`; (5) baselines produced real z-scores (HRV z≈-1.46
vs a 39-day mean) and `None` where variance/history was insufficient.

### Conventions established this phase

- Garmin is an **isolated failure domain**: the connector raises, the `tempo.sync.pipeline`
  catches — a Garmin failure NEVER blocks Strava or analysis (Anti-Pattern 5). Contrast
  Strava, where tenacity backoff on a 429 is fine; a Garmin 429 gets NO retry (account-lockout
  risk, PITFALLS 2).

- Garmin auth is login-once: only `tempo garmin login` touches credentials; sync/backfill
  reuse persisted tokens and never log in. The fragile library sits behind a `GarminClient`
  Protocol seam so it's swappable + fakeable.

- Wellness buckets on Garmin's `calendarDate` (local wake-up day) via the shared bucketing
  rule; multiple raw endpoints collapse into one `wellness_day` row; rebuilt purely from raw
  (zero network).

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
| Phase 10 P01 | 339 | 2 tasks | 10 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Strava-first milestone: prove pull → store → analyse end-to-end on the clean source before the fragile Garmin connector
- Two-layer raw → structured storage: connectors write only to `raw_response`; transforms read raw and write structured, enabling `tempo rederive` with no network
- Date spine in Phase 3 (not later): CTL/ATL EWMAs and ACWR windows are silently wrong without a zero-filled spine
- Journaling early (Phase 5): correlation analysis is data-hungry, so paired subjective history must start accumulating before Garmin

### Roadmap Evolution

- Phase 8 added: Modular Trackers + Heat Adaptation — split plan.md into focused tracker files (`races.md` w/ result + auto-link, new `heat.md`); retire `plan.md`. (2026-05-27)

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

Last session: 2026-05-27T22:00:40.947Z
Stopped at: Phase 5 (Journaling via Claude) complete. Validated `tempo journal add` entrypoint
(`tempo/journal/service.py`) records structured subjective entries (RPE 1–10, feel, notes),
resolves the activity by date+sport (none/one/many handled), computes an sRPE (RPE × duration)
load track, and inserts via parameterised SQL — Claude never writes SQL. Migration 0003 adds
the `journal` table and rebuilds `daily_summary` to LEFT-JOIN journal fields per day (one row
per spine day preserved). sRPE fills the daily load on otherwise-insufficient days, flagged
`method='sRPE'`; objective load still wins. `docs/JOURNALING.md` documents the Claude capture
contract. 235 tests green, ruff clean, shipped and pushed; all three success criteria verified
live against a seeded throwaway DB. Next: plan Phase 6 (Garmin Ingestion).
Resume file: None
