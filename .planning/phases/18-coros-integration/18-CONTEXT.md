# Phase 18: Coros Integration — Context

**Gathered:** 2026-05-29
**Status:** Ready for planning
**Source:** Inline owner spec from conversation 2026-05-29. Locked design — no discuss-phase needed.

This phase introduces **Coros** as a third source connector. The owner is switching off Garmin → Coros for wellness data (HRV, sleep, RHR, stress). Activities continue to flow from Strava (Coros syncs to Strava already). The unique new data is **EvoLab** — Coros's proprietary recovery / fitness / threshold / race-prediction metrics, which we don't currently capture from any source.

<domain>
## Phase Boundary

**What this phase delivers (v1.7):**

- A new `runos/connectors/coros.py` implementing the `Connector` protocol against the unofficial Coros Training Hub API (email + MD5-hashed password → bearer token; reusable token persisted to disk; refresh-on-401). Mirrors `runos/connectors/garmin.py` in shape — fragility isolation, login-once, no-retry-on-auth-failure.
- Two `Settings` fields: `coros_email`, `coros_password` (`SecretStr`), prefixed `RUNOS_COROS_*` to match the existing `RUNOS_GARMIN_*` convention.
- Token persistence at `~/.runos/tokens/coros/` (mode 0700; token file 0600; atomic temp-write→fsync→rename).
- A new wellness transform path: Coros writes raw payloads → `runos/transforms/coros_wellness.py` projects them onto the existing `wellness_day` table. Per-`(day, metric)` priority resolver: **Coros wins when present, Garmin fills gaps**. No schema change to `wellness_day`. Garmin connector + transform remain installed — they silently become inert once Coros has covered the day, which gives a safety net for the switchover.
- A new structured table `coros_evolab_day` for Coros's proprietary metrics. Schema migration `0006_coros_evolab.sql`, `SCHEMA_VERSION` bumped 5 → 6. Fields: `day` (PK + FK to date_spine), `recovery_pct`, `base_fitness`, `vo2max`, `threshold_pace_s_per_km`, `race_prediction_5k_s`, `race_prediction_10k_s`, `race_prediction_half_s`, `race_prediction_full_s`, `fetched_at`. Rederivable from raw via `runos rederive`.
- New transform `runos/transforms/coros_evolab.py` — raw EvoLab payload → `coros_evolab_day` row per day. Pure, no network, deterministic, latest-wins.
- New analysis-layer reader `runos/analysis/coros_evolab.py` exposing `EvoLabDay` + `EvoLabContext` + `latest_evolab(conn)` for downstream report renderers.
- Recovery report gains a `## Coros (EvoLab)` section after the existing `## Heat / Strength / Weight / Nutrition` cluster. 3-state degradation rule (absent → omit, stale > 3d → one-line nudge, current → render block). Block shows: recovery % (today), VO2max (today), base fitness (today + 7d delta), Coros's reported threshold pace (info only, never auto-writes preferences.md), and a one-row race-prediction table (5k / 10k / half / marathon).
- `runos/sync/pipeline.py` gains `run_coros_sync` wrapper with symmetric `SourceResult` isolation. Coros failure NEVER blocks Strava or Garmin or analyses (mirrors the Garmin/Strava isolation contract).
- `runos/sync/daily.py` calls Coros alongside Strava + Garmin every hour.
- New CLI subcommand group `runos coros` with two commands: `runos coros login` (interactive: email + password from .env or prompt, performs the auth handshake, persists token) and `runos coros sync` (on-demand pull). Mirrors `runos garmin login` / `runos garmin sync` exactly.
- `.env.example` documents `RUNOS_COROS_EMAIL` + `RUNOS_COROS_PASSWORD` with the same security-tip block as the Garmin keys (chmod 600, never commit, side-effect note about Training Hub web logout).
- New `docs/COROS.md` documenting: setup walkthrough, fragility profile vs Garmin, the wellness priority resolver, EvoLab fields, the Training Hub side-effect, how to fall back to Garmin if Coros is down.
- Tests: `tests/test_coros_connector.py` (auth + token store + endpoint calls via mocked HTTP; mirrors `tests/test_garmin_connector.py`), `tests/test_coros_wellness_transform.py` (projection + priority resolver), `tests/test_coros_evolab_transform.py`, `tests/test_coros_evolab_analysis.py`, `tests/test_pipeline.py` extended for Coros isolation, `tests/test_recovery.py` extended for the new section, `tests/test_analyze_cli.py` extended for `runos coros sync`.

**What this phase does NOT deliver (out of scope, deferred):**

- **Coros activity ingest.** Activities continue to flow from Strava. Coros has them too (with laps + power + EvoLab-tagged streams that don't always survive the Strava round-trip) but de-duplicating against Strava is a separate phase. Reclassify as `COROS-ACTIVITIES-01` for v1.8 if useful.
- **Coros workout-push.** The Training Hub API supports pushing structured workouts to the watch (some community wrappers expose this). Out of scope.
- **Auto-overwriting `preferences.md` from EvoLab's threshold pace.** Surface it as info only in the report; the owner manually updates preferences.md if Coros's number matches their felt threshold. Auto-writing into a human-edited file is a separate (sensitive) design decision.
- **Removing the Garmin connector.** Garmin stays installed and continues to ingest into raw. The wellness transform just stops surfacing Garmin data on days where Coros has provided it. If the owner ever wants to fully retire Garmin, that's a one-line removal of `run_garmin_sync` from `sync/daily.py` later.
- **A migration of historical Garmin wellness_day rows.** No backfill required: existing rows stay; new Coros rows overwrite per-day-per-metric where applicable; the priority resolver works on whatever's present.
- **Multi-account support.** One Coros account per RunOS install (same as Garmin / Strava).
- **Cygnusb/coros-mcp as a pip dependency.** We reference it as the canonical API contract source, but write a thin Python client ourselves (no new heavyweight deps; just `requests` if not already in deps, else `urllib`).

</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Auth + credentials

- `Settings.coros_email: str | None` and `Settings.coros_password: SecretStr | None`, both prefixed `RUNOS_COROS_*` in `.env` (default `RUNOS_` prefix applies — no `validation_alias` override needed).
- Login flow: interactive `runos coros login` reads credentials from settings (so `.env` is the canonical source); falls back to prompts if not set. Performs the email + MD5(password) handshake. Persists the returned bearer token to `~/.runos/tokens/coros/token` (mode 0600, atomic temp→fsync→rename) using the same pattern as `runos/connectors/tokens.py`.
- `sync` / `backfill` paths load **only** from the persisted token. On 401 they **re-authenticate exactly once** using credentials from settings, persist the new token, retry the original call. If that retry also fails: raise `CorosAuthError` and let the pipeline isolate it. NEVER busy-loop on auth failure (rate-limiting / lockout risk, though Coros has shown no lockout behaviour in community reports).
- MD5 hashing: `hashlib.md5(password.encode("utf-8")).hexdigest()`. The Coros app uses lowercase hex. (Reference: cygnusb/coros-mcp `coros_api.py` login flow.)

### API surface (LOCKED — endpoint contract, verified in 18-01)

The unofficial Training Hub API is JSON over HTTPS. Base host: `https://teameuapi.coros.com` (EU region — UK owner). Endpoints we use in v1.7:

| Endpoint | Purpose |
|----------|---------|
| `POST /account/login` | Auth handshake. Body: `{"account": <email>, "accountType": 2, "pwd": <md5-hex>}`. Response: `{"result": "0000", "data": {"accessToken": "...", "userId": "..."}}`. |
| `GET  /dashboard/query` | HRV dashboard. Returns `data.summaryInfo.sleepHrvData.sleepHrvList[]` — each entry has `happenDay` (YYYYMMDD int), `avgSleepHrv`, `sleepHrvBase`. |
| `GET  /analyse/query` | EvoLab analytics. Returns `data.t7dayList[]` — each entry has `happenDay`, `vo2max`, `staminaLevel`, `trainingLoad`, `lthr`, `ltsp`. |
| `GET  /analyse/dayDetail/query?startDay=YYYYMMDD&endDay=YYYYMMDD` | Per-day detail. Returns `data.dayList[]` — each entry has `happenDay`, `rhr`, `avgSleepHrv`, `sleepHrvBase`, `trainingLoad`, plus additional fields (sleep stages, stress, body battery, etc. — 18-02 executor must enumerate from real payload). |

**Auth headers on every authenticated GET**: `accessToken: <token>` + `yfheader: {"userId": "..."}` (NOT `Authorization: Bearer`).

**Success / failure codes**: success = `"result": "0000"`. Auth-fail = `"0102"` (token invalid) or `"0107"` (token expired) — both trigger the one-shot refresh path.

Raw store endpoint labels (preserved as locked):

- `evolab_dashboard` — keyed by ISO `YYYY-MM-DD` of pull date. Stores response from `/analyse/query`. The `t7dayList[]` holds the last 7 days of EvoLab; the transform iterates and upserts per `happenDay`.
- `sleep` — keyed by ISO `YYYY-MM-DD`. Stores response from `/analyse/dayDetail/query` (full payload duplicated under `sleep` AND `heart_rate` labels; transforms slice what they need from each).
- `hrv` — keyed by ISO `YYYY-MM-DD`. Stores response from `/dashboard/query`.
- `heart_rate` — keyed by ISO `YYYY-MM-DD`. Stores response from `/analyse/dayDetail/query` (duplicate of `sleep` — same payload, different transform consumer).

### `wellness_day` priority resolver (LOCKED)

`wellness_day` is single-row-per-day, no `source` column (intentional — keeps the schema flat). The transform reconciles overlapping data via this resolver, applied per `(day, metric)`:

```
For each metric in (resting_hr, hrv_last_night, hrv_status, sleep_score, sleep_seconds,
                    deep_s, rem_s, light_s, awake_s, body_battery_high, body_battery_low,
                    stress_avg, steps):
    coros_value = coros_payload.get(metric)
    garmin_value = garmin_payload.get(metric)
    wellness_day[metric] = coros_value if coros_value is not None else garmin_value
```

Execution: the **Garmin wellness transform runs FIRST** to populate the day; the **Coros wellness transform runs SECOND** and overwrites any metric where Coros has a value. Order is enforced by `runos/transforms/runner.py`. This keeps each transform independently testable and lets the resolver fall through cleanly when only one source has data.

### EvoLab schema (LOCKED — REVISED after 18-01 surfaced the real payload)

```sql
-- Migration 0006_coros_evolab.sql
CREATE TABLE coros_evolab_day (
    day                       TEXT PRIMARY KEY,           -- ISO YYYY-MM-DD
    vo2max                    REAL,                       -- ml/kg/min
    stamina_level             INTEGER,                    -- 0-100; Coros's base fitness equivalent
    training_load             INTEGER,                    -- Coros's load score (units per their dashboard)
    lthr                      INTEGER,                    -- lactate-threshold HR (bpm)
    ltsp_s_per_km             INTEGER,                    -- lactate-threshold speed/pace, normalised to s/km
    fetched_at                TEXT NOT NULL,              -- ISO 8601 UTC
    FOREIGN KEY (day) REFERENCES date_spine(day)
);

CREATE INDEX coros_evolab_day_fetched_at_idx ON coros_evolab_day (fetched_at);
```

All metric columns nullable: not every field is always populated by Coros. `day` is the local calendar date the value was computed for (`happenDay`, converted from YYYYMMDD int to ISO YYYY-MM-DD).

**Fields dropped from the original plan**:
- `recovery_pct` — not present in `/analyse/query` or `/analyse/dayDetail/query`. May exist in a different endpoint; investigate in a future micro-phase if useful. Drop for v1.7.
- `base_fitness` → renamed to `stamina_level` (matches Coros's own name).
- `race_prediction_5k_s / 10k_s / half_s / full_s` — not present in any endpoint we found in 18-01. Drop for v1.7. May be derivable from VO2max + threshold pace in a future analysis pass; not in the schema for now.

**Field added**: `lthr` (lactate-threshold HR) — Coros provides this; useful info that cross-checks against the user's `Physiology.threshold_hr` in `preferences.md`.

**`ltsp` units note**: Coros's `ltsp` field is reported in some unit (likely m/s or km/h based on convention). The transform normalises to seconds-per-km at parse time — internal storage is always s/km, matching the rest of the codebase. The 18-03 executor MUST verify the unit by inspecting a real payload sample (refer to cygnusb/coros-mcp) and document the conversion factor in the transform module's docstring.

### Analysis-layer reader (LOCKED)

`runos/analysis/coros_evolab.py`:

```python
@dataclass(frozen=True, slots=True)
class EvoLabDay:
    day: date
    vo2max: float | None
    stamina_level: int | None
    training_load: int | None
    lthr: int | None
    ltsp_s_per_km: int | None
    fetched_at: datetime

@dataclass(frozen=True, slots=True)
class EvoLabContext:
    present: bool                                   # False if no rows ever
    days: tuple[EvoLabDay, ...]                     # all rows, sorted by day ascending
    latest: EvoLabDay | None                        # convenience: most recent day with any data

def read_evolab(conn: sqlite3.Connection) -> EvoLabContext: ...
```

Pure stdlib, no network, no pandas.

### Recovery-report integration (LOCKED)

`runos/analysis/recovery.py` gains:

- `RecoveryAssessment.evolab: EvoLabDay | None`
- `RecoveryAssessment.evolab_present: bool`
- `_render_evolab_section(...)` following the existing 3-state pattern:
  - **Absent** (`evolab_present is False` OR no rows): section omitted.
  - **Stale** (`latest.day < today - 3`): `## Coros (EvoLab)\n_Last EvoLab reading N days ago — wear the watch to refresh the dashboard._`
  - **Current**: render the block.

Current-state block format (markdown, follows existing recovery-report style — REVISED after 18-01 surfaced the real payload):

```markdown
## Coros (EvoLab)

- VO2max: 56.4 ml/kg/min
- Stamina: 62 (7d Δ +3)
- Training load (today): 412
- Threshold HR (Coros): 172 bpm — _cross-check vs preferences.md `threshold_hr`_
- Threshold pace (Coros): 3:58/km — _cross-check vs preferences.md `threshold_pace`_
```

Pace formatting uses `runos.units.format_pace` so the user's `Units` preference is honoured (miles/min-per-mile renders, where applicable). Race predictions removed — Coros's `/analyse/query` endpoint does not expose them in the v1.7 surface. If a future micro-phase adds a race-predictions endpoint, that section can be re-added.

### Section placement

Recovery report sections, post-Phase-18, in order:

1. `## Verdict`
2. `## Load (fatigue driver)`
3. `## Recovery markers vs personal baseline` (HRV / RHR / sleep — fed from `wellness_day`)
4. `## Heat adaptation`
5. `## Strength & conditioning`
6. `## Weight`
7. `## Nutrition`
8. **`## Coros (EvoLab)`** ← NEW
9. (future trailing sections)

### CLI surface (LOCKED)

Two new commands, mirroring the Garmin commands exactly:

```
runos coros login           # interactive auth handshake, persists token
runos coros sync            # on-demand wellness + EvoLab pull
```

Plus `runos sync` (the top-level multi-source) calls Coros alongside Strava + Garmin.

### Sync pipeline (LOCKED)

`runos/sync/pipeline.py` gains:

- `CorosAuthError` and `CorosSyncError` exception types (importable from `runos.connectors.coros`).
- `run_coros_sync(conn, connector, *, since=None) -> SourceResult` — symmetric to `run_garmin_sync`. Catches `CorosSyncError` / `CorosAuthError` / bare `Exception` → returns `SourceResult(ok=False, ...)`. NEVER raises.
- `_coros_raw_rows(conn)` counter helper.

`runos/sync/daily.py::run_daily` calls `run_coros_sync` between `run_garmin_sync` and `transform`. Order doesn't matter for the final state (transforms run after all sources) but Coros AFTER Garmin gives the deterministic "Coros wins" priority in the wellness transform.

### Config / .env

- `Settings.coros_email: str | None` — `Field(default=None, description="Coros account email.")`
- `Settings.coros_password: SecretStr | None` — `Field(default=None, description="Coros account password.")`
- Standard `RUNOS_` env prefix → no `validation_alias` needed (the existing prefix applies).
- `.env.example` updated with a new section after the Garmin block:

```
# ---- Coros (Phase 18) ---------------------------------------------------------
# Your personal Coros account credentials (the email + password you use to log
# in to https://training.coros.com / the Coros app). The connector wraps the
# unofficial Training Hub API:
#   - Auth: email + MD5-hashed password; bearer token persisted under
#     RUNOS_DATA_DIR/tokens/coros (mode 0600) and refreshed on 401.
#   - No 2FA, no CAPTCHA, no documented lockout — but treat your password as
#     a secret regardless (chmod 600 .env). If it ever leaks, change the Coros
#     password and re-run `runos coros login`.
#   - Side effect: an API login invalidates your training.coros.com browser
#     session. The mobile app is unaffected. Re-log in to the web UI if you
#     need both at the same time.
# One-time setup:
#   1. Fill in the two lines below in your real .env.
#   2. Run `runos coros login` to perform the first handshake.
#   3. The hourly sync pulls daily wellness + EvoLab automatically.
RUNOS_COROS_EMAIL=
RUNOS_COROS_PASSWORD=
```

### Documentation

- `docs/COROS.md` (new) — setup walkthrough + fragility profile + priority-resolver semantics + EvoLab field reference + troubleshooting.
- `README.md` mention added under "What it tracks" (one line + link to `docs/COROS.md`).

### Test scope (LOCKED)

`tests/test_coros_connector.py`:
- `test_login_md5_hashes_password`
- `test_login_persists_token_atomically_mode_0600`
- `test_sync_reuses_persisted_token`
- `test_sync_refreshes_token_on_401_once`
- `test_sync_raises_auth_error_when_no_token_and_no_creds`
- `test_sync_raises_sync_error_on_unexpected_http_error`
- `test_evolab_endpoint_writes_to_raw_response`
- `test_wellness_endpoints_write_per_day_keyed_iso_date`
- `test_backfill_walks_n_days_default`
- `test_sync_lookback_overlaps_for_revision_safety`

`tests/test_coros_wellness_transform.py`:
- `test_coros_only_day_projects_all_metrics`
- `test_garmin_only_day_remains_unchanged`
- `test_dual_source_day_coros_wins_per_metric`
- `test_dual_source_day_garmin_fills_metric_gaps_coros_doesnt_provide`
- `test_empty_raw_produces_no_rows`
- `test_malformed_payload_skipped_with_log`

`tests/test_coros_evolab_transform.py`:
- `test_evolab_payload_projects_all_fields`
- `test_evolab_missing_fields_remain_none`
- `test_evolab_rederive_idempotent`
- `test_evolab_threshold_pace_parsed_to_s_per_km`

`tests/test_coros_evolab_analysis.py`:
- `test_read_evolab_empty_returns_absent_context`
- `test_read_evolab_returns_latest_correctly`
- `test_read_evolab_sorted_ascending_by_day`

`tests/test_recovery.py` extended:
- `test_recovery_omits_evolab_section_when_absent`
- `test_recovery_emits_stale_evolab_nudge`
- `test_recovery_renders_evolab_block_with_race_predictions`
- `test_recovery_evolab_renders_pace_in_user_units` (miles vs km)

`tests/test_pipeline.py` extended:
- `test_run_coros_sync_returns_ok_on_success`
- `test_run_coros_sync_isolates_auth_error`
- `test_run_coros_sync_isolates_sync_error`
- `test_run_coros_sync_isolates_unexpected_exception`
- `test_run_coros_sync_failure_does_not_block_strava_or_garmin`

`tests/test_analyze_cli.py` / `tests/test_phase7_cli.py` extended:
- `test_runos_coros_login_persists_token`
- `test_runos_coros_sync_calls_connector_and_writes_raw`

Stdlib + pytest's `tmp_path` only. HTTP mocked via `responses` (already a test dep).

### Out-of-band safety items

- Coros password is a `SecretStr`. NEVER log the raw value. The connector logs the email + a redacted indicator only.
- The token file is mode 0600, owner-only. The wrapping directory is 0700.
- The MD5 hash is per the Coros app's protocol — NOT a security claim (MD5 is broken). The wire is HTTPS; the hash is a transport-layer convention Coros chose, and we follow it. Don't editorialise in code comments; just implement what the protocol requires.
- The connector NEVER retries on auth failure beyond a single token refresh. NEVER busy-loops.
- The migration is idempotent (the existing `db.migrate` runner skips already-applied versions).

### Code organisation conventions

- New module: `runos/connectors/coros.py` (mirror `runos/connectors/garmin.py`).
- New module: `runos/transforms/coros_wellness.py` (mirror `runos/transforms/wellness.py`).
- New module: `runos/transforms/coros_evolab.py` (new shape; reference `runos/transforms/strava.py` for the per-endpoint payload-projection pattern).
- New module: `runos/analysis/coros_evolab.py` (mirror `runos/analysis/heat.py` for the simple "rollup of a structured table" reader pattern).
- New migration: `runos/migrations/0006_coros_evolab.sql`.
- New CLI section in `runos/cli.py`: `coros_app = typer.Typer(...)` + `runos coros login` + `runos coros sync`. Mirror the Garmin section exactly (~lines 363-460).
- `Settings.coros_email` + `Settings.coros_password` in `runos/config.py` right after the existing Garmin fields (~lines 73-76).
- New `docs/COROS.md` (mirror `docs/RASPBERRY_PI.md` / `docs/TELEGRAM_BOT.md` shape).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Direct-mirror references

- `runos/connectors/garmin.py` (325 lines) — primary template for `coros.py` (Protocol pattern, exception hierarchy, login/sync/backfill split, no-retry-on-auth).
- `runos/connectors/tokens.py` — atomic token-store pattern (temp-write→fsync→rename, mode 0600).
- `runos/connectors/base.py` — `Connector` protocol + `RawWriter` (don't change either; just implement against them).
- `runos/connectors/factory.py` — where `build_garmin_connector` lives. Add `build_coros_connector` here.
- `runos/transforms/wellness.py` (288 lines) — the existing Garmin wellness transform. Read end-to-end. The Coros wellness transform follows the same project-from-raw pattern.
- `runos/transforms/strava.py` — pattern for projecting a single per-day raw payload onto a structured-table row (the EvoLab transform analog).
- `runos/transforms/runner.py` — where the transforms are sequenced. Add Coros wellness AFTER Garmin so the priority resolver works deterministically.
- `runos/sync/pipeline.py:52-90` — exact shape of `run_garmin_sync`. Copy for `run_coros_sync` with the appropriate exception types.
- `runos/sync/daily.py` — find where `run_garmin_sync` + `run_strava_sync` are called; add `run_coros_sync` next to them.
- `runos/cli.py:363-460` — Garmin CLI block (`garmin_app = typer.Typer(...)` + login + backfill + sync). Mirror as `coros_app` (skip backfill for v1.7 — `sync` covers the use case; backfill can ship later).
- `runos/config.py:73-76` — Garmin fields. Add Coros next to them.
- `runos/analysis/heat.py` / `runos/analysis/weight.py` — simple "read a structured table" analysis-module pattern for `coros_evolab.py`.
- `runos/analysis/recovery.py` — find the `_render_weight_section` + `_render_nutrition_section` for the 3-state pattern. Mirror for `_render_evolab_section`.

### External references (executor reads at build time)

- **cygnusb/coros-mcp** — the canonical Python reference for the Coros Training Hub API. Read `coros_api.py` (or whatever the auth + endpoint module is called in their repo) to verify exact endpoint URLs, request bodies, response shapes, and the MD5 password convention. URL: https://github.com/cygnusb/coros-mcp
- **NYT87/coros-connect** (TypeScript) — fallback reference if cygnusb's Python is incomplete on any endpoint. URL: https://github.com/NYT87/coros-connect
- **xballoy/coros-api** — has a Bruno-collection at `/api` documenting endpoints; useful as a cross-check. URL: https://github.com/xballoy/coros-api

### Settings + db

- `runos/db.py:17` — `SCHEMA_VERSION = 5`. Bump to 6 in the migration commit.
- `runos/migrations/0004_wellness.sql` — model the new EvoLab migration after this one's header style + transactional safety pattern.

</canonical_refs>

<specifics>
## Specific Ideas

- **The owner is switching from Garmin to Coros.** Both connectors stay installed during transition. Once Coros has covered every day reliably, the owner can opt to remove Garmin in a follow-up (single-line change to `sync/daily.py`).
- **Garmin remains the safety net.** If Coros's API breaks (it's unofficial), Garmin's already-stored raw + the priority resolver mean the wellness report keeps working on Garmin data alone.
- **The EvoLab dashboard is the only "new data" net-net.** Everything else duplicates what's already in `wellness_day`. The recovery-report Coros section is therefore where the user experiences the new value most directly.
- **Coros's threshold pace** is reported by EvoLab and is informational. The owner manually maintains the source-of-truth value in `preferences.md`. The report renders Coros's number alongside as a cross-check; mismatch is a discussion ("Coros says 3:58/km, you've got 4:00/km in preferences — update?").
- **No backfill in v1.7.** `runos coros sync` walks a small lookback window (3 days, matching Garmin's `SYNC_LOOKBACK_DAYS`); historical data is out of scope. Add `runos coros backfill` later if useful.
- **Schema version bump is the only breaking change.** Existing users will re-run their idempotent migrations on first restart. No data loss; no Garmin retirement; no historical-row rewrite.
- **Pipeline order**: Strava → Garmin → Coros → transform. Coros last among connectors so its raw lands fresh before the transform runs the priority resolver.

</specifics>

<deferred>
## Deferred Ideas

- **Coros activity ingest** with de-dup against Strava. Layer-2 follow-up.
- **Coros stream ingest** (1Hz HR + GPS + power + cadence). Lives or dies with activity ingest.
- **`runos coros backfill`** for full historical wellness + EvoLab. Trivial extension of `sync` over a longer date range.
- **Workout push** (schedule structured workouts to the watch from RunOS). Real product feature; design separately.
- **Multi-account support.** One Coros account per install.
- **Schema column `source`** on `wellness_day` to track provenance per row. Considered, rejected for v1.7 — the priority resolver in the transform layer is simpler and doesn't add schema complexity. Add later if we ever need to render "this metric came from Garmin / Coros" in the UI.
- **Auto-overwriting `preferences.md` from EvoLab threshold pace.** Touching a human-edited file from automated code needs more design.
- **Removing the Garmin connector entirely.** One-line change later, once the owner is confident Coros covers every metric reliably.

</deferred>

---

*Phase: 18-coros-integration*
*Context written from owner's inline spec: 2026-05-29*
