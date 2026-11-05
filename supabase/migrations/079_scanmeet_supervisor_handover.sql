-- Additive, default-OFF supervision assignments and durable room rotation.
-- Apply manually after 076/077/078; no exam answer, timer or score is changed.
CREATE TABLE IF NOT EXISTS public.scanmeet_exam_supervisors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES public.schools(id) ON DELETE CASCADE,
  exam_id uuid NOT NULL REFERENCES public.exams(id) ON DELETE CASCADE,
  supervisor_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  granted_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  valid_until timestamptz NOT NULL,
  revoked_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (exam_id,supervisor_id)
);
CREATE INDEX IF NOT EXISTS scanmeet_supervisor_lookup
  ON public.scanmeet_exam_supervisors(school_id,supervisor_id,exam_id,valid_until)
  WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS public.scanmeet_supervisor_audit (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid REFERENCES public.schools(id) ON DELETE SET NULL,
  exam_id uuid REFERENCES public.exams(id) ON DELETE SET NULL,
  actor_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  action text NOT NULL CHECK (action IN ('grant','revoke')),
  reason text NOT NULL,
  before_data jsonb,
  after_data jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS scanmeet_supervisor_audit_scope
  ON public.scanmeet_supervisor_audit(school_id,exam_id,created_at);
CREATE TABLE IF NOT EXISTS public.scanmeet_handover_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid REFERENCES public.schools(id) ON DELETE SET NULL,
  exam_id uuid REFERENCES public.exams(id) ON DELETE SET NULL,
  session_id uuid REFERENCES public.scan_live_sessions(id) ON DELETE SET NULL,
  actor_id uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  from_user uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  to_user uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  from_room text NOT NULL,
  to_room text NOT NULL UNIQUE,
  from_version integer NOT NULL CHECK (from_version > 0),
  operation text NOT NULL CHECK (operation IN ('transfer','revoke')),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','completed','cancelled')),
  reason text NOT NULL,
  outcome text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS scanmeet_one_pending_handover
  ON public.scanmeet_handover_jobs(session_id) WHERE state='pending';
CREATE INDEX IF NOT EXISTS scanmeet_handover_scope
  ON public.scanmeet_handover_jobs(school_id,exam_id,created_at);
ALTER TABLE public.scan_live_sessions
  ADD COLUMN IF NOT EXISTS media_version integer NOT NULL DEFAULT 1 CHECK (media_version > 0),
  ADD COLUMN IF NOT EXISTS transfer_state text NOT NULL DEFAULT 'ready' CHECK (transfer_state IN ('ready','closing'));
ALTER TABLE public.scan_live_cleanup_jobs ADD COLUMN IF NOT EXISTS handover_id uuid
  REFERENCES public.scanmeet_handover_jobs(id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX IF NOT EXISTS scanmeet_handover_cleanup_link
  ON public.scan_live_cleanup_jobs(handover_id) WHERE handover_id IS NOT NULL;

ALTER TABLE public.scanmeet_exam_supervisors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanmeet_supervisor_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanmeet_handover_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.scanmeet_exam_supervisors,public.scanmeet_supervisor_audit,public.scanmeet_handover_jobs
  FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.scanmeet_exam_supervisors,public.scanmeet_supervisor_audit,public.scanmeet_handover_jobs
  TO service_role;

-- Private backend predicate; a delegated proctor gains media rights only.
CREATE OR REPLACE FUNCTION public.scanmeet_supervisor_authorized(p_exam uuid,p_user uuid)
RETURNS boolean LANGUAGE sql SECURITY INVOKER SET search_path=public AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.exams e JOIN public.profiles p ON p.id=p_user
    WHERE e.id=p_exam AND e.school_id IS NOT NULL AND (
      p.role='super_admin' OR (p.school_id=e.school_id AND (
        p.role='admin_sekolah' OR (p.role IN ('guru','principal','vice_principal') AND (
          p.id=e.teacher_id OR EXISTS (
            SELECT 1 FROM public.scanmeet_exam_supervisors a
            WHERE a.school_id=e.school_id AND a.exam_id=e.id AND a.supervisor_id=p.id
              AND a.revoked_at IS NULL AND a.valid_until>clock_timestamp()
          )
        ))
      ))
    )
  );
$$;

-- Runs after scanmeet_reserve_package: keep the package cap, shorten only for
-- an assigned supervisor's expiry. The reservation trigger still charges once.
CREATE OR REPLACE FUNCTION public.scanmeet_supervisor_session_limit()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE e public.exams%ROWTYPE; p public.profiles%ROWTYPE; until_at timestamptz;
BEGIN
  SELECT * INTO e FROM public.exams WHERE id=NEW.exam_id;
  SELECT * INTO p FROM public.profiles WHERE id=NEW.created_by;
  IF e.school_id IS DISTINCT FROM NEW.school_id OR
     NOT public.scanmeet_supervisor_authorized(NEW.exam_id,NEW.created_by) THEN
    RAISE EXCEPTION 'scanmeet_supervisor_denied';
  END IF;
  IF p.id<>e.teacher_id AND p.role NOT IN ('super_admin','admin_sekolah') THEN
    SELECT valid_until INTO STRICT until_at FROM public.scanmeet_exam_supervisors
      WHERE school_id=e.school_id AND exam_id=e.id AND supervisor_id=p.id
        AND revoked_at IS NULL AND valid_until>clock_timestamp();
    NEW.expires_at:=LEAST(NEW.expires_at,until_at);
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS zz_scanmeet_supervisor_limit ON public.scan_live_sessions;
CREATE TRIGGER zz_scanmeet_supervisor_limit BEFORE INSERT ON public.scan_live_sessions
  FOR EACH ROW EXECUTE FUNCTION public.scanmeet_supervisor_session_limit();

CREATE OR REPLACE FUNCTION public.scanmeet_begin_handover(
  p_exam uuid,p_actor uuid,p_session uuid,p_target uuid,p_version integer,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE
  e public.exams%ROWTYPE; s public.scan_live_sessions%ROWTYPE;
  a public.profiles%ROWTYPE; j public.scanmeet_handover_jobs%ROWTYPE;
  license public.scanmeet_school_entitlements%ROWTYPE;
BEGIN
  -- Every management RPC locks exam before session/audit/cleanup rows.
  SELECT * INTO e FROM public.exams WHERE id=p_exam FOR UPDATE;
  SELECT * INTO a FROM public.profiles WHERE id=p_actor;
  IF e.id IS NULL OR NOT COALESCE((a.role='super_admin' OR
     (a.role='admin_sekolah' AND a.school_id=e.school_id)),false) THEN
    RAISE EXCEPTION 'scanmeet_supervisor_denied';
  END IF;
  IF p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'scanmeet_reason_required';
  END IF;
  SELECT * INTO s FROM public.scan_live_sessions
    WHERE id=p_session AND exam_id=e.id AND school_id=e.school_id FOR UPDATE;
  IF s.id IS NULL OR s.status<>'active' OR (p_target IS NOT NULL AND
     (s.expires_at IS NULL OR s.expires_at<=clock_timestamp() OR NOT e.scan_live_enabled)) THEN
    RAISE EXCEPTION 'scanmeet_session_unavailable';
  END IF;
  IF s.media_version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'scanmeet_version_conflict'; END IF;
  SELECT * INTO j FROM public.scanmeet_handover_jobs WHERE session_id=s.id AND state='pending';
  IF j.id IS NOT NULL THEN
    IF j.to_user IS NOT DISTINCT FROM p_target AND j.actor_id=p_actor THEN
      RETURN jsonb_build_object('handover_id',j.id,'state',j.state);
    END IF;
    RAISE EXCEPTION 'scanmeet_handover_busy';
  END IF;
  IF s.transfer_state<>'ready' OR EXISTS (
    SELECT 1 FROM public.scan_live_cleanup_jobs WHERE room_name=s.room_name AND status='pending'
  ) THEN RAISE EXCEPTION 'scanmeet_session_closing'; END IF;
  IF p_target IS NOT NULL THEN
    IF p_target=s.created_by THEN RAISE EXCEPTION 'scanmeet_already_supervisor'; END IF;
    IF NOT public.scanmeet_supervisor_authorized(e.id,p_target) THEN
      RAISE EXCEPTION 'scanmeet_target_denied';
    END IF;
    SELECT * INTO license FROM public.scanmeet_school_entitlements WHERE school_id=e.school_id;
    IF NOT COALESCE(license.enabled,false) OR license.status NOT IN ('trial','paid')
       OR license.archived_at IS NOT NULL OR license.valid_until<=clock_timestamp()
       OR license.valid_until IS NULL THEN RAISE EXCEPTION 'scanmeet_license_inactive'; END IF;
  END IF;
  INSERT INTO public.scanmeet_handover_jobs
    (school_id,exam_id,session_id,actor_id,from_user,to_user,from_room,to_room,from_version,operation,reason)
    VALUES(e.school_id,e.id,s.id,p_actor,s.created_by,p_target,s.room_name,
      'sg_'||replace(gen_random_uuid()::text,'-',''),s.media_version,
      CASE WHEN p_target IS NULL THEN 'revoke' ELSE 'transfer' END,trim(p_reason)) RETURNING * INTO j;
  UPDATE public.scan_live_sessions SET transfer_state='closing' WHERE id=s.id;
  UPDATE public.scan_live_hand_raises SET status='closed'
    WHERE session_id=s.id AND status IN ('pending','approving','approved','closing');
  -- Remains active to reserve the same concurrency slot and package allowance.
  INSERT INTO public.scan_live_cleanup_jobs(school_id,session_id,room_name,reason,handover_id)
    VALUES(e.school_id,s.id,s.room_name,'supervisor_'||j.operation,j.id);
  RETURN jsonb_build_object('handover_id',j.id,'state',j.state);
END;
$$;

CREATE OR REPLACE FUNCTION public.scanmeet_supervisor_assignment(
  p_exam uuid,p_actor uuid,p_target uuid,p_until timestamptz,p_version integer,p_reason text,p_revoke boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE
  e public.exams%ROWTYPE; actor public.profiles%ROWTYPE; target public.profiles%ROWTYPE;
  old public.scanmeet_exam_supervisors%ROWTYPE; current_row public.scanmeet_exam_supervisors%ROWTYPE;
  s public.scan_live_sessions%ROWTYPE; handover jsonb;
BEGIN
  SELECT * INTO e FROM public.exams WHERE id=p_exam FOR UPDATE;
  SELECT * INTO actor FROM public.profiles WHERE id=p_actor;
  IF e.id IS NULL OR NOT COALESCE((actor.role='super_admin' OR
     (actor.role='admin_sekolah' AND actor.school_id=e.school_id)),false) THEN
    RAISE EXCEPTION 'scanmeet_supervisor_denied';
  END IF;
  SELECT * INTO target FROM public.profiles WHERE id=p_target;
  IF target.id IS NULL OR target.school_id IS DISTINCT FROM e.school_id
     OR target.role NOT IN ('guru','principal','vice_principal') OR target.id=e.teacher_id THEN
    RAISE EXCEPTION 'scanmeet_target_denied';
  END IF;
  IF p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'scanmeet_reason_required';
  END IF;
  IF p_revoke IS NULL OR (NOT p_revoke AND (p_until IS NULL OR p_until<=clock_timestamp()
     OR p_until>clock_timestamp()+interval '7 days')) THEN RAISE EXCEPTION 'scanmeet_invalid_expiry'; END IF;
  SELECT * INTO old FROM public.scanmeet_exam_supervisors WHERE exam_id=e.id AND supervisor_id=target.id FOR UPDATE;
  IF COALESCE(old.version,0) IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'scanmeet_version_conflict'; END IF;
  IF p_revoke AND old.id IS NULL THEN RAISE EXCEPTION 'scanmeet_assignment_missing'; END IF;
  INSERT INTO public.scanmeet_exam_supervisors(school_id,exam_id,supervisor_id,granted_by,valid_until)
    VALUES(e.school_id,e.id,target.id,p_actor,CASE WHEN p_revoke THEN old.valid_until ELSE p_until END)
    ON CONFLICT(exam_id,supervisor_id) DO UPDATE SET
      valid_until=CASE WHEN p_revoke THEN old.valid_until ELSE excluded.valid_until END,
      revoked_at=CASE WHEN p_revoke THEN clock_timestamp() ELSE NULL END,
      version=scanmeet_exam_supervisors.version+1,granted_by=p_actor,updated_at=clock_timestamp()
    RETURNING * INTO current_row;
  INSERT INTO public.scanmeet_supervisor_audit(school_id,exam_id,actor_id,action,reason,before_data,after_data)
    VALUES(e.school_id,e.id,p_actor,CASE WHEN p_revoke THEN 'revoke' ELSE 'grant' END,
      trim(p_reason),CASE WHEN old.id IS NULL THEN NULL ELSE to_jsonb(old) END,to_jsonb(current_row));
  IF NOT p_revoke THEN
    UPDATE public.scan_live_sessions SET expires_at=LEAST(expires_at,p_until)
      WHERE exam_id=e.id AND school_id=e.school_id AND created_by=target.id AND status='active';
  END IF;
  IF p_revoke THEN
    SELECT * INTO s FROM public.scan_live_sessions WHERE exam_id=e.id AND school_id=e.school_id
      AND created_by=target.id AND status='active' FOR UPDATE;
    IF s.id IS NOT NULL AND s.transfer_state='ready' THEN
      handover:=public.scanmeet_begin_handover(e.id,p_actor,s.id,NULL,s.media_version,p_reason);
    END IF;
  END IF;
  RETURN jsonb_build_object('assignment',to_jsonb(current_row),'handover_id',handover->>'handover_id');
END;
$$;

-- Called only by cleanup AFTER the retired room deletion succeeds (or not-found).
-- The claim token makes a worker that lost its lease unable to finish a transfer.
CREATE OR REPLACE FUNCTION public.scanmeet_finish_handover(p_handover uuid,p_claim text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE
  j public.scanmeet_handover_jobs%ROWTYPE; s public.scan_live_sessions%ROWTYPE;
  e public.exams%ROWTYPE; q public.scan_live_cleanup_jobs%ROWTYPE;
  license public.scanmeet_school_entitlements%ROWTYPE; target public.profiles%ROWTYPE;
  until_at timestamptz; result_reason text;
BEGIN
  SELECT * INTO j FROM public.scanmeet_handover_jobs WHERE id=p_handover;
  IF j.id IS NULL THEN RAISE EXCEPTION 'scanmeet_handover_missing'; END IF;
  IF j.state<>'pending' THEN RETURN jsonb_build_object('state',j.state); END IF;
  SELECT * INTO e FROM public.exams WHERE id=j.exam_id FOR UPDATE;
  SELECT * INTO s FROM public.scan_live_sessions WHERE id=j.session_id FOR UPDATE;
  SELECT * INTO q FROM public.scan_live_cleanup_jobs WHERE handover_id=j.id FOR UPDATE;
  SELECT * INTO j FROM public.scanmeet_handover_jobs WHERE id=p_handover FOR UPDATE;
  IF j.state<>'pending' THEN RETURN jsonb_build_object('state',j.state); END IF;
  IF q.id IS NULL OR q.status<>'pending' OR q.claim_token<>p_claim OR p_claim IS NULL OR p_claim=''
     OR q.claimed_at IS NULL OR q.claimed_at<=clock_timestamp()-interval '5 minutes' THEN
    RAISE EXCEPTION 'scanmeet_claim_lost';
  END IF;
  SELECT * INTO license FROM public.scanmeet_school_entitlements WHERE school_id=j.school_id;
  IF s.id IS NULL OR e.id IS NULL OR s.status<>'active' OR s.transfer_state<>'closing'
     OR s.media_version<>j.from_version OR s.room_name<>j.from_room THEN result_reason:='session_changed';
  ELSIF j.operation='revoke' THEN result_reason:='assignment_revoked';
  ELSIF NOT e.scan_live_enabled THEN result_reason:='exam_off';
  ELSIF s.expires_at IS NULL OR s.expires_at<=clock_timestamp() THEN result_reason:='session_expired';
  ELSIF NOT COALESCE(license.enabled,false) OR license.status NOT IN ('trial','paid')
     OR license.archived_at IS NOT NULL OR license.valid_until IS NULL
     OR license.valid_until<=clock_timestamp() THEN result_reason:='license_inactive';
  ELSIF NOT COALESCE(public.scanmeet_supervisor_authorized(e.id,j.to_user),false) THEN result_reason:='target_revoked';
  END IF;
  IF result_reason IS NULL THEN
    SELECT * INTO target FROM public.profiles WHERE id=j.to_user;
    until_at:=LEAST(s.expires_at,license.valid_until);
    IF target.id<>e.teacher_id AND target.role NOT IN ('super_admin','admin_sekolah') THEN
      SELECT LEAST(until_at,valid_until) INTO until_at FROM public.scanmeet_exam_supervisors
        WHERE exam_id=e.id AND school_id=e.school_id AND supervisor_id=target.id
          AND revoked_at IS NULL AND valid_until>clock_timestamp();
    END IF;
    IF until_at IS NULL OR until_at<=clock_timestamp() THEN result_reason:='target_expired'; END IF;
  END IF;
  IF result_reason IS NULL THEN
    UPDATE public.scan_live_hand_raises SET status='closed'
      WHERE session_id=s.id AND status IN ('pending','approving','approved','closing');
    UPDATE public.scan_live_sessions SET created_by=j.to_user,room_name=j.to_room,
      media_version=media_version+1,transfer_state='ready',expires_at=until_at,
      media_state='unknown',missing_since=NULL,reconciled_at=NULL
      WHERE id=s.id;
    UPDATE public.scanmeet_handover_jobs SET state='completed',outcome='room_rotated',finished_at=clock_timestamp() WHERE id=j.id;
    RETURN jsonb_build_object('state','completed');
  END IF;
  IF s.id IS NOT NULL AND s.status='active' AND s.transfer_state='closing'
     AND s.media_version=j.from_version AND s.room_name=j.from_room THEN
    UPDATE public.scan_live_sessions SET status='ended',transfer_state='ready' WHERE id=s.id;
  END IF;
  UPDATE public.scanmeet_handover_jobs SET state=CASE WHEN j.operation='revoke' THEN 'completed' ELSE 'cancelled' END,
    outcome=result_reason,finished_at=clock_timestamp() WHERE id=j.id;
  RETURN jsonb_build_object('state',CASE WHEN j.operation='revoke' THEN 'completed' ELSE 'cancelled' END,'outcome',result_reason);
END;
$$;

REVOKE ALL ON FUNCTION public.scanmeet_supervisor_authorized(uuid,uuid),
  public.scanmeet_supervisor_session_limit(),public.scanmeet_begin_handover(uuid,uuid,uuid,uuid,integer,text),
  public.scanmeet_supervisor_assignment(uuid,uuid,uuid,timestamptz,integer,text,boolean),
  public.scanmeet_finish_handover(uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.scanmeet_supervisor_authorized(uuid,uuid),
  public.scanmeet_supervisor_session_limit(),public.scanmeet_begin_handover(uuid,uuid,uuid,uuid,integer,text),
  public.scanmeet_supervisor_assignment(uuid,uuid,uuid,timestamptz,integer,text,boolean),
  public.scanmeet_finish_handover(uuid,text) TO service_role;
NOTIFY pgrst,'reload schema';
