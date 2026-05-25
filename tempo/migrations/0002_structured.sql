-- Migration 0002: structured (silver) tables + daily summary (gold) view (Phase 3).
--
-- Phase 1 created the raw (bronze) store, the bare date_spine, and sync_state.
-- This migration adds the structured layer that pure transforms project from raw:
--
--   * `activity`        -- one typed row per Strava activity (STORE-01)
--   * `activity_stream` -- one row per (activity, stream type) time-series (STORE-01)
--   * `daily_summary`   -- the gold VIEW left-joining activities onto the spine,
--                          one row per calendar day, rest days included (STORE-04)
--
-- The schema follows .planning/research/ARCHITECTURE.md's sketch. Every
-- structured table joins through `day` (the athlete's LOCAL calendar date per
-- docs/DATE_BUCKETING.md). The view is shaped so Phase 6 wellness and Phase 5
-- journal can be left-joined in later without reshaping the activity columns.
--
-- Applied in a single transaction by db.migrate(), which then bumps
-- PRAGMA user_version to 2.

-- ── STRUCTURED (silver): one typed row per activity ──────────────────────────
-- A deterministic projection of a raw 'activity_summary' / 'activity' payload.
-- `day` is the local date derived from Strava's start_date_local[:10] (the fake-Z
-- wall-clock value), NOT start_date (true UTC). `start_local` / `start_utc` /
-- `utc_offset` / `timezone` are kept so the bucket stays re-derivable.
CREATE TABLE activity (
    activity_id   INTEGER PRIMARY KEY,          -- Strava activity id
    source        TEXT NOT NULL,                -- 'strava'
    day           TEXT NOT NULL REFERENCES date_spine(day),  -- LOCAL calendar date
    start_local   TEXT,                         -- wall-clock local start (verbatim)
    start_utc     TEXT,                         -- true UTC instant (verbatim)
    utc_offset    REAL,                         -- seconds; kept for re-derivation
    timezone      TEXT,                         -- Strava timezone string (verbatim)
    name          TEXT,
    sport         TEXT,                         -- 'Run','TrailRun',...
    distance_m    REAL,
    moving_s      INTEGER,
    elapsed_s     INTEGER,
    elev_gain_m   REAL,
    avg_hr        REAL,
    max_hr        REAL,
    avg_speed_ms  REAL,                         -- m/s, as Strava reports
    avg_pace_s_km REAL,                         -- derived: seconds per km
    avg_watts     REAL,
    avg_cadence   REAL,
    suffer_score  REAL
);
CREATE INDEX ix_activity_day ON activity(day);

-- ── STRUCTURED (silver): per-activity time-series streams ────────────────────
-- One row per (activity, stream type). `data` is the compact JSON array from the
-- raw streams payload; the raw payload is also retained verbatim in raw_response.
CREATE TABLE activity_stream (
    activity_id   INTEGER NOT NULL REFERENCES activity(activity_id),
    type          TEXT NOT NULL,                -- 'heartrate','latlng','watts','time',...
    data          TEXT,                         -- JSON array of samples
    original_size INTEGER,                      -- Strava's original_size, if present
    resolution    TEXT,                         -- 'high'|'medium'|'low', if present
    PRIMARY KEY (activity_id, type)
);

-- ── GOLD: unified daily summary (VIEW; one row per spine day) ─────────────────
-- LEFT JOIN from date_spine so EVERY calendar day is present, including rest days
-- and gap days (their activity columns are NULL / 0). Multiple activities per day
-- are rolled up. Shaped to admit wellness (Phase 6) and journal (Phase 5) as
-- further LEFT JOINs without changing these columns. See ARCHITECTURE Pattern 3.
CREATE VIEW daily_summary AS
SELECT
    s.day,
    s.dow,
    s.week,
    s.month,
    s.year,
    s.is_rest_planned,
    COALESCE(a.n_activities, 0) AS n_activities,
    a.total_distance_m,
    a.total_moving_s,
    a.total_elapsed_s,
    a.total_elev_gain_m,
    a.max_avg_hr,
    a.max_max_hr,
    a.sports
FROM date_spine s
LEFT JOIN (
    SELECT
        day,
        COUNT(*)            AS n_activities,
        SUM(distance_m)     AS total_distance_m,
        SUM(moving_s)       AS total_moving_s,
        SUM(elapsed_s)      AS total_elapsed_s,
        SUM(elev_gain_m)    AS total_elev_gain_m,
        MAX(avg_hr)         AS max_avg_hr,
        MAX(max_hr)         AS max_max_hr,
        GROUP_CONCAT(DISTINCT sport) AS sports
    FROM activity
    GROUP BY day
) a ON a.day = s.day;
