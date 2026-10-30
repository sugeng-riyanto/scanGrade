-- Migration 078: teacher_assignments INSERT is admin-only.
--
-- The exam-build gate (`app/services/assignments.py::unassigned_class_ids`) trusts
-- every active row in this table, so a policy that lets a teacher INSERT their own
-- row for any (class, subject) in their school is a self-grant: a signed-in
-- teacher calling PostgREST directly (with their own JWT, bypassing the app)
-- could attach themselves to any pair and then build papers for it.
--
-- The "Teachers insert own assignments" policy (migration 010) dates from when the
-- only writer was the exam builder's single-pair dropdown. Since the admin matrix
-- (`app/services/teacher_assignments.py::save`) all in-app writes go through the
-- service key, which bypasses RLS entirely — so removing the self-insert policy
-- changes nothing the app does; it only closes the direct-API door.
--
-- Admins keep INSERT through the existing ALL policies ("Admin sekolah manage
-- assignments" / "Super admin all assignments", and "ta_admin_all" on databases
-- built from `_COMPLETE_SETUP.sql`): a FOR ALL policy without WITH CHECK applies
-- its USING clause to inserts. Both policy names are dropped to cover both
-- bootstrap lineages (numbered migrations and `_COMPLETE_SETUP.sql`).
--
-- Additive and non-destructive: no row is touched, only the INSERT door narrows.
-- Run in the Supabase SQL Editor. Safe to run twice.

DROP POLICY IF EXISTS "Teachers insert own assignments" ON teacher_assignments;
DROP POLICY IF EXISTS "ta_insert_own" ON teacher_assignments;
