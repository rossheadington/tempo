<!-- GSD:project-start source:PROJECT.md -->
## Project

**Tempo**

Tempo is a personal, local-first training and health system for a runner. It
pulls running and wellness data from Strava and Garmin into a structured,
queryable SQLite database, lets the user plan (races + training plan) and reflect
(post-workout journaling via Claude), and runs scheduled Claude analyses that
write markdown reports on recovery, training load, race readiness, and
correlations. It's a single-user tool for the project owner, not a product.

**Core Value:** Turn scattered training and health data into trustworthy, structured signal that
tells the user when to push, when to back off, and whether they're on track for
their goals — combining objective data (Strava/Garmin) with their own plan and
reflections.

### Constraints

- **Tech stack**: Python 3.14, `uv` for packaging/deps, SQLite for storage — chosen for best health-data library support and a zero-infrastructure local tool.
- **Privacy**: Public repo holds code only. Credentials, tokens, and all health data must stay local and gitignored — non-negotiable.
- **Dependencies**: Garmin access relies on an unofficial library that may break; design connectors to fail gracefully and isolate that risk.
- **Rate limits**: Strava API limits require paged, resumable history backfill for the all-time pull.
- **Local-first**: No servers, no hosted database; everything runs on the user's machine, analyses run on a schedule via Claude.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Executive Recommendation
- **`stravalib`** for Strava (it solves OAuth2 token refresh and stream pagination for you).
- **`garminconnect`** for Garmin (the only viable option; isolate it behind a connector — it's unofficial and the most fragile part of the system).
- **Raw `sqlite3` + hand-written SQL** for storage, with a tiny version-number migration helper. No ORM.
- **`typer`** for the CLI, **`pydantic-settings`** for config/secrets, **`httpx`** only for the one-off Strava OAuth handshake if you don't want to lean on stravalib for it.
- **Skip pandas/polars** for v1 — SQL does the joins and aggregates; reach for **polars** later only if in-Python transforms get heavy.
- **`launchd`** for scheduling on macOS (handles sleep/wake; cron does not).
- **`pytest` + `pytest-recording` (vcrpy)** for testing API connectors against recorded cassettes.
## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.14 | Runtime | Project constraint. All recommended libs support it (garminconnect requires ≥3.12, pandas 3.0 requires ≥3.11, everything else ≥3.10/3.11). |
| uv | latest (0.9.x line) | Packaging, venv, dep resolution, script running | Project constraint; now the de-facto standard fast Python packager. Use `uv run tempo ...` and a `[project.scripts]` entry point. |
| stravalib | 2.4 (Jun 2025) | Strava API v3 client | Handles the painful parts: OAuth2 `refresh_access_token()` + auto-refresh, `BatchedResultsIterator` pagination for all-time history, and `get_activity_streams()` for HR/pace/GPS/power/cadence. Actively maintained (1000+ commits), Python 3.11+. |
| garminconnect | 0.3.3 (May 2026) | Garmin Connect wellness data | The only practical option. v0.3.x uses the mobile-app SSO flow via `curl_cffi` TLS impersonation to get past Cloudflare; auto-saves/refreshes DI OAuth tokens to `~/.garminconnect/`. Returns sleep, HRV, body battery, resting HR, stress, steps. Requires Python ≥3.12. **Treat as fragile — see Pitfalls.** |
| SQLite (stdlib `sqlite3`) | bundled with Python 3.14 | Storage (raw + structured) | Project constraint and the correct choice: zero-infra, single-file, transactional DDL. Stdlib driver is sufficient; no ORM needed for a single-user schema you control. |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| typer | 0.25.1 | CLI framework (`tempo sync`, `tempo analyze`, ...) | Always. Type-hint-driven, built on click, great DX, auto help/completion. Subcommand groups map cleanly to connectors + analyses. |
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
# Project init
# Core runtime deps
# Optional (add when transforms get heavy)
# uv add polars
# Dev deps
# Run the CLI (after defining [project.scripts] tempo = "tempo.cli:app")
## Decisions by Dimension
### Strava access — use `stravalib`, not raw httpx
### Garmin access — `garminconnect` is the only realistic choice, isolate it
### SQLite layer — raw `sqlite3` + hand-written schema, tiny migration helper
### Data modelling/transform — SQL first, polars later, pandas not at all
### CLI — `typer`
### Config & secrets — `pydantic-settings` + gitignored `.env`; tokens on disk with 0600
### Scheduling — `launchd` (a `LaunchAgent` plist), not cron
### Testing — `pytest` + recorded HTTP cassettes
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
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
