-- Migration 027: the columns and views the live database has and no migration wrote
--
-- Found by `python deploy/schema_contract.py --live`, which compares what the code
-- names against what the repository's SQL declares *and* against what PostgREST
-- actually serves. Four columns and two views exist in production and in no file
-- here: they were added by hand in the SQL Editor, and a database rebuilt from
-- this repository would be missing them.
--
-- Each of these is reached by live code, so the difference is not cosmetic:
--
--   profiles.consent_at        written on registration (UU PDP consent stamp)
--   profiles.pdp_agreed        written when a pupil accepts the privacy terms
--   school_settings.demo_settings  read by `/super-admin/demo-settings` and by the
--                              `get_demo_settings()` Jinja global on every page
--   schools.email_domain       read to offer a school's own mail domain
--
-- A `SELECT` naming a column that does not exist is not a `None`: PostgREST
-- refuses the whole request with 42703, and a route that wraps its query in
-- `try/except` renders an empty page instead of an error.
--
-- Idempotent, like every migration here — safe to paste twice.

-- ============================================
-- 1. Profiles: the two consent stamps
-- ============================================
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS pdp_agreed BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN profiles.consent_at IS
    'When the account holder accepted the privacy notice (UU PDP 27/2022).';
COMMENT ON COLUMN profiles.pdp_agreed IS
    'Whether the pupil accepted the assessment data terms before an exam.';

-- ============================================
-- 2. school_settings: the demo-mode blob
-- ============================================
-- Single-row table (INT id = 1), still read alongside `schools` (UUID id).
ALTER TABLE school_settings ADD COLUMN IF NOT EXISTS demo_settings JSONB;

COMMENT ON COLUMN school_settings.demo_settings IS
    'Free-text demo credentials shown on /demo; read by get_demo_settings().';

-- ============================================
-- 3. schools: the signup mail domain
-- ============================================
ALTER TABLE schools ADD COLUMN IF NOT EXISTS email_domain TEXT DEFAULT '';

COMMENT ON COLUMN schools.email_domain IS
    'Domain used to suggest accounts for this school, e.g. smpn1.sch.id.';

-- ============================================
-- 4. The two views the API serves
-- ============================================
-- PostgREST serves a view exactly like a table, so the code can (and does) read
-- it — `active_school_years` is the "is there a year open" lookup and
-- `class_student_counts` the roster size per class. Written with an explicit
-- column list so a reader can see the shape without reading the SELECT.
--
-- `security_invoker` is on purpose: without it a view runs as its owner and
-- would hand every school every other school's roster, which is the one thing
-- the RLS policies below the views exist to prevent.
CREATE OR REPLACE VIEW active_school_years
    (id, school_id, name, start_date, end_date, is_active, created_at, updated_at)
WITH (security_invoker = true) AS
SELECT id, school_id, name, start_date, end_date, is_active, created_at, updated_at
FROM school_years
WHERE is_active = TRUE;

CREATE OR REPLACE VIEW class_student_counts
    (class_id, class_name, school_id, student_count)
WITH (security_invoker = true) AS
SELECT c.id              AS class_id,
       c.name            AS class_name,
       c.school_id       AS school_id,
       COUNT(s.id)       AS student_count
FROM classes c
LEFT JOIN students s ON s.class_id = c.id
GROUP BY c.id, c.name, c.school_id;

-- ============================================
-- 4b. question_embeddings: the column the AI grader actually reads
-- ============================================
-- Migration 014 declares `diagram_context` and `question_type` on this table. The
-- live database also carries `context`, which is the name the grading path queries,
-- so a database rebuilt from 014 would answer the grader with 42703.
ALTER TABLE question_embeddings ADD COLUMN IF NOT EXISTS context TEXT;

COMMENT ON COLUMN question_embeddings.context IS
    'The question text embedded alongside the answer, as sent to the model.';

-- ============================================
-- 5. One more object the live API serves and no file here writes
-- ============================================
-- `school-payment-demo` (id BIGINT, created_at) is a leftover from the Midtrans demo
-- screens. Nothing in this repository names it; it is declared here so a database
-- rebuilt from these files has the same API surface as production, and so the
-- contract check has nothing left to report as an unexplained difference.
CREATE TABLE IF NOT EXISTS "school-payment-demo" (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- The two views run with the caller's privileges, not their owner's — see 028 for
-- why, and 028 is what switches `security_invoker` on. Without it a view hands every
-- school every other school's rows, because RLS on the tables underneath is skipped.

