-- Migration 049: which subjects a class offers, and how far each pupil has got
--
-- Two facts the school could not state, both of which the app was silently
-- guessing at:
--
--   * **a class does not offer every subject.** The only place a (class,
--     subject) pair existed was `teacher_assignments` — a pair was real only
--     because someone taught it. A subject waiting for a teacher, or a class
--     between teachers, had no way to say "we teach this here", and the exam
--     builder offered every subject in the school against every class.
--     `class_subjects` is that missing sentence: one row per offered pair.
--
--   * **a pupil is on a track inside one subject.** A school teaches the same
--     subject at more than one depth — *basic*, *intermediate*, *advanced* —
--     and a basic group's marks are not comparable with an advanced group's.
--     Nothing recorded which track a pupil was put in, so a progress report
--     would compare a beginner with an advanced pupil. `student_subject_levels`
--     is that record, one row per pupil, subject and year.
--
-- Additive and non-destructive. No existing row changes meaning: the mapping
-- starts empty, and a subject with no mapping reads exactly as it does today.
-- Both tables are scoped to one school; the app checks every id against the
-- caller's school before writing (the service key passes through RLS, so the
-- policies here are for a caller holding only the public key).
--
-- Run in the Supabase SQL editor, or through `deploy/apply_migration.py`.
-- Safe to run twice.

-- ── the offering: this class teaches this subject ────────────────────────────
CREATE TABLE IF NOT EXISTS class_subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    -- A subject taken off a class is *closed*, never deleted: the row survives so
    -- a paper or a level set under it still points at something. Re-adding it
    -- flips the same row back on.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- One row per offered pair. A class row already belongs to one year
-- (`classes.school_year_id`, migration 046), so the pair is year-scoped by the
-- class it names and needs no year of its own.
CREATE UNIQUE INDEX IF NOT EXISTS idx_class_subjects_unique
    ON class_subjects (class_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_class_subjects_school_subject
    ON class_subjects (school_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_class_subjects_class
    ON class_subjects (class_id);

-- ── the track: how far this pupil has got in this subject ────────────────────
CREATE TABLE IF NOT EXISTS student_subject_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    -- The class the pupil was in when the level was set. Kept so a pupil who
    -- moves class is still reported against the group they were placed in.
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL,
    -- The three tracks. A fourth value is a typo the report would then read as
    -- "not basic, not advanced" — the CHECK makes it impossible to store.
    level TEXT NOT NULL DEFAULT 'basic'
        CHECK (level IN ('basic', 'intermediate', 'advanced')),
    set_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- One level per pupil per subject per year: an edit updates the row in place
-- rather than stacking a second one. NULL year (a pupil placed before the year
-- existed) is left out of the uniqueness, exactly as `student_enrollment` (046)
-- leaves its own NULL year out.
CREATE UNIQUE INDEX IF NOT EXISTS idx_subject_level_pupil_subject_year
    ON student_subject_levels (student_id, subject_id, school_year_id)
    WHERE school_year_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subject_level_subject_class
    ON student_subject_levels (school_id, subject_id, class_id);

-- ── RLS: the same doors the school's other academic rows have ────────────────
ALTER TABLE class_subjects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "class_subjects_school_read" ON class_subjects;
CREATE POLICY "class_subjects_school_read" ON class_subjects
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "class_subjects_admin_write" ON class_subjects;
CREATE POLICY "class_subjects_admin_write" ON class_subjects
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

ALTER TABLE student_subject_levels ENABLE ROW LEVEL SECURITY;

-- A pupil may read their own level; the school's staff read them all.
DROP POLICY IF EXISTS "subject_level_pupil_read" ON student_subject_levels;
CREATE POLICY "subject_level_pupil_read" ON student_subject_levels
    FOR SELECT TO authenticated
    USING (student_id = auth.uid());

DROP POLICY IF EXISTS "subject_level_school_read" ON student_subject_levels;
CREATE POLICY "subject_level_school_read" ON student_subject_levels
    FOR SELECT TO authenticated
    USING (school_id = public._user_school_id());

DROP POLICY IF EXISTS "subject_level_admin_write" ON student_subject_levels;
CREATE POLICY "subject_level_admin_write" ON student_subject_levels
    FOR ALL TO authenticated
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
