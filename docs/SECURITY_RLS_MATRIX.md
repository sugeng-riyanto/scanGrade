# RLS Security Matrix

## Policy Overview

| Table | Owner Type | School_id Check | SELECT | INSERT | UPDATE | DELETE | Status |
|-------|-----------|----------------|--------|--------|--------|--------|--------|
| schools | System | ✅ (id) | ✅ SA/admin/guru/murid | ✅ SA | ✅ SA | ✅ SA | FIXED |
| profiles | Self/School | ✅ | ✅ SA/admin/self | ✅ self | ✅ self/admin | ❌ | FIXED |
| exams | Guru/Admin | ✅ | ✅ SA/guru/admin | ✅ guru/admin | ✅ guru/admin | ✅ guru/admin | FIXED |
| submissions | Guru/Admin/Student | via exam_id | ✅ SA/guru/admin/student | ✅ student | ✅ guru/admin | ❌ | FIXED |
| classes | Admin/School | ✅ | ✅ SA/admin/guru/murid | ✅ SA/admin | ✅ SA/admin | ✅ SA/admin | FIXED |
| subjects | Admin/School | ✅ | ✅ SA/admin/guru/murid | ✅ SA/admin | ✅ SA/admin | ✅ SA/admin | FIXED |
| teachers | System | ✅ | via profiles | via trigger | via profiles | via profiles | FIXED |
| students | System | ✅ | via profiles | via trigger | via profiles | via profiles | FIXED |
| teacher_assignments | Guru/Admin | ✅ | ✅ self/admin | ✅ self/admin | ✅ admin | ✅ admin | FIXED |
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

## Validation Pattern

All NPSN/school_id checks follow this pattern:
```sql
school_id = public._user_school_id()
```
where `_user_school_id()` queries the profiles table for the authenticated user.

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
