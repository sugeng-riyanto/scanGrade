-- Run after scanmeet_package_sql_checks.sql on the isolated empty test project,
-- in the same transaction that is ROLLED BACK. Never production fixture data.
DO $$
DECLARE
  school uuid;
  teacher uuid;
  exams uuid[];
  first_session uuid := gen_random_uuid();
  second_session uuid := gen_random_uuid();
  used integer;
  cap integer;
  deadline timestamptz;
  refused boolean;
BEGIN
  SELECT id INTO STRICT school FROM public.schools WHERE npsn='00998877';
  SELECT array_agg(id ORDER BY id), min(teacher_id::text)::uuid INTO exams,teacher
    FROM public.exams WHERE school_id=school;
  IF array_length(exams,1) <> 3 THEN RAISE EXCEPTION 'membership fixture missing'; END IF;
  UPDATE public.scan_live_sessions SET status='ended' WHERE school_id=school;
  UPDATE public.scanmeet_school_entitlements SET enabled=true,status='paid',
    plan_code='kelas',valid_until=now()+interval '30 days',session_limit_minutes=120,
    participant_limit=40,session_allowance=NULL,sessions_used=100000,concurrent_room_limit=NULL
    WHERE school_id=school;
  refused := false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exams[1],teacher,'sg_member_missing_marker');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_package_incomplete' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'missing allowance without membership marker was accepted'; END IF;
  UPDATE public.scanmeet_school_entitlements SET concurrent_room_limit=1 WHERE school_id=school;
  INSERT INTO public.scan_live_sessions(id,school_id,exam_id,created_by,room_name)
    VALUES(first_session,school,exams[1],teacher,'sg_member_first');
  SELECT sessions_used INTO used FROM public.scanmeet_school_entitlements WHERE school_id=school;
  SELECT participant_limit,expires_at INTO cap,deadline FROM public.scan_live_sessions WHERE id=first_session;
  IF used <> 100001 OR cap <> 40 OR deadline < now()+interval '119 minutes'
     OR deadline > clock_timestamp()+interval '120 minutes' THEN
    RAISE EXCEPTION 'membership limits were not applied';
  END IF;
  refused := false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exams[2],teacher,'sg_member_simultaneous');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_school_room_busy' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'second active class was accepted'; END IF;
  SELECT sessions_used INTO used FROM public.scanmeet_school_entitlements WHERE school_id=school;
  IF used <> 100001 THEN RAISE EXCEPTION 'refused room changed usage'; END IF;
  UPDATE public.scan_live_sessions SET expires_at=clock_timestamp()-interval '1 second'
    WHERE id=first_session;
  INSERT INTO public.scan_live_sessions(id,school_id,exam_id,created_by,room_name)
    VALUES(second_session,school,exams[2],teacher,'sg_member_after_expiry');
  UPDATE public.scan_live_sessions SET status='ended' WHERE id=second_session;
  UPDATE public.scanmeet_school_entitlements SET status='trial',plan_code='trial' WHERE school_id=school;
  refused := false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exams[3],teacher,'sg_member_unlimited_trial');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_package_incomplete' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'trial was allowed without finite allowance'; END IF;
  IF (SELECT prosecdef FROM pg_proc WHERE oid='public.scanmeet_reserve_package_session()'::regprocedure) THEN
    RAISE EXCEPTION 'reservation function must remain security invoker';
  END IF;
END;
$$;
