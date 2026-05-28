-- Migration 002: Add is_published to exams
ALTER TABLE exams ADD COLUMN IF NOT EXISTS is_published BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_exams_published ON exams(is_published) WHERE is_published = TRUE;
