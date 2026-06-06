-- ============================================
-- ScanGrade: ALL MIGRATIONS (fixed order)
-- Copy paste SELURUH file ini ke Supabase SQL Editor
-- Jalankan SEKALI (semua statement pakai IF NOT EXISTS)
-- ============================================

-- Fix: jika classes.school_id masih INT dari migrasi sebelumnya, drop & recreate
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'classes' AND column_name = 'school_id' AND data_type = 'integer'
    ) THEN
        ALTER TABLE classes DROP COLUMN school_id;
    END IF;
END $$;

-- ===== STEP 1: Create missing base tables =====

-- Classes table (jika belum ada)
CREATE TABLE IF NOT EXISTS classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    grade INT,
    academic_year TEXT DEFAULT '2025/2026',
    teacher_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- School settings (jika belum ada)
CREATE TABLE IF NOT EXISTS school_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    school_name TEXT,
    npsn TEXT,
    address TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    academic_year TEXT DEFAULT '2025/2026',
    principal_name TEXT,
    logo_url TEXT,
    tz_offset INT DEFAULT 7,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO school_settings (id, school_name) VALUES (1, 'ScanGrade School')
ON CONFLICT (id) DO NOTHING;

-- FK classes -> profiles (tunda karena profiles mungkin belum punya constraint)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'classes_teacher_id_fkey' AND table_name = 'classes'
    ) THEN
        ALTER TABLE classes ADD CONSTRAINT classes_teacher_id_fkey
            FOREIGN KEY (teacher_id) REFERENCES profiles(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ===== STEP 2: Missing columns di profiles =====
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nisn TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nis TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS class_id UUID REFERENCES classes(id) ON DELETE SET NULL;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS tz_offset INT DEFAULT 7;

-- ===== STEP 3: Migration 004-006 (simple ALTER) =====
ALTER TABLE exams ADD COLUMN IF NOT EXISTS pdf_page_urls JSONB DEFAULT '[]';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_audio JSONB DEFAULT '{}';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_canvas JSONB DEFAULT '{}';
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS teacher_feedback JSONB DEFAULT '{}';

-- ===== STEP 4: 003 - Submission hidden, retracted, question_weights =====
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN DEFAULT FALSE;
ALTER TABLE submissions DROP CONSTRAINT IF EXISTS submissions_status_check;
ALTER TABLE submissions ADD CONSTRAINT submissions_status_check
    CHECK (status IN ('draft', 'submitted', 'graded', 'published', 'retracted'));
ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS tz_offset INT DEFAULT 7;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_weights JSONB DEFAULT '{}';

-- ===== STEP 5: 007 - Multi-School Role Hierarchy =====
CREATE TABLE IF NOT EXISTS schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    npsn TEXT UNIQUE,
    address TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    postal_code TEXT,
    phone TEXT,
    email TEXT,
    website TEXT,
    logo_url TEXT,
    principal_name TEXT,
    principal_nip TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    tz_offset INT DEFAULT 7,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_schools_npsn ON schools(npsn);
CREATE INDEX IF NOT EXISTS idx_schools_status ON schools(status);

CREATE TABLE IF NOT EXISTS school_registration_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_name TEXT NOT NULL,
    npsn TEXT,
    address TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    requester_name TEXT NOT NULL,
    requester_email TEXT NOT NULL,
    requester_phone TEXT,
    requester_position TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    admin_notes TEXT,
    approved_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_req_status ON school_registration_requests(status);

CREATE TABLE IF NOT EXISTS registration_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    code TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('guru', 'murid')),
    max_uses INT DEFAULT 1,
    use_count INT DEFAULT 0,
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reg_codes_code ON registration_codes(code);
CREATE INDEX IF NOT EXISTS idx_reg_codes_school ON registration_codes(school_id);

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE SET NULL;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nuptk TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended'));
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS registration_code_id UUID REFERENCES registration_codes(id) ON DELETE SET NULL;
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_role_check;
UPDATE profiles SET role = 'super_admin' WHERE role = 'admin';
UPDATE profiles SET role = 'guru' WHERE role = 'teacher';
UPDATE profiles SET role = 'murid' WHERE role = 'student';
ALTER TABLE profiles ADD CONSTRAINT profiles_role_check
    CHECK (role IN ('super_admin', 'admin_sekolah', 'guru', 'murid'));
CREATE INDEX IF NOT EXISTS idx_profiles_school ON profiles(school_id);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON profiles(role);
CREATE INDEX IF NOT EXISTS idx_profiles_nuptk ON profiles(nuptk);
CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);

CREATE TABLE IF NOT EXISTS school_years (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sy_school ON school_years(school_id);
CREATE INDEX IF NOT EXISTS idx_sy_active ON school_years(is_active) WHERE is_active = TRUE;

ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE CASCADE;
ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL;
ALTER TABLE classes ADD COLUMN IF NOT EXISTS grade_level TEXT;
CREATE INDEX IF NOT EXISTS idx_classes_school ON classes(school_id);
CREATE INDEX IF NOT EXISTS idx_classes_sy ON classes(school_year_id);

CREATE TABLE IF NOT EXISTS subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subjects_school ON subjects(school_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subjects_school_code ON subjects(school_id, code) WHERE code IS NOT NULL;

CREATE TABLE IF NOT EXISTS teachers (
    id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    nuptk TEXT UNIQUE,
    subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
    employee_id TEXT,
    qualification TEXT,
    join_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_teachers_school ON teachers(school_id);
CREATE INDEX IF NOT EXISTS idx_teachers_subject ON teachers(subject_id);

CREATE TABLE IF NOT EXISTS students (
    id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    nisn TEXT UNIQUE,
    nis TEXT,
    birth_date DATE,
    birth_place TEXT,
    gender TEXT CHECK (gender IN ('L', 'P')),
    address TEXT,
    parent_phone TEXT,
    entry_year INT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'alumni', 'dropped')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_students_school ON students(school_id);
CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_students_nisn ON students(nisn);
CREATE INDEX IF NOT EXISTS idx_students_status ON students(status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    old_data JSONB,
    new_data JSONB,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

ALTER TABLE schools ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_registration_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE registration_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_years ENABLE ROW LEVEL SECURITY;
ALTER TABLE subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- RLS helper functions (di public schema, bukan auth)
CREATE OR REPLACE FUNCTION public._is_role(required_role TEXT)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (
        SELECT 1 FROM profiles WHERE id = auth.uid() AND role = required_role AND status = 'active'
    );
$$ LANGUAGE sql STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public._user_school_id()
RETURNS UUID AS $$
    SELECT school_id FROM profiles WHERE id = auth.uid();
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- RLS policies for new tables
DROP POLICY IF EXISTS "schools_super_admin_all" ON schools;
CREATE POLICY "schools_super_admin_all" ON schools
    FOR ALL USING (public._is_role('super_admin'))
    WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "schools_admin_read_own" ON schools;
CREATE POLICY "schools_admin_read_own" ON schools
    FOR SELECT USING (public._is_role('admin_sekolah') AND id = public._user_school_id());
DROP POLICY IF EXISTS "schools_guru_murid_read_own" ON schools;
CREATE POLICY "schools_guru_murid_read_own" ON schools
    FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND id = public._user_school_id());

DROP POLICY IF EXISTS "reg_req_insert_public" ON school_registration_requests;
CREATE POLICY "reg_req_insert_public" ON school_registration_requests FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS "reg_req_select_public" ON school_registration_requests;
CREATE POLICY "reg_req_select_public" ON school_registration_requests FOR SELECT USING (true);
DROP POLICY IF EXISTS "reg_req_super_admin_all" ON school_registration_requests;
CREATE POLICY "reg_req_super_admin_all" ON school_registration_requests
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));

DROP POLICY IF EXISTS "reg_codes_super_admin_all" ON registration_codes;
CREATE POLICY "reg_codes_super_admin_all" ON registration_codes
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "reg_codes_admin_select_own" ON registration_codes;
CREATE POLICY "reg_codes_admin_select_own" ON registration_codes
    FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "reg_codes_admin_insert_own" ON registration_codes;
CREATE POLICY "reg_codes_admin_insert_own" ON registration_codes
    FOR INSERT WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "reg_codes_admin_update_own" ON registration_codes;
CREATE POLICY "reg_codes_admin_update_own" ON registration_codes
    FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "reg_codes_select_registration" ON registration_codes;
CREATE POLICY "reg_codes_select_registration" ON registration_codes
    FOR SELECT USING (is_active = TRUE AND (expires_at IS NULL OR expires_at > NOW()));

DROP POLICY IF EXISTS "sy_super_admin_all" ON school_years;
CREATE POLICY "sy_super_admin_all" ON school_years
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "sy_admin_select_own" ON school_years;
CREATE POLICY "sy_admin_select_own" ON school_years
    FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "sy_admin_insert_own" ON school_years;
CREATE POLICY "sy_admin_insert_own" ON school_years
    FOR INSERT WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "sy_admin_update_own" ON school_years;
CREATE POLICY "sy_admin_update_own" ON school_years
    FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "sy_admin_delete_own" ON school_years;
CREATE POLICY "sy_admin_delete_own" ON school_years
    FOR DELETE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "sy_guru_murid_read" ON school_years;
CREATE POLICY "sy_guru_murid_read" ON school_years
    FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND school_id = public._user_school_id());

-- Update classes policies
DROP POLICY IF EXISTS "classes_select_all" ON classes;
DROP POLICY IF EXISTS "classes_insert_service" ON classes;
DROP POLICY IF EXISTS "classes_update_service" ON classes;
DROP POLICY IF EXISTS "classes_delete_service" ON classes;
DROP POLICY IF EXISTS "classes_super_admin_all" ON classes;
CREATE POLICY "classes_super_admin_all" ON classes
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "classes_admin_all_own" ON classes;
CREATE POLICY "classes_admin_all_own" ON classes
    FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "classes_guru_murid_read" ON classes;
CREATE POLICY "classes_guru_murid_read" ON classes
    FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "classes_teacher_update" ON classes;
CREATE POLICY "classes_teacher_update" ON classes
    FOR UPDATE USING (public._is_role('guru') AND teacher_id = auth.uid());

DROP POLICY IF EXISTS "subjects_super_admin_all" ON subjects;
CREATE POLICY "subjects_super_admin_all" ON subjects
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "subjects_admin_all_own" ON subjects;
CREATE POLICY "subjects_admin_all_own" ON subjects
    FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "subjects_guru_murid_read" ON subjects;
CREATE POLICY "subjects_guru_murid_read" ON subjects
    FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND school_id = public._user_school_id());

DROP POLICY IF EXISTS "teachers_super_admin_all" ON teachers;
CREATE POLICY "teachers_super_admin_all" ON teachers
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "teachers_admin_select_own" ON teachers;
CREATE POLICY "teachers_admin_select_own" ON teachers
    FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "teachers_guru_read_own" ON teachers;
CREATE POLICY "teachers_guru_read_own" ON teachers
    FOR SELECT USING (public._is_role('guru') AND id = auth.uid());

DROP POLICY IF EXISTS "students_super_admin_all" ON students;
CREATE POLICY "students_super_admin_all" ON students
    FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "students_admin_select_own" ON students;
CREATE POLICY "students_admin_select_own" ON students
    FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "students_guru_select_own" ON students;
CREATE POLICY "students_guru_select_own" ON students
    FOR SELECT USING (public._is_role('guru') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "students_murid_read_own" ON students;
CREATE POLICY "students_murid_read_own" ON students
    FOR SELECT USING (public._is_role('murid') AND id = auth.uid());

DROP POLICY IF EXISTS "audit_insert_service" ON audit_logs;
CREATE POLICY "audit_insert_service" ON audit_logs FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS "audit_select_super_admin" ON audit_logs;
CREATE POLICY "audit_select_super_admin" ON audit_logs FOR SELECT USING (public._is_role('super_admin'));

-- Update profiles policies
DROP POLICY IF EXISTS "profiles_select_own" ON profiles;
DROP POLICY IF EXISTS "profiles_update_own" ON profiles;
DROP POLICY IF EXISTS "profiles_insert_trigger" ON profiles;
DROP POLICY IF EXISTS "profiles_select_own" ON profiles;
CREATE POLICY "profiles_select_own" ON profiles FOR SELECT USING (auth.uid() = id);
DROP POLICY IF EXISTS "profiles_select_super_admin" ON profiles;
CREATE POLICY "profiles_select_super_admin" ON profiles FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "profiles_select_admin_own_school" ON profiles;
CREATE POLICY "profiles_select_admin_own_school" ON profiles
    FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_select_guru" ON profiles;
CREATE POLICY "profiles_select_guru" ON profiles
    FOR SELECT USING (public._is_role('guru') AND (role = 'murid' OR id = auth.uid()) AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_update_own" ON profiles;
CREATE POLICY "profiles_update_own" ON profiles
    FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
DROP POLICY IF EXISTS "profiles_update_admin_own_school" ON profiles;
CREATE POLICY "profiles_update_admin_own_school" ON profiles
    FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_insert_trigger" ON profiles;
CREATE POLICY "profiles_insert_trigger" ON profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- Update exams with school/class/subject FKs
ALTER TABLE exams ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS class_id UUID REFERENCES classes(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS exam_type TEXT DEFAULT 'ulangan' CHECK (exam_type IN ('ulangan', 'uts', 'uas', 'tryout', 'ljk'));
CREATE INDEX IF NOT EXISTS idx_exams_school ON exams(school_id);
CREATE INDEX IF NOT EXISTS idx_exams_class ON exams(class_id);
CREATE INDEX IF NOT EXISTS idx_exams_subject ON exams(subject_id);

-- Update exams policies
DROP POLICY IF EXISTS "exams_select_active" ON exams;
DROP POLICY IF EXISTS "exams_select_teacher" ON exams;
DROP POLICY IF EXISTS "exams_insert_teacher" ON exams;
DROP POLICY IF EXISTS "exams_update_teacher" ON exams;
DROP POLICY IF EXISTS "exams_delete_teacher" ON exams;
DROP POLICY IF EXISTS "exams_select_active" ON exams;
CREATE POLICY "exams_select_active" ON exams FOR SELECT USING (status = 'active');
DROP POLICY IF EXISTS "exams_select_guru" ON exams;
CREATE POLICY "exams_select_guru" ON exams
    FOR SELECT USING ((public._is_role('guru') OR public._is_role('admin_sekolah')) AND teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_select_super_admin" ON exams;
CREATE POLICY "exams_select_super_admin" ON exams FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "exams_insert_guru" ON exams;
CREATE POLICY "exams_insert_guru" ON exams
    FOR INSERT WITH CHECK (public._is_role('guru') AND teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_insert_admin_sekolah" ON exams;
CREATE POLICY "exams_insert_admin_sekolah" ON exams
    FOR INSERT WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "exams_update_guru" ON exams;
CREATE POLICY "exams_update_guru" ON exams FOR UPDATE USING (teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_update_admin_sekolah" ON exams;
CREATE POLICY "exams_update_admin_sekolah" ON exams
    FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "exams_delete_guru" ON exams;
CREATE POLICY "exams_delete_guru" ON exams FOR DELETE USING (teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_delete_admin_sekolah" ON exams;
CREATE POLICY "exams_delete_admin_sekolah" ON exams
    FOR DELETE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

-- Update submissions policies
DROP POLICY IF EXISTS "submissions_select_teacher" ON submissions;
DROP POLICY IF EXISTS "submissions_update_teacher" ON submissions;
DROP POLICY IF EXISTS "submissions_select_guru" ON submissions;
CREATE POLICY "submissions_select_guru" ON submissions
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "submissions_select_admin_sekolah" ON submissions;
CREATE POLICY "submissions_select_admin_sekolah" ON submissions
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "submissions_select_super_admin" ON submissions;
CREATE POLICY "submissions_select_super_admin" ON submissions FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "submissions_select_student" ON submissions;
CREATE POLICY "submissions_select_student" ON submissions
    FOR SELECT USING (student_id = auth.uid() AND is_published = TRUE);
DROP POLICY IF EXISTS "submissions_insert_student" ON submissions;
CREATE POLICY "submissions_insert_student" ON submissions FOR INSERT WITH CHECK (student_id = auth.uid());
DROP POLICY IF EXISTS "submissions_update_teacher" ON submissions;
CREATE POLICY "submissions_update_teacher" ON submissions
    FOR UPDATE USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "submissions_update_admin_sekolah" ON submissions;
CREATE POLICY "submissions_update_admin_sekolah" ON submissions
    FOR UPDATE USING (EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id AND e.school_id = public._user_school_id())
        AND public._is_role('admin_sekolah'));

-- Update access codes policies
DROP POLICY IF EXISTS "codes_select_teacher" ON exam_access_codes;
DROP POLICY IF EXISTS "codes_select_student" ON exam_access_codes;
DROP POLICY IF EXISTS "codes_insert_teacher" ON exam_access_codes;
DROP POLICY IF EXISTS "codes_select_guru" ON exam_access_codes;
CREATE POLICY "codes_select_guru" ON exam_access_codes
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "codes_select_admin_sekolah" ON exam_access_codes;
CREATE POLICY "codes_select_admin_sekolah" ON exam_access_codes
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "codes_select_student" ON exam_access_codes;
CREATE POLICY "codes_select_student" ON exam_access_codes
    FOR SELECT USING (student_id = auth.uid() AND is_used = FALSE);
DROP POLICY IF EXISTS "codes_insert_guru" ON exam_access_codes;
CREATE POLICY "codes_insert_guru" ON exam_access_codes
    FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "codes_insert_admin_sekolah" ON exam_access_codes;
CREATE POLICY "codes_insert_admin_sekolah" ON exam_access_codes
    FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));

-- Update violation logs policies
DROP POLICY IF EXISTS "violations_select_teacher" ON violation_logs;
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
DROP POLICY IF EXISTS "violations_select_guru" ON violation_logs;
CREATE POLICY "violations_select_guru" ON violation_logs
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "violations_select_admin_sekolah" ON violation_logs;
CREATE POLICY "violations_select_admin_sekolah" ON violation_logs
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
CREATE POLICY "violations_insert_service" ON violation_logs FOR INSERT WITH CHECK (true);

-- Update analytics policies
DROP POLICY IF EXISTS "analytics_select_teacher" ON analytics_cache;
DROP POLICY IF EXISTS "analytics_insert_teacher" ON analytics_cache;
DROP POLICY IF EXISTS "analytics_select_guru" ON analytics_cache;
CREATE POLICY "analytics_select_guru" ON analytics_cache
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "analytics_select_admin_sekolah" ON analytics_cache;
CREATE POLICY "analytics_select_admin_sekolah" ON analytics_cache
    FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "analytics_insert_guru" ON analytics_cache;
CREATE POLICY "analytics_insert_guru" ON analytics_cache
    FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "analytics_insert_admin_sekolah" ON analytics_cache;
CREATE POLICY "analytics_insert_admin_sekolah" ON analytics_cache
    FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid()
                WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));

-- Profile triggers
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO profiles (id, full_name, role, school_id)
    VALUES (
        NEW.id,
        NEW.raw_user_meta_data->>'full_name',
        COALESCE(NEW.raw_user_meta_data->>'role', 'murid'),
        (NEW.raw_user_meta_data->>'school_id')::UUID
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION handle_profile_role_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.role = 'guru' AND (OLD.role IS DISTINCT FROM 'guru' OR TG_OP = 'INSERT') THEN
        INSERT INTO teachers (id, school_id, nuptk)
        VALUES (NEW.id, NEW.school_id, NEW.nuptk)
        ON CONFLICT (id) DO NOTHING;
    END IF;
    IF NEW.role = 'murid' AND (OLD.role IS DISTINCT FROM 'murid' OR TG_OP = 'INSERT') THEN
        INSERT INTO students (id, school_id, class_id, nisn, nis)
        VALUES (NEW.id, NEW.school_id, NEW.class_id, NEW.nisn, NEW.nis)
        ON CONFLICT (id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_profile_role_change ON profiles;
CREATE TRIGGER on_profile_role_change
    AFTER INSERT OR UPDATE OF role, school_id, nisn, nis ON profiles
    FOR EACH ROW EXECUTE FUNCTION handle_profile_role_change();

-- Updated_at triggers for new tables
DROP TRIGGER IF EXISTS set_schools_updated_at ON schools;
CREATE TRIGGER set_schools_updated_at BEFORE UPDATE ON schools FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS set_reg_req_updated_at ON school_registration_requests;
CREATE TRIGGER set_reg_req_updated_at BEFORE UPDATE ON school_registration_requests FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS set_sy_updated_at ON school_years;
CREATE TRIGGER set_sy_updated_at BEFORE UPDATE ON school_years FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS set_subjects_updated_at ON subjects;
CREATE TRIGGER set_subjects_updated_at BEFORE UPDATE ON subjects FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS set_teachers_updated_at ON teachers;
CREATE TRIGGER set_teachers_updated_at BEFORE UPDATE ON teachers FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS set_students_updated_at ON students;
CREATE TRIGGER set_students_updated_at BEFORE UPDATE ON students FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Helper function
CREATE OR REPLACE FUNCTION increment_code_use(code_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE registration_codes SET use_count = use_count + 1 WHERE id = code_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Views
CREATE OR REPLACE VIEW active_school_years AS
    SELECT DISTINCT ON (school_id) * FROM school_years
    WHERE is_active = TRUE ORDER BY school_id, name DESC;

CREATE OR REPLACE VIEW class_student_counts AS
    SELECT c.id AS class_id, c.name AS class_name, c.school_id,
           COUNT(s.id) AS student_count
    FROM classes c LEFT JOIN students s ON s.class_id = c.id AND s.status = 'active'
    GROUP BY c.id, c.name, c.school_id;

-- Seed default school
INSERT INTO schools (id, name, npsn, address, province, city, district, principal_name, logo_url, tz_offset, status)
SELECT '00000000-0000-0000-0000-000000000001'::UUID,
    COALESCE(school_name, 'ScanGrade School'),
    npsn, address, province, city, district, principal_name, logo_url,
    COALESCE(tz_offset, 7), 'active'
FROM school_settings WHERE id = 1
ON CONFLICT (npsn) DO NOTHING;

UPDATE profiles SET school_id = '00000000-0000-0000-0000-000000000001'::UUID WHERE school_id IS NULL;
UPDATE classes SET school_id = '00000000-0000-0000-0000-000000000001'::UUID WHERE school_id IS NULL;

-- ===== STEP 6: 008 - Auth Activation =====
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS activation_code VARCHAR(12);
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS is_activated BOOLEAN DEFAULT FALSE;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_reg_req_activation_code ON school_registration_requests(activation_code);
CREATE INDEX IF NOT EXISTS idx_reg_req_email ON school_registration_requests(requester_email);

-- ===== STEP 7: 009 - Super Admin Approval =====
ALTER TABLE school_registration_requests
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS review_notes TEXT,
ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE registration_codes
ADD COLUMN IF NOT EXISTS duration_label TEXT DEFAULT '1-month',
ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ DEFAULT NOW();

-- ===== STEP 8: 010 - Teacher Assignments =====
CREATE TABLE IF NOT EXISTS teacher_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(teacher_id, class_id, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_ta_teacher ON teacher_assignments(teacher_id);
CREATE INDEX IF NOT EXISTS idx_ta_class ON teacher_assignments(class_id);
CREATE INDEX IF NOT EXISTS idx_ta_subject ON teacher_assignments(subject_id);
CREATE INDEX IF NOT EXISTS idx_ta_school ON teacher_assignments(school_id);
ALTER TABLE teacher_assignments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Teachers read own assignments" ON teacher_assignments;
CREATE POLICY "Teachers read own assignments" ON teacher_assignments
    FOR SELECT USING (teacher_id = auth.uid() OR EXISTS (
        SELECT 1 FROM profiles WHERE profiles.id = auth.uid() AND profiles.role IN ('super_admin', 'admin_sekolah')
    ));
DROP POLICY IF EXISTS "Teachers insert own assignments" ON teacher_assignments;
CREATE POLICY "Teachers insert own assignments" ON teacher_assignments
    FOR INSERT WITH CHECK (teacher_id = auth.uid()
        AND school_id = (SELECT school_id FROM profiles WHERE id = auth.uid()));
DROP POLICY IF EXISTS "Admin sekolah manage assignments" ON teacher_assignments;
CREATE POLICY "Admin sekolah manage assignments" ON teacher_assignments
    FOR ALL USING (EXISTS (
        SELECT 1 FROM profiles WHERE profiles.id = auth.uid()
        AND profiles.role IN ('super_admin', 'admin_sekolah') AND profiles.school_id = teacher_assignments.school_id
    ));
DROP POLICY IF EXISTS "Super admin all assignments" ON teacher_assignments;
CREATE POLICY "Super admin all assignments" ON teacher_assignments
    FOR ALL USING (EXISTS (
        SELECT 1 FROM profiles WHERE profiles.id = auth.uid() AND profiles.role = 'super_admin'
    ));
CREATE TRIGGER update_teacher_assignments_updated_at
    BEFORE UPDATE ON teacher_assignments FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ===== STEP 9: 011 - Anti-Cheat Settings =====
ALTER TABLE exams ADD COLUMN IF NOT EXISTS anti_cheat_enabled boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS penalty_per_violation integer DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS max_violations integer DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS auto_submit_on_max boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS fullscreen_required boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_questions boolean DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_options boolean DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS watermark_name boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_copy_paste boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_right_click boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_screenshot boolean DEFAULT false;

COMMENT ON COLUMN exams.anti_cheat_enabled IS 'Enable/disable anti-cheat for this exam';
COMMENT ON COLUMN exams.penalty_per_violation IS 'Points deducted per tab-switch violation (graduated scale)';
COMMENT ON COLUMN exams.max_violations IS 'Max violations before auto-submit (0 = unlimited)';
COMMENT ON COLUMN exams.auto_submit_on_max IS 'Auto-submit exam when max violations reached';
COMMENT ON COLUMN exams.fullscreen_required IS 'Require fullscreen mode during exam';
COMMENT ON COLUMN exams.randomize_questions IS 'Randomize question order per student';
COMMENT ON COLUMN exams.randomize_options IS 'Randomize MCQ option order per student';
COMMENT ON COLUMN exams.watermark_name IS 'Show student name watermark overlay';
COMMENT ON COLUMN exams.block_copy_paste IS 'Block copy/paste in essay textareas';
COMMENT ON COLUMN exams.block_right_click IS 'Block right-click context menu';
COMMENT ON COLUMN exams.block_screenshot IS 'Attempt to block screenshots';

-- ============================================
-- SELESAI. Verifikasi:
-- ============================================
-- SELECT 'Migration OK' AS status;
-- SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;
