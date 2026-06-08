-- ============================================================
-- SCANGRADE — RLS POLICY FIX MIGRATION
-- Tanggal: 2026-06-08
-- 
-- Menambahkan RLS policies untuk tables yang belum memiliki
-- school-scoped access control.
-- ============================================================

-- 1. TEACHER_AI_KEYS — tambah school_id + RLS
-- ============================================================
ALTER TABLE teacher_ai_keys ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE CASCADE;
ALTER TABLE teacher_ai_keys ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_keys_select_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_select_own" ON teacher_ai_keys
  FOR SELECT
  USING (
    teacher_id = auth.uid()
    AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "ai_keys_insert_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_insert_own" ON teacher_ai_keys
  FOR INSERT
  WITH CHECK (
    teacher_id = auth.uid()
    AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "ai_keys_update_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_update_own" ON teacher_ai_keys
  FOR UPDATE
  USING (teacher_id = auth.uid())
  WITH CHECK (teacher_id = auth.uid());

DROP POLICY IF EXISTS "ai_keys_delete_own" ON teacher_ai_keys;
CREATE POLICY "ai_keys_delete_own" ON teacher_ai_keys
  FOR DELETE
  USING (teacher_id = auth.uid());

-- 2. TEACHER_AI_SETTINGS — tambah school_id + RLS
-- ============================================================
ALTER TABLE teacher_ai_settings ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id) ON DELETE CASCADE;
ALTER TABLE teacher_ai_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_settings_select_own" ON teacher_ai_settings;
CREATE POLICY "ai_settings_select_own" ON teacher_ai_settings
  FOR SELECT
  USING (
    teacher_id = auth.uid()
    AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "ai_settings_upsert_own" ON teacher_ai_settings;
CREATE POLICY "ai_settings_upsert_own" ON teacher_ai_settings
  FOR INSERT
  WITH CHECK (
    teacher_id = auth.uid()
    AND school_id = public._user_school_id()
  );

-- 3. INVOICES — RLS
-- ============================================================
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "invoices_select_admin" ON invoices;
CREATE POLICY "invoices_select_admin" ON invoices
  FOR SELECT
  USING (
    public._is_role('admin_sekolah') AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "invoices_select_super_admin" ON invoices;
CREATE POLICY "invoices_select_super_admin" ON invoices
  FOR SELECT
  USING (public._is_role('super_admin'));

-- 4. PAYMENT_TRANSACTIONS — RLS
-- ============================================================
ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "payment_select_admin" ON payment_transactions;
CREATE POLICY "payment_select_admin" ON payment_transactions
  FOR SELECT
  USING (
    public._is_role('admin_sekolah') AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "payment_select_super_admin" ON payment_transactions;
CREATE POLICY "payment_select_super_admin" ON payment_transactions
  FOR SELECT
  USING (public._is_role('super_admin'));

-- 5. SCHOOL_SUBSCRIPTIONS — RLS
-- ============================================================
ALTER TABLE school_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "subscriptions_select_admin" ON school_subscriptions;
CREATE POLICY "subscriptions_select_admin" ON school_subscriptions
  FOR SELECT
  USING (
    public._is_role('admin_sekolah') AND school_id = public._user_school_id()
  );

DROP POLICY IF EXISTS "subscriptions_select_super_admin" ON school_subscriptions;
CREATE POLICY "subscriptions_select_super_admin" ON school_subscriptions
  FOR ALL
  USING (public._is_role('super_admin'))
  WITH CHECK (public._is_role('super_admin'));

-- 6. ACTIVATION_CODES — RLS
-- ============================================================
ALTER TABLE activation_codes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "codes_select_admin" ON activation_codes;
CREATE POLICY "codes_select_admin" ON activation_codes
  FOR SELECT
  USING (
    (public._is_role('admin_sekolah') AND school_id = public._user_school_id())
    OR public._is_role('super_admin')
  );

DROP POLICY IF EXISTS "codes_insert_super_admin" ON activation_codes;
CREATE POLICY "codes_insert_super_admin" ON activation_codes
  FOR INSERT
  WITH CHECK (public._is_role('super_admin'));

-- 7. VIOLATION_LOGS — tambah RLS untuk super_admin + admin_sekolah
-- ============================================================
DROP POLICY IF EXISTS "violations_select_admin_sekolah" ON violation_logs;
CREATE POLICY "violations_select_admin_sekolah" ON violation_logs
  FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM exams e
    WHERE e.id = exam_id
    AND e.school_id = public._user_school_id()
  ));

DROP POLICY IF EXISTS "violations_select_super_admin" ON violation_logs;
CREATE POLICY "violations_select_super_admin" ON violation_logs
  FOR SELECT
  USING (public._is_role('super_admin'));

-- 8. AI_GRADING_LOGS — RLS
-- ============================================================
ALTER TABLE ai_grading_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_logs_select_own" ON ai_grading_logs;
CREATE POLICY "ai_logs_select_own" ON ai_grading_logs
  FOR SELECT
  USING (
    teacher_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM submissions s JOIN exams e ON e.id = s.exam_id
      WHERE s.id = submission_id AND e.school_id = public._user_school_id()
    )
  );

DROP POLICY IF EXISTS "ai_logs_select_admin" ON ai_grading_logs;
CREATE POLICY "ai_logs_select_admin" ON ai_grading_logs
  FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM submissions s JOIN exams e ON e.id = s.exam_id
    WHERE s.id = submission_id AND e.school_id = public._user_school_id()
  ));

-- 9. AUDIT_LOGS — RLS untuk super_admin
-- ============================================================
DROP POLICY IF EXISTS "audit_logs_select_super_admin" ON audit_logs;
CREATE POLICY "audit_logs_select_super_admin" ON audit_logs
  FOR SELECT
  USING (public._is_role('super_admin'));

-- ============================================================
-- VERIFICATION QUERIES (jalan manual di Supabase SQL Editor)
-- ============================================================
-- SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename;
