-- Migration 0006: Coros EvoLab daily metrics (silver) (Phase 18).
--
-- Phases 1-7 built the Strava + Garmin pipeline; Phase 18 adds Coros as a third
-- source. Coros's wellness payloads (HRV / sleep / RHR) project onto the
-- existing `wellness_day` table via a per-(day, metric) priority resolver
-- (Coros wins, Garmin fills gaps -- see runos.transforms.coros_wellness from
-- wave 18-02). The *new* data Coros provides -- its proprietary EvoLab
-- analytics: VO2max, stamina level, training load, lactate-threshold HR and
-- lactate-threshold pace -- has no Garmin equivalent and gets its own table:
--
--   * `coros_evolab_day` -- one structured row per LOCAL calendar day, the
--                           projection of one entry from the
--                           `data.t7dayList[]` array in /analyse/query's
--                           response (raw endpoint `evolab_dashboard`). `day`
--                           is `happenDay` (Coros's YYYYMMDD int) converted to
--                           ISO YYYY-MM-DD, FK to date_spine so it buckets on
--                           the same axis as activities and wellness_day.
--                           Every metric column is nullable: a brand-new
--                           account, or a day where Coros hasn't computed a
--                           given metric, simply leaves the field NULL.
--
-- Fields dropped from the original 18-CONTEXT.md draft after wave 18-01
-- inspected the real /analyse/query payload: `recovery_pct`, `base_fitness`
-- (renamed `stamina_level`), and the four `race_prediction_*` columns. None of
-- those fields exist in the v1.7 endpoint surface; revisit in a future
-- micro-phase if Coros exposes them.
--
-- The Coros connector writes ONLY verbatim payloads to raw_response; this
-- silver row is produced purely from raw by runos.transforms.coros_evolab, so
-- `runos rederive` rebuilds it with zero network calls (ARCHITECTURE
-- Anti-Pattern 1).
--
-- The `daily_summary` view does NOT need rebuilding: EvoLab is consumed
-- directly by runos.analysis.coros_evolab + the recovery report (wave 18-04),
-- not by the daily rollup view.
--
-- Applied in a single transaction by db.migrate(), which then bumps
-- PRAGMA user_version to 6.

-- == STRUCTURED (silver): one EvoLab row per local calendar day ==============
-- `day` is the local calendar date the EvoLab metrics were computed for
-- (Coros's `happenDay`, an integer YYYYMMDD which the transform converts to
-- ISO YYYY-MM-DD). FK to date_spine ensures EvoLab buckets on the shared day
-- axis. `fetched_at` records when the transform last refreshed this row from
-- raw; an `ix_coros_evolab_day_fetched_at` index keeps freshness queries fast
-- (the recovery report's 3-state staleness check is the primary consumer).
CREATE TABLE coros_evolab_day (
    day               TEXT PRIMARY KEY REFERENCES date_spine(day),  -- LOCAL day, ISO YYYY-MM-DD
    vo2max            REAL,                       -- ml/kg/min (Coros's reported VO2max)
    stamina_level     INTEGER,                    -- 0-100; Coros's base-fitness equivalent
    training_load     INTEGER,                    -- Coros's training-load score (per their dashboard)
    lthr              INTEGER,                    -- lactate-threshold HR (bpm)
    ltsp_s_per_km     INTEGER,                    -- lactate-threshold speed/pace, seconds-per-km
    fetched_at        TEXT NOT NULL               -- ISO 8601 UTC (transform pass timestamp)
);
CREATE INDEX ix_coros_evolab_day_fetched_at ON coros_evolab_day (fetched_at);
