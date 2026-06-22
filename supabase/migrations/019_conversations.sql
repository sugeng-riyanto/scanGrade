-- Migration 019: Conversations / Threaded Replies
-- Untuk reply system one-on-one guru ↔ murid

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_1 UUID NOT NULL,
    participant_2 UUID NOT NULL,
    title VARCHAR(200) NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'archived')),
    last_message_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    completed_by UUID
);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Participants can see conversations" ON conversations;
CREATE POLICY "Participants can see conversations"
    ON conversations FOR SELECT
    USING (participant_1 = auth.uid() OR participant_2 = auth.uid());

DROP POLICY IF EXISTS "Participants can insert conversations" ON conversations;
CREATE POLICY "Participants can insert conversations"
    ON conversations FOR INSERT
    WITH CHECK (participant_1 = auth.uid() OR participant_2 = auth.uid());

DROP POLICY IF EXISTS "Participants can update conversations" ON conversations;
CREATE POLICY "Participants can update conversations"
    ON conversations FOR UPDATE
    USING (participant_1 = auth.uid() OR participant_2 = auth.uid());

-- Add conversation_id to notifications (for threaded replies)
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_notifications_conversation ON notifications(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_participants ON conversations(participant_1, participant_2);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
