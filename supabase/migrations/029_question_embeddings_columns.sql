-- ============================================================
-- Migration 029 — the two columns `question_embeddings` is written with
-- ============================================================
--
-- Migration 014 creates this table with `CREATE TABLE IF NOT EXISTS`, and in
-- production the table already existed — so the statement was a no-op and every
-- column 014 added to a *pre-existing* table was never created. Two of them matter:
--
--   question_type     written by `ai_embedding.preprocess_exam_questions()`
--   diagram_context   read by the AI grader's prompt builder
--
-- Measured against production (PostgREST, explicit select):
--
--   select=question_type    -> 400 42703  column question_embeddings.question_type
--   select=diagram_context  -> 400 42703
--   select=context          -> 200        (added by migration 027)
--   select=rubric           -> 200
--   select=embedding        -> 200
--
-- The consequence is the quiet kind. `preprocess_exam_questions()` upserts a row
-- containing `question_type`, the request is refused with 42703, and the surrounding
-- `except Exception` logs a line and moves on — so **every essay question's embedding
-- failed to save**, the AI grading path had nothing to match against, and nothing in
-- the UI said so. The upsert is the only writer of this table.
--
-- `ADD COLUMN IF NOT EXISTS` with the defaults 014 declared, so a database rebuilt
-- from this repository and the database in use finally agree.

ALTER TABLE question_embeddings
    ADD COLUMN IF NOT EXISTS question_type TEXT DEFAULT 'mcq';

ALTER TABLE question_embeddings
    ADD COLUMN IF NOT EXISTS diagram_context TEXT DEFAULT '';

COMMENT ON COLUMN question_embeddings.question_type IS
    'The kind of question this text was embedded from — written by the upsert in ai_embedding.preprocess_exam_questions().';

COMMENT ON COLUMN question_embeddings.diagram_context IS
    'A description of any figure the question refers to, prepended to the grading prompt.';
