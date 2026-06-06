-- Migration 012: Add allow_calculator column to exams table

ALTER TABLE exams ADD COLUMN IF NOT EXISTS allow_calculator boolean DEFAULT false;

COMMENT ON COLUMN exams.allow_calculator IS 'Show scientific calculator during exam';
