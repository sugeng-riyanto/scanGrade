-- Migration 046: a pupil's membership in a class, per year, as a fact
--
-- `school_years` (007) and `classes.school_year_id` (007) already exist, so a
-- class *can* be one year's rombongan belajar. Measured on the live project
-- before writing this: of 22 classes, exactly **1** carried a `school_year_id`,
-- while all 22 carried the legacy `classes.academic_year` TEXT set to a stale
-- '2025/2026' — against an active year of '2026/2027'. Two year fields, and the
-- one that is populated is the one nothing updates.
--
-- What has never existed is where a pupil *was*: `students.class_id` and
-- `profiles.class_id` are single, mutable pointers, and promotion overwrites
-- both. After a promotion there is no row anywhere that says which class a
-- child sat in last year, so exam history can only be preserved by the accident
-- of the school never reusing a class row.
--
-- Additive and non-destructive. No row is deleted, no value is guessed: the
-- backfill reconstructs enrollments in a separate, dry-run-first script
-- (`deploy/backfill_enrollment.py`), which marks anything it cannot prove for a
-- human to decide rather than inventing a year.

-- ── the membership history ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS student_enrollment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    -- NULL is honest: a pupil who has left, or whose class row was removed, still
    -- has a year on file. It is not a missing class to be invented.
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL,
    -- `aktif` is the year in progress; the rest are that year's *outcome*, which
    -- is what makes "tinggal kelas" distinguishable from "naik" and from
    -- "pindah", none of which the old `students.status` could express.
    status TEXT NOT NULL DEFAULT 'aktif'
        CHECK (status IN ('aktif', 'naik', 'tinggal_kelas', 'pindah', 'lulus')),
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- One membership per pupil per year. A pupil who stays back has two rows in two
-- *different* years at the same level, which is exactly the shape that shows the
-- repeat — not two rows in one year.
CREATE UNIQUE INDEX IF NOT EXISTS idx_enrollment_student_year
    ON student_enrollment (student_id, school_year_id)
    WHERE school_year_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_enrollment_school_year
    ON student_enrollment (school_id, school_year_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_class ON student_enrollment (class_id);
CREATE INDEX IF NOT EXISTS idx_enrollment_student ON student_enrollment (student_id);

-- ── one year source for an assignment ────────────────────────────────────────
-- 045 gave `teacher_assignments` a `school_year` TEXT so the pair could be
-- scoped by year. A string cannot be joined, cannot be foreign-keyed, and can
-- hold a value no `school_years` row matches. The FK is the real link; the TEXT
-- column is left in place (dropping it would be the destructive half of a
-- rename) and simply stops being read.
ALTER TABLE teacher_assignments
    ADD COLUMN IF NOT EXISTS school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_ta_school_year ON teacher_assignments (school_year_id);

-- ── a paper's year, so the history filter does not walk through classes ──────
-- `exams.class_ids` already points at class rows that carry `school_year_id`,
-- but filtering a teacher's or a pupil's papers by year would then need a join
-- per class on every list render. Denormalised on purpose; the backfill script
-- derives it from the classes the paper is attached to, and refuses to guess
-- when a paper spans years.
ALTER TABLE exams
    ADD COLUMN IF NOT EXISTS school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_exams_school_year ON exams (school_year_id);

-- ── RLS: the same read door the rest of the school's academic rows have ──────
ALTER TABLE student_enrollment ENABLE ROW LEVEL SECURITY;

-- The backend uses the service key and passes through this; the policy is for a
-- caller holding only the public key, and it names its caller (`TO authenticated`)
-- because a policy without `TO` applies to PUBLIC — including `anon`.
DROP POLICY IF EXISTS "enrollment_read_own_school" ON student_enrollment;
CREATE POLICY "enrollment_read_own_school" ON student_enrollment
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "enrollment_school_official_read" ON student_enrollment;
CREATE POLICY "enrollment_school_official_read" ON student_enrollment
    FOR SELECT TO authenticated
    USING (
        (public._is_role('principal') OR public._is_role('vice_principal'))
        AND school_id = public._user_school_id()
    );

DROP POLICY IF EXISTS "enrollment_admin_write" ON student_enrollment;
CREATE POLICY "enrollment_admin_write" ON student_enrollment
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
