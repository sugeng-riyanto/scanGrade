-- ──────────────────────────────────────────────
-- Migration 014: AI Essay Grading — Tables
-- ──────────────────────────────────────────────
-- Jalankan di Supabase SQL Editor
-- ──────────────────────────────────────────────

-- 1. Question embeddings & essay context
CREATE TABLE IF NOT EXISTS question_embeddings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  question_index INT NOT NULL,
  question_text TEXT NOT NULL,
  question_type TEXT DEFAULT 'mcq',
  rubric JSONB DEFAULT '[]',
  diagram_context TEXT DEFAULT '',
  embedding JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(exam_id, question_index)
);

-- 2. AI grading cache
CREATE TABLE IF NOT EXISTS ai_grading_cache (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  question_index INT NOT NULL,
  ai_score NUMERIC(5,2),
  ai_feedback TEXT,
  ai_provider VARCHAR(20),
  tokens_used INT DEFAULT 0,
  model_used VARCHAR(50),
  prompt_sent TEXT,
  raw_response TEXT,
  teacher_overridden BOOLEAN DEFAULT FALSE,
  teacher_score NUMERIC(5,2),
  teacher_feedback TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(submission_id, question_index)
);

-- 3. Indexes
CREATE INDEX IF NOT EXISTS idx_question_embeddings_exam
  ON question_embeddings(exam_id);

CREATE INDEX IF NOT EXISTS idx_ai_grading_cache_submission
  ON ai_grading_cache(submission_id);

CREATE INDEX IF NOT EXISTS idx_ai_grading_cache_created
  ON ai_grading_cache(created_at DESC);

-- 4. Add question_texts column to exams (for essay question text storage)
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_texts JSONB DEFAULT '{}';

-- 5. Add question_rubrics column to exams (for generated rubrics)
ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_rubrics JSONB DEFAULT '{}';

-- 6. Add diagram_contexts column to exams (for diagram descriptions)
ALTER TABLE exams ADD COLUMN IF NOT EXISTS diagram_contexts JSONB DEFAULT '{}';
