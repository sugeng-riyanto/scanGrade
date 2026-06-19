-- Migration 017: Notifications & Broadcast System
CREATE TABLE IF NOT EXISTS notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sender_id UUID NOT NULL,
    sender_role VARCHAR(20) NOT NULL,
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    target_role VARCHAR(20), -- null = all, or 'admin_sekolah','guru','murid'
    target_school_id UUID REFERENCES schools(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notification_recipients (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notification_id BIGINT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    recipient_id UUID NOT NULL,
    read_at TIMESTAMPTZ,
    UNIQUE(notification_id, recipient_id)
);

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_recipients ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Anyone can read notifications"
    ON notifications FOR SELECT USING (true);

CREATE POLICY "Anyone can read their own recipient status"
    ON notification_recipients FOR SELECT
    USING (recipient_id = auth.uid());
