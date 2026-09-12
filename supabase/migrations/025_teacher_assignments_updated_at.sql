-- Migration 025: the column that teacher_assignments' trigger writes
--
-- Migration 010 (line 62-66) attached `update_teacher_assignments_updated_at`,
-- which runs `update_updated_at()` — a function whose whole body is
-- `NEW.updated_at = NOW()`. The table was created without that column, so every
-- UPDATE on it fails:
--
--     ERROR: 42703: record "new" has no field "updated_at"
--
-- That silently breaks more than it looks. An upsert that conflicts on id, a
-- `.update()` call, editing a row in the Supabase table editor, and any restore
-- that writes rows back with their ids all fail on this table alone.
--
-- Run in the Supabase SQL editor. Safe to run twice.

ALTER TABLE teacher_assignments
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

UPDATE teacher_assignments
   SET updated_at = COALESCE(created_at, NOW())
 WHERE updated_at IS NULL;

-- Confirm the fix: this now returns a row instead of raising 42703.
--   UPDATE teacher_assignments SET class_id = class_id WHERE id = (SELECT id FROM teacher_assignments LIMIT 1);
