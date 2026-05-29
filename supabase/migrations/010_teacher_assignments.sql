-- Migration 010: Teacher Class-Subject Assignments
-- Enables many-to-many: teacher <-> (class, subject) pairs

CREATE TABLE IF NOT EXISTS teacher_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(teacher_id, class_id, subject_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ta_teacher ON teacher_assignments(teacher_id);
CREATE INDEX IF NOT EXISTS idx_ta_class ON teacher_assignments(class_id);
CREATE INDEX IF NOT EXISTS idx_ta_subject ON teacher_assignments(subject_id);
CREATE INDEX IF NOT EXISTS idx_ta_school ON teacher_assignments(school_id);

-- RLS
ALTER TABLE teacher_assignments ENABLE ROW LEVEL SECURITY;

-- Teachers can read their own assignments
CREATE POLICY "Teachers read own assignments" ON teacher_assignments
    FOR SELECT USING (
        teacher_id = auth.uid()
        OR EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('super_admin', 'admin_sekolah')
        )
    );

-- Teachers can insert their own assignments (within their school)
CREATE POLICY "Teachers insert own assignments" ON teacher_assignments
    FOR INSERT WITH CHECK (
        teacher_id = auth.uid()
        AND school_id = (SELECT school_id FROM profiles WHERE id = auth.uid())
    );

-- Admin sekolah can manage all assignments in their school
CREATE POLICY "Admin sekolah manage assignments" ON teacher_assignments
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role IN ('super_admin', 'admin_sekolah')
            AND profiles.school_id = teacher_assignments.school_id
        )
    );

-- Super admin full access
CREATE POLICY "Super admin all assignments" ON teacher_assignments
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role = 'super_admin'
        )
    );

-- Trigger to update updated_at
CREATE TRIGGER update_teacher_assignments_updated_at
    BEFORE UPDATE ON teacher_assignments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
