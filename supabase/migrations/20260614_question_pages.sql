-- Migration: Add question_pages column for per-question PDF page range
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_pages JSONB DEFAULT '{}';
