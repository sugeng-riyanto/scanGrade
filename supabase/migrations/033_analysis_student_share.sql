-- ──────────────────────────────────────────────
-- Migration 033: a shareable link to one learner's report
-- ──────────────────────────────────────────────
-- Jalankan di Supabase SQL Editor (atau lewat deploy/apply_migration.py).
--
-- The class link (`031_analysis_share_links.sql`) answers "what did this paper
-- do?". A parent meeting asks a different question — "how did *my child* do, and
-- what should we work on" — and the answer is one learner's page, not the class
-- table with one row highlighted.
--
-- It is the same table and the same mechanism on purpose. **A link is a row**, and
-- the only thing that changes is what the row is about:
--
--   student_id IS NULL      the exam's report (existing rows, unchanged)
--   student_id = <profile>  that one learner's report
--
-- A second table would have duplicated the token, the expiry, the revoke and the
-- open counter — four things that then drift, and the drift is invisible until a
-- link that should be dead still opens. One column instead, and `analysis_share`
-- filters on it so a learner's link can never be returned as "the exam's link".
--
-- The redaction rule does not move: what a stranger may see is decided in the
-- application (`app/routes/public.py`), and an individual link carries that
-- learner's own page — their name, their questions, their marks — with no answer
-- key and no other learner's name anywhere on it.
--
-- Idempotent: safe to run twice.

ALTER TABLE analysis_share_links
  ADD COLUMN IF NOT EXISTS student_id UUID REFERENCES profiles(id) ON DELETE CASCADE;

-- The lookup is always "the live link for this scope": the exam alone, or the
-- exam and one learner. Both are covered by an index that leads with the exam.
CREATE INDEX IF NOT EXISTS idx_analysis_share_student
  ON analysis_share_links(exam_id, student_id);
