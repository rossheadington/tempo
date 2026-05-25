-- Migration 0001: foundation schema (Phase 1).
--
-- Establishes the raw (bronze) store, the date-spine dimension, and per-source
-- sync state. Structured (silver) and summary (gold) tables arrive in later
-- phases. This migration is applied inside a single transaction by db.migrate()
-- which then bumps PRAGMA user_version to 1.

-- ── RAW (bronze): immutable, append/upsert-only, the source of truth ──────────
-- Every external API response is stored here verbatim before any parsing, so
-- structured tables can always be re-derived without re-fetching.
CREATE TABLE raw_response (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,                 -- 'strava' | 'garmin' | 'mfp'
    endpoint    TEXT NOT NULL,                 -- 'activity' | 'streams' | 'sleep' | ...
    entity_key  TEXT NOT NULL,                 -- activity id, or ISO date for daily sources
    payload     TEXT NOT NULL,                 -- verbatim JSON (TEXT; query via JSON1)
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, endpoint, entity_key)      -- idempotent upsert target
);

-- ── DIM: date spine ──────────────────────────────────────────────────────────
-- One row per calendar day (athlete's LOCAL date). The join backbone so that
-- rest days and single-source days are first-class rows, never dropped by joins.
-- See docs/DATE_BUCKETING.md for the local-date attribution rule.
CREATE TABLE date_spine (
    day             TEXT PRIMARY KEY,          -- 'YYYY-MM-DD' (local date)
    dow             INTEGER,                   -- 0=Mon .. 6=Sun
    week            INTEGER,                   -- ISO week number
    month           INTEGER,
    year            INTEGER,
    is_rest_planned INTEGER NOT NULL DEFAULT 0
);

-- ── SYNC STATE: per-source watermarks for idempotent incremental sync ─────────
CREATE TABLE sync_state (
    source            TEXT PRIMARY KEY,        -- 'strava' | 'garmin'
    last_sync_at      TEXT,                    -- last successful sync (ISO datetime)
    last_entity_ts    TEXT,                    -- watermark: latest entity timestamp
    backfill_cursor   TEXT,                    -- resumable backfill checkpoint
    backfill_complete INTEGER NOT NULL DEFAULT 0
);
