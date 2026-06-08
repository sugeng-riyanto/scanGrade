-- ============================================================
-- SCANGRADE — ENABLE RLS ON ALL TABLES + SCHOOL-SCOPED POLICIES
-- Migration: 001 (standalone — dapat dijalankan kapan saja)
-- ============================================================
-- Migration ini mengaktifkan Row Level Security pada SEMUA tabel
-- yang memiliki data spesifik-sekolah, dan membuat policy agar
-- user hanya bisa membaca/menulis data sekolah mereka sendiri.
-- ============================================================

-- 1. HELPER FUNCTIONS (idempotent)
-- ============================================================
CREATE OR REPLACE FUNCTION public._is_role(required_role TEXT)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = required_role AND status = 'active');
$$ LANGUAGE sql STABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public._user_school_id()
RETURNS UUID AS $$
    SELECT school_id FROM profiles WHERE id = auth.uid();
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- 2. ENABLE RLS ON ALL TABLES (safe — ignores missing tables)
-- ============================================================
DO $$ BEGIN ALTER TABLE schools ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE profiles ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE exams ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE submissions ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE classes ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE subjects ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE teachers ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE students ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE teacher_assignments ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE school_years ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE violation_logs ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE exam_access_codes ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE teacher_ai_keys ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE teacher_ai_settings ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE invoices ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE school_subscriptions ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE ai_grading_logs ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE usage_tracking ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE demo_requests ENABLE ROW LEVEL SECURITY; EXCEPTION WHEN undefined_table THEN NULL; END $$;

-- 3. SCHOOLS — super_admin ALL, admin/guru/murid SELECT own school only
-- ============================================================
DROP POLICY IF EXISTS "schools_super_admin_all" ON schools;
CREATE POLICY "schools_super_admin_all" ON schools FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "schools_read_own" ON schools;
CREATE POLICY "schools_read_own" ON schools FOR SELECT USING (id = public._user_school_id());

-- 4. PROFILES — users see own, admin see own school, super_admin see all
-- ============================================================
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
DROP POLICY IF EXISTS "profiles_insert_trigger" ON profiles;
CREATE POLICY "profiles_insert_trigger" ON profiles FOR INSERT WITH CHECK (auth.uid() = id);

-- 5. EXAMS — guru own exams, admin_sekolah own school, super_admin all
-- ============================================================
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

-- 6. SUBMISSIONS — via exam_id join + student own
-- ============================================================
DROP POLICY IF EXISTS "submissions_select_guru" ON submissions;
CREATE POLICY "submissions_select_guru" ON submissions FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "submissions_select_admin_sekolah" ON submissions;
CREATE POLICY "submissions_select_admin_sekolah" ON submissions FOR SELECT USING (EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id AND e.school_id = public._user_school_id()));
DROP POLICY IF EXISTS "submissions_select_super_admin" ON submissions;
CREATE POLICY "submissions_select_super_admin" ON submissions FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "submissions_select_student" ON submissions;
CREATE POLICY "submissions_select_student" ON submissions FOR SELECT USING (student_id = auth.uid() AND is_published = TRUE);
DROP POLICY IF EXISTS "submissions_insert_student" ON submissions;
CREATE POLICY "submissions_insert_student" ON submissions FOR INSERT WITH CHECK (student_id = auth.uid());
DROP POLICY IF EXISTS "submissions_update_guru" ON submissions;
CREATE POLICY "submissions_update_guru" ON submissions FOR UPDATE USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));

-- 7. CLASSES — super_admin all, admin own school, guru/murid read own school
-- ============================================================
DROP POLICY IF EXISTS "classes_super_admin_all" ON classes;
CREATE POLICY "classes_super_admin_all" ON classes FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "classes_admin_all_own" ON classes;
CREATE POLICY "classes_admin_all_own" ON classes FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id()) WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "classes_read_own_school" ON classes;
CREATE POLICY "classes_read_own_school" ON classes FOR SELECT USING (school_id = public._user_school_id());

-- 8. SUBJECTS — super_admin all, admin own school, guru/murid read own school
-- ============================================================
DROP POLICY IF EXISTS "subjects_super_admin_all" ON subjects;
CREATE POLICY "subjects_super_admin_all" ON subjects FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));
DROP POLICY IF EXISTS "subjects_admin_all_own" ON subjects;
CREATE POLICY "subjects_admin_all_own" ON subjects FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id()) WITH CHECK (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "subjects_read_own_school" ON subjects;
CREATE POLICY "subjects_read_own_school" ON subjects FOR SELECT USING (school_id = public._user_school_id());

-- 9. TEACHER ASSIGNMENTS — teacher own, admin own school
-- ============================================================
DROP POLICY IF EXISTS "ta_read_own" ON teacher_assignments;
CREATE POLICY "ta_read_own" ON teacher_assignments FOR SELECT USING (teacher_id = auth.uid() OR public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "ta_admin_all" ON teacher_assignments;
CREATE POLICY "ta_admin_all" ON teacher_assignments FOR ALL USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

-- 10. VIOLATION LOGS — guru own exam, admin_sekolah own school, super_admin all
-- ============================================================
DROP POLICY IF EXISTS "violations_select_guru" ON violation_logs;
CREATE POLICY "violations_select_guru" ON violation_logs FOR SELECT USING (EXISTS (SELECT 1 FROM exams WHERE id = exam_id AND teacher_id = auth.uid()));
DROP POLICY IF EXISTS "violations_select_admin_sekolah" ON violation_logs;
CREATE POLICY "violations_select_admin_sekolah" ON violation_logs FOR SELECT USING (EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id AND e.school_id = public._user_school_id()));
DROP POLICY IF EXISTS "violations_select_super_admin" ON violation_logs;
CREATE POLICY "violations_select_super_admin" ON violation_logs FOR SELECT USING (public._is_role('super_admin'));
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
CREATE POLICY "violations_insert_service" ON violation_logs FOR INSERT WITH CHECK (true);

-- 11. TEACHER AI KEYS — teacher own
-- ============================================================
DROP POLICY IF EXISTS "ai_keys_select_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_select_own" ON teacher_ai_keys FOR SELECT USING (teacher_id = auth.uid() AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "ai_keys_insert_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_insert_own" ON teacher_ai_keys FOR INSERT WITH CHECK (teacher_id = auth.uid() AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "ai_keys_update_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_update_own" ON teacher_ai_keys FOR UPDATE USING (teacher_id = auth.uid());
DROP POLICY IF EXISTS "ai_keys_delete_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_delete_own" ON teacher_ai_keys FOR DELETE USING (teacher_id = auth.uid());

-- 12. INVOICES — admin_sekolah own school, super_admin all
-- ============================================================
DROP POLICY IF EXISTS "invoices_select_admin" ON invoices;
CREATE POLICY "invoices_select_admin" ON invoices FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "invoices_select_super_admin" ON invoices;
CREATE POLICY "invoices_select_super_admin" ON invoices FOR SELECT USING (public._is_role('super_admin'));

-- 13. PAYMENT TRANSACTIONS — admin_sekolah own school, super_admin all
-- ============================================================
DROP POLICY IF EXISTS "payment_select_admin" ON payment_transactions;
CREATE POLICY "payment_select_admin" ON payment_transactions FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "payment_select_super_admin" ON payment_transactions;
CREATE POLICY "payment_select_super_admin" ON payment_transactions FOR SELECT USING (public._is_role('super_admin'));

-- 14. SCHOOL SUBSCRIPTIONS — admin_sekolah own school, super_admin all
-- ============================================================
DROP POLICY IF EXISTS "subscriptions_select_admin" ON school_subscriptions;
CREATE POLICY "subscriptions_select_admin" ON school_subscriptions FOR SELECT USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());
DROP POLICY IF EXISTS "subscriptions_select_super_admin" ON school_subscriptions;
CREATE POLICY "subscriptions_select_super_admin" ON school_subscriptions FOR ALL USING (public._is_role('super_admin')) WITH CHECK (public._is_role('super_admin'));

-- 15. AUDIT LOGS — super_admin only
-- ============================================================
DROP POLICY IF EXISTS "audit_logs_select_super_admin" ON audit_logs;
CREATE POLICY "audit_logs_select_super_admin" ON audit_logs FOR SELECT USING (public._is_role('super_admin'));
