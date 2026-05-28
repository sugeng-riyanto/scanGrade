-- Migration 006: Teacher feedback per question (scores + comments)
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS teacher_feedback JSONB DEFAULT '{}';
