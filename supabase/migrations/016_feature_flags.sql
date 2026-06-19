-- Migration 016: Feature Flags
CREATE TABLE IF NOT EXISTS feature_flags (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default flags
INSERT INTO feature_flags (name, description, enabled) VALUES
    ('omr_scan', 'OMR Scanner via Kamera', true),
    ('ai_grading', 'AI Essay Grading', true),
    ('whiteboard', 'Collaborative Whiteboard', false),
    ('proctoring', 'Proctoring Dashboard Real-Time', true),
    ('cheat_detection', 'Cheat Pattern Detection', true)
ON CONFLICT (name) DO NOTHING;

ALTER TABLE feature_flags ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Super admin can manage feature flags"
    ON feature_flags FOR ALL USING (auth.jwt()->>'role' = 'super_admin');
