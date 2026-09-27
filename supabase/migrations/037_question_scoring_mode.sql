-- ──────────────────────────────────────────────────────────────
-- Migration 037: how each question is marked (Pilihan Ganda Kompleks)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (uji rollback dulu), atau tempel di
-- Supabase SQL Editor.
--
-- A complex multiple choice question asks a pupil to judge several statements, and
-- the Kemendikbud AKM guidance prices that in a way no existing rule expresses: a
-- binary mark across a 3-5 statement, two-category question, and a 2/1/0 ladder
-- outside that band. The teacher chooses which of the three rules applies, *per
-- question*, because the right one depends on the question they wrote rather than
-- on the paper.
--
-- Shape: `{"3": "akm_standard", "7": "proportional"}` — the question *index* as
-- text, the same keying `question_types`, `question_weights`, `answer_key` and
-- `question_cognitive` already use, to one of three mode names. Absent means the
-- question was written before this column existed, or the teacher never chose, and
-- both read as the default (`akm_standard`) in `app/services/question_types.py`.
--
-- JSONB and not a table, for the reason migration 032 already sets out: this is a
-- fact about one question, read in the same query as the exam, and never queried
-- on its own. A sibling table would add a join to every scoring site to hold one
-- column of a document the teacher edits on one page.
--
-- Why a function and not a plain CHECK: PostgreSQL has no `CHECK` that can reach
-- *inside* a jsonb map, and a subquery is not allowed in a constraint. So the
-- vocabulary is enforced by an IMMUTABLE function — which PostgreSQL does permit
-- in a CHECK — instead of by leaving the column unconstrained. The three names
-- below are the same three `question_types.SCORING_MODES` holds, and
-- `tests/unit/test_pgk_scoring.py` fails if the two drift apart, because a mode
-- the database accepts but the grader does not know is a marking rule that
-- silently does nothing.
--
-- Idempotent: safe to run twice, and additive — no exam carries a PGK yet, so
-- every existing paper keeps an empty map and is marked exactly as it was.
--
-- No backfill, deliberately: inventing a marking mode for a question nobody asked
-- about would write a marking rule no teacher chose.

CREATE OR REPLACE FUNCTION question_scoring_valid(modes JSONB)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT modes IS NULL OR CASE
        -- `jsonb_each_text` raises on a non-object, so an array or a scalar pasted
        -- into the column would make the constraint *error* rather than refuse.
        -- CASE and not AND, because SQL does not promise to evaluate the type test
        -- before the function that depends on it.
        WHEN jsonb_typeof(modes) <> 'object' THEN FALSE
        ELSE NOT EXISTS (
            SELECT 1
            FROM jsonb_each_text(modes) AS entry(question_index, mode)
            WHERE entry.mode NOT IN ('akm_standard', 'proportional', 'all_or_nothing')
        )
    END;
$$;

ALTER TABLE exams ADD COLUMN IF NOT EXISTS question_scoring JSONB DEFAULT '{}';

-- Dropped and re-added so a second run replaces the definition rather than failing
-- on the name; the function above is replaced in place for the same reason.
ALTER TABLE exams DROP CONSTRAINT IF EXISTS exams_question_scoring_valid;
ALTER TABLE exams ADD CONSTRAINT exams_question_scoring_valid
    CHECK (question_scoring_valid(question_scoring));
