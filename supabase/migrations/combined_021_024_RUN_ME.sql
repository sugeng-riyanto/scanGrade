-- ============================================================
-- COMBINED MIGRATION 021 + 024
-- Safe to run multiple times (uses IF NOT EXISTS everywhere)
-- Copy this entire file into Supabase SQL Editor → Run
-- ============================================================

-- ── MIGRATION 021: Conversation CRUD ──────────────────────────

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


-- ── MIGRATION 024: Fix pengumuman.school_id type (INT → UUID) ──

-- Drop existing FK constraint
ALTER TABLE pengumuman DROP CONSTRAINT IF EXISTS pengumuman_school_id_fkey;

-- Change column type to UUID; drop default first if present
ALTER TABLE pengumuman ALTER COLUMN school_id DROP DEFAULT;
ALTER TABLE pengumuman ALTER COLUMN school_id TYPE UUID USING school_id::text::uuid;

-- Re-add FK to schools table
ALTER TABLE pengumuman ADD CONSTRAINT pengumuman_school_id_fkey
    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE;

-- Performance indexes for pengumuman_read
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_pengumuman_id ON pengumuman_read(pengumuman_id);
CREATE INDEX IF NOT EXISTS idx_pengumuman_read_reader_id ON pengumuman_read(reader_id);


-- ============================================================
-- VERIFICATION QUERIES (run these separately to confirm)
-- ============================================================

-- 1. Check conversations columns
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'conversations'
  AND column_name IN ('deleted_at_p1', 'deleted_at_p2')
ORDER BY column_name;
-- Expected: 2 rows

-- 2. Check notifications columns
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'notifications'
  AND column_name IN ('deleted_at', 'deleted_by', 'is_deleted')
ORDER BY column_name;
-- Expected: 3 rows

-- 3. Check message_hides table exists
SELECT EXISTS (
    SELECT FROM information_schema.tables WHERE table_name = 'message_hides'
) AS message_hides_exists;
-- Expected: true

-- 4. Check pengumuman.school_id is UUID
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'pengumuman' AND column_name = 'school_id';
-- Expected: data_type = 'uuid'

-- 5. Check pengumuman FK points to schools
SELECT tc.constraint_name, tc.table_name, kcu.column_name,
       ccu.table_name AS foreign_table_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
WHERE tc.table_name = 'pengumuman' AND tc.constraint_type = 'FOREIGN KEY';
-- Expected: pengumuman_school_id_fkey → schools(id)

-- 6. Check pengumuman_read indexes exist
SELECT indexname FROM pg_indexes
WHERE tablename = 'pengumuman_read'
  AND indexname LIKE 'idx_pengumuman_read_%';
-- Expected: 2 rows
