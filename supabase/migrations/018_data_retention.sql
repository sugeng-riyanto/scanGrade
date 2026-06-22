-- Migration 018: Data Retention & UU PDP Compliance
-- Soft-delete columns, retention tracking, deletion requests

-- Add soft-delete columns to submissions
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS deletion_reason TEXT;

-- Add soft-delete + retention to notifications
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Add soft-delete to violation_logs
ALTER TABLE violation_logs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Profiles: anonymization tracking
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS anonymized_at TIMESTAMPTZ;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Table for tracking data deletion requests
CREATE TABLE IF NOT EXISTS deletion_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    processed_by UUID,
    notes TEXT
);

ALTER TABLE deletion_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can see own requests" ON deletion_requests;
CREATE POLICY "Users can see own requests"
    ON deletion_requests FOR SELECT
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS "Users can insert own requests" ON deletion_requests;
CREATE POLICY "Users can insert own requests"
    ON deletion_requests FOR INSERT
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS "Admin can see all requests" ON deletion_requests;
CREATE POLICY "Admin can see all requests"
    ON deletion_requests FOR SELECT
    USING (
        auth.uid() IN (
            SELECT id FROM profiles WHERE role IN ('super_admin', 'admin_sekolah')
        )
        OR user_id = auth.uid()
    );

DROP POLICY IF EXISTS "Admin can update requests" ON deletion_requests;
CREATE POLICY "Admin can update requests"
    ON deletion_requests FOR UPDATE
    USING (
        auth.uid() IN (
            SELECT id FROM profiles WHERE role IN ('super_admin', 'admin_sekolah')
        )
    );

-- Indexes for purge queries
CREATE INDEX IF NOT EXISTS idx_submissions_deleted ON submissions(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_deleted ON notifications(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_violation_logs_deleted ON violation_logs(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_profiles_deleted ON profiles(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_expires ON notifications(expires_at) WHERE expires_at IS NOT NULL;
