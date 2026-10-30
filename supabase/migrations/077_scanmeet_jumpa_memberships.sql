-- Monthly/yearly memberships at Jumpa Guru Pro prices; legacy grants survive.
-- NULL means an older contract retains its previous concurrency terms.
ALTER TABLE public.scanmeet_school_entitlements
  ADD COLUMN IF NOT EXISTS concurrent_room_limit integer CHECK (concurrent_room_limit > 0);
COMMENT ON COLUMN public.scanmeet_school_entitlements.concurrent_room_limit IS
  'NULL preserves legacy contracts. New memberships reserve one active classroom per school.';

CREATE OR REPLACE FUNCTION public.scanmeet_reserve_package_session()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
  license public.scanmeet_school_entitlements%ROWTYPE;
  observed_at timestamptz;
BEGIN
  SELECT * INTO license FROM public.scanmeet_school_entitlements
    WHERE school_id = NEW.school_id FOR UPDATE;
  observed_at := clock_timestamp();
  IF license.plan_code IS NOT NULL THEN
    IF NOT license.enabled OR license.status NOT IN ('trial','paid')
       OR license.archived_at IS NOT NULL OR license.valid_until IS NULL
       OR license.valid_until <= observed_at THEN
      RAISE EXCEPTION 'scanmeet_license_inactive';
    END IF;
    IF license.session_limit_minutes IS NULL OR license.participant_limit IS NULL
       OR (license.session_allowance IS NULL AND NOT (
         license.status = 'paid' AND license.plan_code IN ('kelas','sekolah')
         AND COALESCE(license.concurrent_room_limit,0) = 1)) THEN
      RAISE EXCEPTION 'scanmeet_package_incomplete';
    END IF;
    IF license.session_allowance IS NOT NULL
       AND license.sessions_used >= license.session_allowance THEN
      RAISE EXCEPTION 'scanmeet_session_allowance_exhausted';
    END IF;
    -- The license row lock serializes different exams in one school as well.
    -- NULL preserves the simultaneous-room terms of older finite contracts.
    IF license.concurrent_room_limit IS NOT NULL AND (
      SELECT count(*) FROM public.scan_live_sessions
      WHERE school_id = NEW.school_id AND status = 'active'
        AND (expires_at IS NULL OR expires_at > observed_at)
    ) >= license.concurrent_room_limit THEN
      RAISE EXCEPTION 'scanmeet_school_room_busy';
    END IF;
    NEW.created_at := observed_at;
    NEW.expires_at := LEAST(license.valid_until,
      observed_at + make_interval(mins => license.session_limit_minutes));
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
NOTIFY pgrst, 'reload schema';
