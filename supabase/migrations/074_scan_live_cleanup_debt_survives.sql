-- ScanMeet: the cleanup debt outlives the row it was recorded for.
--
-- Migration 073 made the ledger, and the independent review of 421d0e1 found the
-- hole in it: `scan_live_cleanup_jobs.session_id REFERENCES scan_live_sessions(id)
-- ON DELETE CASCADE` (and the school FK likewise) means deleting a session — or a
-- school, or a retention purge — erases a `pending` or `abandoned` job while the
-- LiveKit room it names is still carrying pupil video. The debt is the *only*
-- record that a deletion is owed, so it cannot be a child of the thing it
-- outlives.
--
-- Forward-only: 073 is already applied on staging, so this file changes the
-- constraint rather than editing history.
--
-- Why `SET NULL` and not `RESTRICT`
-- --------------------------------
-- `RESTRICT` would make deleting a session fail while its room is queued, which
-- turns a media problem into a *database* problem: a teacher's Stop would 500,
-- a school could not be closed, and the retention purge would stop on an
-- unrelated row. The debt does not need the parent — `room_name` is what
-- `delete_room` takes — it only needs to survive, and an orphaned job is the
-- clearest possible statement that a person has to look. So the reference is
-- released, the row stays, and `orphaned_at` records when it lost its parent.
--
-- What is deliberately NOT done here
-- ----------------------------------
-- No column is dropped, no row is rewritten, and the unique partial index is
-- untouched, so a box that already applied 073 keeps every job it has. The one
-- behavioural change is that a `pending` job with NULL parents is still
-- deletable — the worker reads `room_name`, never the foreign keys.

-- ── 1. the reference is released, the job is not ─────────────────────────────
ALTER TABLE public.scan_live_cleanup_jobs
  ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE public.scan_live_cleanup_jobs
  ALTER COLUMN school_id DROP NOT NULL;

ALTER TABLE public.scan_live_cleanup_jobs
  DROP CONSTRAINT IF EXISTS scan_live_cleanup_jobs_session_id_fkey;
ALTER TABLE public.scan_live_cleanup_jobs
  DROP CONSTRAINT IF EXISTS scan_live_cleanup_jobs_school_id_fkey;

ALTER TABLE public.scan_live_cleanup_jobs
  ADD CONSTRAINT scan_live_cleanup_jobs_session_id_fkey
  FOREIGN KEY (session_id) REFERENCES public.scan_live_sessions(id)
  ON DELETE SET NULL;
ALTER TABLE public.scan_live_cleanup_jobs
  ADD CONSTRAINT scan_live_cleanup_jobs_school_id_fkey
  FOREIGN KEY (school_id) REFERENCES public.schools(id)
  ON DELETE SET NULL;

-- When the parent went away. Set by the trigger below rather than by the
-- application, because the delete that nulls the reference is exactly the path
-- the application is not on — a retention purge, a school closure, a manual
-- `DELETE` in the SQL editor.
ALTER TABLE public.scan_live_cleanup_jobs
  ADD COLUMN IF NOT EXISTS orphaned_at timestamptz;

CREATE OR REPLACE FUNCTION public.scan_live_cleanup_mark_orphan()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (OLD.session_id IS NOT NULL AND NEW.session_id IS NULL)
     OR (OLD.school_id IS NOT NULL AND NEW.school_id IS NULL) THEN
    NEW.orphaned_at := COALESCE(NEW.orphaned_at, now());
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS scan_live_cleanup_orphan ON public.scan_live_cleanup_jobs;
CREATE TRIGGER scan_live_cleanup_orphan
  BEFORE UPDATE OF session_id, school_id ON public.scan_live_cleanup_jobs
  FOR EACH ROW EXECUTE FUNCTION public.scan_live_cleanup_mark_orphan();

-- An orphan is something a person reads, so it gets the index that makes that
-- list cheap: "every job that lost its parent and is still open".
CREATE INDEX IF NOT EXISTS scan_live_cleanup_orphan_idx
  ON public.scan_live_cleanup_jobs (status, orphaned_at);

-- ── 2. reconciliation gets a candidate state, not a verdict ──────────────────
-- The review's second point about the sweep: one empty room list must not close a
-- live lesson. `missing_since` is when the room was *first* absent, so the park
-- requires an absence that has persisted for `MISSING_GRACE` — see
-- app/services/live_reconcile.py. NULL means "not currently missing".
ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS missing_since timestamptz;
