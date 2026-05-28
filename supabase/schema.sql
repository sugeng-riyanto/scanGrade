-- ScanGrade Database Schema

-- 1. Profiles (extends auth.users)
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name TEXT,
    phone TEXT,
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'teacher', 'admin')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Exams
CREATE TABLE IF NOT EXISTS exams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES profiles(id),
    title TEXT NOT NULL,
    description TEXT,
    subject TEXT,
    duration_minutes INT NOT NULL DEFAULT 60,
    total_questions INT DEFAULT 0,
    passing_score INT DEFAULT 70,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'closed')),
    is_published BOOLEAN DEFAULT FALSE,
    pdf_url TEXT,
    answer_key JSONB,
    question_types JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exams_teacher ON exams(teacher_id);
CREATE INDEX IF NOT EXISTS idx_exams_status ON exams(status);

-- 3. Exam Access Codes
CREATE TABLE IF NOT EXISTS exam_access_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    code TEXT NOT NULL UNIQUE,
    student_id UUID REFERENCES profiles(id),
    is_used BOOLEAN DEFAULT FALSE,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_codes_exam ON exam_access_codes(exam_id);
CREATE INDEX IF NOT EXISTS idx_access_codes_code ON exam_access_codes(code);

-- 4. Submissions
CREATE TABLE IF NOT EXISTS submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES profiles(id),
    answers JSONB,
    score DECIMAL(5,2),
    max_score DECIMAL(5,2),
    violations INT DEFAULT 0,
    penalty DECIMAL(5,2) DEFAULT 0,
    final_score DECIMAL(5,2),
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'graded', 'published')),
    is_published BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    graded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_submissions_exam ON submissions(exam_id);
CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id);
CREATE INDEX IF NOT EXISTS idx_submissions_published ON submissions(is_published) WHERE is_published = TRUE;

-- 5. Violation Logs
CREATE TABLE IF NOT EXISTS violation_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES profiles(id),
    violation_type TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_violations_exam ON violation_logs(exam_id);
CREATE INDEX IF NOT EXISTS idx_violations_user ON violation_logs(user_id);

-- 6. Analytics Cache
CREATE TABLE IF NOT EXISTS analytics_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    cache_data JSONB,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(exam_id, cache_key)
);

-- Enable RLS
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams ENABLE ROW LEVEL SECURITY;
ALTER TABLE exam_access_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE violation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics_cache ENABLE ROW LEVEL SECURITY;

-- Profiles Policies
DROP POLICY IF EXISTS "profiles_select_own" ON profiles;
CREATE POLICY "profiles_select_own" ON profiles
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "profiles_update_own" ON profiles;
CREATE POLICY "profiles_update_own" ON profiles
    FOR UPDATE USING (auth.uid() = id);

DROP POLICY IF EXISTS "profiles_insert_trigger" ON profiles;
CREATE POLICY "profiles_insert_trigger" ON profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- Exams Policies
DROP POLICY IF EXISTS "exams_select_active" ON exams;
CREATE POLICY "exams_select_active" ON exams
    FOR SELECT USING (status = 'active');

DROP POLICY IF EXISTS "exams_select_teacher" ON exams;
CREATE POLICY "exams_select_teacher" ON exams
    FOR SELECT USING (teacher_id = auth.uid());

DROP POLICY IF EXISTS "exams_insert_teacher" ON exams;
CREATE POLICY "exams_insert_teacher" ON exams
    FOR INSERT WITH CHECK (teacher_id = auth.uid());

DROP POLICY IF EXISTS "exams_update_teacher" ON exams;
CREATE POLICY "exams_update_teacher" ON exams
    FOR UPDATE USING (teacher_id = auth.uid());

DROP POLICY IF EXISTS "exams_delete_teacher" ON exams;
CREATE POLICY "exams_delete_teacher" ON exams
    FOR DELETE USING (teacher_id = auth.uid());

-- Access Codes Policies
DROP POLICY IF EXISTS "codes_select_teacher" ON exam_access_codes;
CREATE POLICY "codes_select_teacher" ON exam_access_codes
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

DROP POLICY IF EXISTS "codes_select_student" ON exam_access_codes;
CREATE POLICY "codes_select_student" ON exam_access_codes
    FOR SELECT USING (student_id = auth.uid() AND is_used = FALSE);

DROP POLICY IF EXISTS "codes_insert_teacher" ON exam_access_codes;
CREATE POLICY "codes_insert_teacher" ON exam_access_codes
    FOR INSERT WITH CHECK (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

-- Submissions Policies
DROP POLICY IF EXISTS "submissions_select_teacher" ON submissions;
CREATE POLICY "submissions_select_teacher" ON submissions
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

DROP POLICY IF EXISTS "submissions_select_student" ON submissions;
CREATE POLICY "submissions_select_student" ON submissions
    FOR SELECT USING (student_id = auth.uid() AND is_published = TRUE);

DROP POLICY IF EXISTS "submissions_insert_student" ON submissions;
CREATE POLICY "submissions_insert_student" ON submissions
    FOR INSERT WITH CHECK (student_id = auth.uid());

DROP POLICY IF EXISTS "submissions_update_teacher" ON submissions;
CREATE POLICY "submissions_update_teacher" ON submissions
    FOR UPDATE USING (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

-- Violation Logs Policies
DROP POLICY IF EXISTS "violations_select_teacher" ON violation_logs;
CREATE POLICY "violations_select_teacher" ON violation_logs
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

-- Service key bypasses RLS, but we allow insert via service role
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
CREATE POLICY "violations_insert_service" ON violation_logs
    FOR INSERT WITH CHECK (true);

-- Analytics Cache Policies
DROP POLICY IF EXISTS "analytics_select_teacher" ON analytics_cache;
CREATE POLICY "analytics_select_teacher" ON analytics_cache
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

DROP POLICY IF EXISTS "analytics_insert_teacher" ON analytics_cache;
CREATE POLICY "analytics_insert_teacher" ON analytics_cache
    FOR INSERT WITH CHECK (
        EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid())
    );

-- Auto-create profile on user signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO profiles (id, full_name, role)
    VALUES (NEW.id, NEW.raw_user_meta_data->>'full_name', COALESCE(NEW.raw_user_meta_data->>'role', 'student'));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- Triggers for updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_profiles_updated_at ON profiles;
CREATE TRIGGER set_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS set_exams_updated_at ON exams;
CREATE TRIGGER set_exams_updated_at
    BEFORE UPDATE ON exams
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS set_submissions_updated_at ON submissions;
CREATE TRIGGER set_submissions_updated_at
    BEFORE UPDATE ON submissions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS set_analytics_cache_updated_at ON analytics_cache;
CREATE TRIGGER set_analytics_cache_updated_at
    BEFORE UPDATE ON analytics_cache
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Storage bucket for exam PDFs
INSERT INTO storage.buckets (id, name, public) VALUES ('exam-pdfs', 'exam-pdfs', true)
ON CONFLICT (id) DO NOTHING;

-- Storage RLS policies for exam-pdfs
DROP POLICY IF EXISTS "exam_pdfs_select" ON storage.objects;
CREATE POLICY "exam_pdfs_select" ON storage.objects
  FOR SELECT TO public USING (bucket_id = 'exam-pdfs');

DROP POLICY IF EXISTS "exam_pdfs_insert" ON storage.objects;
CREATE POLICY "exam_pdfs_insert" ON storage.objects
  FOR INSERT TO authenticated WITH CHECK (bucket_id = 'exam-pdfs');

DROP POLICY IF EXISTS "exam_pdfs_delete" ON storage.objects;
CREATE POLICY "exam_pdfs_delete" ON storage.objects
  FOR DELETE TO authenticated USING (bucket_id = 'exam-pdfs');

-- Seed data (optional)
-- INSERT INTO profiles (id, full_name, role) VALUES
--   ('00000000-0000-0000-0000-000000000001', 'Admin User', 'admin');
