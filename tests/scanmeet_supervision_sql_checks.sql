-- Isolated EMPTY test database only. Run after 076, 077 and 079 inside BEGIN;
-- ROLLBACK; never a production fixture. Exercise real locks/constraints/RPCs.
DO $$
DECLARE
  school uuid:=gen_random_uuid(); other_school uuid:=gen_random_uuid();
  owner_id uuid:=gen_random_uuid(); admin_id uuid:=gen_random_uuid();
  deputy uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid();
  exam uuid:=gen_random_uuid(); exam2 uuid:=gen_random_uuid(); sid uuid:=gen_random_uuid();
  result jsonb; job uuid; deadline timestamptz; started timestamptz; cap integer;
  allowed boolean; refused boolean; count_used integer; row_session public.scan_live_sessions%ROWTYPE;
BEGIN
  IF EXISTS(SELECT 1 FROM public.schools) OR EXISTS(SELECT 1 FROM public.scan_live_sessions) THEN
    RAISE EXCEPTION 'requires empty isolated database';
  END IF;
  INSERT INTO public.schools(id,name,npsn) VALUES
    (school,'ZZ transactional supervision','00997766'),(other_school,'ZZ other tenant','00997765');
  INSERT INTO auth.users(id,email) SELECT x,x::text||'@example.invalid'
    FROM unnest(ARRAY[owner_id,admin_id,deputy,stranger]) x;
  INSERT INTO public.profiles(id,school_id,role) VALUES
    (owner_id,school,'guru'),(admin_id,school,'admin_sekolah'),
    (deputy,school,'guru'),(stranger,other_school,'admin_sekolah')
    ON CONFLICT(id) DO UPDATE SET school_id=excluded.school_id,role=excluded.role;
  INSERT INTO public.exams(id,school_id,teacher_id,title,scan_live_enabled) VALUES
    (exam,school,owner_id,'ZZ transaction exam',true),(exam2,school,owner_id,'ZZ another exam',true);
  INSERT INTO public.scanmeet_school_entitlements
    (school_id,enabled,status,valid_until,plan_code,session_limit_minutes,participant_limit,session_allowance,concurrent_room_limit)
    VALUES(school,true,'trial',now()+interval '2 hours','trial',45,40,3,1);
  IF public.scanmeet_supervisor_authorized(exam,deputy) THEN RAISE EXCEPTION 'unassigned staff allowed'; END IF;
  -- Unknown/null actors must fail closed, as must other-school administrators.
  FOREACH admin_id IN ARRAY ARRAY[NULL::uuid,stranger,owner_id] LOOP
    refused:=false;
    BEGIN
      PERFORM public.scanmeet_supervisor_assignment(exam,admin_id,deputy,now()+interval '1 hour',0,'absent teacher',false);
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM='scanmeet_supervisor_denied' THEN refused:=true; ELSE RAISE; END IF;
    END;
    IF NOT refused THEN RAISE EXCEPTION 'non-admin assignment succeeded'; END IF;
  END LOOP;
  SELECT id INTO admin_id FROM public.profiles WHERE school_id=school AND role='admin_sekolah';
  result:=public.scanmeet_supervisor_assignment(exam,admin_id,deputy,now()+interval '30 minutes',0,'absent teacher',false);
  IF NOT public.scanmeet_supervisor_authorized(exam,deputy) THEN RAISE EXCEPTION 'assigned staff denied'; END IF;
  refused:=false;
  BEGIN
    PERFORM public.scanmeet_supervisor_assignment(exam,admin_id,deputy,now()+interval '1 hour',0,'stale tab',false);
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_version_conflict' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'stale assignment overwrote a newer grant'; END IF;
  INSERT INTO public.scan_live_sessions(id,school_id,exam_id,created_by,room_name)
    VALUES(sid,school,exam,owner_id,'sg_supervision_old');
  SELECT expires_at,created_at,participant_limit INTO deadline,started,cap FROM public.scan_live_sessions WHERE id=sid;
  result:=public.scanmeet_begin_handover(exam,admin_id,sid,deputy,1,'teacher absent');
  job:=(result->>'handover_id')::uuid;
  IF (public.scanmeet_begin_handover(exam,admin_id,sid,deputy,1,'retry')->>'handover_id')::uuid<>job THEN
    RAISE EXCEPTION 'duplicate request created a second handover';
  END IF;
  SELECT * INTO row_session FROM public.scan_live_sessions WHERE id=sid;
  IF row_session.transfer_state<>'closing' OR row_session.status<>'active' THEN RAISE EXCEPTION 'handover released slot'; END IF;
  refused:=false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name) VALUES(school,exam2,owner_id,'sg_steal_slot');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_school_room_busy' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'handover failed to reserve concurrency'; END IF;
  refused:=false;
  BEGIN
    PERFORM public.scanmeet_finish_handover(job,'wrong-lease');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_claim_lost' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'unclaimed handover completed'; END IF;
  UPDATE public.scan_live_cleanup_jobs SET claim_token='synthetic-lease',claimed_at=clock_timestamp() WHERE handover_id=job;
  PERFORM public.scanmeet_finish_handover(job,'synthetic-lease');
  SELECT * INTO row_session FROM public.scan_live_sessions WHERE id=sid;
  IF row_session.created_by<>deputy OR row_session.media_version<>2 OR row_session.room_name='sg_supervision_old'
    OR row_session.transfer_state<>'ready' OR row_session.created_at<>started OR row_session.participant_limit<>cap
    OR row_session.expires_at>deadline OR row_session.expires_at>now()+interval '30 minutes' THEN
    RAISE EXCEPTION 'transfer changed ownership, quota, cap or deadline incorrectly';
  END IF;
  SELECT sessions_used INTO count_used FROM public.scanmeet_school_entitlements WHERE school_id=school;
  IF count_used<>1 THEN RAISE EXCEPTION 'handover charged an additional session'; END IF;
  PERFORM public.scanmeet_finish_handover(job,'synthetic-lease');
  IF (SELECT media_version FROM public.scan_live_sessions WHERE id=sid)<>2 THEN RAISE EXCEPTION 'retry rotated twice'; END IF;
  result:=public.scanmeet_supervisor_assignment(exam,admin_id,deputy,NULL,1,'permission withdrawn',true);
  job:=(result->>'handover_id')::uuid;
  IF job IS NULL OR public.scanmeet_supervisor_authorized(exam,deputy) THEN RAISE EXCEPTION 'revocation failed'; END IF;
  UPDATE public.scan_live_cleanup_jobs SET claim_token='revoke-lease',claimed_at=clock_timestamp() WHERE handover_id=job;
  PERFORM public.scanmeet_finish_handover(job,'revoke-lease');
  IF (SELECT status FROM public.scan_live_sessions WHERE id=sid)<>'ended' THEN RAISE EXCEPTION 'revoked room remained active'; END IF;
  IF (SELECT count(*) FROM public.scanmeet_supervisor_audit WHERE exam_id=exam)<>2 THEN RAISE EXCEPTION 'audit missing'; END IF;
  -- A revoked delegate cannot create a new session, nor consume quota on failure.
  refused:=false;
  BEGIN
    INSERT INTO public.scan_live_sessions(school_id,exam_id,created_by,room_name)
      VALUES(school,exam,deputy,'sg_revoked_start');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM='scanmeet_supervisor_denied' THEN refused:=true; ELSE RAISE; END IF;
  END;
  IF NOT refused OR (SELECT sessions_used FROM public.scanmeet_school_entitlements WHERE school_id=school)<>1 THEN
    RAISE EXCEPTION 'revoked start succeeded or charged quota';
  END IF;
  -- OFF after deletion is queued must cancel, never resurrect, a room.
  sid:=gen_random_uuid();
  INSERT INTO public.scan_live_sessions(id,school_id,exam_id,created_by,room_name)
    VALUES(sid,school,exam,owner_id,'sg_cancelled_transfer');
  result:=public.scanmeet_begin_handover(exam,admin_id,sid,admin_id,1,'admin stepping in');
  job:=(result->>'handover_id')::uuid;
  UPDATE public.exams SET scan_live_enabled=false WHERE id=exam;
  UPDATE public.scan_live_cleanup_jobs SET claim_token='cancel-lease',claimed_at=clock_timestamp() WHERE handover_id=job;
  result:=public.scanmeet_finish_handover(job,'cancel-lease');
  IF result->>'state'<>'cancelled' OR (SELECT status FROM public.scan_live_sessions WHERE id=sid)<>'ended' THEN
    RAISE EXCEPTION 'OFF did not cancel handover';
  END IF;
  IF has_table_privilege('authenticated','public.scanmeet_exam_supervisors','INSERT')
    OR has_function_privilege('anon','public.scanmeet_begin_handover(uuid,uuid,uuid,uuid,integer,text)','EXECUTE') THEN
    RAISE EXCEPTION 'browser may bypass private administration';
  END IF;
END;
$$;
