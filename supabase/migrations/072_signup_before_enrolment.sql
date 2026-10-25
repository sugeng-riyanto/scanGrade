-- A signup has no school yet.
--
-- The first row a new account produces is a `profiles` row, written by
-- `handle_new_user` from `auth.users`; `handle_profile_role_change` then copies
-- that row into `students` or `teachers` — including its `school_id`, which at
-- signup time is NULL, because the pupil is enrolled afterwards by an admin.
--
-- Migration 007 declares both `school_id` columns NOT NULL, so that copy raised
-- `23502 null value in column "school_id" of relation "students" violates
-- not-null constraint` inside GoTrue's insert. GoTrue answers such a failure
-- with the generic "Database error creating new user", so the real cause never
-- reaches the operator, and **no account can be created at all** on a project
-- bootstrapped from the migration history.
--
-- Production already allows it (its columns are nullable — the constraint was
-- dropped there by hand long before this file existed), which is why the defect
-- was invisible there and only surfaced on the isolated staging project. This
-- migration records the shape production actually runs, so a fresh project can
-- sign a user up without anyone editing the schema in a dashboard.
--
-- Tenancy is not weakened by this: every query that reads these tables filters
-- on `school_id` (see `app/services/school_membership.py` and the `_user_school_id`
-- helpers in migration 044/063), an unenrolled account simply belongs to no
-- school until an admin enrolls it, and the RLS policies that scope by
-- `_user_school_id()` already treat a NULL school as "sees nothing".

ALTER TABLE public.students ALTER COLUMN school_id DROP NOT NULL;
ALTER TABLE public.teachers ALTER COLUMN school_id DROP NOT NULL;
