# Wave 18-01: Coros connector module — Summary

**Status:** Complete
**Files created:** 2
**Files modified:** 1
**Tests added:** 12 (all passing)
**ruff:** clean

## Delivered

| File | Lines | Role |
|------|-------|------|
| `runos/connectors/coros.py` | 748 (~445 LoC + docstrings) | `SOURCE = "coros"`; endpoint constants; `CorosAuthError` / `CorosSyncError`; `CorosHttpClient` Protocol; `CorosConnector` with `backfill` / `sync` / `_login`; one-shot refresh-on-401; `CorosTokenStore` inlined (separate from `tokens.py` because Coros's bearer+userId model doesn't fit the existing rotating-token schema) |
| `tests/test_coros_connector.py` | 558 | 12 tests covering auth, token storage (atomic, mode 0600), endpoint pulls, 401 refresh, isolation patterns |
| `runos/connectors/factory.py` | +120 | `coros_token_dir` / `coros_token_store` / `build_coros_connector` / `coros_login` / `_coros_credentials` helpers |

## Verification

```
uv run python -m pytest tests/test_coros_connector.py -x  → 12 passed in 0.77s
uv run ruff check runos/connectors/coros.py runos/connectors/factory.py tests/test_coros_connector.py → clean
```

## CRITICAL: API surface differed from CONTEXT.md draft

The original CONTEXT.md had several endpoint URLs / request shapes that don't match the real Coros API. The executor verified the real shapes from `cygnusb/coros-mcp` and corrected them in code. CONTEXT.md has been updated post-wave to reflect reality:

- **Base host**: `https://teameuapi.coros.com` (EU region, UK owner)
- **Login**: `POST /account/login` with body `{"account": <email>, "accountType": 2, "pwd": <md5-hex>}`. Response: `{"result": "0000", "data": {"accessToken": "...", "userId": "..."}}`
- **Auth headers**: `accessToken: <token>` + `yfheader: {"userId": "..."}` (NOT `Authorization: Bearer`)
- **HRV**: `GET /dashboard/query` → `data.summaryInfo.sleepHrvData.sleepHrvList[]`
- **EvoLab**: `GET /analyse/query` → `data.t7dayList[]` with `vo2max`, `staminaLevel`, `trainingLoad`, `lthr`, `ltsp` (NO recovery%, NO race predictions — those don't exist in this endpoint surface)
- **Sleep + RHR**: `GET /analyse/dayDetail/query?startDay=YYYYMMDD&endDay=YYYYMMDD` → `data.dayList[]`
- **Success code**: `"result": "0000"`. Auth-fail: `"0102"` / `"0107"`.

The wave-locked endpoint **labels** (`EP_EVOLAB` / `EP_SLEEP` / `EP_HRV` / `EP_HEART_RATE`) are preserved; only URL routing was corrected.

## Knock-on changes (already applied to CONTEXT.md + 18-03 + 18-04 plans)

- **`coros_evolab_day` schema revised**: dropped `recovery_pct`, `base_fitness` (renamed to `stamina_level`), all four `race_prediction_*` columns. Added `lthr` (threshold HR) and `training_load`. New schema: `(day, vo2max, stamina_level, training_load, lthr, ltsp_s_per_km, fetched_at)`.
- **`EvoLabDay` dataclass** mirror-updated.
- **Recovery report block** revised: no race-predictions table. Renders vo2max / stamina (+7d delta) / training_load / lthr / ltsp as a five-line block.

## Implementation notes worth keeping

- **Token storage shape** is `{"access_token", "user_id"}` JSON — both required because Coros's `yfheader.userId` is mandatory on every authenticated call.
- **`CorosTokenStore` inlined** in `coros.py` rather than extending `runos/connectors/tokens.py`. The existing `TokenSet` is shaped for rotating Strava refresh-tokens; Coros's fixed bearer+userId is structurally different. Kept local to avoid widening `tokens.py` for one consumer.
- **HTTP library**: `requests` (already pulled transitively by `stravalib` and `garminconnect`; verified `import requests` works at 2.34.2). No new heavy deps. 18-05 may want to add it explicitly to `pyproject.toml` for visibility.
- **EU region hard-coded** as `COROS_BASE_URL`. If anyone needs US/Asia later, swap the constant or add `Settings.coros_region` in 18-05.
- **`factory.coros_login()` calls `connector._login()`** (private). Justified — the wave plan says it mirrors `garmin_login` and the handshake-then-persist logic only lives in `_login`.
- **Auth refresh contract**: catches `0102` / `0107` codes → calls `_login` once → retries → propagates `CorosAuthError` on second failure. NEVER busy-loops.

## Not done (intentionally — owned by downstream waves)

- No `Settings.coros_email` / `Settings.coros_password` fields yet (18-05).
- No wire-up into `runos/sync/pipeline.py` (18-05).
- No CLI commands (18-05).
- No transforms (18-02 for wellness, 18-03 for EvoLab).
- No recovery-report integration (18-04).
- No schema migration yet (18-03).
- No docs/COROS.md (18-05).
