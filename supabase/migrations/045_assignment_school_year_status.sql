-- Migration 045: the year and the life of a class-subject assignment
--
-- `teacher_assignments` (010) records the (teacher, class, subject) pair an
-- admin hands a guru, and until now that pair had no year and no end. Two
-- consequences the app could not express:
--
--   * `app/services/assignments.py` scopes exam writes to the pairs a teacher
--     *actively* holds. Without a `status` the only way to take a pair away was
--     to delete the row — and deleting it does not just close the door, it
--     erases the fact that the teacher ever taught it, which the exam history
--     and the audit log both refer to.
--   * a new school year could only be expressed by deleting last year's rows,
--     so "what did this teacher teach in 2025/2026" had no answer.
--
-- Additive and non-destructive: no row is deleted, no value is guessed. Both
-- columns are defaulted so every existing row stays meaningful without a
-- backfill — `status='active'` (every row on file grants its door today) and
-- `school_year=NULL` (the year is not on the row and is not invented here).
--
-- Homeroom (wali kelas) is deliberately *not* duplicated here: the app already
-- keeps exactly one source of truth for it, `classes.teacher_id`, read by
-- `/admin-sekolah/classes` and `/admin-sekolah/promote`. A second table would
-- be a second answer to "who is this class's homeroom teacher", and the two
-- would drift. A per-year history of homeroom belongs with `classes`, which
-- already carries `school_year_id`.
--
-- Run in the Supabase SQL editor, or through `deploy/apply_migration.py`.
-- Safe to run twice.

ALTER TABLE teacher_assignments
    ADD COLUMN IF NOT EXISTS school_year TEXT;

ALTER TABLE teacher_assignments
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

-- The lookup the exam write path makes: "the active pairs of this teacher in
-- this school, for this year". 010's `idx_ta_teacher` covers the teacher; this
-- covers the triple the scoping rule actually filters on.
CREATE INDEX IF NOT EXISTS idx_ta_teacher_year_status
    ON teacher_assignments (teacher_id, school_year, status);

-- A teacher is active or not *for a year*; anything else is a typo waiting to
-- be read as "active" by a caller that only tests for the string it expects.
DO $$ BEGIN
    ALTER TABLE teacher_assignments
        ADD CONSTRAINT teacher_assignments_status_check
        CHECK (status IN ('active', 'inactive'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
