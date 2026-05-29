# Wave 18-05: Sync pipeline + CLI + Settings + .env.example + docs — Summary

**Status:** Complete
**Files modified:** 7
**Files created:** 3
**Tests added:** 6 (5 pipeline isolation + 1 CLI smoke)
**ruff:** clean

## Delivered

| File | Lines | Change |
|------|-------|--------|
| `runos/config.py` | +9 | `coros_email` + `coros_password: SecretStr` after the Garmin block |
| `runos/sync/pipeline.py` | +60 | `run_coros_sync` mirroring Garmin; `_coros_raw_rows`; `run_full_sync` calls Coros LAST among connectors (Strava → Garmin → Coros) |
| `runos/cli.py` | +99 | `coros_app` Typer group with `runos coros login` + `runos coros sync`. Password prompted via `hide_input=True`. Error handling mirrors Garmin (auth → yellow remediation; missing-creds `ValueError` → red) |
| `runos/sync/daily.py` | unchanged | `daily.py::run_daily` already calls `pipeline.run_full_sync(...)` which now includes Coros |
| `.env.example` | +20 | Coros block appended after Garmin |
| `tests/conftest.py` | +2 | `RUNOS_COROS_EMAIL` / `RUNOS_COROS_PASSWORD` added to env-var scrub list |
| `tests/test_analyze_cli.py` | +49 | `test_runos_coros_sync_calls_connector_and_writes_raw` |
| `docs/COROS.md` | 168 (new) | 8 sections per 18-05 PLAN |
| `tests/test_pipeline.py` | 172 (new) | 5 Coros isolation tests + a sanity error-type-distinct test |

## CLI surface (live)

```
runos coros login           # ONE-TIME: prompts for password (hidden), persists token
runos coros sync            # incremental wellness + EvoLab pull, reuses token
runos --help                # `coros` subcommand visible
runos coros --help          # `login` + `sync` visible
```

## Pipeline isolation

`run_coros_sync(conn, connector, *, since=None) -> SourceResult` catches `CorosSyncError`, `CorosAuthError`, and bare `Exception`. Returns `SourceResult(ok=False, ...)` on any failure. NEVER raises. The hourly sync therefore proceeds to Strava + Garmin + analyses regardless of Coros state.

## Decisions worth knowing

1. **`daily.py` untouched.** `run_daily` calls `pipeline.run_full_sync(conn, settings)` which enumerates sources internally; adding Coros to `run_full_sync` automatically gives the hourly job the new connector.
2. **`requests` left as transitive dep** (pulled by stravalib + garminconnect). No `pyproject.toml` churn for v1.7; left a TODO for a future micro-phase to make it explicit.
3. **CLI password prompt** uses `typer.prompt("Coros password", hide_input=True)` — matches Garmin's MFA-prompt style.
4. **`run_full_sync` early-return-on-Garmin refactored** to `try/except/else` so a Garmin build failure now logs + records but still attempts Coros (Garmin is no longer the last connector). Symmetric isolation preserved.

## Verification

```
uv run python -m pytest tests/test_pipeline.py tests/test_analyze_cli.py tests/test_phase7_cli.py -x → 26 passed
uv run python -m pytest tests/ -x → 735 passed, 1 deselected (no regression)
uv run ruff check runos/ tests/ → clean
runos --help | grep coros → "coros  Coros wellness + EvoLab ingestion: one-time login, …"
runos coros --help → "login" + "sync" visible
```
