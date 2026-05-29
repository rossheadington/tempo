# Phase 18 — Verification

**Date:** 2026-05-29
**Verifier:** orchestrator (goal-backward check against CONTEXT.md)
**Verdict:** PASS

## Goal: integrate Coros as the new wellness + EvoLab source, replacing Garmin for the metrics Coros provides while keeping Garmin installed as fallback. Activities continue to flow from Strava (no change).

## Acceptance criteria from CONTEXT.md § Phase Boundary

| Criterion | Status |
|-----------|--------|
| `runos/connectors/coros.py` implementing the `Connector` protocol; MD5-password auth; token persistence; one-shot refresh on 401 | ✅ 748 lines; 12 tests green |
| `Settings.coros_email` + `Settings.coros_password` (SecretStr); `RUNOS_COROS_*` env naming | ✅ Added in 18-05; `.env.example` documents both |
| Token persistence at `~/.runos/tokens/coros/` (mode 0700; token file 0600; atomic write) | ✅ `CorosTokenStore` inlined in `coros.py` (Coros's bearer+userId model didn't fit the existing rotating-token schema) |
| `runos/transforms/coros_wellness.py` writing into `wellness_day` with per-(day, metric) priority resolver via COALESCE-on-update | ✅ Coros wins on non-NULL; Garmin's writes preserved on NULL; ordering enforced in `transforms/runner.py` |
| `coros_evolab_day` schema migration (`SCHEMA_VERSION` bumped 5 → 6) | ✅ Migration `0006_coros_evolab.sql`; schema verified against fresh tmp DB |
| `runos/transforms/coros_evolab.py` projecting raw → structured | ✅ Pure, idempotent, defensive-parsing |
| `runos/analysis/coros_evolab.py` exposing `EvoLabDay`/`EvoLabContext`/`read_evolab` | ✅ Frozen+slots dataclasses; pure stdlib |
| Recovery report `## Coros (EvoLab)` section with 3-state degradation | ✅ Per-line None-omission, all-None falls through to absent, stale > 3d nudge |
| `runos/sync/pipeline.py::run_coros_sync` with symmetric `SourceResult` isolation | ✅ Catches CorosAuthError / CorosSyncError / bare Exception; NEVER raises |
| `runos/sync/daily.py` calls Coros alongside Strava + Garmin | ✅ Inherits via `pipeline.run_full_sync`; no `daily.py` edit needed |
| `runos coros login` + `runos coros sync` CLI commands | ✅ Mirror Garmin commands exactly |
| `.env.example` documents `RUNOS_COROS_EMAIL` + `RUNOS_COROS_PASSWORD` with the same security-tip block | ✅ Done |
| `docs/COROS.md` end-to-end documentation | ✅ 168 lines, 8 sections |
| Tests: connector + wellness transform + EvoLab transform + EvoLab analysis + pipeline + recovery + CLI | ✅ 12 + 6 + 6 + 3 + 5 + 8 + 1 = 41 new tests across the phase |

## Out-of-scope items (intentionally deferred, per CONTEXT.md § Deferred Ideas)

- **Coros activity ingest** with de-dup vs Strava — `COROS-ACTIVITIES-01` for v1.8.
- **Coros stream ingest** — depends on activity ingest.
- **`runos coros backfill`** for full historical wellness + EvoLab.
- **Workout push** to the watch.
- **Multi-account support.**
- **Schema `source` column on `wellness_day`** — COALESCE-resolver in transform is simpler and ships.
- **Auto-overwriting `preferences.md` from EvoLab threshold pace.**
- **Removing the Garmin connector.** Stays installed as safety net.

## Risk gates

| Gate | Result |
|------|--------|
| Full test suite (`uv run python -m pytest tests/ -x --deselect tests/test_bot_transcribe.py::test_transcribe_file_real_fixture_returns_nonempty`) | 760 passed, 1 deselected ✅ |
| Ruff lint (`uv run ruff check runos/ tests/`) | All checks passed ✅ |
| Schema migration applies cleanly to fresh DB | `PRAGMA user_version = 6`, `coros_evolab_day` table present ✅ |
| CLI surface (`runos --help`, `runos coros --help`) | Both visible ✅ |
| No-network invariant intact (analysis layer doesn't reach the network) | New analysis modules are pure stdlib reads ✅ |
| Garmin tests continue to pass (no regression in the existing source) | 7/7 Garmin wellness tests pass ✅ |

## Privacy gate

| Check | Result |
|-------|--------|
| `coros_password` uses `SecretStr` | ✅ |
| Connector NEVER logs raw email/password/token | ✅ Only logs email domain + presence indicator |
| Token file mode 0600, wrapping dir 0700, atomic write | ✅ Mirrors Garmin/Strava tokens.py pattern |
| MD5 hash documented as protocol convention, not security claim | ✅ Module docstring notes this |

## Net behaviour change

For Ross:
- New file in `.env`: `RUNOS_COROS_EMAIL` + `RUNOS_COROS_PASSWORD` (currently unset).
- New CLI commands: `runos coros login` + `runos coros sync`. Hourly sync now calls Coros automatically once credentials + token are in place.
- New recovery-report section `## Coros (EvoLab)` (appears once Coros data lands).
- Coros wins for `resting_hr` + `hrv_last_night` when both Coros and Garmin have data for the same day. All other wellness metrics (sleep stages, stress, body battery, steps) keep coming from Garmin until Coros's endpoint surface grows to expose them.
- Garmin connector + sync untouched — keeps providing wellness data as before.

For developers (code-side):
- New connector pattern (`coros.py`) follows the Garmin template closely; new transforms (`coros_wellness.py`, `coros_evolab.py`) follow the Garmin wellness template closely.
- New analysis module (`coros_evolab.py`) follows the heat.py / weight.py shape.
- Schema version 5 → 6 (one new table).

## Conclusion

Phase 18 delivers the Coros integration end-to-end: connector, wellness transform with priority resolver, new EvoLab structured table + transform + analysis reader, recovery-report integration, sync pipeline + CLI + docs. 41 new tests, all green; 760 total passing; ruff clean; schema migration verified. Garmin remains installed as a safety net; Coros is now the authoritative source for the metrics it provides.

**Verdict:** PASS (all acceptance criteria met, all risk gates green, privacy gate green).
