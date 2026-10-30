-- Run ONLY on an isolated empty test database, after the migration, inside a
-- transaction that is ROLLED BACK. No production fixture data or credentials.
DO $$
DECLARE
  school uuid := gen_random_uuid();
  teacher uuid := gen_random_uuid();
  exam1 uuid := gen_random_uuid();
  exam2 uuid := gen_random_uuid();
  exam3 uuid := gen_random_uuid();
  session1 uuid := gen_random_uuid();
  used integer;
  cap integer;
  deadline timestamptz;
  refused boolean := false;
BEGIN
  IF EXISTS (SELECT 1 FROM public.schools) THEN
    RAISE EXCEPTION 'SQL package fixture requires an empty isolated database';
  END IF;
  INSERT INTO public.schools(id,name,npsn) VALUES(school,'ZZ ScanMeet transaction-only test','00998877');
  INSERT INTO auth.users(id,email) VALUES(teacher,teacher::text || '@example.invalid');
  INSERT INTO public.profiles(id,school_id,role) VALUES(teacher,school,'guru')
    ON CONFLICT(id) DO UPDATE SET school_id=excluded.school_id,role=excluded.role;
  INSERT INTO public.exams(id,school_id,teacher_id,title)
    VALUES(exam1,school,teacher,'transaction-only 1'),
          (exam2,school,teacher,'transaction-only 2'),
          (exam3,school,teacher,'transaction-only 3');
  INSERT INTO public.scanmeet_school_entitlements
    (school_id,enabled,status,valid_until,plan_code,session_limit_minutes,participant_limit,session_allowance)
    VALUES(school,true,'trial',now()+interval '10 minutes','trial',30,5,2);
  INSERT INTO public.scan_live_sessions(id,school_id,exam_id,created_by,room_name)
    VALUES(session1,school,exam1,teacher,'sg_transaction_test_1');
  SELECT sessions_used INTO used FROM public.scanmeet_school_entitlements WHERE school_id=school;
  SELECT participant_limit,expires_at INTO cap,deadline FROM public.scan_live_sessions WHERE id=session1;
  IF used <> 1 OR cap <> 5 OR deadline <> now()+interval '10 minutes' THEN
    RAISE EXCEPTION 'reservation did not apply quota, capacity and license deadline';
  END IF;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exam1,teacher,'sg_transaction_duplicate');
    RAISE EXCEPTION 'duplicate active exam unexpectedly succeeded';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  SELECT sessions_used INTO used FROM public.scanmeet_school_entitlements WHERE school_id=school;
  IF used <> 1 THEN RAISE EXCEPTION 'failed insert charged the allowance'; END IF;
  INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
    VALUES(school,exam2,teacher,'sg_transaction_test_2');
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exam3,teacher,'sg_transaction_over_quota');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'scanmeet_session_allowance_exhausted' THEN refused := true;
    ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'allowance exhaustion was not enforced'; END IF;
  UPDATE public.scanmeet_school_entitlements SET enabled=false,status='disabled' WHERE school_id=school;
  refused := false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exam3,teacher,'sg_transaction_disabled');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'scanmeet_license_inactive' THEN refused := true;
    ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'disabled license was not enforced'; END IF;
  IF has_table_privilege('anon','public.scanmeet_plan_requests','SELECT')
     OR has_table_privilege('authenticated','public.scanmeet_plan_requests','INSERT')
     OR has_function_privilege('anon','public.scanmeet_reserve_package_session()','EXECUTE') THEN
    RAISE EXCEPTION 'a browser role can access package administration';
  END IF;
END;
$$;
