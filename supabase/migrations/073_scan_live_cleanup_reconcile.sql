-- ScanMeet: durable room cleanup, and a session state for media that vanished.
--
-- Two failures migration 070 could not survive, both found by the release review:
--
-- 1. `end_room()` was called once. When it raised, the route logged a line and
--    answered `media_cleanup: pending` — a debt with no ledger. The database row
--    still gates new tokens, so no *new* participant could join, but the room kept
--    running for everyone already inside it, and nothing on the box would ever try
--    again. `scan_live_cleanup_jobs` is that ledger: one row per room, retried with
--    an exponential backoff by a worker that survives a restart.
--
-- 2. A session could be `active` while its room no longer existed (LiveKit empties
--    or restarts a room and the row never hears about it). The teacher's Stop button
--    then deletes nothing and reports success. `status` gains `stale` for exactly
--    that mismatch, and `media_state`/`reconciled_at` record what the sweep saw.

-- ── 1. the session can be parked ─────────────────────────────────────────────
-- `stale` is not `ended`: `ended` is a person's decision, `stale` is the system
-- saying the media it promised is gone. Both refuse new join tokens (every route
-- reads `status = 'active'`), and both free the one-active-session index, so a
-- teacher can start a fresh room without the old row being revived underneath.
ALTER TABLE public.scan_live_sessions
  DROP CONSTRAINT IF EXISTS scan_live_sessions_status_check;
ALTER TABLE public.scan_live_sessions
  ADD CONSTRAINT scan_live_sessions_status_check
  CHECK (status IN ('active', 'stale', 'ended'));

-- `unknown` is a session nobody has joined yet: LiveKit has no room because none
-- has been created, and that is not a fault. `live` means the sweep has *seen* the
-- room, so its later absence is the mismatch worth parking. Only `live` → missing
-- may turn a session stale — a probe that has never confirmed the room cannot
-- prove it disappeared.
ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS media_state text NOT NULL DEFAULT 'unknown';
ALTER TABLE public.scan_live_sessions
  DROP CONSTRAINT IF EXISTS scan_live_sessions_media_state_check;
ALTER TABLE public.scan_live_sessions
  ADD CONSTRAINT scan_live_sessions_media_state_check
  CHECK (media_state IN ('unknown', 'live', 'missing'));
ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS reconciled_at timestamptz;
ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS stale_reason text;

-- ── 2. the cleanup ledger ────────────────────────────────────────────────────
-- `session_id` is kept even though the row may be deleted, because the *reason* a
-- room is being deleted outlives the decision to stop using it (an audit needs to
-- answer "why did this room get deleted 40 minutes after the lesson").
CREATE TABLE IF NOT EXISTS public.scan_live_cleanup_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  session_id uuid NOT NULL REFERENCES public.scan_live_sessions(id) ON DELETE CASCADE,
  room_name text NOT NULL,
  reason text NOT NULL DEFAULT 'session_ended',
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'done', 'abandoned')),
  -- `attempts` counts *failures*, not claims: a worker that dies mid-call leaves
  -- the count where it was, so a crash loop cannot exhaust the budget.
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error text,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  -- The claim is a compare-and-swap on this column: a worker writes a token of its
  -- own only if the token it read is still there, so two workers racing for one
  -- room cannot both delete it, and a worker that restarts can pick up a claim
  -- whose lease expired.
  claim_token text NOT NULL DEFAULT '',
  claimed_by text,
  claimed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- One *open* job per room. Enqueuing twice (a retry of the same failed Stop, a
-- sweep that found the same missing room twice) annotates the open row instead of
-- queueing a second deletion; a room that is cleaned, recreated and cleaned again
-- gets a new row because the first one is `done`.
CREATE UNIQUE INDEX IF NOT EXISTS scan_live_one_cleanup_per_room
  ON public.scan_live_cleanup_jobs (session_id, room_name) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS scan_live_cleanup_due_idx
  ON public.scan_live_cleanup_jobs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS scan_live_cleanup_school_idx
  ON public.scan_live_cleanup_jobs (school_id, status);

ALTER TABLE public.scan_live_cleanup_jobs ENABLE ROW LEVEL SECURITY;
-- Same rule as the rest of ScanMeet: the browser has no business here. A pupil or
-- a teacher reading this table would learn which rooms exist after a lesson ended.
REVOKE ALL ON public.scan_live_cleanup_jobs FROM anon, authenticated;
