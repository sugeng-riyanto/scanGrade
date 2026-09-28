# RLS Security Matrix

## Policy Overview

| Table | Owner Type | School_id Check | SELECT | INSERT | UPDATE | DELETE | Status |
|-------|-----------|----------------|--------|--------|--------|--------|--------|
| schools | System | ✅ (id) | ✅ SA/admin/guru/murid + officials | ✅ SA | ✅ SA | ✅ SA | FIXED |
| profiles | Self/School | ✅ | ✅ SA/admin/self + officials (same school) | ✅ self | ✅ self/admin | ❌ | FIXED |
| exams | Guru/Admin | ✅ | ✅ SA/guru/admin + officials | ✅ guru/admin | ✅ guru/admin | ✅ guru/admin | FIXED |
| submissions | Guru/Admin/Student | via exam_id | ✅ SA/guru/admin/student + officials | ✅ student | ✅ guru/admin | ❌ | FIXED |
| classes | Admin/School | ✅ | ✅ SA/admin/guru/murid + officials | ✅ SA/admin | ✅ SA/admin | ✅ SA/admin | FIXED |
| subjects | Admin/School | ✅ | ✅ SA/admin/guru/murid + officials | ✅ SA/admin | ✅ SA/admin | ✅ SA/admin | FIXED |
| teachers | System | ✅ | via profiles + officials | via trigger | via profiles | via profiles | FIXED |
| students | System | ✅ | via profiles + officials | via trigger | via profiles | via profiles | FIXED |
| teacher_assignments | Guru/Admin | ✅ | ✅ self/admin + officials | ✅ self/admin | ✅ admin | ✅ admin | FIXED |
| school_years | System | ✅ | via profiles | via trigger | via trigger | via trigger | FIXED |
| violation_logs | System | via exam_id | ✅ guru/admin/SA | ✅ service | ❌ | ❌ | FIXED |
| exam_access_codes | System | via exam_id | ✅ guru/admin/student | ✅ guru | ❌ | ❌ | FIXED |
| teacher_ai_keys | Guru | ✅ *(new)* | ✅ self + school | ✅ self + school | ✅ self | ✅ self | FIXED |
| teacher_ai_settings | Guru | ✅ *(new)* | ✅ self + school | ✅ self + school | ❌ | ❌ | FIXED |
| invoices | Admin/School | ✅ | ✅ admin/SA | ❌ | ❌ | ❌ | FIXED |
| payment_transactions | Admin/School | ✅ | ✅ admin/SA | ❌ | ❌ | ❌ | FIXED |
| school_subscriptions | Admin/School | ✅ | ✅ admin/SA | ❌ | ❌ | ❌ | FIXED |
| activation_codes | System | ✅ | ✅ admin/SA | ✅ SA | ❌ | ❌ | FIXED |
| ai_grading_logs | Guru/Admin | via submission_id | ✅ self/admin | ❌ | ❌ | ❌ | FIXED |
| audit_logs | System | via user_id | ✅ SA | ❌ | ❌ | ❌ | FIXED |

## The Two School Officials (`officials`)

`principal` and `vice_principal` were added by migration `038_school_officials_roles.sql`,
which deliberately opened only the *name*: the role `CHECK` in one release, the policies in
another. Migration `039_school_official_rls.sql` is that second release, and it gives them a
`FOR SELECT` policy on each table below — `schools` (by `id`), `profiles`, `teachers`,
`students`, `classes`, `subjects`, `teacher_assignments`, `exams`, and `submissions` (through
`exam_id`). In the table above they are the `officials` shorthand.

Four properties are the point of it:

* **Read-only, at the database layer too.** Not one policy in `039` is anything but `FOR
  SELECT`. `principal` exists to watch; a write policy would hand that away through the API
  rather than through a route.
* **Scoped by NPSN, not by trust.** Every predicate compares the row's school with
  `public._user_school_id()`. `submissions` carries no `school_id`, so the predicate walks
  `exam_id` into `exams` — the same shape as `submissions_select_admin_sekolah`.
* **The same two roles the decorator admits.** Each policy names both, mirroring
  `role_required(*OFFICIAL_ROLES)` in `app/utils/auth.py`. Narrowing one of them here would
  make the two differ in the database while they are one door in the app.
* **`audit_logs` and `pengumuman` are not in the list.** A raw log answers "who did what"
  and belongs to the super admin; the vice principal's authority over announcements is a
  *write* path (draft and approve) that needs its own migration, not an addition to a
  read-only file.

This does not change who enforces access: the backend uses the service key and bypasses RLS
entirely, so the route decorators remain layer 1. What `039` adds is the answer to a
question the routes cannot answer — *what does a caller with the public key and no session
reach?* Before it, that answer was "nothing", but only because neither role was named in any
policy, which left the Flask code as the sole thing keeping one school's principal out of
another school's roster. `tests/unit/test_school_official_rls.py` evaluates that isolation
row by row across two synthetic schools, and `tests/unit/test_school_officials.py` holds the
route-level refusal (both dashboards, and every `/admin-sekolah/officials/*` write).

## Validation Pattern

All NPSN/school_id checks follow this pattern:
```sql
school_id = public._user_school_id()
```
where `_user_school_id()` queries the profiles table for the authenticated user. A table that
has no `school_id` of its own reaches it through the row it belongs to:
```sql
EXISTS (SELECT 1 FROM exams e WHERE e.id = exam_id
        AND e.school_id = public._user_school_id())
```

## Defense in Depth

1. **Supabase RLS:** Row-level security at database level (bypassed by service key)
2. **Flask Decorators:** `@require_school_access` at route level
3. **Query Filters:** `.eq("school_id", sid)` in every data query
4. **Role Decorators:** `@admin_sekolah_required`, `@guru_required`

## When Adding New Tables

Checklist:
- [ ] Add `school_id` column (UUID FK → schools.id)
- [ ] Enable RLS: `ALTER TABLE xxx ENABLE ROW LEVEL SECURITY;`
- [ ] Create SELECT/INSERT/UPDATE/DELETE policies
- [ ] Add `@require_school_access` decorator to Flask route
- [ ] Add integration test for cross-school isolation
