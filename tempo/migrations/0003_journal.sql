-- Migration 0003: journal (subjective reflection) table + sRPE-aware daily_summary (Phase 5).
--
-- Phases 1-4 built the raw store, the structured `activity` layer, the zero-filled
-- date_spine, and the gold `daily_summary` VIEW (one row per calendar day, shaped
-- to admit journal + wellness later). This migration adds the journal source:
--
--   * `journal`        -- one structured post-workout / rest-day reflection row,
--                         written ONLY through the validated `tempo journal add`
--                         entrypoint (JRNL-01/02; ARCHITECTURE Pattern 5 + Anti-
--                         Pattern 4: Claude never writes free-form SQL).
--   * `daily_summary`  -- replaced so journal fields (rpe, feel, srpe, has_notes)
--                         LEFT-JOIN in per day, preserving one-row-per-spine-day
--                         and never dropping a day (JRNL-03; STORE-04).
--
-- sRPE (session RPE = RPE x duration_minutes) is a validated subjective load track
-- (see .planning/research/FEATURES.md). It is persisted per entry so analysis can
-- use it as a FALLBACK load on days where pace/HR-based load is insufficient.
--
-- Applied in a single transaction by db.migrate(), which then bumps
-- PRAGMA user_version to 3.

-- ── STRUCTURED (silver): subjective journal entries ──────────────────────────
-- `day` is the resolved LOCAL calendar date (FK to date_spine), so journal rows
-- bucket on the same axis as activities (docs/DATE_BUCKETING.md). `activity_id`
-- is nullable: a journal entry can link to a resolved activity OR stand alone for
-- a rest-day / non-activity reflection. `srpe` is computed at insert time from
-- rpe x duration_min (duration from the linked activity, else an explicit arg).
CREATE TABLE journal (
    id           INTEGER PRIMARY KEY,
    day          TEXT NOT NULL REFERENCES date_spine(day),       -- resolved LOCAL date
    activity_id  INTEGER REFERENCES activity(activity_id),       -- nullable: rest-day notes
    rpe          INTEGER NOT NULL CHECK (rpe BETWEEN 1 AND 10),  -- session RPE 1..10
    feel         TEXT,                                           -- 'great','flat','sore',...
    notes        TEXT,                                           -- free-text reflection
    sport        TEXT,                                           -- optional resolved/declared sport
    duration_min REAL,                                           -- minutes used for sRPE
    srpe         REAL,                                           -- rpe * duration_min (when known)
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_journal_day ON journal(day);
CREATE INDEX ix_journal_activity ON journal(activity_id);

-- ── GOLD: rebuild daily_summary to LEFT-JOIN journal per day ──────────────────
-- A VIEW cannot be ALTERed, so we drop and recreate it with the same activity
-- rollup as 0002 plus a journal rollup. The journal LEFT JOIN keeps the
-- one-row-per-spine-day invariant: days with no journal entry simply have NULL
-- journal columns; no spine day is ever dropped (ARCHITECTURE Anti-Pattern 2).
--
-- Multiple entries per day are rolled up: rpe/feel/notes take the most recent
-- entry (MAX(created_at)); srpe is SUMMED across the day so cross-training /
-- multiple sessions accumulate subjective load. `has_journal` flags presence.
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
    COALESCE(j.has_notes, 0)   AS has_notes
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
        -- rpe/feel from the latest entry of the day (ties broken by id).
        (SELECT j2.rpe  FROM journal j2 WHERE j2.day = j1.day
         ORDER BY j2.created_at DESC, j2.id DESC LIMIT 1) AS rpe,
        (SELECT j2.feel FROM journal j2 WHERE j2.day = j1.day
         ORDER BY j2.created_at DESC, j2.id DESC LIMIT 1) AS feel,
        SUM(srpe)                          AS srpe,
        1                                  AS has_journal,
        MAX(CASE WHEN notes IS NOT NULL AND TRIM(notes) <> '' THEN 1 ELSE 0 END) AS has_notes
    FROM journal j1
    GROUP BY day
) j ON j.day = s.day;
