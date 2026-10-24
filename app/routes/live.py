"""Feature-gated LiveKit exam APIs, independent of submission/autosave routes."""
from __future__ import annotations

import logging
import secrets

from flask import Blueprint, current_app, g, jsonify, request, render_template
from app.services import live_policy, livekit_gateway
from app.utils.auth import get_supabase, login_required
from app.utils.exam_access import can_manage_exam, exam_sitting_allowed

live_bp = Blueprint("live", __name__, url_prefix="/api/live")
live_ui_bp = Blueprint("live_ui", __name__, url_prefix="/live")
log = logging.getLogger(__name__)
STAFF = ("guru", "principal", "vice_principal", "admin_sekolah", "super_admin")


def _off():
    if not live_policy.feature_enabled(current_app.config):
        return jsonify({"error": "ScanGrade Live is disabled"}), 404
    return None


def _one(query):
    rows = query.limit(1).execute().data or []
    return rows[0] if rows else None


def _exam(db, exam_id):
    return _one(db.table("exams").select(
        "id,school_id,teacher_id,is_published,status,class_ids,target_mode"
    ).eq("id", exam_id))


def _access(db, exam, *, staff_only=False):
    if not exam or not exam.get("school_id"):
        return None
    school = g.get("user_school_id")
    role = g.get("user_role")
    uid = g.get("user_id")
    if not uid or not school or str(exam["school_id"]) != str(school):
        return None
    if role in STAFF and can_manage_exam(uid, role, school, exam):
        return "teacher"
    if not staff_only and role == "murid":
        allowed, _ = exam_sitting_allowed(db, exam, exam["id"], uid)
        if allowed:
            return "student"
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
    return _access(db, exam, staff_only=staff_only)


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
        "id,exam_id,status,created_at"
    ).eq("exam_id", exam_id).eq("school_id", exam["school_id"])
        .eq("status", "active"))
    return jsonify({"session": session, "role": role,
                    "policy": live_policy.EXAM_POLICY})


@live_bp.route("/exams/<exam_id>/sessions", methods=["POST"])
@login_required
def create_session(exam_id):
    if (off := _off()) is not None:
        return off
    if not live_policy.client_ready(current_app.config):
        return _unavailable()
    db = get_supabase()
    exam = _exam(db, exam_id)
    if _access(db, exam, staff_only=True) != "teacher":
        return _denied()
    existing = _one(db.table("scan_live_sessions").select("*")
                    .eq("school_id", exam["school_id"]).eq("exam_id", exam_id)
                    .eq("status", "active"))
    if existing:
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
    approved = False
    if role == "student":
        approved = bool(_one(db.table("scan_live_hand_raises").select("id")
                             .eq("session_id", sid).eq("school_id", session["school_id"])
                             .eq("student_id", g.user_id).eq("status", "approved")))
    try:
        result = livekit_gateway.issue_join_token(
            current_app.config, sid, session["room_name"], g.user_id,
            role, approved)
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
    active = _one(db.table("scan_live_hand_raises").select("id,status")
                  .eq("session_id", sid).eq("school_id", session["school_id"])
                  .eq("student_id", g.user_id).in_("status", ["pending", "approving", "approved"]))
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
    if role == "student":
        own = _one(db.table("scan_live_hand_raises").select("id,status,created_at")
                   .eq("session_id", sid).eq("school_id", session["school_id"])
                   .eq("student_id", g.user_id)
                   .order("created_at", desc=True))
        return jsonify({"request": own})
    if role != "teacher":
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
    if _check(db, session, staff_only=True) != "teacher":
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
        db.table("scan_live_hand_raises").update({"status": final_state}) \
            .eq("id", rid).eq("session_id", sid) \
            .eq("school_id", session["school_id"]) \
            .eq("status", transitional).execute()
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


@live_bp.route("/sessions/<sid>/end", methods=["POST"])
@login_required
def end_session(sid):
    if (off := _off()) is not None:
        return off
    db = get_supabase()
    session = _session(db, sid)
    if _check(db, session, staff_only=True) != "teacher":
        return _denied()
    rows = (db.table("scan_live_sessions").update({"status": "ended"})
            .eq("id", sid).eq("school_id", session["school_id"])
            .eq("status", "active").execute().data or [])
    if not rows:
        return jsonify({"error": "Session already closed"}), 409
    try:
        livekit_gateway.end_room(current_app.config, session["room_name"])
    except Exception:
        # Database is the authoritative gate for all NEW join tokens.
        # A cleanup job should retry deleting any lingering media room.
        log.exception("Live room cleanup failed for %s", sid)
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
    if _check(db, session, staff_only=True) != "teacher":
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
