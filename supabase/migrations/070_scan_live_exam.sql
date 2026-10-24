-- ScanGrade Live Exam Mode: additive, OFF until both environment flags are true.
-- Apply in Supabase SQL editor before enabling any Live session.
CREATE TABLE IF NOT EXISTS public.scan_live_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  exam_id uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  created_by uuid NOT NULL REFERENCES public.profiles(id),
  room_name text NOT NULL UNIQUE,
  policy jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','ended')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS scan_live_one_active_exam
  ON public.scan_live_sessions (school_id,exam_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS scan_live_session_school_idx
  ON public.scan_live_sessions (school_id,status);
CREATE TABLE IF NOT EXISTS public.scan_live_hand_raises (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES public.scan_live_sessions(id) ON DELETE CASCADE,
  school_id uuid NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','approving','approved','closing','rejected','closed')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS scan_live_one_open_request
  ON public.scan_live_hand_raises(session_id,student_id)
  WHERE status IN ('pending','approving','approved','closing');
CREATE INDEX IF NOT EXISTS scan_live_queue_idx
  ON public.scan_live_hand_raises(school_id,session_id,status,created_at);
ALTER TABLE public.scan_live_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scan_live_hand_raises ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scan_live_sessions FROM anon, authenticated;
REVOKE ALL ON public.scan_live_hand_raises FROM anon, authenticated;
-- Only the backend service-role connection can access these tables.
-- Never expose this service key to browser or LiveKit.
