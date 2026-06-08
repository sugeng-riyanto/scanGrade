# Security Audit — RLS & School Isolation

**Date:** 8 June 2026
**Auditor:** Automated code analysis
**Scope:** All database tables, RLS policies, and Flask route-level access control

---

## Architecture Overview

ScanGrade uses Supabase as its database with two clients:
- **`get_supabase()`** — initialized with `SUPABASE_SERVICE_KEY` (bypasses RLS)
- **`get_auth_client()`** — initialized with `SUPABASE_ANON_KEY` (respects RLS)

**Critical Finding:** All data operations use the service key, which means **RLS policies are completely bypassed**. School isolation depends entirely on application-level filters (`.eq("school_id", g.user_school_id)` in every query) and route-level decorator checks.

---

## Per-Table Audit

| # | Table | Has `school_id`? | RLS Enabled? | Policies | Risk | Notes |
|---|-------|-----------------|--------------|----------|------|-------|
| 1 | `schools` | ✅ (id) | ✅ | 3 (SA all, admin read own, guru/murid read own) | LOW | OK |
| 2 | `profiles` | ✅ | ✅ | 8 (select own, SA all, admin own school, guru read murid, etc.) | LOW | OK |
| 3 | `exams` | ✅ | ✅ | 8 (guru/admin/SA select, insert guru/admin, update guru/admin, delete guru/admin) | LOW | OK |
| 4 | `submissions` | via exam_id | ✅ | 6 (guru, admin, SA, student, insert, update) | LOW | OK |
| 5 | `classes` | ✅ | ✅ | 3 (SA all, admin all own, guru/murid read) | LOW | OK |
| 6 | `subjects` | ✅ | ✅ | 3 (SA all, admin all own, guru/murid read) | LOW | OK |
| 7 | `teachers` | ✅ | ✅ | via profiles RLS | LOW | Extends profiles |
| 8 | `students` | ✅ | ✅ | via profiles RLS | LOW | Extends profiles |
| 9 | `teacher_assignments` | ✅ | ✅ | 3 (read own, insert own, admin all) | LOW | OK |
| 10 | `school_years` | ✅ | ✅ | via profiles RLS | LOW | OK |
| 11 | `violation_logs` | via exam_id | ✅ | 4 (guru, admin_sekolah, SA, service insert) | LOW | OK |
| 12 | `exam_access_codes` | via exam_id | ✅ | 4 (guru, admin_sekolah, student, guru insert) | LOW | OK |
| 13 | `teacher_ai_keys` | ❌ **NEW** | ❌ **NEW** | ❌ (migration adds school_id + RLS) | CRITICAL | **FIXED** |
| 14 | `teacher_ai_settings` | ❌ **NEW** | ❌ **NEW** | ❌ (migration adds school_id + RLS) | CRITICAL | **FIXED** |
| 15 | `invoices` | ✅ | ❌ **NEW** | ❌ (migration adds RLS) | HIGH | **FIXED** |
| 16 | `payment_transactions` | ✅ | ❌ **NEW** | ❌ (migration adds RLS) | HIGH | **FIXED** |
| 17 | `school_subscriptions` | ✅ | ❌ **NEW** | ❌ (migration adds RLS) | HIGH | **FIXED** |
| 18 | `activation_codes` | ✅ | ❌ **NEW** | ❌ (migration adds RLS) | MEDIUM | **FIXED** |
| 19 | `ai_grading_logs` | via submission_id | ❌ **NEW** | ❌ (migration adds RLS) | MEDIUM | **FIXED** |
| 20 | `audit_logs` | via user_id | ✅ | 1 (SA only) | LOW | OK |

---

## Risk Summary

| Risk Level | Count | Details |
|-----------|-------|---------|
| **CRITICAL** | 2 | `teacher_ai_keys`, `teacher_ai_settings` — no school_id column, no RLS |
| **HIGH** | 3 | `invoices`, `payment_transactions`, `school_subscriptions` — no RLS |
| **MEDIUM** | 2 | `activation_codes`, `ai_grading_logs` — no RLS |
| **LOW** | 13 | All others with proper RLS policies |

---

## Defense in Depth Layers

1. **Supabase RLS** — database-level (bypassed by service key, kept for future anon-key migration)
2. **Flask `@require_school_access` decorator** — route-level school_id match check
3. **Application query filters** — `.eq("school_id", sid)` in every data query
4. **Role-based access control** — `@admin_sekolah_required`, `@guru_required` decorators

---

## Key Finding: Service Key bypasses RLS

The app initializes `get_supabase()` with `SUPABASE_SERVICE_KEY` (admin key) on line 50 of `app/__init__.py`:
```python
supabase: Client = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_KEY)
```

The service key bypasses ALL RLS policies. To make RLS effective, the app should use `SUPABASE_ANON_KEY` for user-facing queries and the service key only for admin/trusted operations. This is a larger refactor outside the current scope.

**Mitigation:** The `@require_school_access` decorator provides application-level defense even with the service key.
