-- Whiteboard Feature — Real-time Collaborative Whiteboard
-- 7 new tables, no existing tables altered

-- 1. whiteboards
CREATE TABLE IF NOT EXISTS whiteboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    teacher_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_whiteboards_school ON whiteboards(school_id);
CREATE INDEX IF NOT EXISTS idx_whiteboards_teacher ON whiteboards(teacher_id);
CREATE INDEX IF NOT EXISTS idx_whiteboards_class ON whiteboards(class_id);
CREATE INDEX IF NOT EXISTS idx_whiteboards_status ON whiteboards(status);

-- 2. whiteboard_members
CREATE TABLE IF NOT EXISTS whiteboard_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    can_annotate BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(whiteboard_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_wb_members_whiteboard ON whiteboard_members(whiteboard_id);
CREATE INDEX IF NOT EXISTS idx_wb_members_student ON whiteboard_members(student_id);

-- 3. whiteboard_slides
CREATE TABLE IF NOT EXISTS whiteboard_slides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    slide_number INT NOT NULL,
    background_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(whiteboard_id, slide_number)
);
CREATE INDEX IF NOT EXISTS idx_wb_slides_whiteboard ON whiteboard_slides(whiteboard_id);

-- 4. whiteboard_ops
CREATE TABLE IF NOT EXISTS whiteboard_ops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    slide_number INT NOT NULL,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    op_type VARCHAR(50) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    timestamp BIGINT NOT NULL,
    seq_number BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wb_ops_whiteboard ON whiteboard_ops(whiteboard_id);
CREATE INDEX IF NOT EXISTS idx_wb_ops_slide ON whiteboard_ops(whiteboard_id, slide_number);
CREATE INDEX IF NOT EXISTS idx_wb_ops_seq ON whiteboard_ops(whiteboard_id, slide_number, seq_number);

-- 5. whiteboard_reactions
CREATE TABLE IF NOT EXISTS whiteboard_reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    emoji VARCHAR(10) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wb_reactions_whiteboard ON whiteboard_reactions(whiteboard_id);

-- 6. whiteboard_anti_cheat_log
CREATE TABLE IF NOT EXISTS whiteboard_anti_cheat_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wb_acheat_whiteboard ON whiteboard_anti_cheat_log(whiteboard_id);
CREATE INDEX IF NOT EXISTS idx_wb_acheat_user ON whiteboard_anti_cheat_log(user_id);

-- 7. whiteboard_snapshots
CREATE TABLE IF NOT EXISTS whiteboard_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whiteboard_id UUID NOT NULL REFERENCES whiteboards(id) ON DELETE CASCADE,
    slide_number INT NOT NULL,
    image_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wb_snapshots_whiteboard ON whiteboard_snapshots(whiteboard_id);

-- Add display_settings column for white/black board, grid, log scale
ALTER TABLE whiteboards ADD COLUMN IF NOT EXISTS display_settings JSONB DEFAULT '{"board_mode":"white","grid_enabled":false,"grid_spacing":50,"grid_logarithmic":false}';

-- Add features column to schools for per-school feature toggles (whiteboard, etc.)
ALTER TABLE schools ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '{}';
