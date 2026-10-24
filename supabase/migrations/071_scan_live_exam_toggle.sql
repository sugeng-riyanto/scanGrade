-- ScanMeet per-examination switch. Additive and OFF by default.
--
-- Migration 070 made the Live room possible; it did not record the teacher's
-- decision per paper, so "ScanMeet is on" could only be inferred from an active
-- session and could not survive the room being recreated. This column is that
-- decision, and it is the value the join routes read before issuing a token.
--
-- Rollback (safe: nothing reads the column once the release is reverted):
--   ALTER TABLE public.exams DROP COLUMN IF EXISTS scan_live_enabled;
--
-- Apply in the Supabase SQL editor before enabling ScanMeet on any paper.
ALTER TABLE public.exams
  ADD COLUMN IF NOT EXISTS scan_live_enabled boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.exams.scan_live_enabled IS
  'Teacher per-exam ScanMeet switch. Advisory to nothing: the API refuses join '
  'tokens while this is false, and the two SCANGRADE_LIVE_* environment flags '
  'remain deployment-level kill switches.';
