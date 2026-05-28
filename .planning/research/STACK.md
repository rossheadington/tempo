# Stack Research

**Domain:** Personal, local-first training/health data pipeline (Strava + Garmin → SQLite, scheduled Claude analysis)
**Researched:** 2026-05-26
**Confidence:** HIGH (versions verified against PyPI/GitHub on 2026-05-26; not from training data)

## Executive Recommendation

Build it small and boring. For a single-user, local, batch tool the right shape is:

- **`stravalib`** for Strava (it solves OAuth2 token refresh and stream pagination for you).
- **`garminconnect`** for Garmin (the only viable option; isolate it behind a connector — it's unofficial and the most fragile part of the system).
- **Raw `sqlite3` + hand-written SQL** for storage, with a tiny version-number migration helper. No ORM.
- **`typer`** for the CLI, **`pydantic-settings`** for config/secrets, **`httpx`** only for the one-off Strava OAuth handshake if you don't want to lean on stravalib for it.
- **Skip pandas/polars** for v1 — SQL does the joins and aggregates; reach for **polars** later only if in-Python transforms get heavy.
- **`launchd`** for scheduling on macOS (handles sleep/wake; cron does not).
- **`pytest` + `pytest-recording` (vcrpy)** for testing API connectors against recorded cassettes.

Everything managed with **`uv`**.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.14 | Runtime | Project constraint. All recommended libs support it (garminconnect requires ≥3.12, pandas 3.0 requires ≥3.11, everything else ≥3.10/3.11). |
| uv | latest (0.9.x line) | Packaging, venv, dep resolution, script running | Project constraint; now the de-facto standard fast Python packager. Use `uv run runos ...` and a `[project.scripts]` entry point. |
| stravalib | 2.4 (Jun 2025) | Strava API v3 client | Handles the painful parts: OAuth2 `refresh_access_token()` + auto-refresh, `BatchedResultsIterator` pagination for all-time history, and `get_activity_streams()` for HR/pace/GPS/power/cadence. Actively maintained (1000+ commits), Python 3.11+. |
| garminconnect | 0.3.3 (May 2026) | Garmin Connect wellness data | The only practical option. v0.3.x uses the mobile-app SSO flow via `curl_cffi` TLS impersonation to get past Cloudflare; auto-saves/refreshes DI OAuth tokens to `~/.garminconnect/`. Returns sleep, HRV, body battery, resting HR, stress, steps. Requires Python ≥3.12. **Treat as fragile — see Pitfalls.** |
| SQLite (stdlib `sqlite3`) | bundled with Python 3.14 | Storage (raw + structured) | Project constraint and the correct choice: zero-infra, single-file, transactional DDL. Stdlib driver is sufficient; no ORM needed for a single-user schema you control. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| typer | 0.25.1 | CLI framework (`runos sync`, `runos analyze`, ...) | Always. Type-hint-driven, built on click, great DX, auto help/completion. Subcommand groups map cleanly to connectors + analyses. |
| pydantic-settings | 2.14.1 | Config + secrets from `.env` / env vars | Always. Typed settings (Strava client id/secret, Garmin email, paths) with validation. Pairs with a gitignored `.env`. |
| python-dotenv | 1.2.2 | `.env` loading | Optional — only if you want dotenv loading outside pydantic-settings (pydantic-settings already reads `.env` natively). Usually not needed. |
| httpx | 0.28.1 | Direct HTTP (Strava OAuth bootstrap, ad-hoc calls) | Use for the one-time browser-redirect OAuth code exchange if you'd rather not route it through stravalib, or for any endpoint a wrapper doesn't cover. Modern, sync+async, good defaults. |
| polars | 1.41.0 | Columnar dataframe transforms | **Defer.** Add only if SQL-side aggregation becomes awkward (e.g. rolling fitness/fatigue windows, ACWR). Reads directly from SQLite via `read_database`. Prefer over pandas for new code: faster, lower memory, cleaner API, no index footguns. |
| tenacity | 9.x | Retry/backoff for flaky calls | Recommended for wrapping Garmin calls (429s, transient SSO failures) and Strava rate-limit `429`/`X-RateLimit` handling with exponential backoff + jitter. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pytest | 9.0.3 — test runner | Standard. Use fixtures for a temp SQLite DB per test. |
| pytest-recording (vcrpy 8.x) | Record/replay HTTP cassettes | Record real Strava/Garmin responses once, replay offline. **Scrub tokens/PII from cassettes** before they touch the repo. This is also how garminconnect itself tests. |
| ruff | latest — lint + format | One tool for lint + format; replaces black/isort/flake8. Fast. |
| mypy or ty | Type checking | Optional but valuable given typed settings/connectors. `ty` (Astral's checker) is emerging; mypy is the safe default. |
| launchd | macOS scheduler | Not a Python lib — see Scheduling section. The daily-sync trigger. |

## Installation

```bash
# Project init
uv init runos
cd RunOS

# Core runtime deps
uv add stravalib garminconnect typer pydantic-settings httpx tenacity

# Optional (add when transforms get heavy)
# uv add polars

# Dev deps
uv add --dev pytest pytest-recording ruff mypy

# Run the CLI (after defining [project.scripts] tempo = "runos.cli:app")
uv run runos --help
```

## Decisions by Dimension

### Strava access — use `stravalib`, not raw httpx
Strava access tokens expire every 6 hours; you need refresh-token rotation, paged history, and stream fetching. `stravalib` gives you `Client.refresh_access_token()` plus auto-refresh, a `BatchedResultsIterator` that lazily pages `get_activities()`, and `get_activity_streams()` for the time-series (HR, pace, latlng, power, cadence, altitude). Reimplementing that on raw `httpx` is avoidable busywork. **Caveat:** stravalib does not transparently throttle to Strava's rate limits — wrap calls with backoff (tenacity) and honour `429` responses yourself. Use `httpx` only for the initial one-time authorization-code exchange if you prefer a minimal handshake script. Confidence: HIGH.

### Garmin access — `garminconnect` is the only realistic choice, isolate it
There is no official individual Garmin Health API. `garminconnect` 0.3.x logs in via the Android-app SSO flow and uses `curl_cffi` to impersonate browser TLS fingerprints and clear Cloudflare. It auto-persists/refreshes DI OAuth tokens (`~/.garminconnect/garmin_tokens.json`, mode 0600) so you log in (with MFA via the `prompt_mfa` callback) only when the refresh token dies. It exposes sleep, HRV, body battery, resting HR, stress, steps, and more (130+ methods). **This is your single biggest stability risk:** it can break on Garmin site/Cloudflare changes, and Garmin rate-limits SSO *per account* (issue #344, fixed via a `widget+cffi` strategy in 0.3.x — so pin ≥0.3.3). Design the Garmin connector behind a narrow interface, store raw responses verbatim, and fail gracefully so a Garmin outage never blocks Strava ingestion or analysis. Confidence: HIGH on current state; the library's *future* reliability is inherently LOW.

### SQLite layer — raw `sqlite3` + hand-written schema, tiny migration helper
A single-user schema you fully control does not need an ORM. Raw `sqlite3` with parameterised SQL keeps the two-layer raw→structured design transparent and makes "re-derive structured tables from raw without re-fetching" a plain SQL re-run. For schema evolution, use a `user_version`/version-table pattern (bump an integer, apply ordered migration steps in a transaction). Avoid Alembic+SQLAlchemy here — it's weight you don't need and SQLite's limited `ALTER TABLE` makes ORM autogenerate migrations more fiddly than helpful for this size. `sqlite-utils` is a reasonable convenience for ad-hoc ingest/exploration and for its 12-step table rebuilds, but you don't need to build the schema layer on it. Store raw JSON responses in `TEXT`/`JSON` columns; SQLite's JSON1 functions let you query them directly. Confidence: HIGH.

### Data modelling/transform — SQL first, polars later, pandas not at all
For daily summaries, date-spine joins, and most training-load math, SQL views/queries are sufficient and keep logic next to the data. Don't add a dataframe lib for v1. When in-Python numeric work does appear (rolling 7/28-day load, ACWR, correlations), reach for **polars** (1.41) — faster, lower memory, no index surprises, and it reads SQLite directly. Skip **pandas** for new code; pandas 3.0 is fine but offers no advantage here and brings heavier deps. Confidence: HIGH (recommendation), MEDIUM (exact future need).

### CLI — `typer`
Type-hint-driven, built on click, near-zero boilerplate, automatic `--help` and shell completion. Subcommands model the domain cleanly (`runos strava backfill`, `runos strava sync`, `runos garmin sync`, `runos analyze recovery`). Plain `click` is the fallback if you want fewer abstractions, but typer is strictly more ergonomic for this. Confidence: HIGH.

### Config & secrets — `pydantic-settings` + gitignored `.env`; tokens on disk with 0600
Typed settings object reads Strava client id/secret, Garmin email, and paths from a gitignored `.env`/env vars with validation. OAuth tokens: let stravalib hold the Strava refresh token (persist it to a gitignored file under e.g. `~/.config/runos/` or the project's gitignored data dir), and let garminconnect manage its own token cache in `~/.garminconnect/`. Ensure `.env`, the SQLite DB, tokens, and `reports/` health output are in `.gitignore` from the first commit (project constraint: code-only public repo). macOS Keychain is an optional hardening step but `.env` + 0600 token files are the standard, sufficient baseline for a single-user local tool. Confidence: HIGH.

### Scheduling — `launchd` (a `LaunchAgent` plist), not cron
On macOS, prefer a `~/Library/LaunchAgents/com.runos.dailysync.plist` `StartCalendarInterval` agent over cron. Key reason: launchd runs a missed job when the Mac wakes from sleep — essential for a laptop that isn't on at 6am. cron is deprecated on macOS and silently skips jobs while asleep. The agent should invoke `uv run runos sync && uv run runos analyze` (or a small wrapper script). "Running Claude Code on a schedule" is the *delivery* layer for the analysis step (Claude reads the DB + markdown plan files and writes reports), but the *trigger* should still be launchd kicking off the sync + the analysis run; don't rely on a Claude-side scheduler for data ingestion. Confidence: HIGH.

### Testing — `pytest` + recorded HTTP cassettes
Unit-test transforms/SQL against an in-memory or temp-file SQLite DB. For the connectors, use `pytest-recording`/vcrpy to record real Strava and Garmin responses once and replay them offline — fast, deterministic, no live API hits in CI. **Scrub access tokens, refresh tokens, and any PII from cassettes** before committing (vcrpy supports filtering). This mirrors how garminconnect tests itself. Confidence: HIGH.

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| stravalib | raw httpx + manual OAuth | If you want zero dependencies and full control of the wire format; accept that you reimplement token refresh, pagination, and stream parsing. |
| garminconnect | garth (lower-level) / garmy | garth is the lower-level auth/HTTP primitive; garminconnect now bundles its own SSO. garmy is a newer alternative wrapper — watch it, but garminconnect is far more proven. Switch only if garminconnect stalls. |
| raw sqlite3 + SQL | SQLModel / SQLAlchemy 2.0 | If the schema grows complex, you want typed models shared with pydantic, and you're comfortable adding Alembic for migrations. Overkill for single-user v1. |
| version-table migrations | Alembic 1.18 / sqlite-migrate | Alembic if you adopt SQLAlchemy; `sqlite-migrate` (Simon Willison) if you want a light decorator-based system without an ORM. |
| SQL-first transforms | polars 1.41 | When rolling-window / correlation math is cleaner in a dataframe than in SQL. Add then, not now. |
| polars (when needed) | pandas 3.0 | Only if a downstream lib hands you pandas or you already know pandas and the dataset is tiny. |
| typer | click 8.x | If you dislike the type-hint magic and want explicit decorators. |
| launchd | cron | Only on a machine that's always on (a server), where missed-while-asleep isn't a concern. Not this laptop use case. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| An ORM (SQLAlchemy/SQLModel) for v1 | Adds abstraction + a migration framework for a schema you fully own and that must transparently re-derive from raw JSON. Net friction. | Raw `sqlite3` + parameterised SQL + version-table migrations. |
| Alembic for v1 | Mature but heavy; SQLite's limited `ALTER TABLE` makes ORM-driven autogenerate fiddly at this size. | Integer `user_version` + ordered migration functions in a transaction. |
| pandas for new transform code | No advantage here over polars; heavier, index footguns, mutable-state bugs. | polars (only when SQL isn't enough). |
| cron on macOS | Deprecated on macOS; silently skips jobs while the Mac sleeps — your daily sync would just not run. | launchd LaunchAgent with `StartCalendarInterval`. |
| Unofficial/scraped MyFitnessPal libs | API removed 2020; scrapers are fragile and ToS-hostile. Already out of scope. | Defer; later CSV-drop ingest if ever needed. |
| garminconnect < 0.3.3 | Earlier versions hit per-account 429 SSO rate limiting (issue #344). | Pin `garminconnect>=0.3.3` (widget+cffi login strategy). |
| Committing tokens/`.env`/DB | Public repo; non-negotiable privacy constraint. | gitignore from first commit; 0600 token files; pydantic-settings + `.env`. |

## Terms-of-Service & Rate-Limit Reality (read this)

**Strava API limits (default, unverified app):** 200 requests / 15 min and 2,000 / day overall; 100 / 15 min and 1,000 / day for non-upload (read) endpoints. An all-time backfill — activities + per-activity streams — *will* exceed a 15-minute window for an active athlete, so the backfill must be **paged, resumable, and rate-aware**: checkpoint progress to SQLite, watch `X-RateLimit-Usage`/`Limit` headers, and back off on `429`. Daily incremental sync stays well within limits. Confidence: HIGH (from developers.strava.com).

**Strava API Agreement caveat (flag prominently):** the standard agreement states *no Strava Data shall remain in your cache longer than seven days*, requires a registered Developer Application even for personal use, and forbids displaying other users' data. The project's design (store all-time raw responses verbatim, indefinitely) is in tension with the 7-day caching clause. For a **private, single-user tool operating only on the owner's own data, never redistributed**, practical/enforcement risk is low — but this is a genuine terms conflict, not a non-issue. Document it; keep the repo data-free; do not publish or share the stored data. Confidence: HIGH that the clause exists; MEDIUM on how strictly it's interpreted for personal self-data.

**Garmin:** no official personal API and no per-user agreement covering `garminconnect`; you're using credentials against a private endpoint. Per-account SSO rate limiting exists — log in rarely (rely on token refresh), don't loop logins, and pin ≥0.3.3. Confidence: HIGH.

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| Python 3.14 | garminconnect 0.3.3 | Lib requires ≥3.12; 3.14 fine. |
| Python 3.14 | pandas 3.0.3 | pandas requires ≥3.11. (Not recommended anyway.) |
| Python 3.14 | stravalib 2.4, typer 0.25, pydantic-settings 2.14, polars 1.41, httpx 0.28 | All ≥3.10/3.11; 3.14 supported. |
| garminconnect 0.3.3 | curl_cffi ≥0.6, requests ≥2.28, ua-generator ≥1.0 | Bundled deps; curl_cffi is the Cloudflare-bypass workhorse — don't replace it. |
| stravalib 2.4 | requests | Uses `requests` internally (not httpx); fine to also have httpx for your own calls. |
| pydantic-settings 2.14 | pydantic v2 | Both Pydantic v2 era; consistent across the stack. |

## Sources

- PyPI JSON API (queried 2026-05-26) — exact current versions + requires_python for garminconnect 0.3.3, stravalib 2.4, garth 0.8.0, httpx 0.28.1, sqlite-utils 3.39, sqlmodel 0.0.38, sqlalchemy 2.0.50, typer 0.25.1, pydantic-settings 2.14.1, python-dotenv 1.2.2, polars 1.41.0, pandas 3.0.3, pytest 9.0.3, pytest-recording 0.13.4, vcrpy 8.1.1, alembic 1.18.4. Confidence: HIGH.
- github.com/cyberjunky/python-garminconnect (+ README, issue #344) — auth/SSO/MFA flow, curl_cffi TLS impersonation, token storage, per-account 429 fix in 0.3.x. Confidence: HIGH.
- github.com/stravalib/stravalib + stravalib.readthedocs.io — token refresh, BatchedResultsIterator pagination, get_activity_streams, no built-in rate limiting. Confidence: HIGH.
- developers.strava.com/docs/rate-limits — 200/15min, 2000/day (100/1000 non-upload). Confidence: HIGH.
- strava.com/legal/api — 7-day caching clause, developer-app requirement, other-user data restriction. Confidence: HIGH (clause), MEDIUM (personal-use interpretation).
- Apple developer docs + community guides — launchd preferred over cron on macOS, runs missed jobs after sleep. Confidence: HIGH.
- simonw/sqlite-migrate, alembic docs, SQLite ALTER TABLE limitations — migration approach rationale. Confidence: MEDIUM/HIGH.

---
*Stack research for: personal local-first Strava/Garmin → SQLite training-health pipeline*
*Researched: 2026-05-26*
