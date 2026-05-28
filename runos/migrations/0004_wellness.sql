-- Migration 0004: Garmin wellness_day (silver) + wellness-joined daily_summary (Phase 6).
--
-- Phases 1-5 built the raw store, the structured `activity` layer, the zero-filled
-- date_spine, the `journal` source, and the gold `daily_summary` VIEW (one row per
-- calendar day, already shaped to admit wellness). This migration adds the Garmin
-- wellness source:
--
--   * `wellness_day`   -- one structured row per LOCAL calendar day, the projection
--                         of multiple raw Garmin endpoints (sleep / hrv / stats),
--                         keyed by `day` = Garmin's `calendarDate` (the wake-up day
--                         Garmin assigns to overnight sleep/HRV so the cross-midnight
--                         ambiguity is removed). See docs/DATE_BUCKETING.md and
--                         .planning/research/PITFALLS.md Pitfall 6 (GRMN-04; STORE-05).
--   * `daily_summary`  -- replaced so wellness fields (resting_hr, hrv, sleep score /
--                         duration / stages, body battery, stress, steps) LEFT-JOIN
--                         in per day, preserving one-row-per-spine-day and never
--                         dropping a day (GRMN-04; STORE-04; ARCHITECTURE Anti-Pattern 2).
--
-- The Garmin connector writes ONLY verbatim payloads to raw_response; this silver
-- row is produced purely from raw by runos.transforms.wellness, so `tempo rederive`
-- rebuilds it with zero network calls (ARCHITECTURE Anti-Pattern 1).
--
-- Applied in a single transaction by db.migrate(), which then bumps
-- PRAGMA user_version to 4.

-- == STRUCTURED (silver): one wellness row per local calendar day ==============
-- `day` is Garmin's calendarDate (LOCAL wake-up day), FK to date_spine so wellness
-- buckets on the same axis as activities and journal. All metrics are nullable: a
-- given day may have sleep but no HRV, steps but no sleep, etc. Garmin's own
-- proprietary scores (sleep_score, hrv_status, body battery) are consumed as inputs
-- verbatim -- we do not re-derive them (REQUIREMENTS: out of scope to re-derive
-- Garmin's black-box scores). Sleep stage seconds are stored as available.
CREATE TABLE wellness_day (
    day               TEXT PRIMARY KEY REFERENCES date_spine(day),  -- LOCAL calendarDate
    resting_hr        INTEGER,                  -- bpm, from daily stats / sleep
    hrv_last_night    REAL,                     -- overnight HRV (ms, lastNightAvg)
    hrv_status        TEXT,                     -- Garmin status: 'BALANCED','LOW',...
    sleep_score       INTEGER,                  -- Garmin's 0-100 sleep score
    sleep_seconds     INTEGER,                  -- total measured sleep duration (s)
    deep_s            INTEGER,                  -- deep sleep seconds
    rem_s             INTEGER,                  -- REM sleep seconds
    light_s           INTEGER,                  -- light sleep seconds
    awake_s           INTEGER,                  -- awake seconds during sleep window
    body_battery_high INTEGER,                  -- day's body-battery peak (0-100)
    body_battery_low  INTEGER,                  -- day's body-battery trough (0-100)
    stress_avg        INTEGER,                  -- average all-day stress (0-100)
    steps             INTEGER,                  -- total steps
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_wellness_day ON wellness_day(day);

-- == GOLD: rebuild daily_summary to LEFT-JOIN wellness per day =================
-- A VIEW cannot be ALTERed, so we drop and recreate it with the same activity and
-- journal rollups as 0003 plus a wellness LEFT JOIN. wellness_day is already one
-- row per day so it joins directly. The LEFT JOIN keeps the one-row-per-spine-day
-- invariant: days with no Garmin sync simply have NULL wellness columns; no spine
-- day is ever dropped (ARCHITECTURE Anti-Pattern 2; STORE-04).
DROP VIEW IF EXISTS daily_summary;
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
    a.sports,
    j.rpe,
    j.feel,
    j.srpe,
    COALESCE(j.has_journal, 0) AS has_journal,
    COALESCE(j.has_notes, 0)   AS has_notes,
    -- Wellness (Garmin) -- NULL on days with no wellness data.
    w.resting_hr,
    w.hrv_last_night,
    w.hrv_status,
    w.sleep_score,
    w.sleep_seconds,
    w.deep_s,
    w.rem_s,
    w.light_s,
    w.awake_s,
    w.body_battery_high,
    w.body_battery_low,
    w.stress_avg,
    w.steps,
    CASE WHEN w.day IS NOT NULL THEN 1 ELSE 0 END AS has_wellness
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
) a ON a.day = s.day
LEFT JOIN (
    SELECT
        day,
        (SELECT j2.rpe  FROM journal j2 WHERE j2.day = j1.day
         ORDER BY j2.created_at DESC, j2.id DESC LIMIT 1) AS rpe,
        (SELECT j2.feel FROM journal j2 WHERE j2.day = j1.day
         ORDER BY j2.created_at DESC, j2.id DESC LIMIT 1) AS feel,
        SUM(srpe)                          AS srpe,
        1                                  AS has_journal,
        MAX(CASE WHEN notes IS NOT NULL AND TRIM(notes) <> '' THEN 1 ELSE 0 END) AS has_notes
    FROM journal j1
    GROUP BY day
) j ON j.day = s.day
LEFT JOIN wellness_day w ON w.day = s.day;
