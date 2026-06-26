-- ============================================================
-- Migration 022: Pengumuman (Broadcast) & Chat (Percakapan/Pesan)
-- ============================================================

-- 1. PENGUMUMAN (Broadcast Notifications)
-- ============================================================
CREATE TABLE IF NOT EXISTS pengumuman (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    sender_role TEXT NOT NULL CHECK (sender_role IN ('super_admin', 'admin_sekolah', 'guru')),
    target_role TEXT NOT NULL CHECK (target_role IN ('guru', 'murid', 'admin_sekolah')),
    school_id INT REFERENCES school_settings(id) ON DELETE CASCADE,
    class_id UUID REFERENCES classes(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    attachment_url TEXT,
    is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pengumuman_sender_id ON pengumuman(sender_id);
CREATE INDEX idx_pengumuman_target_role ON pengumuman(target_role);
CREATE INDEX idx_pengumuman_school_id ON pengumuman(school_id);
CREATE INDEX idx_pengumuman_class_id ON pengumuman(class_id);
CREATE INDEX idx_pengumuman_created_at ON pengumuman(created_at DESC);
CREATE INDEX idx_pengumuman_expires_at ON pengumuman(expires_at) WHERE expires_at IS NOT NULL;

-- 2. PENGUMUMAN_READ (Read Tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS pengumuman_read (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pengumuman_id UUID NOT NULL REFERENCES pengumuman(id) ON DELETE CASCADE,
    reader_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pengumuman_id, reader_id)
);

CREATE INDEX idx_pengumuman_read_pengumuman_id ON pengumuman_read(pengumuman_id);
CREATE INDEX idx_pengumuman_read_reader_id ON pengumuman_read(reader_id);

-- 3. PERCAKAPAN (1-on-1 Conversation Threads)
-- ============================================================
CREATE TABLE IF NOT EXISTS percakapan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_a_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    user_b_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    subject TEXT,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT percakapan_no_self CHECK (user_a_id != user_b_id),
    CONSTRAINT percakapan_normalized CHECK (user_a_id < user_b_id)
);

CREATE UNIQUE INDEX idx_percakapan_pair ON percakapan(user_a_id, user_b_id);
CREATE INDEX idx_percakapan_user_a_id ON percakapan(user_a_id);
CREATE INDEX idx_percakapan_user_b_id ON percakapan(user_b_id);
CREATE INDEX idx_percakapan_last_message_at ON percakapan(last_message_at DESC NULLS LAST);

-- 4. PESAN (Individual Messages)
-- ============================================================
CREATE TABLE IF NOT EXISTS pesan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    percakapan_id UUID NOT NULL REFERENCES percakapan(id) ON DELETE CASCADE,
    sender_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    media_urls TEXT[] DEFAULT '{}',
    is_edited BOOLEAN NOT NULL DEFAULT FALSE,
    edited_at TIMESTAMPTZ,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pesan_percakapan_id ON pesan(percakapan_id);
CREATE INDEX idx_pesan_sender_id ON pesan(sender_id);
CREATE INDEX idx_pesan_created_at ON pesan(percakapan_id, created_at);
CREATE INDEX idx_pesan_unread ON pesan(percakapan_id, sender_id, is_read) WHERE NOT is_read AND NOT is_deleted;
CREATE INDEX idx_pesan_is_deleted ON pesan(is_deleted) WHERE is_deleted = TRUE;

-- ============================================================
-- TRIGGER: Auto-update updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_pengumuman_updated_at
    BEFORE UPDATE ON pengumuman
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_percakapan_updated_at
    BEFORE UPDATE ON percakapan
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_pesan_updated_at
    BEFORE UPDATE ON pesan
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- ============================================================
-- TRIGGER: Update percakapan.last_message_at on new pesan
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_update_percakapan_last_message()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE percakapan
    SET last_message_at = NEW.created_at
    WHERE id = NEW.percakapan_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_percakapan_last_message_on_insert
    AFTER INSERT ON pesan
    FOR EACH ROW
    EXECUTE FUNCTION trigger_update_percakapan_last_message();

-- ============================================================
-- TRIGGER: Auto-mark pesan as read when fetched by recipient
-- NOTE: This is a helper; actual mark-read happens in Flask API
-- after SELECT. This trigger handles the batch update.
-- ============================================================

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================

-- Enable RLS on all tables
ALTER TABLE pengumuman ENABLE ROW LEVEL SECURITY;
ALTER TABLE pengumuman_read ENABLE ROW LEVEL SECURITY;
ALTER TABLE percakapan ENABLE ROW LEVEL SECURITY;
ALTER TABLE pesan ENABLE ROW LEVEL SECURITY;

-- ── PENGUMUMAN RLS ──

-- SELECT: User can see pengumuman targeted to their role OR sent by them
-- NOTE: School-scoping is enforced at Flask layer (profiles.school_id may not exist)
CREATE POLICY pengumuman_select ON pengumuman FOR SELECT
USING (
    -- Sender can always see their own
    sender_id = auth.uid()
    OR
    -- Target role matches user's role; not expired; not archived
    (
        target_role = (SELECT role FROM profiles WHERE id = auth.uid())
        AND
        (expires_at IS NULL OR expires_at > NOW())
        AND
        NOT is_archived
    )
    OR
    -- Super admin sees all
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- INSERT: Based on sender role rules
CREATE POLICY pengumuman_insert ON pengumuman FOR INSERT
WITH CHECK (
    sender_id = auth.uid()
    AND
    sender_role = (SELECT role FROM profiles WHERE id = auth.uid())
    AND
    CASE (SELECT role FROM profiles WHERE id = auth.uid())
        WHEN 'murid' THEN FALSE  -- murid cannot create
        WHEN 'guru' THEN target_role = 'murid'  -- guru only to murid
        WHEN 'admin_sekolah' THEN target_role IN ('guru', 'murid')  -- admin to guru/murid
        WHEN 'super_admin' THEN TRUE  -- super admin any
        ELSE FALSE
    END
);

-- UPDATE: Only sender or super_admin
CREATE POLICY pengumuman_update ON pengumuman FOR UPDATE
USING (
    sender_id = auth.uid()
    OR
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- DELETE: Only sender or super_admin
CREATE POLICY pengumuman_delete ON pengumuman FOR DELETE
USING (
    sender_id = auth.uid()
    OR
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- ── PENGUMUMAN_READ RLS ──

CREATE POLICY pengumuman_read_select ON pengumuman_read FOR SELECT
USING (
    reader_id = auth.uid()
    OR
    (SELECT role FROM profiles WHERE id = auth.uid()) IN ('super_admin', 'admin_sekolah')
);

CREATE POLICY pengumuman_read_insert ON pengumuman_read FOR INSERT
WITH CHECK (
    reader_id = auth.uid()
);

CREATE POLICY pengumuman_read_delete ON pengumuman_read FOR DELETE
USING (
    reader_id = auth.uid()
);

-- ── PERCAKAPAN RLS ──

-- SELECT: Only participants can see their conversations
CREATE POLICY percakapan_select ON percakapan FOR SELECT
USING (
    user_a_id = auth.uid() OR user_b_id = auth.uid()
);

-- INSERT: Must be one of the participants; validated role pairs in Flask
CREATE POLICY percakapan_insert ON percakapan FOR INSERT
WITH CHECK (
    auth.uid() IN (user_a_id, user_b_id)
);

-- UPDATE: Only participants can archive
CREATE POLICY percakapan_update ON percakapan FOR UPDATE
USING (
    user_a_id = auth.uid() OR user_b_id = auth.uid()
);

-- DELETE: Only super_admin or both participants
CREATE POLICY percakapan_delete ON percakapan FOR DELETE
USING (
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- ── PESAN RLS ──

-- SELECT: Only participants of the conversation
CREATE POLICY pesan_select ON pesan FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM percakapan
        WHERE id = pesan.percakapan_id
        AND (user_a_id = auth.uid() OR user_b_id = auth.uid())
    )
);

-- INSERT: Only participants; validated in Flask
CREATE POLICY pesan_insert ON pesan FOR INSERT
WITH CHECK (
    sender_id = auth.uid()
    AND
    EXISTS (
        SELECT 1 FROM percakapan
        WHERE id = pesan.percakapan_id
        AND (user_a_id = auth.uid() OR user_b_id = auth.uid())
    )
);

-- UPDATE: Only sender can edit content; super_admin can edit
CREATE POLICY pesan_update ON pesan FOR UPDATE
USING (
    sender_id = auth.uid()
    OR
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- DELETE: Only sender can soft-delete; super_admin can hard-delete
CREATE POLICY pesan_delete ON pesan FOR DELETE
USING (
    sender_id = auth.uid()
    OR
    (SELECT role FROM profiles WHERE id = auth.uid()) = 'super_admin'
);

-- ============================================================
-- FUNCTION: Get unread pengumuman count
-- ============================================================
CREATE OR REPLACE FUNCTION get_unread_pengumuman_count(user_id UUID)
RETURNS INTEGER AS $$
DECLARE
    user_role TEXT;
    total INTEGER;
BEGIN
    SELECT role INTO user_role FROM profiles WHERE id = user_id;

    SELECT COUNT(*) INTO total
    FROM pengumuman p
    WHERE
        p.target_role = user_role
        AND (p.expires_at IS NULL OR p.expires_at > NOW())
        AND NOT p.is_archived
        AND NOT EXISTS (
            SELECT 1 FROM pengumuman_read pr
            WHERE pr.pengumuman_id = p.id AND pr.reader_id = user_id
        );

    RETURN total;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: Get unread pesan count for a percakapan
-- ============================================================
CREATE OR REPLACE FUNCTION get_unread_pesan_count(percakapan_id UUID, user_id UUID)
RETURNS INTEGER AS $$
DECLARE
    total INTEGER;
BEGIN
    SELECT COUNT(*) INTO total
    FROM pesan
    WHERE
        percakapan_id = $1
        AND sender_id != $2
        AND NOT is_read
        AND NOT is_deleted;

    RETURN total;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================
-- FUNCTION: Mark pesan as read (batch)
-- ============================================================
CREATE OR REPLACE FUNCTION mark_pesan_read(percakapan_id UUID, user_id UUID)
RETURNS INTEGER AS $$
DECLARE
    updated INTEGER;
BEGIN
    UPDATE pesan
    SET is_read = TRUE, read_at = NOW()
    WHERE
        percakapan_id = $1
        AND sender_id != $2
        AND NOT is_read
        AND NOT is_deleted;
    GET DIAGNOSTICS updated = ROW_COUNT;
    RETURN updated;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
