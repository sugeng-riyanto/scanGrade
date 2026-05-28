-- Migration 005: Add audio and canvas support per question
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_audio JSONB DEFAULT '{}';
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_canvas JSONB DEFAULT '{}';
