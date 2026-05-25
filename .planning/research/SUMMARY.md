# Project Research Summary

**Project:** Tempo
**Domain:** Personal local-first training/health data pipeline (Strava + Garmin → SQLite, scheduled Claude analysis)
**Researched:** 2026-05-26
**Confidence:** HIGH

## Executive Summary

Tempo is a single-user batch ELT pipeline, not a product — and the research is unanimous that the right shape is small and boring: a medallion-architecture SQLite store (raw → structured → summary), thin connector adapters over `stravalib` and `garminconnect`, raw SQL for transforms, `typer` for the CLI, and `launchd` for scheduling. The core value is trustworthy signal delivered as Claude-authored markdown narrative — a synthesis of objective device data, subjective journal entries, training plan context, and race goals that no dashboard can replicate. The build order is forced by dependency and risk: Strava first (clean official OAuth2 API, proves the full pipeline end-to-end), Garmin second (fragile unofficial library, isolated behind a connector so its failures never block Strava or analysis).

The most load-bearing architectural decisions are the ones that must land earliest: (a) the two-layer raw→structured storage, which makes every future metric derivable without re-fetching against rate-limited APIs; (b) the date spine with zero-fill, which is silently required by every EWMA and rolling-window calculation (CTL/ATL, ACWR, correlations); and (c) token persistence for both Strava (rotating refresh tokens that invalidate on each use) and Garmin (SSO session that triggers 48h+ account lockouts if you re-login on every run). These are foundation concerns, not later hardening steps.

Two explicit conflicts require decisions before building: first, the Strava API Agreement's 7-day caching clause and prohibition on feeding Strava data to AI models is in direct tension with the project's design (store all-time data indefinitely, analyse with Claude) — the practical enforcement risk is low for private self-data never shared, but this is a real clause that should be acknowledged and documented; second, the repo is public and Claude-written reports contain personal health data — the reports directory and the database must be gitignored or stored outside the repo tree, not merely in `.gitignore` from inside the tree.

---

## Key Findings

### Recommended Stack

The stack is intentionally minimal. Python 3.14 + `uv` is a project constraint and well-supported. `stravalib` 2.4 handles Strava OAuth2 rotating token refresh, `BatchedResultsIterator` pagination, and stream fetching. `garminconnect` ≥0.3.3 is the only viable Garmin option — uses `curl_cffi` TLS impersonation to clear Cloudflare, persists SSO tokens to `~/.garminconnect/garmin_tokens.json`; must be treated as the system's single fragile dependency and isolated behind a connector interface. Raw `sqlite3` with hand-written SQL is the right storage layer (no ORM, no Alembic). `pydantic-settings` with a gitignored `.env` handles config/secrets. `launchd` (not cron) handles scheduling — cron silently skips jobs while the Mac sleeps. `tenacity` wraps calls for retry/backoff. `pytest` + `pytest-recording` (vcrpy) records real API responses for deterministic offline testing.

**Core technologies:**
- `stravalib` 2.4 — Strava OAuth2, paged history, streams; handles token refresh automatically
- `garminconnect` ≥0.3.3 — Garmin wellness; the only option; isolate as a failure domain; pin ≥0.3.3 (fixes 429 account-lockout bug from issue #344)
- `sqlite3` (stdlib) — raw + structured storage; no ORM; version-table migrations
- `typer` 0.25 — CLI (`tempo sync`, `tempo analyze`, `tempo journal`); subcommands map cleanly to domain
- `pydantic-settings` 2.14 — typed config/secrets from gitignored `.env`
- `launchd` LaunchAgent plist — macOS scheduler; runs missed jobs on wake; cron does not
- `tenacity` 9.x — retry/backoff for Strava rate limits and Garmin 429s
- `uv` — packaging, venv, script runner (project constraint)

**Defer:** `polars` (add only when SQL-side rolling windows become awkward); avoid `pandas` entirely for new code.

### Expected Features

The feature dependency chain is strict: everything flows from per-activity load metrics, which flow from the date spine, which flows from the raw→structured store, which flows from ingestion. Build out of order and analyses produce confidently wrong output.

**Must have for Strava milestone (P1):**
- Strava ingest (activities + streams) into raw→structured store
- Zero-filled date spine — silently required by every EWMA and rolling window
- Per-activity load: rTSS (pace-based, configurable threshold pace) as primary, hrTSS (HR-zone weighted) as fallback; flag which method produced each day's value
- CTL/ATL/TSB time series — simple EWMA recurrence once daily load exists
- ACWR / ramp-rate guardrail — near-free given the daily load series
- Load and trend analysis report (weekly volume, intensity mix, CTL/ramp rate)
- races.md + plan.md reading for context
- Race-readiness analysis (Riegel/VDOT + CTL/TSB form check)
- Journaling via Claude → structured rows linked to activity (RPE, feel, notes, sRPE load) — start early so correlation has history
- Markdown reports into reports/ + CLI entrypoint

**Must have for Garmin milestone (P2):**
- Garmin wellness ingest (HRV, sleep, RHR, body battery, stress) — gated until Strava loop is trusted
- Personal HRV/RHR/sleep rolling baselines (raw wellness values are meaningless without personal baseline)
- Recovery/overtraining analysis (multi-signal; encode the "HRV can paradoxically rise in deep OTS" subtlety)
- Correlation insight (data-hungry; honest "insufficient data" reporting until history accumulates)
- Scheduled daily sync + "only surface when noteworthy" check
- sRPE as parallel load track for cross-training and gappy data

**Defer to v2+:** Effective VO2max/pace-at-HR trend; EWMA-method ACWR; marathon-shape long-run model; CSV nutrition ingest; structured plan-vs-actual engine.

**Explicit anti-features:** Web/mobile UI; real-time tracking; social features; multi-user/hosting; structured plan engine; re-deriving Garmin's closed proprietary scores; auto-prescribing workouts.

### Architecture Approach

Tempo is a small batch ELT pipeline using medallion layering (bronze=raw, silver=structured, gold=daily summary) collapsed into a single SQLite file. Three non-negotiable architectural rules: (1) connectors write only to `raw_response`, transforms read only from raw and write to structured — this boundary makes `tempo rederive` work without network calls; (2) everything joins through a date spine so rest days, Garmin-only days, and activity-only days are first-class rows (not dropped by inner joins); (3) Claude writes structured rows only through a validated entrypoint with field checks and activity-id resolution — never free-form SQL.

**Major components:**
1. **Connectors** — auth, paging, rate-limit handling, retries; write only to `raw_response`; thin `Connector` protocol (`backfill()`, `sync(since)`)
2. **Raw store / bronze** — `raw_response` table; verbatim JSON; idempotent upsert on `(source, endpoint, entity_key)`
3. **Transforms** — pure functions: read raw JSON → upsert typed structured rows; deterministic and re-runnable
4. **Structured store / silver** — `activity`, `activity_stream`, `wellness_day`, `journal`; all keyed through `date_spine`
5. **Date spine + daily summary / gold** — `date_spine` dim + `daily_summary` view; one row per calendar day; left-joins all sources
6. **Analysis layer** — reads gold/silver + plan/race markdown; produces findings; Claude renders narrative
7. **Report writer** — dated markdown to `reports/`
8. **Journal capture** — validated `tempo journal add` entrypoint; Claude calls it, never writes SQL directly
9. **CLI + launchd scheduler** — `typer` app orchestrating sync → transform → analyze daily

### Critical Pitfalls

1. **Secret or health-data leak into the public repo** — Keep the SQLite DB, tokens, `.env`, and `reports/` outside the repo tree entirely (e.g., `~/.tempo/`). Install a `gitleaks` pre-commit hook. Decide before the first commit whether `reports/` contains personal health data — if yes, gitignore or store outside the tree. Foundation phase, not a later concern.

2. **Garmin account lockout from repeated logins** — Authenticate once, persist tokens, reuse indefinitely. The scheduled job must never trigger a fresh login. On any 429: fail the run, log, back off for hours — do not retry. Retrying compounds the block. Token persistence + no-retry-on-429 are core requirements of the Garmin phase, not hardening.

3. **Strava refresh-token rotation lost** — After every token refresh, persist the new `refresh_token` and `expires_at` atomically (write to temp, fsync, rename) before any API calls. A single non-atomic write that loses the rotated token requires a full browser OAuth re-flow. Foundation-level concern for the Strava phase.

4. **Missing date spine corrupts every analysis** — CTL/ATL EWMAs and ACWR rolling windows are silently wrong if rest days have no row. Build the zero-filled date spine in the same phase as Strava transforms, before any analysis.

5. **Wrong-day timezone bucketing corrupts the date spine** — Strava's `start_date_local` has a trailing `Z` that does NOT mean UTC (it's wall-clock local). Garmin sleep spans two days — key by Garmin's `calendarDate`, not the sleep start timestamp. Handle in the transform layer so it can be fixed and re-derived from raw. Edge-case tests are a success criterion for the storage phase.

6. **Strava all-time backfill blows the rate limit with no checkpoint** — Streams cost ≥2 API calls per activity; no bulk export. The backfill must be resumable via `backfill_cursor` in `sync_state`, idempotent (raw upserts = re-runs never re-fetch), rate-limit-header-aware, and spread deliberately across multiple days. Fetch streams lazily, not eagerly for all-time history. A design requirement of the Strava connector phase.

7. **Garmin library fragility — auth can break overnight** — `garth` was deprecated 2026-03-27 after Garmin changed their auth flow. Design the Garmin connector behind a narrow `Connector` interface so a library replacement is a class swap. Make Garmin sync failures non-fatal: a broken Garmin pull logs and skips; Strava sync + analysis on existing data still complete. Document the manual FIT/CSV export fallback.

---

## Implications for Roadmap

### Suggested Phase Structure

**Phase 1: Foundation — DB, credentials, CLI skeleton**
Rationale: Security decisions (data/tokens outside repo tree, pre-commit hook) and schema decisions (date bucketing rule, migration pattern) made here are expensive to change later. Must exist before any connector writes anything.
Delivers: `raw_response`, `date_spine`, `sync_state` tables; credential loading; token file paths outside repo tree; `gitleaks` hook; `tempo` CLI shell; WAL mode on; `.env.example` committed; reports/ policy decided.
Avoids: Secret/health-data leak (Pitfall 1); date bucketing rule defined before data enters (Pitfall 6).

**Phase 2: Strava Ingestion — connector, backfill, incremental sync**
Rationale: Strava is the clean source. Proves extract + idempotent raw store under real rate limits. The backfill is the first real test of the resumable/checkpointed design.
Delivers: Strava OAuth one-time handshake; atomic rotating refresh-token persistence; paged resumable backfill with `backfill_cursor`; rate-limit-header awareness; watermark incremental sync; all responses to `raw_response` only.
DECISION REQUIRED: Document the Strava API Agreement conflict (7-day caching, AI-analysis prohibition) before proceeding. Record the stance — private self-data, never shared, low practical risk — as a known accepted conflict.
Avoids: Token rotation loss (Pitfall 4); backfill rate-limit blowout (Pitfall 5).

**Phase 3: Strava Transforms + Date Spine + Daily Summary**
Rationale: Transforms are pure functions of raw — testable without live API. Date spine and daily summary are correctness prerequisites for every analysis.
Delivers: `tempo rederive`; `activity` + `activity_stream` structured tables; `date_spine` zero-filled; `daily_summary` view; local-date bucketing tested with edge cases (11pm run, timezone travel, DST).
Avoids: Missing spine corrupting analyses; timezone bucketing errors (Pitfall 6).

**Phase 4: Load Metrics + First Analysis Report (Strava end-to-end milestone)**
Rationale: First shippable milestone — Strava pull → store → analyse → report. Validates the architecture in practice.
Delivers: rTSS (configurable threshold pace) + hrTSS fallback; CTL/ATL/TSB daily series; ACWR/ramp-rate guardrail; plan.md/races.md parsing; load & trend report; race-readiness analysis (Riegel/VDOT + CTL/TSB); dated markdown reports with data-freshness headers.
Research flag: rTSS/NGP implementation has moderate complexity — brief research pass recommended during planning on grade-adjusted pace handling and threshold-pace estimation approach.
Avoids: Analysis trusting stale data (every report states per-source last-sync); confidently-wrong output (degrade gracefully to "insufficient data").

**Phase 5: Journaling via Claude**
Rationale: Journaling should start accumulating data as early as possible — every day without a journal entry is a lost paired data point for the correlation analysis.
Delivers: `journal` table; `tempo journal add` validated entrypoint (RPE 1–10, activity-id resolution by date+sport); journal rows in `daily_summary`; sRPE parallel load track.
Avoids: Claude writing SQL freely (architecture anti-pattern 4).

**Phase 6: Garmin Ingestion + Wellness Transforms**
Rationale: Garmin is an isolated phase because of its fragility. By this point the connector/transform pattern is proven; Garmin is "just another adapter."
Delivers: `garminconnect` wrapper implementing `Connector` protocol; token persistence; explicit `tempo garmin login` command separate from scheduled sync; per-account 429 → fail-log-skip (no retry); Garmin failure isolation; `wellness_day` structured table; `calendarDate`-keyed sleep/HRV; daily summary now joins wellness.
Research flag: Higher uncertainty than any other phase. Monitor `garminconnect` upstream; pin version explicitly; budget time for possible version bump.
Avoids: Garmin account lockout (Pitfall 2); Garmin library fragility (Pitfall 3).

**Phase 7: Recovery Analysis + Full Analysis Suite + Scheduler**
Rationale: Recovery analysis requires both the load model (CTL/ATL ramp) and Garmin wellness baselines — correctly the last of the four target analyses. Correlation is data-hungry and must wait for accumulated history.
Delivers: Personal HRV/RHR/sleep rolling baselines; recovery/overtraining analysis (multi-signal; HRV-rises-in-OTS subtlety); correlation insight (honest "insufficient data / weak signal" reporting); "surface only when noteworthy" threshold check; launchd LaunchAgent plist; `last_successful_sync` per source in every report; catch-up sync on wake.
Research flag: HRV baseline cold-start handling and multi-signal combination weighting may benefit from a brief research pass during planning.
Avoids: Scheduled job silent failure (Pitfall 7 — launchd not cron, catch-up sync, staleness flagging).

### Phase Ordering Rationale

- **Strava before Garmin** is forced: Strava proves the full pipeline end-to-end cleanly; Garmin is the single biggest stability risk and should only be added after the architecture is validated.
- **Raw store before transforms before analysis** follows the data dependency strictly — no shortcuts.
- **Date spine in Phase 3, not later** is forced by correctness: CTL/ATL EWMAs are silently wrong without it.
- **Journaling in Phase 5** (after Strava end-to-end, before Garmin) is deliberately early — the correlation analysis is data-hungry and needs every day of paired subjective data it can get.
- **Garmin isolated in Phase 6** — the connector interface designed in Phases 1–2 makes it a class swap, not a pipeline rewrite.

### Research Flags

Needs brief research during planning:
- **Phase 4:** rTSS/NGP formula implementation details (GPS dropout, flat-run fallback, threshold-pace estimation approach)
- **Phase 6:** Monitor `garminconnect` upstream; budget version-bump time
- **Phase 7:** HRV baseline cold-start handling; multi-signal recovery combination logic

Standard patterns (skip research-phase):
- **Phase 1:** DB setup, credential loading, migration pattern, CLI skeleton
- **Phase 2:** stravalib docs comprehensive; rate-limit handling and token rotation well-understood
- **Phase 3:** SQL transforms, date spine, views — standard patterns
- **Phase 5:** Validated insert boundary, activity-id resolution — straightforward

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions verified against PyPI 2026-05-26; garminconnect future reliability inherently uncertain (unofficial) |
| Features | HIGH | Metric formulas verified against TrainingPeaks, Intervals.icu, Runalyze, peer-reviewed sources |
| Architecture | HIGH | Medallion layering, connector pattern, watermark sync — standard patterns applied to a clear domain |
| Pitfalls | HIGH | Strava from official docs; Garmin lockout from active GitHub issues; garth deprecation from maintainer; launchd from Apple docs |

**Overall confidence:** HIGH

### Gaps to Address

- **rTSS/NGP implementation:** Formula documented in FEATURES.md but implementation details (GPS dropout handling, flat-run fallback, threshold-pace estimation) need a planning-phase research pass for Phase 4. hrTSS-only is a valid v1 fallback.
- **Threshold pace estimation:** User-configured value in `.env` is fine for v1; auto-derivation from best-effort data is a later decision.
- **Strava API Agreement stance:** The 7-day caching clause and AI-analysis prohibition are real conflicts with the project's design. Document the stance explicitly in Phase 2 (private self-data, never shared, low practical enforcement risk) — not silently ignored.
- **reports/ policy:** PROJECT.md says reports go "into a reports/ folder in the repo." PITFALLS flags this as a health-data leak on a public repo. Must decide in Phase 1: gitignore `reports/`? Store outside repo tree? Commit only sanitized reports?
- **HRV baseline cold-start:** Recovery analysis will be low-quality for the first several weeks of Garmin data. Reports must flag this honestly; plan for it in Phase 7 requirements.
- **Activity stream fetch strategy:** The exact trigger for stream fetching (analyse-time, explicit command, background for recent activities) needs a decision in Phase 2/8 planning.

---

## Sources

**Primary (HIGH):** PyPI JSON API (2026-05-26); developers.strava.com/docs/rate-limits + authentication; strava.com/legal/api; github.com/cyberjunky/python-garminconnect (README + issues #344, #213, #127); Garmin developer portal (calendarDate field); github.com/matin/garth/discussions/222 (garth deprecation 2026-03-27); Apple developer docs (launchd).

**Secondary (MEDIUM):** TrainingPeaks Help Center (CTL/ATL/TSB, rTSS); Intervals.icu (Fitness/Fatigue/Form); Runalyze docs (Marathon Shape, Effective VO2max); Science for Sport (ACWR 0.8–1.3 / >1.5); peer-reviewed PMC sources (ACWR injury risk, sRPE validation, sleep/performance correlation); RunnersConnect + sport-calculator.com (Riegel, VDOT); Databricks/Microsoft Learn (medallion architecture); openwearables.io (Strava streams cost, no bulk export); Strava developer community group (start_date_local fake-Z).

**Tertiary (LOW):** garmin-health-data repo (curl_cffi TLS impersonation details); schemalens.tech (SQLite migration patterns).

---
*Research completed: 2026-05-26*
*Ready for roadmap: yes*
