-- Additive ScanMeet packages. Existing custom licenses and exam answers survive.
ALTER TABLE public.scanmeet_school_entitlements
  ADD COLUMN IF NOT EXISTS plan_code text,
  ADD COLUMN IF NOT EXISTS session_limit_minutes integer CHECK (session_limit_minutes > 0),
  ADD COLUMN IF NOT EXISTS participant_limit integer CHECK (participant_limit > 1),
  ADD COLUMN IF NOT EXISTS session_allowance integer CHECK (session_allowance > 0),
  ADD COLUMN IF NOT EXISTS sessions_used integer NOT NULL DEFAULT 0 CHECK (sessions_used >= 0),
  ADD COLUMN IF NOT EXISTS trial_started_at timestamptz,
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;
UPDATE public.scanmeet_school_entitlements
  SET trial_started_at = COALESCE(approved_at, updated_at, now())
  WHERE status = 'trial' AND trial_started_at IS NULL;

ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS participant_limit integer CHECK (participant_limit > 1);

-- One reservation per successfully inserted room. The row lock serializes
-- simultaneous starts for one school. A failed unique-room INSERT rolls the
-- counter back in the same transaction. Reusing an existing room spends nothing.
CREATE OR REPLACE FUNCTION public.scanmeet_reserve_package_session()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
DECLARE license public.scanmeet_school_entitlements%ROWTYPE;
BEGIN
  SELECT * INTO license FROM public.scanmeet_school_entitlements
    WHERE school_id = NEW.school_id FOR UPDATE;
  IF license.plan_code IS NOT NULL THEN
    IF NOT license.enabled OR license.status NOT IN ('trial','paid')
       OR license.archived_at IS NOT NULL OR license.valid_until IS NULL
       OR license.valid_until <= now() THEN
      RAISE EXCEPTION 'scanmeet_license_inactive';
    END IF;
    IF license.session_limit_minutes IS NULL OR license.participant_limit IS NULL
       OR license.session_allowance IS NULL THEN
      RAISE EXCEPTION 'scanmeet_package_incomplete';
    END IF;
    IF license.sessions_used >= license.session_allowance THEN
      RAISE EXCEPTION 'scanmeet_session_allowance_exhausted';
    END IF;
    NEW.created_at := now();
    NEW.expires_at := LEAST(license.valid_until,
      now() + make_interval(mins => license.session_limit_minutes));
    NEW.participant_limit := license.participant_limit;
    UPDATE public.scanmeet_school_entitlements
      SET sessions_used = sessions_used + 1, updated_at = clock_timestamp()
      WHERE school_id = NEW.school_id;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.scanmeet_reserve_package_session() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.scanmeet_reserve_package_session() TO service_role;
DROP TRIGGER IF EXISTS scanmeet_reserve_package ON public.scan_live_sessions;
CREATE TRIGGER scanmeet_reserve_package BEFORE INSERT ON public.scan_live_sessions
  FOR EACH ROW EXECUTE FUNCTION public.scanmeet_reserve_package_session();
CREATE INDEX IF NOT EXISTS scan_live_session_expiry_idx
  ON public.scan_live_sessions (expires_at) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS public.scanmeet_plan_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  requester_id uuid NOT NULL REFERENCES public.profiles(id),
  plan_code text NOT NULL CHECK (plan_code IN ('trial','kelas','sekolah')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  note text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS scanmeet_one_pending_school_request
  ON public.scanmeet_plan_requests (school_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS scanmeet_requests_school_created_idx
  ON public.scanmeet_plan_requests (school_id, created_at);
CREATE INDEX IF NOT EXISTS scanmeet_requests_requester_idx
  ON public.scanmeet_plan_requests (requester_id);
ALTER TABLE public.scanmeet_plan_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scanmeet_plan_requests FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.scanmeet_plan_requests TO service_role;
COMMENT ON TABLE public.scanmeet_plan_requests IS
  'School-scoped ScanMeet requests. Requests never charge a user or activate media; Super Admin approval is required.';
NOTIFY pgrst, 'reload schema';
