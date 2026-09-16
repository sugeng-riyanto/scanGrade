-- Migration 028: least privilege for the API — the policies that were open to the world
--
-- Found by pointing the *anon* key (the public one, shipped in every page) at every
-- object PostgREST serves and asking for one row. Eight of forty-nine answered with
-- rows and no session at all, because these policies were written with `USING (true)`
-- and no `TO` clause — and a policy with no `TO` applies to `PUBLIC`, which includes
-- `anon`.
--
-- The worst of them, measured:
--
--   exams_select_active     SELECT USING (status = 'active' AND is_published = TRUE)
--                           → an anonymous caller read `answer_key` for every
--                             published exam. Anyone who opens devtools and copies
--                             the public key could read the keys before sitting the
--                             paper. `{"0":"D","1":"B", …}` came back verbatim.
--   school_registration_requests
--                           SELECT USING (true) → the activation code of every
--                             pending school registration, which is what activates a
--                             new school administrator's account.
--   notifications           "Anyone can read notifications" USING (true) → every
--                             staff announcement and direct message.
--   classes / schools / school_settings
--                           SELECT USING (true) → the roster structure and contact
--                             details of all eleven schools across every tenant.
--   violation_logs          INSERT CHECK (true) → any caller could *write* an
--                             anti-cheat violation against any student, which is the
--                             record the penalty is computed from.
--   audit_logs              INSERT CHECK (true) → any caller could write audit rows.
--
-- The `*_service` policies are a separate, quieter mistake: they were written so the
-- server could write, but the server uses the **service key**, and the service role
-- bypasses RLS entirely. They grant nothing the app needs and everything an attacker
-- wants, so they are replaced with `TO service_role`, which states the same intent and
-- grants nobody else anything.
--
-- Why this is safe to apply: the app reaches PostgREST with the service key
-- (`get_supabase()`), and the anon key is used only for GoTrue calls —
-- `sign_up`, `sign_in_with_password`, `get_user` — which never touch these tables.
-- RLS here is the second line of defence against a caller who has the public key,
-- and today that line was not there at all.
--
-- Idempotent: every statement drops first or uses IF EXISTS.

-- ============================================
-- 1. exams — the answer key is not public
-- ============================================
-- Dropped, not narrowed. A student has no business reading an `exams` row through the
-- API at all: the paper is rendered server-side, and `answer_key` lives on the same
-- row. Teachers, school admins and the super admin keep their own scoped policies
-- (`exams_select_guru`, `exams_select_admin_sekolah`, `exams_select_super_admin`).
DROP POLICY IF EXISTS "exams_select_active" ON exams;

-- ============================================
-- 2. notifications — a message is for its people
-- ============================================
-- Read: the sender, or someone it was addressed to. A broadcast is addressed to a
-- role/school, so a recipient row is what proves it.
DROP POLICY IF EXISTS "Anyone can read notifications" ON notifications;
DROP POLICY IF EXISTS "notifications_select_participant" ON notifications;
CREATE POLICY "notifications_select_participant" ON notifications
    FOR SELECT TO authenticated
    USING (
        sender_id = auth.uid()
        OR EXISTS (SELECT 1 FROM notification_recipients r
                   WHERE r.notification_id = notifications.id
                     AND r.recipient_id = auth.uid())
    );

-- Write: only the sender may create the notification, and only as themselves.
DROP POLICY IF EXISTS "Users can insert notifications" ON notifications;
CREATE POLICY "notifications_insert_sender" ON notifications
    FOR INSERT TO authenticated
    WITH CHECK (sender_id = auth.uid());

-- Recipients: only the person who sent the notification may add or remove its
-- recipients. `WITH CHECK (true)` let any caller address anything to anyone.
DROP POLICY IF EXISTS "Users can insert their own recipients" ON notification_recipients;
CREATE POLICY "notification_recipients_insert_sender" ON notification_recipients
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (SELECT 1 FROM notifications n
                WHERE n.id = notification_id AND n.sender_id = auth.uid())
    );

-- `Users can delete recipients` — USING (true) — exists in the live database and in
-- no file here; it let any caller empty any notification's recipient list.
DROP POLICY IF EXISTS "Users can delete recipients" ON notification_recipients;
CREATE POLICY "notification_recipients_delete_own" ON notification_recipients
    FOR DELETE TO authenticated
    USING (
        recipient_id = auth.uid()
        OR EXISTS (SELECT 1 FROM notifications n
                   WHERE n.id = notification_id AND n.sender_id = auth.uid())
    );

-- ============================================
-- 3. school registration — public signup, private codes
-- ============================================
-- The select leaked `activation_code` for every pending request. The signup form is
-- posted to this app, which inserts with the service key, so the public select and
-- insert are both unnecessary. Super admins keep their own scoped policies.
DROP POLICY IF EXISTS "reg_req_select_public" ON school_registration_requests;
DROP POLICY IF EXISTS "reg_req_insert_public" ON school_registration_requests;
DROP POLICY IF EXISTS "reg_req_insert_service" ON school_registration_requests;
CREATE POLICY "reg_req_insert_service" ON school_registration_requests
    FOR INSERT TO service_role WITH CHECK (true);

-- A code is validated server-side before it is accepted; reading the table is not
-- part of that. Admins keep the scoped policies from 007/009.
DROP POLICY IF EXISTS "reg_codes_select_registration" ON registration_codes;
DROP POLICY IF EXISTS "reg_codes_select_authenticated" ON registration_codes;
CREATE POLICY "reg_codes_select_authenticated" ON registration_codes
    FOR SELECT TO authenticated
    USING (public._is_role('super_admin') OR public._is_role('admin_sekolah'));

-- ============================================
-- 4. the school directory — a tenant boundary, not a public list
-- ============================================
-- `classes_select_all`, `schools_select_own`, `school_select_all` were all
-- `USING (true)`: eleven schools' classes, contact details and settings were readable
-- by anyone. The scoped policies — `schools_read_own`, `schools_guru_murid_read_own`,
-- `classes_guru_murid_read`, `classes_admin_all_own`, `classes_super_admin_all` —
-- already cover every signed-in role, and the server reads with the service key.
DROP POLICY IF EXISTS "classes_select_all" ON classes;
DROP POLICY IF EXISTS "schools_select_own" ON schools;
DROP POLICY IF EXISTS "school_select_all" ON school_settings;

-- The write policies that were `USING (true)` / `CHECK (true)` for the sake of the
-- server, restated for the role that actually does the writing.
DROP POLICY IF EXISTS "classes_insert_service" ON classes;
DROP POLICY IF EXISTS "classes_update_service" ON classes;
DROP POLICY IF EXISTS "classes_delete_service" ON classes;
CREATE POLICY "classes_write_service" ON classes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "schools_insert_service" ON schools;
DROP POLICY IF EXISTS "schools_update_service" ON schools;
CREATE POLICY "schools_write_service" ON schools
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "school_insert_service" ON school_settings;
DROP POLICY IF EXISTS "school_update_service" ON school_settings;
CREATE POLICY "school_settings_write_service" ON school_settings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================
-- 5. the records that must not be forgeable
-- ============================================
-- A violation is the evidence a penalty is computed from, and an audit row is what
-- answers "who did this". Both are written by the server with the service key, which
-- needs no policy — so `CHECK (true)` granted a stranger the power to fabricate the
-- other side's record.
DROP POLICY IF EXISTS "violations_insert_service" ON violation_logs;
CREATE POLICY "violations_insert_service" ON violation_logs
    FOR INSERT TO service_role WITH CHECK (true);

DROP POLICY IF EXISTS "audit_insert_service" ON audit_logs;
CREATE POLICY "audit_insert_service" ON audit_logs
    FOR INSERT TO service_role WITH CHECK (true);

-- ============================================
-- 6. the two views — they ran as their owner and skipped RLS entirely
-- ============================================
-- `active_school_years` (4 rows) and `class_student_counts` (28 rows) both answered an
-- anonymous caller, because a view without `security_invoker` executes with the
-- privileges of the role that owns it and no row-level policy applies underneath.
-- With it on, the views see exactly what the caller may see in `school_years`,
-- `classes` and `students`.
ALTER VIEW active_school_years SET (security_invoker = true);
ALTER VIEW class_student_counts SET (security_invoker = true);
