-- Migration 003: Add is_hidden + retracted status to submissions

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN DEFAULT FALSE;

ALTER TABLE submissions DROP CONSTRAINT IF EXISTS submissions_status_check;
ALTER TABLE submissions ADD CONSTRAINT submissions_status_check
    CHECK (status IN ('draft', 'submitted', 'graded', 'published', 'retracted'));

ALTER TABLE classes ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
ALTER TABLE classes ADD COLUMN IF NOT EXISTS grade_level TEXT;

ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS tz_offset INT DEFAULT 7;

ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_weights JSONB DEFAULT '{}';
