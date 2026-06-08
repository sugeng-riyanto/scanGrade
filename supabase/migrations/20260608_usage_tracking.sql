-- ============================================================
-- SCANGRADE — Migration: Usage Tracking + Tier Enforcement
-- Tanggal: 2026-06-08
-- ============================================================

-- 1. USAGE_TRACKING — track per-school usage metrics
-- ============================================================
CREATE TABLE IF NOT EXISTS usage_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,  -- 'exams_created', 'students_imported', 'ai_gradings'
    count INT NOT NULL DEFAULT 1,
    period TEXT NOT NULL DEFAULT 'yearly',  -- 'yearly', 'monthly'
    recorded_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_school_metric ON usage_tracking(school_id, metric);

ALTER TABLE usage_tracking ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "usage_select_admin" ON usage_tracking;
CREATE POLICY "usage_select_admin" ON usage_tracking
    FOR SELECT
    USING (public._is_role('admin_sekolah') AND school_id = public._user_school_id());

DROP POLICY IF EXISTS "usage_select_super_admin" ON usage_tracking;
CREATE POLICY "usage_select_super_admin" ON usage_tracking
    FOR ALL
    USING (public._is_role('super_admin'))
    WITH CHECK (public._is_role('super_admin'));

-- 2. ADD tier column to school_subscriptions (for plan categorization)
-- ============================================================
ALTER TABLE school_subscriptions ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'trial'
    CHECK (tier IN ('trial', 'basic', 'pro', 'enterprise'));

-- 3. DEMO_REQUESTS — capture landing page demo requests
-- ============================================================
CREATE TABLE IF NOT EXISTS demo_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT DEFAULT '',
    message TEXT DEFAULT '',
    status TEXT DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'converted', 'closed')),
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE demo_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "demo_requests_select_admin" ON demo_requests;
CREATE POLICY "demo_requests_select_admin" ON demo_requests
    FOR ALL
    USING (public._is_role('super_admin'))
    WITH CHECK (public._is_role('super_admin'));
