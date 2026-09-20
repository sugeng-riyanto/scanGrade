-- ──────────────────────────────────────────────
-- Migration 031: a shareable link to one exam's item analysis
-- ──────────────────────────────────────────────
-- Jalankan di Supabase SQL Editor (atau lewat deploy/apply_migration.py).
--
-- A teacher who wants a school's curriculum lead, a colleague or a parent to
-- read what a paper measured needs a link that works without an account on this
-- box. The link *is* the row: it can be revoked with one UPDATE, it expires on
-- its own date, and every open is counted — which is the difference between "I
-- sent it" and "somebody read it".
--
-- The token is stored as it is, and that is a decision rather than an oversight.
-- Hashing it would mean the only moment a teacher can see their own link is the
-- moment it is created; reload the page and it is gone, and a link nobody can
-- find again is a link they replace with something worse. The compensating
-- controls are the ones that matter: the table is reachable **only** with the
-- service key (RLS below, and no policy for anybody else, so an anonymous client
-- carrying the public key reads nothing), the token is 32 random bytes —
-- `secrets.token_urlsafe(32)`, 256 bits, unguessable to a person and to a
-- scanner — and revocation is immediate rather than eventual.
--
-- The report behind the link is redacted in the application, not here: a shared
-- page carries the statistics and never the answer key or a student's name. That
-- rule lives in `app/routes/public.py` where it can be tested; this table only
-- decides *who may look*, never *what they see*.
--
-- Idempotent: safe to run twice.

CREATE TABLE IF NOT EXISTS analysis_share_links (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  exam_id         UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  token           TEXT NOT NULL UNIQUE,
  created_by      UUID REFERENCES profiles(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- NULL = no expiry. A dated link is the default the application chooses; the
  -- column allows "until I turn it off" without a schema change.
  expires_at      TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  views           INT NOT NULL DEFAULT 0,
  last_viewed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_analysis_share_exam ON analysis_share_links(exam_id);
CREATE INDEX IF NOT EXISTS idx_analysis_share_token ON analysis_share_links(token);

ALTER TABLE analysis_share_links ENABLE ROW LEVEL SECURITY;

-- The application reaches it with the service key, which bypasses RLS. Nobody
-- else may read or write it: this row is the access, so a policy that opened it
-- to a session or to `anon` would hand out every shared report on the box.
DROP POLICY IF EXISTS analysis_share_links_service_only ON analysis_share_links;
CREATE POLICY analysis_share_links_service_only ON analysis_share_links
  FOR ALL USING (auth.role() = 'service_role');
