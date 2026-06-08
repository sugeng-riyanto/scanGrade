# Security Checklist — ScanGrade

Use this checklist when adding new features or tables.

## Pre-Merge Checklist

### Database Schema
- [ ] All new tables have a `school_id UUID REFERENCES schools(id)` column
- [ ] `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` is included
- [ ] SELECT/INSERT/UPDATE/DELETE policies defined for each role
- [ ] Migration SQL is version-controlled in `supabase/migrations/`

### Flask Routes
- [ ] `@require_school_access()` decorator applied to all routes with resource ID params
- [ ] Decorator correctly chained: `school_years` → direct, `submissions` → `("exam_id", "exams")`
- [ ] Routes without URL params use `.eq("school_id", sid)` in queries

### API Endpoints
- [ ] Return 403 JSON with `"error": "Access denied: different school"` when school doesn't match
- [ ] Return 404 JSON when resource not found
- [ ] Rate limiting applied (see `app/utils/rate_limiter.py`)

### Tests
- [ ] Integration test covers cross-school denial for each new table
- [ ] Test verifies both SELECT and write (POST/PUT/DELETE) denial
- [ ] Test uses actual Supabase queries (not mocked)

## Periodic Audit

- [ ] Run `SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public';` to verify all tables have policies
- [ ] Check for any `supabase.table(...)` query missing `.eq("school_id", ...)` filter
- [ ] Verify `@require_school_access` is applied to new routes added in last sprint
- [ ] Review audit logs for denied access attempts

## Incident Response

If a cross-school data leak is suspected:
1. Check audit logs for unusual access patterns
2. Verify all RLS policies are enabled via Supabase dashboard
3. Review recent route changes for missing `@require_school_access`
4. Check if any query bypasses `.eq("school_id", ...)` filter
5. Run test suite: `pytest tests/test_rls_security.py -v`
