-- Migration 021: Conversation CRUD (WhatsApp-like)
-- Adds: delete conversation, unsend message, delete for self, archive/unarchive

-- Conversations: soft-delete support (per-participant tracking)
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at_p1 TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at_p2 TIMESTAMPTZ;

-- Notifications: unsend/delete tracking
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS deleted_by UUID;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;

-- Per-user message hiding (delete for self)
CREATE TABLE IF NOT EXISTS message_hides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notification_id BIGINT NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(notification_id, user_id)
);

ALTER TABLE message_hides ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage own hides" ON message_hides;
CREATE POLICY "Users can manage own hides"
    ON message_hides FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE INDEX IF NOT EXISTS idx_message_hides_user ON message_hides(user_id);
CREATE INDEX IF NOT EXISTS idx_message_hides_notification ON message_hides(notification_id);
