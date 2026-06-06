-- Migration 011: Anti-cheat settings columns on exams table
-- Adds: anti_cheat_enabled, penalty_per_violation, max_violations, auto_submit_on_max,
--        fullscreen_required, randomize_questions, randomize_options, watermark_name,
--        block_copy_paste, block_right_click, block_screenshot

ALTER TABLE exams ADD COLUMN IF NOT EXISTS anti_cheat_enabled boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS penalty_per_violation integer DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS max_violations integer DEFAULT 5;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS auto_submit_on_max boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS fullscreen_required boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_questions boolean DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS randomize_options boolean DEFAULT false;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS watermark_name boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_copy_paste boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_right_click boolean DEFAULT true;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS block_screenshot boolean DEFAULT false;

COMMENT ON COLUMN exams.anti_cheat_enabled IS 'Enable/disable anti-cheat for this exam';
COMMENT ON COLUMN exams.penalty_per_violation IS 'Points deducted per tab-switch violation (graduated scale)';
COMMENT ON COLUMN exams.max_violations IS 'Max violations before auto-submit (0 = unlimited)';
COMMENT ON COLUMN exams.auto_submit_on_max IS 'Auto-submit exam when max violations reached';
COMMENT ON COLUMN exams.fullscreen_required IS 'Require fullscreen mode during exam';
COMMENT ON COLUMN exams.randomize_questions IS 'Randomize question order per student';
COMMENT ON COLUMN exams.randomize_options IS 'Randomize MCQ option order (A/B/C/D/E) per student';
COMMENT ON COLUMN exams.watermark_name IS 'Show student name watermark overlay on exam pages';
COMMENT ON COLUMN exams.block_copy_paste IS 'Block copy/paste in essay textareas';
COMMENT ON COLUMN exams.block_right_click IS 'Block right-click context menu during exam';
COMMENT ON COLUMN exams.block_screenshot IS 'Attempt to block screenshots (PrintScreen key)';
