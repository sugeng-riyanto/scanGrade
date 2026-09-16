-- ──────────────────────────────────────────────
-- Migration 026: the objects the code was already using
-- ──────────────────────────────────────────────
-- Jalankan di Supabase SQL Editor (atau lewat deploy/apply_migration.py).
--
-- Every statement below answers a request the running application already makes.
-- They are in one file because they are one class of defect: the code was written
-- against a schema that never reached this database, and each failure was silent
-- or misleading rather than loud.
--
-- Idempotent: safe to run twice.

-- ── 1. system_settings ───────────────────────────────────────────────────────
-- Read by the privacy/PSE pages (`/super-admin/privacy-settings`, the public
-- privacy info on `/api/public/privacy-info`, and the DPO block on the public
-- pages) and written when those settings are saved. The table did not exist, so
-- the settings could never be saved and the pages always came up blank.
--
-- Key/value on purpose: these are a handful of single values about the
-- *installation* (DPO contact, PSE registration number, data-controller name and
-- address), not per-school rows — per-school data lives in `schools`.
CREATE TABLE IF NOT EXISTS system_settings (
  key         TEXT PRIMARY KEY,
  value       TEXT,
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE system_settings ENABLE ROW LEVEL SECURITY;

-- The application reaches it with the service key (which bypasses RLS); nobody
-- else may read or write it. A DPO contact is publishable, a controller's
-- internal notes are not, and neither belongs to an anonymous client.
DROP POLICY IF EXISTS system_settings_service_only ON system_settings;
CREATE POLICY system_settings_service_only ON system_settings
  FOR ALL USING (auth.role() = 'service_role');

-- ── 2. schools.trial_expires_at ──────────────────────────────────────────────
-- Written by the super admin's "extend trial" action, which answered
-- `{"success": true}` — a lie: the UPDATE was refused with 42703 and the school
-- was never reactivated. The column it wants, next to the `status` it flips.
ALTER TABLE schools ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMPTZ;

-- ── 3. the ai_grading_cache columns migration 014 never added ────────────────
-- 014 creates this table with `CREATE TABLE IF NOT EXISTS`, and the table already
-- existed in an older shape when 014 was pasted — so the statement did nothing
-- and the columns below were never added. The AI grading cache then failed on
-- every save (`prompt_sent`/`raw_response` are not columns) inside a `try` that
-- only logs, so the cache never filled: every essay was re-sent to the model.
--
-- `ADD COLUMN IF NOT EXISTS` is what actually adds them to a table that exists.
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS model_used         VARCHAR(50);
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS prompt_sent        TEXT;
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS raw_response       TEXT;
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_overridden BOOLEAN DEFAULT FALSE;
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_score      NUMERIC(5,2);
ALTER TABLE ai_grading_cache ADD COLUMN IF NOT EXISTS teacher_feedback   TEXT;

-- `_save_cache` upserts `on_conflict=["submission_id", "question_index"]`, and
-- PostgREST refuses that without a unique index on exactly those columns
-- (`42P10: there is no unique or exclusion constraint matching the ON CONFLICT
-- specification`). 014 declares UNIQUE(...) inside its CREATE TABLE, which the
-- existing table never received.
CREATE UNIQUE INDEX IF NOT EXISTS ai_grading_cache_submission_question_key
  ON ai_grading_cache(submission_id, question_index);

-- ── verify ───────────────────────────────────────────────────────────────────
-- Run this after applying; every row must say `t`:
--
--   SELECT to_regclass('public.system_settings')                IS NOT NULL AS system_settings,
--          EXISTS (SELECT 1 FROM information_schema.columns
--                   WHERE table_name='schools' AND column_name='trial_expires_at') AS trial_expires_at,
--          (SELECT count(*) FROM information_schema.columns
--             WHERE table_name='ai_grading_cache'
--               AND column_name IN ('model_used','prompt_sent','raw_response',
--                                   'teacher_overridden','teacher_score','teacher_feedback')) = 6
--                                                                     AS ai_cache_columns,
--          EXISTS (SELECT 1 FROM pg_indexes
--                   WHERE tablename='ai_grading_cache'
--                     AND indexname='ai_grading_cache_submission_question_key') AS ai_cache_unique;
