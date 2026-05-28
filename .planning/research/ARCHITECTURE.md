# Architecture Research

**Domain:** Local-first personal data pipeline / analytics (multi-source health & training ingest → SQLite store → scheduled Claude analyses)
**Researched:** 2026-05-26
**Confidence:** HIGH (medallion layering, connector pattern, Strava/Garmin API surface, SQLite JSON storage all verified against official docs and library docs; some recommendations are opinionated design judgement)

## Standard Architecture

RunOS is a small, single-user **batch ELT pipeline** with a **medallion (raw → structured → summary) layering** collapsed into one SQLite file, plus a thin analysis/report layer. The well-trodden pattern for this class of tool:

- **Connectors (extract)** own all I/O with external APIs and write *verbatim* responses.
- **Raw layer (bronze)** stores every payload as JSON, immutable, append-only.
- **Transforms (structured/silver)** parse raw JSON into typed, queryable tables — pure functions of the raw layer, so they can be re-run without re-fetching.
- **Summary layer (gold)** is a unified per-day join (the "daily summary") built on a shared date spine.
- **Analysis + report layer** reads gold/silver and writes markdown.
- **CLI + scheduler** orchestrate the above on a daily cadence.

This mirrors the medallion architecture (bronze=raw, silver=validated/structured, gold=business-ready) — confirmed as the standard ELT layering — but right-sized: no lakehouse, no Spark, just one SQLite file with three table tiers.

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         CLI / Scheduler                            │
│   runos sync · runos backfill · runos analyze · runos journal      │
│   (cron / launchd triggers `runos sync && runos analyze` daily)    │
└───────────────┬──────────────────────────────┬───────────────────┘
                │ orchestrates                  │
┌───────────────▼──────────────┐   ┌────────────▼──────────────────┐
│        CONNECTORS (extract)   │   │   ANALYSIS + REPORTS (gold→md) │
│  ┌──────────┐ ┌────────────┐  │   │  recovery · load · readiness  │
│  │ Strava   │ │  Garmin    │  │   │  correlation                  │
│  │ connector│ │  connector │  │   │  → writes reports/*.md        │
│  └────┬─────┘ └─────┬──────┘  │   └────────────▲──────────────────┘
│       │  (MFP later)│         │                │ reads
└───────┼─────────────┼─────────┘                │
        │ writes verbatim                         │
┌───────▼─────────────▼──────────────────────────┴───────────────────┐
│                      SQLite single file (runos.db)                   │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ RAW (bronze): raw_response  (source,endpoint,key,payload,ts)  │   │
│  └───────────────────────────┬─────────────────────────────────┘   │
│            transforms (pure: raw → typed)  ▼                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌───────────┐  │
│  │ STRUCTURED   │ │ wellness_day │ │ activity_    │ │ journal    │  │
│  │ activity     │ │ (silver)     │ │ stream       │ │ (Claude)   │  │
│  └──────┬───────┘ └──────┬───────┘ └──────────────┘ └─────┬─────┘  │
│         │                │                                  │        │
│  ┌──────▼────────────────▼──────────────────────────────────▼────┐ │
│  │ date_spine (dim) ──< daily_summary (gold view/table) >── join   │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Connector** (per source) | Auth, paging, rate-limit handling, retries; fetch and hand raw payloads to the raw writer. Knows the API; knows *nothing* about structured tables. | A class implementing a `Connector` protocol (`backfill()`, `sync(since)`); wraps `stravalib`/`requests` or `garminconnect`. |
| **Raw store (bronze)** | Persist every response verbatim, idempotently, with provenance. Source of truth for re-derivation. | Single `raw_response` table; JSON in a `TEXT` column; uniqueness on (source, endpoint, entity_key). |
| **Transform** (per source/entity) | Pure function: read raw JSON → upsert typed rows in structured tables. Deterministic, re-runnable. | Module of functions `transform_strava_activity(payload) -> ActivityRow`; SQL `INSERT … ON CONFLICT … UPDATE`. |
| **Structured store (silver)** | Typed, queryable per-entity tables (activities, wellness_day, streams, journal). | Normalised SQLite tables, FKs to `date_spine`. |
| **Date spine (dim)** | One row per calendar date — the join backbone so days with no activity/wellness still exist. | `date_spine(day DATE PK, dow, week, month, year, is_workout_planned …)`. |
| **Summary store (gold)** | `daily_summary` — one row per day joining activity rollups + wellness + journal, ready for analysis. | A `VIEW` first; promote to a materialised table if analysis gets slow. |
| **Analysis** | Read gold/silver (+ markdown plan/race context), produce findings. | Functions returning structured findings; Claude invoked for narrative reports. |
| **Report writer** | Render analysis to dated markdown in `reports/`. | Jinja/templated markdown writer. |
| **Journal capture** | Claude writes structured journal rows linked to an activity. | `runos journal` / MCP/tool call → validated insert into `journal`. |
| **CLI / scheduler** | Orchestrate sync → transform → analyze; entrypoint `runos`. | `typer`/`click` app; cron or launchd. |

## Recommended Project Structure

```
tempo/
├── src/runos/
│   ├── __init__.py
│   ├── cli.py                # typer app: sync, backfill, analyze, journal, rederive
│   ├── config.py             # paths, env, settings (db path, reports dir)
│   ├── db.py                 # connection, pragmas, migration runner
│   ├── migrations/           # ordered .sql schema migrations (0001_init.sql …)
│   ├── connectors/
│   │   ├── base.py           # Connector protocol + RawWriter
│   │   ├── strava.py         # auth, paging, rate limits → raw_response
│   │   ├── garmin.py         # garminconnect wrapper, fail-soft → raw_response
│   │   └── credentials.py    # token/keyring handling (never committed)
│   ├── transforms/
│   │   ├── strava.py         # raw → activity, activity_stream
│   │   ├── garmin.py         # raw → wellness_day
│   │   └── spine.py          # ensure date_spine rows, build daily_summary
│   ├── store/
│   │   ├── raw.py            # upsert into raw_response (idempotent)
│   │   ├── activities.py     # upserts/queries for structured activity tables
│   │   ├── wellness.py
│   │   └── journal.py
│   ├── sync/
│   │   ├── watermark.py      # read/write sync_state per source
│   │   └── pipeline.py       # extract → raw → transform orchestration
│   ├── analysis/
│   │   ├── recovery.py       # load vs HRV/sleep/RHR
│   │   ├── load.py           # CTL/ATL-style volume & intensity trends
│   │   ├── readiness.py      # progress vs goal race
│   │   ├── correlation.py    # sleep/HRV/RPE vs performance
│   │   └── context.py        # parse plan.md / races.md
│   └── reports/
│       └── writer.py         # render markdown into reports/
├── plan.md                   # user-maintained training plan (read for context)
├── races.md                  # user-maintained race calendar
├── reports/                  # generated markdown analyses (gitignored if private)
├── data/runos.db             # SQLite store (gitignored)
└── pyproject.toml            # uv-managed
```

### Structure Rationale

- **`connectors/` vs `transforms/` are strictly separated.** This is the single most important boundary: connectors do network I/O and write *only* raw; transforms read *only* the DB and write structured. This is what makes "re-derive structured from raw without re-fetching" trivial — `runos rederive` runs transforms over existing `raw_response` rows with zero network calls. It also isolates the fragile Garmin dependency: if garminconnect breaks, transforms and analysis still work on already-stored raw.
- **`migrations/` as ordered SQL** keeps schema evolution honest in a long-lived personal DB; raw is append-only so re-deriving after a schema change is safe.
- **`sync/` holds watermark + pipeline** so incremental logic lives in one place, not smeared across connectors.
- **`analysis/` is read-only over gold/silver** and stays independent of how data arrived — analyses don't care whether a number came from Strava or a backfill three months ago.

## Architectural Patterns

### Pattern 1: Connector / Adapter (pluggable sources)

**What:** Each source implements a common `Connector` protocol. The pipeline talks to the protocol, never to a specific API. Adding Garmin (then MFP via CSV) means adding a class, not editing the pipeline.
**When to use:** Always here — it is the core extensibility decision (Strava now, Garmin next, MFP later).
**Trade-offs:** Slight upfront abstraction cost; pays off at source #2. Keep the protocol *thin* — don't over-generalise the shape of returned data, since Strava (per-activity) and Garmin (per-day) differ fundamentally. Normalise differences in `transforms/`, not in the connector interface.

**Example:**
```python
class Connector(Protocol):
    source: str
    def backfill(self, raw: RawWriter) -> None: ...          # all-time history
    def sync(self, raw: RawWriter, since: date | None) -> None: ...  # incremental

class StravaConnector:
    source = "strava"
    def sync(self, raw, since):
        after = int(datetime.combine(since, time.min).timestamp()) if since else None
        page = 1
        while (acts := self._get_activities(after=after, page=page, per_page=200)):
            for a in acts:
                raw.put(endpoint="activity", key=str(a["id"]), payload=a)
            page += 1
```

### Pattern 2: Raw-first ELT (store verbatim, transform later)

**What:** Persist the exact API payload before any parsing. Structured tables are a *projection* of raw, produced by deterministic transforms. Never let a connector write a structured table directly.
**When to use:** Whenever the source schema is rich/unstable or you may want metrics you haven't thought of yet — exactly RunOS's case (Strava streams, Garmin's deeply nested DTOs).
**Trade-offs:** Storage duplication (negligible for one person's history; raw is small JSON). Buys total replayability: discover you want a new metric? Add a column + transform, run `runos rederive`, done — no API calls, no rate-limit risk.

**Example:**
```python
def transform_strava_activity(payload: dict) -> ActivityRow:
    return ActivityRow(
        activity_id=payload["id"],
        source="strava",
        day=payload["start_date_local"][:10],   # ties to date_spine
        sport=payload["sport_type"],
        distance_m=payload["distance"],
        moving_s=payload["moving_time"],
        avg_hr=payload.get("average_heartrate"),
        avg_watts=payload.get("average_watts"),
    )
# rederive: for row in raw where source='strava' and endpoint='activity': upsert(transform(row.payload))
```

### Pattern 3: Date spine + unified daily summary (gold join)

**What:** A `date_spine` dimension with one row per calendar day is the join backbone. `daily_summary` left-joins activity rollups, wellness, and journal onto the spine so *every* day exists — including rest days with only sleep data, or days with HRV but no run. This is what makes "load vs HRV over time" and correlation analysis possible without gaps.
**When to use:** Any time-series analysis joining sources with different grains (Strava = per-activity, Garmin = per-day). Essential here.
**Trade-offs:** Must keep the spine populated forward (a tiny job that ensures rows up to today). Multiple activities per day require a rollup decision (sum distance, max intensity, etc.) baked into the summary.

**Example:**
```sql
CREATE VIEW daily_summary AS
SELECT s.day,
       a.n_activities, a.total_distance_m, a.total_moving_s, a.max_avg_hr,
       w.resting_hr, w.hrv_last_night, w.sleep_score, w.body_battery_low, w.stress_avg,
       j.rpe, j.feel
FROM date_spine s
LEFT JOIN (SELECT day, COUNT(*) n_activities, SUM(distance_m) total_distance_m,
                  SUM(moving_s) total_moving_s, MAX(avg_hr) max_avg_hr
           FROM activity GROUP BY day) a ON a.day = s.day
LEFT JOIN wellness_day w ON w.day = s.day
LEFT JOIN (SELECT activity.day day, journal.rpe, journal.feel
           FROM journal JOIN activity USING (activity_id)) j ON j.day = s.day;
```

### Pattern 4: Watermark-based incremental sync (idempotent)

**What:** Persist a per-source watermark (last successful sync time / last activity timestamp). Daily sync fetches only data newer than the watermark; raw upserts are idempotent so re-running is safe. Backfill is resumable by recording the last page/date reached.
**When to use:** All scheduled syncs. Critical for Strava's rate limits (200 req / 15 min, 2,000 / day verified) — the all-time backfill must be paged and resumable, spread across days if history is large.
**Trade-offs:** Watermark must advance only on *success*; partial failures should not skip data. Use idempotent upserts (`ON CONFLICT`) so an over-fetch (overlapping window) is harmless — prefer slight overlap over gaps.

**Example:**
```python
def run_sync(conn, connector):
    since = read_watermark(conn, connector.source)         # last good day
    connector.sync(RawWriter(conn, connector.source), since=since)
    write_watermark(conn, connector.source, today())       # only after success
```

### Pattern 5: Claude-as-writer with a validated insert boundary

**What:** Claude is the journaling UI, but it does not write SQL freely. It calls a single typed entrypoint (`runos journal add` / an MCP tool) that validates fields (RPE 1–10, links to a real `activity_id`) before inserting. Same boundary used by analysis: Claude reads `daily_summary` + plan/race markdown, writes prose to `reports/`, but structured rows go through validated functions.
**When to use:** Any time an LLM mutates the store. Keeps the DB trustworthy ("trustworthy structured signal" is the stated core value).
**Trade-offs:** Slightly less flexible than free-form SQL; far safer. Resolve "which activity?" by date + sport before insert so journal rows reliably link to activities.

## Data Flow

### Ingest → analyze flow (the daily cycle)

```
cron/launchd (daily)
    ↓
runos sync
    Strava.sync(since=watermark) ─┐
    Garmin.sync(since=watermark) ─┤→ raw_response (verbatim JSON, idempotent upsert)
                                  ↓
    transforms: raw → activity / activity_stream / wellness_day (upsert)
                                  ↓
    spine.ensure(today) ; daily_summary refreshed (view = automatic)
    ↓
runos analyze
    read daily_summary + plan.md + races.md
    recovery / load / readiness / correlation  → findings
    Claude renders → reports/2026-05-26-recovery.md
```

### Re-derivation flow (no network)

```
runos rederive [--source strava]
    SELECT payload FROM raw_response WHERE source=? 
    → run transforms → upsert structured tables
    (zero API calls; safe after schema/transform changes)
```

### Journaling flow

```
user → "tell Claude" → Claude calls runos journal add
    validate (rpe, feel, notes) + resolve activity_id (by date+sport)
    → INSERT journal (FK activity_id)  → flows into daily_summary
```

### Key Data Flows

1. **Extract is the only network boundary.** Everything downstream of `raw_response` is pure DB work — making the system testable offline and resilient to Garmin breakage.
2. **Structured is always reproducible from raw.** No structured row exists that can't be regenerated from a raw payload.
3. **The spine guarantees continuity.** Analyses iterate days, not events, so rest days and missing-source days are first-class.

## Proposed SQLite Schema Sketch

```sql
-- ── RAW (bronze): immutable, append/upsert-only, source of truth ──────────
CREATE TABLE raw_response (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,            -- 'strava' | 'garmin' | 'mfp'
    endpoint    TEXT NOT NULL,            -- 'activity' | 'streams' | 'sleep' | 'hrv' | 'stats'
    entity_key  TEXT NOT NULL,            -- activity id, or ISO date for daily sources
    payload     TEXT NOT NULL,            -- verbatim JSON (TEXT; query via JSON1)
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, endpoint, entity_key) -- idempotent upsert target
);

-- ── DIM: date spine ───────────────────────────────────────────────────────
CREATE TABLE date_spine (
    day     TEXT PRIMARY KEY,             -- 'YYYY-MM-DD'
    dow     INTEGER, week INTEGER, month INTEGER, year INTEGER,
    is_rest_planned INTEGER DEFAULT 0
);

-- ── STRUCTURED (silver) ────────────────────────────────────────────────────
CREATE TABLE activity (
    activity_id   INTEGER PRIMARY KEY,    -- Strava id
    source        TEXT NOT NULL,
    day           TEXT NOT NULL REFERENCES date_spine(day),
    start_local   TEXT,
    sport         TEXT,                   -- 'Run','TrailRun',...
    distance_m    REAL, moving_s INTEGER, elapsed_s INTEGER,
    elev_gain_m   REAL,
    avg_hr        REAL, max_hr REAL,
    avg_pace_s_km REAL, avg_watts REAL, avg_cadence REAL,
    suffer_score  REAL
);
CREATE INDEX ix_activity_day ON activity(day);

CREATE TABLE activity_stream (                 -- detailed time-series per activity
    activity_id INTEGER REFERENCES activity(activity_id),
    type        TEXT,                     -- 'heartrate','latlng','watts','altitude','time'
    data        TEXT,                     -- JSON array (kept compact; raw also retained)
    PRIMARY KEY (activity_id, type)
);

CREATE TABLE wellness_day (                    -- Garmin, one row per day
    day              TEXT PRIMARY KEY REFERENCES date_spine(day),
    resting_hr       INTEGER,
    hrv_last_night   REAL, hrv_status TEXT,
    sleep_seconds    INTEGER, sleep_score INTEGER,
    deep_s INTEGER, rem_s INTEGER, light_s INTEGER, awake_s INTEGER,
    body_battery_high INTEGER, body_battery_low INTEGER,
    stress_avg       INTEGER, steps INTEGER
);

CREATE TABLE journal (                         -- Claude-written, linked to activity
    id          INTEGER PRIMARY KEY,
    activity_id INTEGER REFERENCES activity(activity_id),
    day         TEXT REFERENCES date_spine(day),  -- denormalised for rest-day notes
    rpe         INTEGER CHECK (rpe BETWEEN 1 AND 10),
    feel        TEXT,                     -- 'great','flat','sore',...
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── SYNC STATE: watermarks for idempotent incremental sync ──────────────────
CREATE TABLE sync_state (
    source            TEXT PRIMARY KEY,
    last_sync_at      TEXT,
    last_entity_ts    TEXT,              -- e.g. latest activity start; epoch for Strava 'after'
    backfill_cursor   TEXT,             -- resumable: last page/date reached
    backfill_complete INTEGER DEFAULT 0
);

-- ── GOLD: unified daily summary (view first; materialise only if slow) ───────
CREATE VIEW daily_summary AS
SELECT s.day,
       a.n_activities, a.total_distance_m, a.total_moving_s, a.max_avg_hr,
       w.resting_hr, w.hrv_last_night, w.sleep_score, w.body_battery_low, w.stress_avg, w.steps,
       j.rpe, j.feel
FROM date_spine s
LEFT JOIN (SELECT day, COUNT(*) n_activities, SUM(distance_m) total_distance_m,
                  SUM(moving_s) total_moving_s, MAX(avg_hr) max_avg_hr
           FROM activity GROUP BY day) a ON a.day = s.day
LEFT JOIN wellness_day w ON w.day = s.day
LEFT JOIN (SELECT day, MAX(rpe) rpe, MAX(feel) feel FROM journal GROUP BY day) j ON j.day = s.day;
```

**Schema notes:**
- **`raw_response.payload` as `TEXT` (JSON1), not BLOB.** SQLite's JSON1 functions let you query/extract straight from raw when iterating on transforms (`json_extract(payload,'$.average_heartrate')`). One person's all-time history is comfortably small; no need for a separate file-per-payload store.
- **`entity_key` unifies grains:** activity id for Strava, ISO date for Garmin daily endpoints — both upsert idempotently on `(source, endpoint, entity_key)`.
- **`day` is the universal foreign key** to `date_spine`; every structured table joins through it.
- **Garmin maps to multiple raw rows per day** (endpoints `sleep`, `hrv`, `stats`) that one transform collapses into a single `wellness_day` row — verified against `get_sleep_data`/`get_hrv_data`/`get_stats`, all keyed by ISO date.
- **Summary as a VIEW** keeps gold always-fresh and avoids a materialisation step; promote to a table with a refresh only if analysis queries get slow (unlikely at single-user scale).
- Enable `PRAGMA journal_mode=WAL;` and `PRAGMA foreign_keys=ON;` on connect.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 user, ~years of history | Current design is ideal. SQLite single file, view-based gold, JSON-in-TEXT raw. Nothing to change. |
| Streams get large (GPS/HR per second × thousands of runs) | `activity_stream` JSON can grow. Lazily fetch/transform streams (on demand, not every sync); consider compressing stream JSON or storing only derived series you actually analyse. Raw stream payloads can be fetched only for activities you'll analyse. |
| If ever multi-user (out of scope) | Add a `user_id` column; or one DB file per user. Not a concern for RunOS. |

### Scaling Priorities

1. **First bottleneck: Strava backfill rate limits**, not storage. 200 req/15 min and 2,000/day mean a large all-time history with per-activity stream fetches can take multiple days. Make backfill resumable (`backfill_cursor`) and prioritise the activity list before detailed streams.
2. **Second bottleneck: stream volume in SQLite.** If detailed streams for every run bloat the file, fetch streams on demand rather than eagerly for all-time history.

## Anti-Patterns

### Anti-Pattern 1: Connectors writing structured tables directly

**What people do:** Parse the API response inside the connector and insert into `activity`/`wellness_day`, skipping raw.
**Why it's wrong:** Breaks re-derivation (the stated requirement) — you can't add a new metric without re-fetching, and you're exposed to rate limits and Garmin breakage forever. Couples network shape to schema.
**Do this instead:** Connector writes only `raw_response`. A separate transform reads raw → structured. Re-derivation becomes a no-network DB pass.

### Anti-Pattern 2: No date spine — joining sources directly

**What people do:** `JOIN activity ON wellness_day.day = activity.day`, losing every day that has only one source (rest days with sleep but no run, runs on days with no Garmin sync).
**Why it's wrong:** Recovery and correlation analyses need *continuous* days; inner joins silently drop the most interesting days.
**Do this instead:** Left-join everything onto `date_spine`. Days are first-class; gaps are visible (NULLs), not deleted.

### Anti-Pattern 3: Watermark advanced before success / no overlap tolerance

**What people do:** Advance the sync watermark before confirming the fetch+store succeeded, or fetch a strict non-overlapping window.
**Why it's wrong:** A partial failure permanently skips data; a strict window can miss boundary records.
**Do this instead:** Advance watermark only on success; allow slight overlap (idempotent upserts make re-fetch harmless). Prefer over-fetch to gaps.

### Anti-Pattern 4: Letting Claude write SQL/structured rows freely

**What people do:** Hand the LLM raw DB access to "just insert the journal."
**Why it's wrong:** Undermines the trustworthy-signal core value; bad RPE values, orphaned rows, accidental writes.
**Do this instead:** A single validated entrypoint (`runos journal add` / MCP tool) with field checks and activity-id resolution. Claude reads freely, writes through a typed boundary.

### Anti-Pattern 5: Hard-failing the whole sync when Garmin breaks

**What people do:** One try/except around the whole pipeline; Garmin's unofficial library throws on a site change and the daily Strava sync + analysis never runs.
**Why it's wrong:** The fragile dependency takes down the robust one. PROJECT.md explicitly calls for graceful isolation.
**Do this instead:** Per-connector isolation — a Garmin failure logs and skips, Strava sync + transforms + analysis on existing data still complete.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Strava API v3 | OAuth2; one-time auth, refresh token, paged `GET /athlete/activities` with `after`/`before`/`page`/`per_page` (up to 200). Per-activity detail + `streams` endpoints. | Rate limits: **200 req/15 min, 2,000/day** (non-upload: 100/1,000) — verified. Backfill must be paged + resumable; `after`=epoch for incremental. Library: `stravalib` or thin `requests` wrapper. |
| Garmin Connect | Unofficial `garminconnect` library (`/cyberjunky/python-garminconnect`); logs in with Connect credentials; daily endpoints `get_sleep_data`, `get_hrv_data`, `get_stats` keyed by ISO date — verified. | No official API; can break on site changes / MFA. **Isolate as a failure domain.** Store every response raw so analysis survives outages. |
| MyFitnessPal | Deferred (API removed 2020). | Future: CSV-drop connector writing to `raw_response` with `source='mfp'` — fits the same pattern with no pipeline changes. |
| Claude | Journaling capture + report narrative. Reads `daily_summary` + `plan.md`/`races.md`; writes structured rows via validated entrypoint, prose to `reports/`. | Keep mutations behind a typed boundary. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| connector ↔ raw store | `RawWriter.put(endpoint, key, payload)` | Only place network data enters the DB. |
| raw ↔ transforms | DB read of `raw_response` | Pure, no network — enables `rederive`. |
| transforms ↔ structured | idempotent upserts (`ON CONFLICT`) | Deterministic projection of raw. |
| structured ↔ analysis | read-only over `daily_summary`/silver | Analysis source-agnostic. |
| Claude ↔ store | validated `journal add` entrypoint | No free-form SQL writes. |

## Suggested Build Order (dependencies)

Honours the Strava-first decision and the raw→structured→summary dependency chain. Each step is shippable.

1. **DB + raw layer + CLI skeleton.** `db.py`, migrations (`raw_response`, `date_spine`, `sync_state`), `runos` CLI shell, credential handling. *Foundation for everything; no source logic yet.*
2. **Strava connector → raw (backfill + incremental).** OAuth, paged resumable backfill, watermark sync. Proves extract + idempotent raw store under real rate limits. *Depends on 1.*
3. **Strava transforms → `activity` (+ spine).** raw → structured; `date_spine` ensure; `rederive` command. Proves re-derivation without re-fetch. *Depends on 2.*
4. **`daily_summary` view + first analysis + markdown report.** Build gold view; ship one analysis (training load/trend) end-to-end to `reports/`. **This completes Strava end-to-end (pull → store → analyse) — the validating milestone.** *Depends on 3.*
5. **Journaling via Claude.** `journal` table + validated `journal add` entrypoint + activity resolution; flows into `daily_summary`. *Depends on 3/4.*
6. **Garmin connector → raw → `wellness_day`.** Add the second source through the exact same connector/transform pattern; isolate failures. Spine now joins two sources. *Depends on 1–4; reuses the proven pattern.*
7. **Full analysis suite + daily scheduler.** Recovery (load vs HRV/sleep/RHR), readiness, correlation — now that wellness exists. Wire cron/launchd for daily `sync && analyze`. *Depends on 6.*
8. **Activity streams (on demand).** Detailed HR/GPS/power streams into `activity_stream`, fetched lazily for deeper analysis. *Optional/deferable; depends on 2/3.*
9. **(Later) MFP CSV connector.** Same pattern, new source. *Out of current scope.*

**Why this order:** Each layer depends on the one below (raw → structured → summary → analysis), so the build follows the data flow. Doing *all five layers for Strava first* (steps 1–4) de-risks the whole architecture before touching the fragile Garmin source — by the time Garmin arrives (step 6) the connector/transform pattern is proven and Garmin is "just another adapter."

## Sources

- Databricks — What is Medallion Architecture (bronze/silver/gold layering): https://www.databricks.com/blog/what-is-medallion-architecture (HIGH)
- Microsoft Learn — Medallion lakehouse architecture: https://learn.microsoft.com/en-us/azure/databricks/lakehouse/medallion (HIGH)
- Strava API — Rate Limits (200/15min, 2,000/day; non-upload 100/1,000), last updated 2024-11-11: https://developers.strava.com/docs/rate-limits/ (HIGH)
- Strava API — getLoggedInAthleteActivities (`after`/`before`/`page`/`per_page`): https://developers.strava.com/docs/reference/ (HIGH)
- python-garminconnect docs via Context7 (`get_sleep_data`, `get_hrv_data`, `get_stats` keyed by ISO date): /cyberjunky/python-garminconnect (HIGH)
- SQLite JSON1 extension (query JSON in TEXT columns): https://www.sqlite.org/json1.html (HIGH, training+docs)

---
*Architecture research for: local-first personal training/health data pipeline (RunOS)*
*Researched: 2026-05-26*
