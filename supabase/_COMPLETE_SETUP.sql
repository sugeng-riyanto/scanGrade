-- ============================================================
-- SCANGRADE — COMPLETE DATABASE SETUP
-- Jalankan SEMUA script ini di Supabase SQL Editor (sekali jalan)
-- ============================================================

-- 1. SCHEMA (idempotent — aman dijalankan berulang)
-- ============================================================

-- Schools
CREATE TABLE IF NOT EXISTS schools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    npsn TEXT UNIQUE, address TEXT, province TEXT, city TEXT, district TEXT,
    postal_code TEXT, phone TEXT, email TEXT, website TEXT, logo_url TEXT,
    principal_name TEXT, principal_nip TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    tz_offset INT DEFAULT 7,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- School Registration Requests
CREATE TABLE IF NOT EXISTS school_registration_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_name TEXT NOT NULL, npsn TEXT, address TEXT, province TEXT, city TEXT, district TEXT,
    requester_name TEXT NOT NULL, requester_email TEXT NOT NULL, requester_phone TEXT, requester_position TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    is_activated BOOLEAN DEFAULT FALSE,
    activation_code TEXT,
    expires_at TIMESTAMPTZ,
    profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    admin_notes TEXT, review_notes TEXT,
    approved_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Registration Codes
CREATE TABLE IF NOT EXISTS registration_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    code TEXT NOT NULL UNIQUE, role TEXT NOT NULL CHECK (role IN ('guru', 'murid')),
    max_uses INT DEFAULT 1, use_count INT DEFAULT 0,
    expires_at TIMESTAMPTZ, is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES profiles(id) ON DELETE SET NULL, created_at TIMESTAMPTZ DEFAULT NOW()
);

-- School Years
CREATE TABLE IF NOT EXISTS school_years (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Subjects
CREATE TABLE IF NOT EXISTS subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL, code TEXT, description TEXT, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Classes (extended)
ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE CASCADE;
ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_year_id UUID REFERENCES school_years(id) ON DELETE SET NULL;
ALTER TABLE classes ADD COLUMN IF NOT EXISTS grade_level TEXT;

-- Teachers extension
CREATE TABLE IF NOT EXISTS teachers (
    id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    nuptk TEXT UNIQUE, subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
    employee_id TEXT, qualification TEXT, join_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Students extension
CREATE TABLE IF NOT EXISTS students (
    id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    nisn TEXT UNIQUE, nis TEXT, birth_date DATE, birth_place TEXT,
    gender TEXT CHECK (gender IN ('L', 'P')), address TEXT, parent_phone TEXT,
    entry_year INT, status TEXT DEFAULT 'active' CHECK (status IN ('active', 'alumni', 'dropped')),
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Teacher Assignments (many-to-many: teacher <-> class, subject)
CREATE TABLE IF NOT EXISTS teacher_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(teacher_id, class_id, subject_id)
);

-- Add missing columns to school_registration_requests (if table already exists)
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS is_activated BOOLEAN DEFAULT FALSE;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS activation_code TEXT;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL;
ALTER TABLE school_registration_requests ADD COLUMN IF NOT EXISTS review_notes TEXT;

-- Audit Logs
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT,
    old_data JSONB, new_data JSONB, ip_address TEXT, user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. ADD MISSING COLUMNS TO EXISTING TABLES
-- ============================================================

-- Profiles: add school_id, new role values
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE SET NULL;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS nuptk TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended'));
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS registration_code_id UUID REFERENCES registration_codes(id) ON DELETE SET NULL;

-- Migrate old roles to new hierarchy
UPDATE profiles SET role = 'super_admin' WHERE role IN ('admin', 'super_admin');
UPDATE profiles SET role = 'guru' WHERE role IN ('teacher', 'guru');
UPDATE profiles SET role = 'murid' WHERE role IN ('student', 'murid');

-- Update role CHECK constraint
ALTER TABLE profiles DROP CONSTRAINT IF EXISTS profiles_role_check;
ALTER TABLE profiles ADD CONSTRAINT profiles_role_check
    CHECK (role IN ('super_admin', 'admin_sekolah', 'guru', 'murid'));

-- Exams: add all missing columns
ALTER TABLE exams ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS class_id UUID REFERENCES classes(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS exam_type TEXT DEFAULT 'ulangan' CHECK (exam_type IN ('ulangan', 'uts', 'uas', 'tryout', 'ljk'));
ALTER TABLE exams ADD COLUMN IF NOT EXISTS pdf_page_urls JSONB DEFAULT '[]';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_audio JSONB DEFAULT '{}';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_canvas JSONB DEFAULT '{}';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_weights JSONB DEFAULT '{}';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS anti_cheat_enabled BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS penalty_per_violation INTEGER DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS max_violations INTEGER DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS auto_submit_on_max BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS fullscreen_required BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_questions BOOLEAN DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_options BOOLEAN DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS watermark_name BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_copy_paste BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_right_click BOOLEAN DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_screenshot BOOLEAN DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS allow_calculator BOOLEAN DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS teacher_feedback JSONB DEFAULT '{}';

-- Submissions: add missing columns
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS teacher_feedback JSONB DEFAULT '{}';
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN DEFAULT FALSE;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS retracted BOOLEAN DEFAULT FALSE;

-- 3. INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_profiles_school ON profiles(school_id);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON profiles(role);
CREATE INDEX IF NOT EXISTS idx_exams_school ON exams(school_id);
CREATE INDEX IF NOT EXISTS idx_exams_class ON exams(class_id);
CREATE INDEX IF NOT EXISTS idx_exams_subject ON exams(subject_id);
CREATE INDEX IF NOT EXISTS idx_submissions_published ON submissions(is_published) WHERE is_published = TRUE;

-- 4. RLS — ENABLE ON ALL TABLES
-- ============================================================
ALTER TABLE schools ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_registration_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE registration_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE school_years ENABLE ROW LEVEL SECURITY;
ALTER TABLE subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE teacher_assignments ENABLE ROW LEVEL SECURITY;

-- 5. RLS POLICIES (role-based CRUD)
-- ============================================================

-- Helper functions (di public schema, bukan auth — utk hindari permission error)
CREATE OR REPLACE FUNCTION public._is_role(required_role TEXT)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = required_role AND status = 'active');
$$ LANGUAGE sql STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public._user_school_id()
RETURNS UUID AS $$
    SELECT school_id FROM profiles WHERE id = auth.uid();
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- SCHOOLS
DROP POLICY IF EXISTS "schools_super_admin_all" ON schools;
CREATE POLICY "schools_super_admin_all" ON schools FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "schools_admin_read_own" ON schools;
CREATE POLICY "schools_admin_read_own" ON schools FOR SELECT USING (public._is_role('admin_sekolah') AND id = public._user_school_id());
DROP POLICY IF EXISTS "schools_guru_murid_read_own" ON schools;
CREATE POLICY "schools_guru_murid_read_own" ON schools FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND id = public._user_school_id());

-- PROFILES
DROP POLICY IF EXISTS "profiles_select_own" ON profiles;
CREATE POLICY "profiles_select_own" ON profiles FOR SELECT USING (auth.uid() = id);
DROP POLICY IF EXISTS "profiles_select_super_admin" ON profiles;
CREATE POLICY "profiles_select_super_admin" ON profiles FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "profiles_select_admin_own_school" ON profiles;
CREATE POLICY "profiles_select_admin_own_school" ON profiles FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_select_guru" ON profiles;
CREATE POLICY "profiles_select_guru" ON profiles FOR SELECT USING (public._is_role('guru') AND (role = 'murid' OR id = auth.uid()) AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_update_own" ON profiles;
CREATE POLICY "profiles_update_own" ON profiles FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
DROP POLICY IF EXISTS "profiles_update_admin_own_school" ON profiles;
CREATE POLICY "profiles_update_admin_own_school" ON profiles FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id()) WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "profiles_insert_trigger" ON profiles;
CREATE POLICY "profiles_insert_trigger" ON profiles FOR INSERT WITH CHECK (auth.uid() = id);

-- EXAMS
DROP POLICY IF EXISTS "exams_select_active" ON exams;
CREATE POLICY "exams_select_active" ON exams FOR SELECT USING (status = 'active' AND is_published = TRUE);
DROP POLICY IF EXISTS "exams_select_guru" ON exams;
CREATE POLICY "exams_select_guru" ON exams FOR SELECT USING (public._is_role('guru') AND teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_select_admin_sekolah" ON exams;
CREATE POLICY "exams_select_admin_sekolah" ON exams FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "exams_select_super_admin" ON exams;
CREATE POLICY "exams_select_super_admin" ON exams FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "exams_insert_guru" ON exams;
CREATE POLICY "exams_insert_guru" ON exams FOR INSERT WITH CHECK (public._is_role('guru') AND teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_insert_admin_sekolah" ON exams;
CREATE POLICY "exams_insert_admin_sekolah" ON exams FOR INSERT WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "exams_update_guru" ON exams;
CREATE POLICY "exams_update_guru" ON exams FOR UPDATE USING (teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_update_admin_sekolah" ON exams;
CREATE POLICY "exams_update_admin_sekolah" ON exams FOR UPDATE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "exams_delete_guru" ON exams;
CREATE POLICY "exams_delete_guru" ON exams FOR DELETE USING (teacher_id = auth.uid());
DROP POLICY IF EXISTS "exams_delete_admin_sekolah" ON exams;
CREATE POLICY "exams_delete_admin_sekolah" ON exams FOR DELETE USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

-- SUBMISSIONS
DROP POLICY IF EXISTS "submissions_select_guru" ON submissions;
CREATE POLICY "submissions_select_guru" ON submissions FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "submissions_select_admin_sekolah" ON submissions;
CREATE POLICY "submissions_select_admin_sekolah" ON submissions FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid() WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "submissions_select_super_admin" ON submissions;
CREATE POLICY "submissions_select_super_admin" ON submissions FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "submissions_select_student" ON submissions;
CREATE POLICY "submissions_select_student" ON submissions FOR SELECT USING (student_id = auth.uid() AND is_published = TRUE);
DROP POLICY IF EXISTS "submissions_insert_student" ON submissions;
CREATE POLICY "submissions_insert_student" ON submissions FOR INSERT WITH CHECK (student_id = auth.uid());
DROP POLICY IF EXISTS "submissions_update_guru" ON submissions;
CREATE POLICY "submissions_update_guru" ON submissions FOR UPDATE USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "submissions_update_admin_sekolah" ON submissions;
CREATE POLICY "submissions_update_admin_sekolah" ON submissions FOR UPDATE USING (EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id AND e.school_id = public._user_school_id()) AND public._is_role('admin_sekolah'));

-- EXAM ACCESS CODES
DROP POLICY IF EXISTS "codes_select_guru" ON exam_access_codes;
CREATE POLICY "codes_select_guru" ON exam_access_codes FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "codes_select_admin_sekolah" ON exam_access_codes;
CREATE POLICY "codes_select_admin_sekolah" ON exam_access_codes FOR SELECT USING (EXISTS (SELECT 1 FROM exams e JOIN profiles p ON p.id = auth.uid() WHERE e.id = exam_id AND p.role = 'admin_sekolah' AND e.school_id = p.school_id));
DROP POLICY IF EXISTS "codes_select_student" ON exam_access_codes;
CREATE POLICY "codes_select_student" ON exam_access_codes FOR SELECT USING (student_id = auth.uid() AND is_used = FALSE);
DROP POLICY IF EXISTS "codes_insert_guru" ON exam_access_codes;
CREATE POLICY "codes_insert_guru" ON exam_access_codes FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));

-- VIOLATION LOGS
DROP POLICY IF EXISTS "violations_select_guru" ON violation_logs;
CREATE POLICY "violations_select_guru" ON violation_logs FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
CREATE POLICY "violations_insert_service" ON violation_logs FOR INSERT WITH CHECK (true);

-- CLASSES
DROP POLICY IF EXISTS "classes_super_admin_all" ON classes;
CREATE POLICY "classes_super_admin_all" ON classes FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "classes_admin_all_own" ON classes;
CREATE POLICY "classes_admin_all_own" ON classes FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id()) WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "classes_guru_murid_read" ON classes;
CREATE POLICY "classes_guru_murid_read" ON classes FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND school_id = public._user_school_id());

-- SUBJECTS
DROP POLICY IF EXISTS "subjects_super_admin_all" ON subjects;
CREATE POLICY "subjects_super_admin_all" ON subjects FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "subjects_admin_all_own" ON subjects;
CREATE POLICY "subjects_admin_all_own" ON subjects FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id()) WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "subjects_guru_murid_read" ON subjects;
CREATE POLICY "subjects_guru_murid_read" ON subjects FOR SELECT USING ((public._is_role('guru') OR public._is_role('murid')) AND school_id = public._user_school_id());

-- TEACHER ASSIGNMENTS
DROP POLICY IF EXISTS "ta_read_own" ON teacher_assignments;
CREATE POLICY "ta_read_own" ON teacher_assignments FOR SELECT USING (teacher_id = auth.uid() OR EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role IN ('super_admin', 'admin_sekolah')));
DROP POLICY IF EXISTS "ta_insert_own" ON teacher_assignments;
CREATE POLICY "ta_insert_own" ON teacher_assignments FOR INSERT WITH CHECK (teacher_id = auth.uid() AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "ta_admin_all" ON teacher_assignments;
CREATE POLICY "ta_admin_all" ON teacher_assignments FOR ALL USING (EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role IN ('super_admin', 'admin_sekolah') AND school_id = teacher_assignments.school_id));

-- 6. STORAGE BUCKETS
-- ============================================================
INSERT INTO storage.buckets (id, name, public) VALUES ('exam-pdfs', 'exam-pdfs', true) ON CONFLICT (id) DO NOTHING;
INSERT INTO storage.buckets (id, name, public) VALUES ('student-answers', 'student-answers', true) ON CONFLICT (id) DO NOTHING;

-- Storage policies
DROP POLICY IF EXISTS "exam_pdfs_select" ON storage.objects;
CREATE POLICY "exam_pdfs_select" ON storage.objects FOR SELECT TO public USING (bucket_id IN ('exam-pdfs', 'student-answers'));
DROP POLICY IF EXISTS "exam_pdfs_insert" ON storage.objects;
CREATE POLICY "exam_pdfs_insert" ON storage.objects FOR INSERT TO authenticated WITH CHECK (bucket_id IN ('exam-pdfs', 'student-answers'));
DROP POLICY IF EXISTS "exam_pdfs_delete" ON storage.objects;
CREATE POLICY "exam_pdfs_delete" ON storage.objects FOR DELETE TO authenticated USING (bucket_id IN ('exam-pdfs', 'student-answers'));

-- 7. TRIGGERS
-- ============================================================
-- Function to auto-create profile on auth signup.
-- Uses SECURITY DEFINER + explicit search_path to avoid schema issues.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, full_name, role, status)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'role', 'murid'),
        'active'
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

-- Drop existing trigger if any, then recreate
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- Profile role change → auto-create teachers/students records
CREATE OR REPLACE FUNCTION handle_profile_role_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.role = 'guru' AND (OLD.role IS DISTINCT FROM 'guru' OR TG_OP = 'INSERT') THEN
        INSERT INTO teachers (id, school_id) VALUES (NEW.id, NEW.school_id) ON CONFLICT (id) DO NOTHING;
    END IF;
    IF NEW.role = 'murid' AND (OLD.role IS DISTINCT FROM 'murid' OR TG_OP = 'INSERT') THEN
        INSERT INTO students (id, school_id, class_id, nisn, nis) VALUES (NEW.id, NEW.school_id, NEW.class_id, NEW.nisn, NEW.nis) ON CONFLICT (id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_profile_role_change ON profiles;
CREATE TRIGGER on_profile_role_change AFTER INSERT OR UPDATE OF role, school_id ON profiles FOR EACH ROW EXECUTE FUNCTION handle_profile_role_change();

-- 8. SEED DEFAULT SCHOOL
-- ============================================================
INSERT INTO schools (id, name, npsn, status, tz_offset)
VALUES ('00000000-0000-0000-0000-000000000001', 'ScanGrade School', '99999999', 'active', 7)
ON CONFLICT (id) DO NOTHING;

-- Assign existing profiles to default school
UPDATE profiles SET school_id = '00000000-0000-0000-0000-000000000001'::UUID WHERE school_id IS NULL;
UPDATE classes SET school_id = '00000000-0000-0000-0000-000000000001'::UUID WHERE school_id IS NULL;

-- ============================================================
-- Subscription & Payment System
-- ============================================================
CREATE TABLE IF NOT EXISTS midtrans_settings (
    id SERIAL PRIMARY KEY,
    merchant_id TEXT NOT NULL DEFAULT '',
    client_key TEXT NOT NULL DEFAULT '',
    server_key TEXT NOT NULL DEFAULT '',
    is_production BOOLEAN DEFAULT false,
    updated_by UUID,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscription_plans (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    duration_label TEXT NOT NULL DEFAULT '',
    duration_days INTEGER NOT NULL DEFAULT 0,
    price DECIMAL(12,2) NOT NULL DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS school_subscriptions (
    id SERIAL PRIMARY KEY,
    school_id UUID NOT NULL,
    plan_id INTEGER REFERENCES subscription_plans(id),
    status TEXT NOT NULL DEFAULT 'trial',
    trial_days INTEGER NOT NULL DEFAULT 14,
    trial_start TIMESTAMPTZ DEFAULT now(),
    trial_end TIMESTAMPTZ,
    subscription_start TIMESTAMPTZ,
    subscription_end TIMESTAMPTZ,
    activation_code TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id SERIAL PRIMARY KEY,
    school_id UUID NOT NULL,
    plan_id INTEGER REFERENCES subscription_plans(id),
    order_id TEXT UNIQUE NOT NULL,
    gross_amount DECIMAL(12,2),
    status TEXT DEFAULT 'pending',
    snap_token TEXT,
    snap_redirect_url TEXT,
    payment_type TEXT,
    transaction_time TIMESTAMPTZ,
    settlement_time TIMESTAMPTZ,
    activation_code TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trial_settings (
    id SERIAL PRIMARY KEY,
    trial_days INTEGER NOT NULL DEFAULT 14,
    updated_by UUID,
    updated_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO subscription_plans (name, duration_label, duration_days, price, sort_order) VALUES
    ('1 Bulan', '1 Bulan', 30, 59000, 1),
    ('3 Bulan', '3 Bulan', 90, 149000, 2),
    ('4 Bulan', '4 Bulan', 120, 179000, 3),
    ('6 Bulan', '6 Bulan', 180, 249000, 4),
    ('1 Tahun', '1 Tahun', 365, 399000, 5),
    ('2 Tahun', '2 Tahun', 730, 699000, 6),
    ('3 Tahun', '3 Tahun', 1095, 949000, 7),
    ('5 Tahun', '5 Tahun', 1825, 1399000, 8),
    ('7 Tahun', '7 Tahun', 2555, 1799000, 9),
    ('Selamanya', 'Selamanya', 0, 2499000, 10)
ON CONFLICT DO NOTHING;

INSERT INTO trial_settings (trial_days) VALUES (14)
ON CONFLICT DO NOTHING;

ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS pricing_config JSONB DEFAULT '{"model": "flat", "tiers": []}';

ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS payment_fee_config JSONB DEFAULT '{"fee_percent": 0, "fee_flat": 4000, "fee_note": "Biaya admin Rp 4.000 (transfer bank)"}';

ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS payment_details JSONB DEFAULT '{}';

-- Exam scheduling & class/subject improvements
ALTER TABLE exams ADD COLUMN IF NOT EXISTS start_at TIMESTAMPTZ;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS end_at TIMESTAMPTZ;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS class_ids JSONB DEFAULT '[]';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS subject_id INTEGER REFERENCES subjects(id);
ALTER TABLE exams ADD COLUMN IF NOT EXISTS is_template BOOLEAN DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS source_exam_id UUID;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS max_attempts INTEGER DEFAULT 1;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS publish_mode TEXT DEFAULT 'manual';

-- Prevent duplicate class/subject names per school
ALTER TABLE classes ADD COLUMN IF NOT EXISTS created_by UUID;
ALTER TABLE subjects ADD COLUMN IF NOT EXISTS created_by UUID;
