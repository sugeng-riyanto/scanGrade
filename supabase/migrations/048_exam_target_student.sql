-- Migration 048: who a paper is FOR, pupil by pupil
--
-- Until now an exam's audience was the classes it named in `class_ids`: pick a
-- class and every pupil in it sits the paper. A teacher who needs to leave one
-- child out — izin, sakit, a discipline matter — had no way to say so, and the
-- only workaround (a second paper) loses the shared marking.
--
-- `exam_target_student` is that missing sentence: one row per (exam, pupil) with
-- `included` or `excluded`, written from the class roster so "everyone" stays the
-- default and the teacher unchecks only the exceptions.
--
-- The three cases Fase 2 has to keep apart are kept apart by the *absence of
-- evidence*, not by a fourth status:
--   * excluded by the teacher      -> a row, status='excluded', reason filled;
--   * not enrolled in that class   -> no row at all (never on the roster);
--   * joined the class afterwards  -> no row, and `set_at` on the class's other
--     rows predates them, which the roster reports rather than inventing a seat.
--
-- `exams.target_mode` is the switch the access check reads. It exists so the
-- per-request check stays ONE light indexed lookup instead of a count that
-- cannot tell "no targets at all" from "this pupil is not among them": mode
-- 'class' is every exam written before this table (membership decides, exactly
-- as today), mode 'students' is an exam with a target list (the row decides).
--
-- Additive and non-destructive. No existing exam changes meaning: the column
-- defaults to 'class' and no `exam_target_student` row is written by this file.

ALTER TABLE exams
    ADD COLUMN IF NOT EXISTS target_mode TEXT NOT NULL DEFAULT 'class';

DO $$ BEGIN
    ALTER TABLE exams
        ADD CONSTRAINT exams_target_mode_check
        CHECK (target_mode IN ('class', 'students'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS exam_target_student (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    -- The class the pupil sat in when the target was written. Kept so a pupil who
    -- moves class after the paper is still reported against the roster they were
    -- chosen from, rather than silently following them.
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'included'
        CHECK (status IN ('included', 'excluded')),
    -- Free text the teacher typed ("izin", "sakit", …). For the audit and the
    -- roster only; no access decision reads it.
    reason TEXT,
    set_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    set_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- One row per pupil per paper: an edit updates the row, it never inserts a second.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exam_target_student_unique
    ON exam_target_student (exam_id, student_id);

-- The two lookups the app makes: "the target list of this paper" and "is THIS
-- pupil included in it" (the per-request access check).
CREATE INDEX IF NOT EXISTS idx_exam_target_student_exam_status
    ON exam_target_student (exam_id, status);
CREATE INDEX IF NOT EXISTS idx_exam_target_student_student
    ON exam_target_student (student_id, exam_id);

ALTER TABLE exam_target_student ENABLE ROW LEVEL SECURITY;

-- The backend uses the service key and passes through these; the policies are for
-- a caller holding only the public key. Named callers, not PUBLIC: a policy
-- without `TO` also applies to `anon`.
DROP POLICY IF EXISTS "exam_target_school_read" ON exam_target_student;
CREATE POLICY "exam_target_school_read" ON exam_target_student
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM exams e
             WHERE e.id = exam_id
               AND e.school_id = public._user_school_id()
        )
    );

DROP POLICY IF EXISTS "exam_target_admin_write" ON exam_target_student;
CREATE POLICY "exam_target_admin_write" ON exam_target_student
    FOR ALL TO authenticated
    USING (
        public._is_role('admin_sekolah') AND EXISTS (
            SELECT 1 FROM exams e
             WHERE e.id = exam_id
               AND e.school_id = public._user_school_id()
        )
    );
