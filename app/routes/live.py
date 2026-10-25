"""Feature-gated LiveKit exam APIs, independent of submission/autosave routes."""
from __future__ import annotations

import logging
import secrets

from flask import Blueprint, current_app, g, jsonify, request, render_template
from app.decorators.year_lock import open_year_required
from app.services import live_cleanup, live_exam_toggle, live_policy, livekit_gateway, live_school_entitlement
from app.utils.auth import get_supabase, login_required
from app.utils.exam_access import can_manage_exam, exam_sitting_allowed

live_bp = Blueprint("live", __name__, url_prefix="/api/live")
live_ui_bp = Blueprint("live_ui", __name__, url_prefix="/live")


@live_ui_bp.app_context_processor
def scanmeet_template_context():
    """Expose the same two-switch policy to links as to the API and pages."""
    return {"scanmeet_enabled": live_policy.feature_enabled(current_app.config)}

log = logging.getLogger(__name__)
STAFF = ("guru", "principal", "vice_principal", "admin_sekolah", "super_admin")


def _off():
    if not live_policy.feature_enabled(current_app.config):
        return jsonify({"error": "ScanGrade Live is disabled"}), 404
    return None


def _one(query):
    rows = query.limit(1).execute().data or []
    return rows[0] if rows else None


EXAM_COLUMNS = "id,school_id,teacher_id,is_published,status,class_ids,target_mode"
#: Migration 071's per-exam switch. Asked for separately from the columns above
#: because a box that has not applied the migration must read as OFF (the feature
#: default) rather than answer every page with a PostgREST error.
EXAM_COLUMNS_SWITCHED = EXAM_COLUMNS + "," + live_exam_toggle.ENABLED_COLUMN


def _exam(db, exam_id):
    try:
        return _one(db.table("exams").select(EXAM_COLUMNS_SWITCHED)
                    .eq("id", exam_id))
    except Exception as exc:
        if live_exam_toggle.ENABLED_COLUMN not in str(exc):
            raise
        log.warning("exams.%s is missing; migration 071 is not applied, so "
                    "ScanMeet stays OFF", live_exam_toggle.ENABLED_COLUMN)
        return _one(db.table("exams").select(EXAM_COLUMNS).eq("id", exam_id))


def _switch_off():
    """This paper's teacher switch is OFF, independently of the kill switches."""
    return jsonify({"error": "ScanMeet is off for this exam"}), 409


def _access(db, exam, *, staff_only=False):
    if not exam or not exam.get("school_id"):
        return None
    school = g.get("user_school_id")
    role = g.get("user_role")
    uid = g.get("user_id")
    if not uid or not school or str(exam["school_id"]) != str(school):
        return None
    if role in STAFF and can_manage_exam(uid, role, school, exam):
        return live_policy.LIVE_TEACHER
    if not staff_only and role == "murid":
        allowed, _ = exam_sitting_allowed(db, exam, exam["id"], uid)
        if allowed:
            return live_policy.LIVE_STUDENT
    return None


def _session(db, sid):
    if not g.get("user_school_id"):
        return None
    return _one(db.table("scan_live_sessions").select("*")
                .eq("id", sid).eq("school_id", g.user_school_id))


def _check(db, session, staff_only=False):
    if not session or session.get("status") != "active":
        return None
    exam = _exam(db, session["exam_id"])
    role = _access(db, exam, staff_only=staff_only)
    if role == live_policy.LIVE_TEACHER and str(session.get("created_by") or "") != str(g.get("user_id") or ""):
        return None  # A second teacher cannot see pupil tracks allowed to the room creator.
    return role


def _denied():
    return jsonify({"error": "Live session not available for this account"}), 403


def _unavailable():
    return jsonify({"error": "Live media service unavailable; exam continues"}), 503


@live_bp.route("/exams/<exam_id>", methods=["GET"])
@login_required
def status(exam_id):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    exam = _exam(db, exam_id)
    role = _access(db, exam)
    if not role:
        return _denied()
    session = _one(db.table("scan_live_sessions").select(
        "id,exam_id,status,created_at,created_by"
    ).eq("exam_id", exam_id).eq("school_id", exam["school_id"])
        .eq("status", "active"))
    if role == live_policy.LIVE_TEACHER and session and str(session.get("created_by") or "") != str(g.user_id):
        return jsonify({"error": "Another teacher already supervises this Live session"}), 409
    return jsonify({"session": session, "role": role,
                    "enabled": live_exam_toggle.enabled(exam),
                    "policy": live_policy.EXAM_POLICY})


@live_bp.route("/exams/<exam_id>/sessions", methods=["POST"])
@login_required
@open_year_required("exam_id")
def create_session(exam_id):
    if (off := _off()) is not None:
        return off
    if not live_policy.client_ready(current_app.config):
        return _unavailable()
    db = get_supabase()
    exam = _exam(db, exam_id)
    if _access(db, exam, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    if not live_school_entitlement.active(db, exam["school_id"]):
        return jsonify({"error": "ScanMeet school add-on unavailable"}), 403
    if not live_exam_toggle.enabled(exam):
        return _switch_off()
    existing = _one(db.table("scan_live_sessions").select("*")
                    .eq("school_id", exam["school_id"]).eq("exam_id", exam_id)
                    .eq("status", "active"))
    if existing:
        if str(existing.get("created_by") or "") != str(g.user_id):
            return jsonify({"error": "Another teacher already supervises this Live session"}), 409
        return jsonify({"session_id": existing["id"], "existing": True})
    # Unique partial index prevents parallel callers creating two active rooms.
    row = {
        "school_id": exam["school_id"], "exam_id": exam_id,
        "created_by": g.user_id, "room_name": "sg_" + secrets.token_urlsafe(22),
        "status": "active", "policy": dict(live_policy.EXAM_POLICY),
    }
    try:
        created = db.table("scan_live_sessions").insert(row).execute().data[0]
    except Exception:
        log.warning("Concurrent Live creation or Live migration unavailable",
                    exc_info=True)
        return jsonify({"error": "Could not start Live; exam unaffected"}), 503
    return jsonify({"session_id": created["id"], "existing": False}), 201


@live_bp.route("/exams/<exam_id>/enabled", methods=["POST"])
@login_required
@open_year_required("exam_id")
def set_exam_enabled(exam_id):
    """The per-examination ScanMeet switch. Server-side authorization only.

    OFF ends an active room and refuses every subsequent join token; ON permits
    an explicit `POST /sessions` but never creates one here. The two environment
    flags remain the deployment-level kill switch, checked before this runs.
    """
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    exam = _exam(db, exam_id)
    if _access(db, exam, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    body = request.get_json(silent=True) or {}
    value = body.get("enabled")
    if not isinstance(value, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    if value and not live_school_entitlement.active(db, exam["school_id"]):
        return jsonify({"error": "ScanMeet school add-on unavailable"}), 403
    try:
        result = live_exam_toggle.set_enabled(
            db, exam, value=value,
            end_room=lambda room: livekit_gateway.end_room(
                current_app.config, room),
            # OFF revoked every new token above; a room that would not delete is
            # recorded so the retry worker finishes the job after this response.
            record_cleanup=lambda session, exc: live_cleanup.enqueue(
                db, session, reason="switch_off", error=exc))
    except live_exam_toggle.Refused as exc:
        log.warning("ScanMeet switch refused for %s: %s", exam_id, exc)
        return jsonify({"error": "Could not save the ScanMeet switch; exam unaffected"}), 409
    except Exception:
        log.warning("ScanMeet switch unavailable for %s", exam_id, exc_info=True)
        return jsonify({"error": "Could not save the ScanMeet switch; exam unaffected"}), 503
    return jsonify(result)


@live_bp.route("/sessions/<sid>/token", methods=["POST"])
@login_required
def join_token(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    role = _check(db, session)
    if not role:
        return _denied()
    if not live_school_entitlement.active(db, session["school_id"]):
        return jsonify({"error": "ScanMeet school add-on unavailable"}), 403
    # A session that exists is not permission to join it: the teacher may have
    # switched the paper off after the room was created, and the row's cleanup
    # is the only thing between that decision and a pupil's camera.
    if not live_exam_toggle.enabled(_exam(db, session["exam_id"]) or {}):
        return _switch_off()
    approved = False
    if role == live_policy.LIVE_STUDENT:
        approved = bool(_one(db.table("scan_live_hand_raises").select("id")
                             .eq("session_id", sid).eq("school_id", session["school_id"])
                             .eq("student_id", g.user_id).eq("status", "approved")))
    try:
        result = livekit_gateway.issue_join_token(
            current_app.config, sid, session["room_name"], g.user_id,
            role, approved, supervisor_id=session.get("created_by"))
    except (ImportError, RuntimeError, ValueError):
        log.warning("LiveKit join token unavailable", exc_info=True)
        return _unavailable()
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@live_bp.route("/sessions/<sid>/hand-raises", methods=["POST"])
@login_required
def hand_raise(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session) != "student":
        return _denied()
    # These four are the states migration 070's unique partial index calls open.
    # Reading the three this route used to name left ``closing`` — including a
    # ``closing`` row parked by an interrupted close — invisible here, so the
    # insert below hit the index and the pupil got 503 "retry" for an action
    # that could never succeed until the teacher repaired the row.
    active = _one(db.table("scan_live_hand_raises").select("id,status")
                  .eq("session_id", sid).eq("school_id", session["school_id"])
                  .eq("student_id", g.user_id).in_("status", list(live_policy.OPEN_GRANTS)))
    if active:
        return jsonify(active)
    try:
        row = db.table("scan_live_hand_raises").insert({
            "session_id": sid, "school_id": session["school_id"],
            "student_id": g.user_id, "status": "pending",
        }).execute().data[0]
    except Exception:
        return jsonify({"error": "Request not stored; retry"}), 503
    return jsonify({"id": row["id"], "status": row["status"]}), 201


@live_bp.route("/sessions/<sid>/hand-raises", methods=["GET"])
@login_required
def hand_raise_queue(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    role = _check(db, session)
    if role == live_policy.LIVE_STUDENT:
        own = _one(db.table("scan_live_hand_raises").select("id,status,created_at")
                   .eq("session_id", sid).eq("school_id", session["school_id"])
                   .eq("student_id", g.user_id)
                   .order("created_at", desc=True))
        return jsonify({"request": own})
    if role != live_policy.LIVE_TEACHER:
        return _denied()
    rows = (db.table("scan_live_hand_raises").select(
        "id,student_id,status,created_at"
    ).eq("session_id", sid).eq("school_id", session["school_id"])
        .in_("status", ["pending", "approving", "approved"])
        .order("created_at").execute().data or [])
    return jsonify({"requests": rows})


@live_bp.route("/sessions/<sid>/hand-raises/<rid>/<action>", methods=["POST"])
@login_required
def moderate(sid, rid, action):
    if (off := _off()) is not None:
        return off
    if action not in ("approve", "reject", "close"):
        return jsonify({"error": "Invalid action"}), 400
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    raise_row = _one(db.table("scan_live_hand_raises").select("*")
                     .eq("id", rid).eq("session_id", sid)
                     .eq("school_id", session["school_id"]))
    if not raise_row:
        return _denied()
    expected = "approved" if action == "close" else "pending"
    if raise_row["status"] != expected:
        return jsonify({"error": "Request state has changed"}), 409
    transitional = "closing" if action == "close" else (
        "approving" if action == "approve" else "rejected")
    updated = (db.table("scan_live_hand_raises").update({"status": transitional})
               .eq("id", rid).eq("session_id", sid)
               .eq("school_id", session["school_id"])
               .eq("status", expected).execute().data or [])
    if not updated:
        return jsonify({"error": "Request state has changed"}), 409
    if action == "reject":
        return jsonify({"status": "rejected"})
    enabled = action == "approve"
    try:
        livekit_gateway.set_student_mic(
            current_app.config, sid, session["room_name"],
            raise_row["student_id"], enabled)
        final_state = "approved" if enabled else "closed"
        finished = (db.table("scan_live_hand_raises").update({"status": final_state})
                    .eq("id", rid).eq("session_id", sid)
                    .eq("school_id", session["school_id"])
                    .eq("status", transitional).execute().data or [])
        if not finished:
            # The row moved while the permission was being applied. Reporting
            # "approved" here would be a claim about a row this request no
            # longer owns, and for an approve it would leave a live microphone
            # nobody voted for. Fail closed and say so.
            if enabled:
                livekit_gateway.set_student_mic(
                    current_app.config, sid, session["room_name"],
                    raise_row["student_id"], False)
            return jsonify({"error": "Request state has changed"}), 409
    except Exception:
        # Fail CLOSED if the permission update or bookkeeping did not finish.
        # A retry is possible after resetting the transitional state.
        try:
            if enabled:
                livekit_gateway.set_student_mic(
                    current_app.config, sid, session["room_name"],
                    raise_row["student_id"], False)
            db.table("scan_live_hand_raises").update({"status": expected}) \
                .eq("id", rid).eq("session_id", sid) \
                .eq("school_id", session["school_id"]) \
                .eq("status", transitional).execute()
        except Exception:
            log.exception("Live moderation needs operator reconciliation")
        return _unavailable()
    return jsonify({"status": final_state})


@live_bp.route("/sessions/<sid>/hand-raises/<rid>/reconcile", methods=["POST"])
@login_required
def reconcile(sid, rid):
    """Clear a transitional grant left behind by an interrupted moderation.

    ``approve``/``close`` park the row in ``approving``/``closing`` before the
    LiveKit permission call and only their own error path resets it, so a worker
    that dies in between (or whose reset also fails) leaves a row the student's
    ``hand_raise`` keeps returning and the teacher cannot decide. Repair is
    always fail-closed: mute the pupil, then step back to a decided state.
    """
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    raise_row = _one(db.table("scan_live_hand_raises").select("*")
                     .eq("id", rid).eq("session_id", sid)
                     .eq("school_id", session["school_id"]))
    if not raise_row:
        return _denied()
    stuck = raise_row["status"]
    if stuck not in ("approving", "closing"):
        return jsonify({"error": "Nothing to reconcile"}), 409
    # Mute first: an interrupted ``close`` may still be publishing audio, and an
    # interrupted ``approve`` must not hand the pupil an open microphone back.
    try:
        livekit_gateway.set_student_mic(
            current_app.config, sid, session["room_name"],
            raise_row["student_id"], False)
    except Exception:
        log.exception("Live reconcile could not mute %s", rid)
        return _unavailable()
    # ``approving`` goes back to pending so the teacher decides again;
    # ``closing`` is finished, because the teacher already asked for silence.
    final_state = "pending" if stuck == "approving" else "closed"
    updated = (db.table("scan_live_hand_raises").update({"status": final_state})
               .eq("id", rid).eq("session_id", sid)
               .eq("school_id", session["school_id"])
               .eq("status", stuck).execute().data or [])
    if not updated:
        return jsonify({"error": "Request state has changed"}), 409
    return jsonify({"status": final_state})


@live_bp.route("/sessions/<sid>/end", methods=["POST"])
@login_required
def end_session(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    rows = (db.table("scan_live_sessions").update({"status": "ended"})
            .eq("id", sid).eq("school_id", session["school_id"])
            .eq("status", "active").execute().data or [])
    if not rows:
        return jsonify({"error": "Session already closed"}), 409
    try:
        livekit_gateway.end_room(current_app.config, session["room_name"])
    except Exception as exc:
        # The database row is the authoritative gate for all NEW join tokens, so
        # the session is ended either way — but the room may still be publishing
        # to everyone already inside it. That deletion is owed, and it is written
        # down (`scan_live_cleanup_jobs`) rather than logged: a worker retries it
        # after this request, through a restart if necessary.
        log.exception("Live room cleanup failed for %s", sid)
        try:
            live_cleanup.enqueue(db, session, reason="session_ended", error=exc)
        except Exception:  # noqa: BLE001 - the session is already ended
            log.exception("Live cleanup debt could not be recorded for %s", sid)
        return jsonify({"status": "ended", "media_cleanup": "pending"}), 202
    return jsonify({"status": "ended"})


@live_bp.route("/sessions/<sid>/roster", methods=["GET"])
@login_required
def roster(sid):
    """Authorized teacher maps room pseudonyms to pupils."""
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    from app.services import exam_targets
    from app.utils.exam_access import exam_class_ids
    exam = _exam(db, session["exam_id"])
    members = exam_targets.roster_for_classes(
        db, session["school_id"], exam_class_ids(exam))
    names = {}
    by_student = {}
    for member in members:
        student_id = member.get("student_id")
        if not student_id:
            continue
        if exam.get("target_mode") == "students" and not exam_targets.student_is_included(
                db, exam["id"], student_id):
            continue
        name_key = live_policy.participant_identity(
            current_app.config, sid, student_id)
        names[name_key] = member.get("full_name") or "Peserta"
        by_student[str(student_id)] = names[name_key]
    return jsonify({"participants": names, "students": by_student})


@live_ui_bp.route("/exam/<exam_id>", methods=["GET"])
@login_required
def live_exam_page(exam_id):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    exam = _exam(db, exam_id)
    role = _access(db, exam)
    if not role:
        return _denied()
    return render_template("live/exam.html", live_exam_id=exam_id, live_role=role)


@live_bp.route("/sessions/<sid>/events", methods=["GET"])
@login_required
def recent_events(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != live_policy.LIVE_TEACHER:
        return _denied()
    rows = (db.table("scan_live_events")
            .select("event_type,participant_identity,received_at")
            .eq("school_id", session["school_id"]).eq("session_id", sid)
            .order("received_at", desc=True).limit(25).execute().data or [])
    return jsonify({"events": rows})
