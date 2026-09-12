import io
import json
import os
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, g, jsonify, current_app, make_response, flash
from app.utils.auth import login_required, get_supabase
from app.utils.cache import cache_get, cache_set
from app.utils.helpers import row_or_none
from app.utils.exam_access import result_released, exam_sitting_allowed
from app.utils.exam_recovery import issue_code, redeem_code
from app.services.audit_service import log_activity
from app.utils.rate_limiter import limiter

student_bp = Blueprint("student", __name__)


def _rate_limit(n):
    return limiter.limit(n) if limiter else (lambda f: f)


@student_bp.route("/dashboard")
@login_required
def dashboard():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")

    # Try cache first (30s TTL)
    cache_key = f"dash:{g.user_id}"
    cached = cache_get(cache_key)
    if cached:
        return render_template("student/dashboard.html", **cached)

    available_exams = []
    submitted_ids = set()
    student_class_id = None
    student_school_id = None
    try:
        prof = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute()
        if prof.data:
            student_class_id = prof.data.get("class_id")
            student_school_id = prof.data.get("school_id")
    except Exception:
        pass
    try:
        query = supabase.table("exams").select("id,title,subject,start_at,class_ids,question_types,total_questions,duration_minutes").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        all_exams = query.execute().data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        # Filter by class_id if student has one, AND check scheduling
        for e in all_exams:
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            cids = e.get("class_ids") or []
            if isinstance(cids, str):
                try:
                    cids = json.loads(cids)
                except (json.JSONDecodeError, TypeError):
                    cids = []
            if isinstance(cids, list):
                cids = [c for c in cids if c]
            if student_class_id:
                if not cids or student_class_id in cids:
                    available_exams.append(e)
            else:
                if not cids:
                    available_exams.append(e)
        subs_ids = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
        submitted_ids = {s["exam_id"] for s in subs_ids}
        # Exclude retracted
        retracted = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).eq("status", "retracted").execute().data or []
        submitted_ids -= {s["exam_id"] for s in retracted}
    except Exception as e:
        current_app.logger.error(f"Dashboard query error: {e}")
    available_exams = [e for e in available_exams if e["id"] not in submitted_ids]

    subs = []
    try:
        subs = supabase.table("submissions").select("id, exam_id, student_id, score, max_score, violations, penalty, final_score, status, is_published, submitted_at, graded_at, exams(id, title, question_types, total_questions)").eq("student_id", g.user_id).neq("status", "retracted").order("submitted_at", desc=True).execute().data or []
    except Exception as e:
        current_app.logger.error(f"Dashboard submissions query error: {e}")
    completed_exams = []
    all_scores = []
    for s in subs:
        # Only show submitted/graded/published in dashboard (hide drafts)
        if s.get("status") in ("draft",):
            continue
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        s.setdefault("is_hidden", False)
        # A mark is not official until the teacher releases it. Blanking it here
        # keeps the row (so the student still sees the exam is being marked) while
        # every metric below — average, mastery level, subject averages, trend and
        # weak areas — reads the same fields, so they all skip it automatically.
        if not result_released(s):
            s["score"] = None
            s["final_score"] = None
            s["penalty"] = None
        completed_exams.append(s)
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None:
            all_scores.append(float(sc))
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else "-"
    user_name = g.user_name or g.user_email or ""

    # ── Learning Progress Metrics ──
    # Subject-level score breakdown
    subject_scores = {}
    for s in completed_exams:
        exam = s.get("exam") or {}
        subject = exam.get("subject") or "Umum"
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None:
            if subject not in subject_scores:
                subject_scores[subject] = []
            subject_scores[subject].append(float(sc))
    subject_averages = {k: round(sum(v) / len(v), 1) for k, v in subject_scores.items()}

    # Score trend (last 5 exams, oldest first)
    score_trend = []
    for s in reversed(completed_exams[:5]):
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None:
            score_trend.append({
                "title": (s.get("exam") or {}).get("title", "-")[:20],
                "score": float(sc)
            })

    # Weak areas (exams with score < 70)
    weak_areas = []
    for s in completed_exams:
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None and float(sc) < 70:
            exam = s.get("exam") or {}
            weak_areas.append({
                "title": exam.get("title", "-")[:30],
                "subject": exam.get("subject") or "Umum",
                "score": float(sc)
            })

    # Mastery level (based on avg score)
    if all_scores:
        avg = sum(all_scores) / len(all_scores)
        if avg >= 90: mastery_level = "Sangat Baik"
        elif avg >= 80: mastery_level = "Baik"
        elif avg >= 70: mastery_level = "Cukup"
        elif avg >= 60: mastery_level = "Perlu Perbaikan"
        else: mastery_level = "Sangat Perlu Bimbingan"
    else:
        mastery_level = "Belum Ada Data"

    # Get student's class info
    student_class = None
    subject_count = 0
    try:
        profile = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute().data or {}
        if profile.get("class_id"):
            cls = supabase.table("classes").select("name, grade_level").eq("id", profile["class_id"]).single().execute().data
            if cls:
                student_class = cls
        if profile.get("school_id"):
            cnt = supabase.table("teacher_assignments").select("id", count="exact") \
                .eq("school_id", profile["school_id"]) \
                .execute()
            subject_count = cnt.count or 0
            if student_class and student_class.get("name"):
                class_subj = supabase.table("teacher_assignments").select("id", count="exact") \
                    .eq("school_id", profile["school_id"]) \
                    .execute()
                subject_count = class_subj.count or 0
    except Exception:
        pass

    # School info
    school_info = {}
    try:
        profile = supabase.table("profiles").select("school_id").eq("id", g.user_id).single().execute().data or {}
        if profile.get("school_id"):
            school_info = supabase.table("schools").select("name, npsn, logo_url").eq("id", profile["school_id"]).single().execute().data or {}
    except Exception:
        pass
    # Active whiteboards for student's class (only if enabled by super admin)
    active_whiteboards = []
    if student_class_id and student_school_id:
        try:
            # Check school feature toggle
            feat = supabase.table("schools").select("features").eq("id", student_school_id).single().execute().data or {}
            f = feat.get("features") or {}
            if isinstance(f, str):
                f = json.loads(f)
            if not f.get("whiteboard_enabled", True):
                pass  # whiteboard disabled for this school
            else:
                wbs = supabase.table("whiteboards").select("id,title,status,created_at") \
                    .eq("class_id", student_class_id) \
                    .eq("school_id", student_school_id) \
                    .eq("status", "active") \
                    .order("created_at", desc=True) \
                    .limit(5).execute()
                active_whiteboards = wbs.data or []
        except Exception:
            pass

    template_data = {
        "available_exams": available_exams,
        "completed_exams": completed_exams[:5],
        "avg_score": avg_score,
        "user_name": user_name,
        "student_class": student_class,
        "subject_count": subject_count,
        "school_info": school_info,
        "active_whiteboards": active_whiteboards,
        "subject_averages": subject_averages,
        "score_trend": score_trend,
        "weak_areas": weak_areas,
        "mastery_level": mastery_level,
    }
    # Cache for 30 seconds (skip large/non-serializable fields)
    try:
        cache_set(cache_key, template_data, ttl=30)
    except Exception:
        pass
    return render_template("student/dashboard.html", **template_data)


@student_bp.route("/reset-password", methods=["POST"])
@login_required
def student_reset_password():
    if g.get("user_role") not in ("murid",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    pw = request.form.get("password", "").strip()
    if len(pw) < 6:
        return jsonify({"error": "Password minimal 6 karakter"}), 400
    try:
        supabase.auth.admin.update_user_by_id(g.user_id, {"password": pw})
        log_activity("reset_password", "user", g.user_id, user_id=g.user_id)
        return jsonify({"success": True, "message": "Password berhasil diubah"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@student_bp.route("/profile/update", methods=["POST"])
@login_required
def student_update_profile():
    if g.get("user_role") not in ("murid",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    data = {}
    for key in ("phone",):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    if not data:
        return jsonify({"error": "Tidak ada data yang diubah"}), 400
    try:
        supabase.table("profiles").update(data).eq("id", g.user_id).execute()
        log_activity("update", "profile", g.user_id, new_data=data, user_id=g.user_id)
        return jsonify({"success": True, "message": "Profil berhasil diperbarui"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@student_bp.route("/exams")
@login_required
def exam_list():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")

    # Get student's class_id and school_id
    student_class_id = None
    student_school_id = None
    try:
        prof = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute()
        if prof.data:
            student_class_id = prof.data.get("class_id")
            student_school_id = prof.data.get("school_id")
    except Exception:
        pass

    exams = []
    try:
        query = supabase.table("exams").select("*").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        res = query.order("created_at", desc=True).execute()
        all_exams = res.data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        # Filter by class_id if student has one, AND check scheduling
        for e in all_exams:
            # Skip exams with future start_at
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            cids = e.get("class_ids") or []
            if isinstance(cids, str):
                try:
                    cids = json.loads(cids)
                except (json.JSONDecodeError, TypeError):
                    cids = []
            if isinstance(cids, list):
                cids = [c for c in cids if c]
            if student_class_id:
                if not cids or student_class_id in cids:
                    exams.append(e)
            else:
                if not cids:
                    exams.append(e)
    except Exception as e:
        current_app.logger.error(f"Exam list query error: {e}")

    submitted_ids = set()
    try:
        # Only hide exams that have been submitted/graded/published — NOT drafts
        subs = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
        submitted_ids = {s["exam_id"] for s in subs}
        # Also exclude retracted but allow draft to still show
        retracted = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).eq("status", "retracted").execute().data or []
        submitted_ids -= {s["exam_id"] for s in retracted}
    except Exception as e:
        current_app.logger.error(f"Submission query error: {e}")
    exams = [e for e in exams if e["id"] not in submitted_ids]
    resp = make_response(render_template("student/exam_list.html", exams=exams))
    resp.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"
    return resp


@student_bp.route("/exams/<exam_id>")
@login_required
def take_exam(exam_id):
    supabase = get_supabase()
    try:
        res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
        exam = res.data
    except Exception as e:
        current_app.logger.error(f"Take exam query error: {e}")
        flash("Ujian tidak ditemukan", "error")
        return redirect("/student/exams")
    if not exam:
        flash("Ujian tidak ditemukan", "error")
        return redirect("/student/exams")

    current_app.logger.info("Student exam %s: pdf_page_urls=%s, pdf_url=%s",
                            exam_id, exam.get("pdf_page_urls"), exam.get("pdf_url"))
    # Check if exam is scheduled for the future
    start_at = exam.get("start_at")
    if start_at:
        try:
            if isinstance(start_at, str):
                start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            else:
                start_dt = start_at
            if start_dt > datetime.now(timezone.utc):
                flash("Ujian ini belum tersedia. Silakan cek kembali jadwal.", "error")
                return redirect("/student/exams")
        except Exception:
            pass

    # Published + active + the student's own school and class. This used to
    # require only ``status == 'active'``, so an exam whose scores were not
    # published yet could still be opened by navigating straight to its URL.
    allowed, reason = exam_sitting_allowed(supabase, exam, exam_id, g.user_id)
    if not allowed:
        flash(reason, "error")
        return redirect("/student/exams")

    # Check if student already reached max attempts (exclude draft + retracted)
    max_attempts = exam.get("max_attempts", 1)
    try:
        existing = supabase.table("submissions").select("id", count="exact").eq("exam_id", exam_id).eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published"]).execute()
        attempt_count = existing.count or 0
        if attempt_count >= max_attempts:
            flash(f"Anda sudah mencapai batas maksimal {max_attempts}x mengerjakan ujian ini", "error")
            return redirect("/student/exams")
    except Exception:
        pass
    # Ensure anti-cheat defaults (handle missing columns or NULL values)
    ac_defaults = {
        "anti_cheat_enabled": True,
        "penalty_per_violation": 5,
        "max_violations": 5,
        "auto_submit_on_max": True,
        "fullscreen_required": True,
        "block_copy_paste": True,
        "block_right_click": True,
        "block_screenshot": True,
        "watermark_name": True,
        "allow_calculator": False,
    }
    for k, v in ac_defaults.items():
        if k not in exam or exam[k] is None:
            exam[k] = v
    # Parse JSON fields that may come as strings from Supabase
    for _field in ("question_types", "answer_key", "question_weights", "question_pages", "pdf_page_urls"):
        _val = exam.get(_field)
        if isinstance(_val, str):
            try:
                exam[_field] = json.loads(_val)
            except (json.JSONDecodeError, TypeError):
                exam[_field] = {}
    # Strip answer_key from exam before passing to template (students must not see correct answers)
    safe_exam = {k: v for k, v in exam.items() if k != "answer_key"}
    # Persist exam start time for accurate timer across refresh
    started_at = None
    try:
        draft = supabase.table("submissions").select("id,started_at,status").eq("exam_id", exam_id).eq("student_id", g.user_id).in_("status", ["draft"]).limit(1).execute().data
        if draft:
            started_at = draft[0].get("started_at")
    except Exception:
        pass
    if not started_at:
        import datetime as _dt
        started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        try:
            # Check if any draft exists, if not update/create with started_at
            draft = supabase.table("submissions").select("id,status").eq("exam_id", exam_id).eq("student_id", g.user_id).in_("status", ["draft"]).limit(1).execute().data
            if draft:
                supabase.table("submissions").update({"started_at": started_at}).eq("id", draft[0]["id"]).execute()
            else:
                supabase.table("submissions").insert({
                    "exam_id": exam_id,
                    "student_id": g.user_id,
                    "answers": {},
                    "score": 0,
                    "max_score": 100,
                    "status": "draft",
                    "started_at": started_at,
                }).execute()
        except Exception:
            pass
    anti_cheat_config = json.dumps({k: exam.get(k, v) for k, v in ac_defaults.items()})
    # Cek existing draft submission untuk timer persist across devices
    exam_started_at = None
    try:
        draft = supabase.table("submissions").select("started_at").eq("exam_id", exam_id).eq("student_id", g.user_id).eq("status", "draft").limit(1).execute()
        if draft.data and draft.data[0].get("started_at"):
            exam_started_at = draft.data[0]["started_at"]
    except Exception:
        pass
    # The way back in when the phone dies or the WiFi does. Issued with the
    # session and shown in the exam topbar; never a precondition for opening.
    recovery_code = issue_code(supabase, g.user_id, exam_id)
    resp = make_response(render_template("student/take_exam.html", exam=safe_exam, anti_cheat_config=anti_cheat_config, exam_started_at=exam_started_at, recovery_code=recovery_code))
    resp.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"
    return resp


@student_bp.route("/recover")
@login_required
def recover_exam_page():
    """The way back into an exam session that is still open.

    Lists the student's own sessions as links (no code needed when the server
    already knows who they are) and offers the code form as the fallback for when
    the exam list is stale, empty, or the exam is no longer on it.
    """
    supabase = get_supabase()
    open_sessions = []
    try:
        drafts = supabase.table("submissions").select(
            "id,exam_id,started_at,exams(id,title,subject,is_published,status,school_id,class_ids)"
        ).eq("student_id", g.user_id).eq("status", "draft").order("started_at", desc=True).execute().data or []
        for d in drafts:
            exam = d.get("exams") or {}
            if not exam:
                continue
            # Only offer what the student may actually open — the page must not
            # promise a session the rules would refuse.
            allowed, _reason = exam_sitting_allowed(supabase, exam, d.get("exam_id"), g.user_id)
            if allowed:
                open_sessions.append({
                    "exam_id": d.get("exam_id"),
                    "title": exam.get("title") or "Ujian",
                    "subject": exam.get("subject") or "",
                    "started_at": d.get("started_at"),
                })
    except Exception:
        current_app.logger.exception("Could not list open exam sessions for %s", g.user_id)

    return render_template("student/recover.html", open_sessions=open_sessions)


@student_bp.route("/api/recover-exam", methods=["POST"])
@login_required
@_rate_limit("20 per minute")
def api_recover_exam():
    """Trade a recovery code for the exam it belongs to.

    The code is scoped to the caller and the exam still has to pass the normal
    sitting rules, so this cannot be used to reach anyone else's session or an
    exam the student is not entitled to.
    """
    supabase = get_supabase()
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip()
    if len(code) != 6 or not code.isdigit():
        return jsonify({"error": "Masukkan 6 angka kode recovery."}), 400

    exam_id = redeem_code(supabase, g.user_id, code)
    if not exam_id:
        return jsonify({"error": "Kode tidak ditemukan untuk akun ini. Periksa kembali 6 angkanya."}), 404

    try:
        exam = row_or_none(
            supabase.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
        )
    except Exception:
        current_app.logger.exception("Recovery: exam lookup failed for %s", exam_id)
        return jsonify({"error": "Gagal memuat ujian. Coba lagi."}), 500
    if not exam:
        return jsonify({"error": "Ujian untuk kode ini sudah tidak tersedia."}), 404

    allowed, reason = exam_sitting_allowed(supabase, exam, exam_id, g.user_id)
    if not allowed:
        return jsonify({"error": reason}), 403

    latest = None
    try:
        latest = row_or_none(
            supabase.table("submissions").select("id,status")
            .eq("exam_id", exam_id).eq("student_id", g.user_id)
            .order("created_at", desc=True).limit(1).maybe_single().execute()
        )
    except Exception:
        current_app.logger.exception("Recovery: submission lookup failed for %s", exam_id)
    if latest and latest.get("status") in ("submitted", "graded", "published"):
        return jsonify({
            "error": "Ujian ini sudah Anda kumpulkan.",
            "redirect": "/student/results",
        }), 409

    log_activity("update", "submission", exam_id, new_data={"recovered": True},
                 user_id=g.user_id)
    return jsonify({"redirect": f"/student/exams/{exam_id}"})


@student_bp.route("/exams/<exam_id>/submit", methods=["POST"])
@login_required
def submit_exam(exam_id):
    # Check subscription
    from app.utils.auth import check_subscription_write
    allowed, msg = check_subscription_write()
    if not allowed:
        return jsonify({"error": msg}), 403

    supabase = get_supabase()

    # ── Query 1: GET exam (only needed columns) ──
    exam = supabase.table("exams").select(
        "id,is_published,status,class_ids,max_attempts,publish_mode,"
        "total_questions,answer_key,question_types,question_weights,question_pages"
    ).eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    if not exam.get("is_published") or exam.get("status") != "active":
        return jsonify({"error": "Exam is not available for submission"}), 403

    # ── Query 2: the student's own school and class ──
    # Replaces a class-only check that skipped itself when the student had no
    # class_id and swallowed its own errors — and that never checked the SCHOOL at
    # all, so a student could submit to another school's exam by posting its id and
    # pollute that school's results. One lookup, so the round-trip cost is the same.
    allowed, reason = exam_sitting_allowed(supabase, exam, exam_id, g.user_id)
    if not allowed:
        return jsonify({"error": reason}), 403

    # ── Query 3: GET existing submissions (single query for both checks) ──
    max_attempts = exam.get("max_attempts", 1)
    all_subs = supabase.table("submissions").select("id,status").eq(
        "exam_id", exam_id).eq("student_id", g.user_id).execute().data or []

    # Check attempts (exclude draft + retracted)
    active_statuses = ["submitted", "graded", "published"]
    attempt_count = sum(1 for s in all_subs if s.get("status") in active_statuses)
    if attempt_count >= max_attempts:
        return jsonify({"error": f"Anda sudah mencapai batas maksimal {max_attempts}x mengerjakan ujian ini"}), 409

    # Check for double submit
    already_active = [s for s in all_subs if s.get("status") in active_statuses]
    if already_active:
        current_app.logger.warning("Double submit blocked for exam %s user %s", exam_id, g.user_id)
        if request.is_json:
            return jsonify({"success": True, "note": "already_submitted"})
        return redirect("/student/results")

    # Find existing draft
    draft_sub = next((s for s in all_subs if s.get("status") == "draft"), None)

    answers = {}
    if request.is_json:
        data = request.get_json()
        answers = data.get("answers_json", data.get("answers", {}))
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except json.JSONDecodeError:
                pass
    else:
        answers_json = request.form.get("answers_json", "{}")
        try:
            answers = json.loads(answers_json)
        except json.JSONDecodeError:
            pass

    # Parse JSON fields that may be strings from Supabase
    for _fld in ("answer_key", "question_types", "question_weights", "question_pages"):
        _v = exam.get(_fld)
        if isinstance(_v, str):
            try:
                exam[_fld] = json.loads(_v)
            except (json.JSONDecodeError, TypeError):
                exam[_fld] = {}

    question_types = exam.get("question_types") or {}
    total_q = exam["total_questions"]
    mcq_count = sum(1 for v in (exam.get("answer_key") or {}).values() if v not in ("essay", "essay_text", "essay_canvas", None))
    essay_count = total_q - mcq_count
    question_weights = exam.get("question_weights") or {}
    if not question_weights and total_q > 0:
        mcq_pct, essay_pct = 70, 30
        if mcq_count == 0:
            mcq_pct, essay_pct = 0, 100
        elif essay_count == 0:
            mcq_pct, essay_pct = 100, 0
        if mcq_count > 0:
            each = round(mcq_pct / mcq_count, 2)
            for i in range(total_q):
                if question_types.get(str(i), "mcq") == "mcq":
                    question_weights[str(i)] = each
        if essay_count > 0:
            each = round(essay_pct / essay_count, 2)
            for i in range(total_q):
                if question_types.get(str(i), "mcq") != "mcq":
                    question_weights[str(i)] = each
    earned = 0.0
    for i in range(exam["total_questions"]):
        qtype = question_types.get(str(i), "mcq")
        key = exam.get("answer_key", {}).get(str(i))
        w = float(question_weights.get(str(i), 0))
        if qtype == "mcq" and key and w > 0:
            ans = answers.get(str(i))
            if isinstance(ans, dict):
                ans = ans.get('answer', '')
            if key == "bonus":
                if ans and str(ans).strip():
                    earned += w
            elif isinstance(key, list):
                if ans in key:
                    earned += w
            elif ans == key:
                earned += w

    score = round(min(earned, 100), 2)

    # Device mismatch detection: bandingkan IP/UA dengan first sync
    flags = []
    if isinstance(answers, dict):
        device_info = answers.get("_device_info", {})
        if device_info:
            current_ip = request.remote_addr or ""
            current_ua = request.headers.get("User-Agent", "")
            expected_ip = device_info.get("ip_address", "")
            expected_ua = device_info.get("user_agent", "")
            if current_ip and expected_ip and current_ip != expected_ip:
                flags.append({
                    "type": "device_mismatch",
                    "detail": f"IP berubah: {expected_ip} → {current_ip}",
                    "timestamp": int(time.time()),
                })
            if current_ua and expected_ua and current_ua != expected_ua:
                flags.append({
                    "type": "device_mismatch",
                    "detail": "User-Agent berubah",
                    "timestamp": int(time.time()),
                })

    # Speed analysis: detect suspiciously fast answering
    timestamps = answers.pop("_timestamps", {}) if isinstance(answers, dict) else {}
    if timestamps:
        mcq_times = []
        for i in range(exam["total_questions"]):
            if question_types.get(str(i), "mcq") == "mcq" and str(i) in timestamps:
                mcq_times.append(timestamps[str(i)])
        if len(mcq_times) >= 5:
            mcq_times.sort()
            time_span_s = (mcq_times[-1] - mcq_times[0]) / 1000
            seconds_per_q = time_span_s / len(mcq_times)
            if seconds_per_q < 1.5:
                flags.append({
                    "type": "suspicious_speed",
                    "detail": f"{len(mcq_times)} MCQ dalam {time_span_s:.1f}s ({seconds_per_q:.2f}s/soal)",
                    "timestamp": int(time.time()),
                })

    # ── Query 4: GET violation count ──
    from app.services.anti_cheat_service import calculate_graduated_penalty
    try:
        violation_count = supabase.table("violation_logs").select("id", count="exact").eq(
            "user_id", g.user_id).eq("exam_id", exam_id).execute().count or 0
    except Exception:
        violation_count = 0
    penalty_info = calculate_graduated_penalty(violation_count, exam)
    penalty = penalty_info["penalty"]

    final_score = max(0.0, round(score - penalty, 2))
    if flags:
        existing_answers = answers.get("_flags") or []
        if isinstance(existing_answers, list):
            existing_answers.extend(flags)
        else:
            existing_answers = flags
        answers["_flags"] = existing_answers

    submission = {
        "exam_id": exam_id,
        "student_id": g.user_id,
        "answers": {k: v for k, v in answers.items() if v is not None},
        "score": score,
        "max_score": 100.0,
        "violations": violation_count,
        "penalty": round(penalty, 2),
        "final_score": final_score,
        "status": "submitted",
        "is_published": exam.get("publish_mode") == "auto",
    }
    try:
        # ── Query 5: INSERT or UPDATE (draft found earlier) ──
        if draft_sub:
            supabase.table("submissions").update(submission).eq("id", draft_sub["id"]).execute()
        else:
            supabase.table("submissions").insert(submission).execute()
        log_activity("submit", "submission", None, new_data={"exam_id": exam_id, "score": score}, user_id=g.user_id)
    except Exception as e:
        import traceback
        current_app.logger.error("Submit error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")


@student_bp.route("/results")
@login_required
def results():
    supabase = get_supabase()
    submissions = []
    try:
        res = supabase.table("submissions") \
            .select("id, status, is_published, score, final_score, penalty, submitted_at, exams(id, title, subject)") \
            .eq("student_id", g.user_id) \
            .order("submitted_at", desc=True) \
            .execute()
        submissions = res.data or []
    except Exception as e:
        current_app.logger.error(f"Results query error: {e}")
        submissions = []
    for s in submissions:
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        ans = s.get("answers")
        if isinstance(ans, str):
            try:
                s["answers"] = json.loads(ans)
            except (json.JSONDecodeError, TypeError):
                s["answers"] = {}
        if not isinstance(s.get("answers"), dict):
            s["answers"] = {}
    # An unreleased result must not ship its marks to the browser. This list renders
    # every row's score and penalty, and the template hides them with `x-show` —
    # which still leaves the value in the DOM. So blank them server-side instead of
    # trusting a display toggle. The status label ('On Progress') already tells the
    # student the exam is being marked.
    for s in submissions:
        if not result_released(s):
            s["score"] = None
            s["final_score"] = None
            s["penalty"] = None
    # Hanya tampilkan 1 submission terbaru per exam (draft boleh standalone)
    seen = {}
    for s in submissions:
        eid = s.get("exam", {}).get("id")
        if not eid:
            continue
        if eid in seen:
            existing = seen[eid]
            # Draft diganti sama non-draft (final)
            if existing["status"] == "draft" and s["status"] != "draft":
                seen[eid] = s
            # Non-draft tidak diganti draft
            elif existing["status"] != "draft" and s["status"] == "draft":
                continue
        else:
            seen[eid] = s
    seen_no_key = [s for s in submissions if not s.get("exam", {}).get("id")]
    submissions = seen_no_key + list(seen.values())
    # Group by subject for total scores
    subjects = {}
    for s in submissions:
        subj = (s.get("exam") or {}).get("subject", "Lainnya")
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if subj not in subjects:
            subjects[subj] = {"scores": [], "count": 0}
        if sc is not None:
            subjects[subj]["scores"].append(float(sc))
        subjects[subj]["count"] += 1
    subject_totals = []
    for subj, data in subjects.items():
        scores = data["scores"]
        avg = round(sum(scores) / len(scores), 1) if scores else 0
        subject_totals.append({
            "name": subj,
            "avg": avg,
            "count": data["count"],
            "exam_count": len(scores),
        })
    subject_totals.sort(key=lambda x: x["name"])
    return render_template("student/results.html", submissions=submissions, subject_totals=subject_totals)


@student_bp.route("/results/<submission_id>")
@login_required
def result_detail(submission_id):
    supabase = get_supabase()
    try:
        res = supabase.table("submissions") \
            .select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, started_at, submitted_at, graded_at, teacher_feedback, exams(id, title, subject, answer_key, question_types, total_questions, pdf_page_urls)") \
            .eq("id", submission_id) \
            .eq("student_id", g.user_id) \
            .single() \
            .execute()
        submission = res.data
    except Exception:
        return redirect("/student/results")
    if not submission:
        return redirect("/student/results")
    if submission.get("exams"):
        submission["exam"] = submission.pop("exams")
    submission.setdefault("is_hidden", False)
    # Parse JSON strings
    for field in ("teacher_feedback", "answers"):
        val = submission.get(field)
        if isinstance(val, str):
            try:
                submission[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                submission[field] = {} if field != "answers" else {}
        if not isinstance(submission.get(field), dict):
            submission[field] = {} if field != "answers" else {}
    # Parse exam JSON fields
    for _field in ("question_types", "answer_key", "question_weights", "question_pages", "pdf_page_urls"):
        _val = submission.get("exam", {}).get(_field)
        if isinstance(_val, str):
            try:
                submission["exam"][_field] = json.loads(_val)
            except (json.JSONDecodeError, TypeError):
                submission["exam"][_field] = {}

    # An unreleased result must not carry the answer key to the browser at all.
    # Hiding it in the template would still put it in the HTML source, and the
    # student can open this page mid-exam — the draft submission created when the
    # exam is opened is listed in /student/results and links straight here.
    released = result_released(submission)
    if not released:
        submission.get("exam", {}).pop("answer_key", None)
    submission["released"] = released

    student_name = g.user_name or g.user_email or ""
    return render_template("student/result_detail.html", submission=submission,
                           student_name=student_name, released=released)


@student_bp.route("/results/<submission_id>/download-pdf")
@login_required
def download_result_pdf(submission_id):
    import base64
    import re
    import os
    from xhtml2pdf import pisa
    from PIL import Image, ImageDraw, ImageFont

    supabase = get_supabase()
    try:
        res = supabase.table("submissions") \
            .select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, started_at, submitted_at, graded_at, teacher_feedback, exams(id, title, subject, answer_key, question_types, total_questions, pdf_page_urls)") \
            .eq("id", submission_id) \
            .eq("student_id", g.user_id) \
            .single() \
            .execute()
        submission = res.data
    except Exception:
        return redirect("/student/results")
    if not submission:
        return redirect("/student/results")
    if submission.get("exams"):
        submission["exam"] = submission.pop("exams")
    submission.setdefault("is_hidden", False)

    # The PDF prints the answer key and per-question marks, so it follows the
    # same release rule as the on-screen view.
    if not result_released(submission):
        flash("Hasil ujian belum dirilis oleh guru.", "error")
        return redirect("/student/results")

    student_name = g.user_name or g.user_email or ""

    # Fetch teacher & school info
    teacher_name = ""
    school_info = {"name": "", "address": "", "logo_url": ""}
    try:
        exam_id = submission.get("exam_id", "")
        ex = supabase.table("exams").select("teacher_id").eq("id", exam_id).single().execute().data
        if ex:
            t = supabase.table("profiles").select("full_name").eq("id", ex["teacher_id"]).single().execute().data
            if t: teacher_name = t.get("full_name", "")
        prof = supabase.table("profiles").select("school_id").eq("id", g.user_id).single().execute().data
        if prof and prof.get("school_id"):
            sch = supabase.table("schools").select("name, address, logo_url").eq("id", prof["school_id"]).single().execute().data
            if sch: school_info = sch
    except:
        pass

    exam = submission.get("exam") or {}
    fb = submission.get("teacher_feedback") or {}
    fb_overlay = fb.get("overlay_pages", {})
    fb_scores = fb.get("scores", {})
    fb_comments = fb.get("comments", {})
    answer_key = exam.get("answer_key", {})
    question_types = exam.get("question_types", {})
    question_weights = exam.get("question_weights", {})
    total_q = exam.get("total_questions", 0)
    pdf_page_urls = exam.get("pdf_page_urls") or []
    answers = submission.get("answers") or {}
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}

    def data_url_to_pil(data_url):
        try:
            if not data_url or "," not in data_url:
                return None
            _, b64 = data_url.split(",", 1)
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        except Exception:
            return None

    def local_path_to_pil(path):
        try:
            if not path:
                return None
            if path.startswith("/static/"):
                full = os.path.join(current_app.static_folder, path.replace("/static/", "", 1))
            else:
                full = path
            if not os.path.exists(full):
                return None
            return Image.open(full).convert("RGBA")
        except Exception:
            return None

    def remove_black_pixels(img):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = data[x, y]
                if r < 20 and g < 20 and b < 20:
                    data[x, y] = (r, g, b, 0)
                elif r < 60 and g < 60 and b < 60:
                    alpha = int((r + g + b) / 3 * 255 / 60)
                    data[x, y] = (r, g, b, min(a, alpha))
        return img

    def draw_text_boxes(img, text_boxes, border_color, bg_color):
        if not text_boxes:
            return img
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        w, h = img.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for box in text_boxes:
            text = box.get("text", "").strip()
            if not text:
                continue
            x_pct = box.get("x_pct", 0)
            y_pct = box.get("y_pct", 0)
            bx = int(x_pct * w)
            by = int(y_pct * h)
            try:
                bbox = draw.textbbox((bx + 4, by + 4), text, font=font)
                tw = bbox[2] - bbox[0] + 10
                th = bbox[3] - bbox[1] + 8
            except Exception:
                tw, th = max(80, len(text) * 8), 22
            draw.rectangle([bx, by, bx + tw, by + th], fill=bg_color, outline=border_color, width=2)
            draw.text((bx + 5, by + 4), text, fill=(30, 41, 59, 255), font=font)
        return Image.alpha_composite(img, overlay)

    def pil_to_data_url(img, fmt="PNG"):
        buf2 = io.BytesIO()
        rgb = Image.new("RGB", img.size, (255, 255, 255))
        rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        rgb.save(buf2, format=fmt, quality=85)
        b64 = base64.b64encode(buf2.getvalue()).decode("ascii")
        return f"data:image/{fmt.lower()};base64,{b64}"

    merged_pages = {}
    for i in range(total_q):
        qtype = (question_types or {}).get(str(i), "mcq")
        student_ans = answers.get(str(i), "")
        student_ans_data = student_ans if isinstance(student_ans, dict) else {}
        teacher_ov = fb_overlay.get(str(i), {})

        s_pages = student_ans_data.get("pages", {}) if (student_ans_data and student_ans_data.get("pages")) else {}
        page_imgs = {}

        for p_idx_str, p_data in s_pages.items():
            p_idx = int(p_idx_str)
            pdf_idx = p_idx if ("0" in s_pages) else (p_idx - 1)
            pdf_url = pdf_page_urls[pdf_idx] if (0 <= pdf_idx < len(pdf_page_urls)) else ""

            bg = local_path_to_pil(pdf_url)
            if bg is None and p_data.get("canvas"):
                bg = data_url_to_pil(p_data.get("canvas", ""))
            if bg is None:
                continue

            canvas_raw = p_data.get("canvas", "")
            is_png = canvas_raw.startswith("data:image/png")
            has_canvas = canvas_raw and len(canvas_raw) > (800 if is_png else 3000)
            student_tbs = p_data.get("textBoxes") or []

            if has_canvas and pdf_url:
                overlay = data_url_to_pil(canvas_raw)
                if overlay:
                    is_jpeg = canvas_raw.startswith("data:image/jpeg") or canvas_raw.startswith("data:image/jpg")
                    if is_jpeg:
                        overlay = remove_black_pixels(overlay)
                    overlay = overlay.resize(bg.size, Image.LANCZOS)
                    bg = Image.alpha_composite(bg, overlay)

            if student_tbs:
                bg = draw_text_boxes(bg, student_tbs, (249, 115, 22, 255), (255, 255, 255, 235))

            page_imgs[pdf_idx] = bg

        for ov_p_str, ov_data in teacher_ov.items():
            ov_p_idx = int(ov_p_str)
            if ov_p_idx in page_imgs:
                bg = page_imgs[ov_p_idx]
            else:
                pdf_url = pdf_page_urls[ov_p_idx] if (0 <= ov_p_idx < len(pdf_page_urls)) else ""
                bg = local_path_to_pil(pdf_url)
                if bg is None:
                    continue

            ov_canvas = ov_data.get("canvas", "")
            is_ov_png = ov_canvas.startswith("data:image/png")
            has_ov_canvas = ov_canvas and len(ov_canvas) > (800 if is_ov_png else 1000)
            ov_tbs = ov_data.get("textBoxes") or []

            if has_ov_canvas:
                ov = data_url_to_pil(ov_canvas)
                if ov:
                    is_jpeg = ov_canvas.startswith("data:image/jpeg") or ov_canvas.startswith("data:image/jpg")
                    if is_jpeg:
                        ov = remove_black_pixels(ov)
                    ov = ov.resize(bg.size, Image.LANCZOS)
                    bg = Image.alpha_composite(bg, ov)

            if ov_tbs:
                bg = draw_text_boxes(bg, ov_tbs, (5, 150, 105, 255), (236, 253, 245, 235))

            page_imgs[ov_p_idx] = bg

        sorted_imgs = [page_imgs[k] for k in sorted(page_imgs.keys())]
        if sorted_imgs:
            merged_pages[f"{i}"] = [pil_to_data_url(img) for img in sorted_imgs]

    html_string = render_template(
        "student/result_detail_pdf.html",
        submission=submission,
        student_name=student_name,
        teacher_name=teacher_name,
        school_info=school_info,
        merged_pages=merged_pages,
    )
    buf = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buf)
    if pisa_status.err:
        current_app.logger.error("PDF generation error: %s", pisa_status.err)
    buf.seek(0)
    safe_title = re.sub(r'[^\w\s-]', '', exam.get('title', 'ujian')).replace(' ', '_')
    safe_name = re.sub(r'[^\w\s-]', '', student_name).replace(' ', '_')
    filename = f"hasil_{safe_title}_{safe_name}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@student_bp.route("/submissions/<submission_id>/retract", methods=["POST"])
@login_required
def retract_submission(submission_id):
    supabase = get_supabase()
    # Scoped to the caller, so another student's submission simply does not match.
    # ``maybe_single()`` rather than ``single()``: single() RAISES when nothing
    # matches, which made the 404 below unreachable and answered 500 instead.
    try:
        sub = row_or_none(
            supabase.table("submissions").select("answers,status")
            .eq("id", submission_id).eq("student_id", g.user_id)
            .maybe_single().execute()
        )
    except Exception:
        sub = None
    if not sub:
        if request.is_json:
            return jsonify({"error": "Not found"}), 404
        return redirect("/student/results")
    # A retraction voids the attempt and lets the exam be retaken, so it may only
    # be requested while the attempt is still open — never after the result has
    # been marked or released, which would let a student undo a finished score.
    if sub.get("status") not in ("draft", "submitted"):
        if request.is_json:
            return jsonify({"error": "Hasil sudah dinilai atau dirilis, tidak bisa ditarik."}), 409
        flash("Hasil sudah dinilai atau dirilis, sehingga tidak bisa ditarik. "
              "Silakan ajukan banding penalti bila perlu.", "error")
        return redirect("/student/results")
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict):
        answers = {}
    answers["_retract_request"] = {"status": "pending", "requested_at": datetime.now(timezone.utc).isoformat()}
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_request", "submission", submission_id, user_id=g.user_id)
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")


@student_bp.route("/submissions/<submission_id>/toggle-visibility", methods=["POST"])
@login_required
def toggle_submission_visibility(submission_id):
    return jsonify({"error": "Fitur belum tersedia (migrasi DB belum dijalankan)"}), 501


@student_bp.route("/submissions/<submission_id>/delete", methods=["POST"])
@login_required
def delete_submission(submission_id):
    if g.get("user_role") == "murid":
        return jsonify({"error": "Siswa tidak bisa menghapus submission"}), 403
    supabase = get_supabase()
    sub = supabase.table("submissions").select("id, status, student_id").eq("id", submission_id).single().execute().data
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if g.get("user_role") not in ("super_admin", "admin_sekolah") and sub.get("student_id") != g.user_id:
        return jsonify({"error": "Unauthorized"}), 403
    if sub.get("status") not in ("submitted", "draft"):
        return jsonify({"error": "Cannot delete this submission"}), 403
    supabase.table("submissions").delete().eq("id", submission_id).execute()
    return jsonify({"success": True})


@student_bp.route("/comms")
@login_required
def student_comms():
    return render_template("shared/comms.html")


@student_bp.route("/settings", methods=["GET"])
@login_required
def student_settings():
    """Student settings page (password, data export, deletion request, PDP settings)."""
    supabase = get_supabase()
    profile = {}
    try:
        # Fetch PDP consent status
        profile_data = supabase.table("profiles").select(
            "id, full_name, phone, role, pdp_agreed"
        ).eq("id", g.user_id).single().execute().data
        if profile_data:
            profile = profile_data
    except Exception as e:
        current_app.logger.error(f"Error fetching student profile for settings: {e}")
        profile = {}

    return render_template("student/settings.html", profile=profile)


@student_bp.route("/settings/pdp-update", methods=["POST"])
@login_required
def student_update_pdp_settings():
    if g.get("user_role") != "murid":
        return jsonify({"error": "Forbidden"}), 403

    supabase = get_supabase()
    data = request.get_json() or {}
    pdp_agreed = data.get("pdp_agreed", False)

    try:
        supabase.table("profiles").update({"pdp_agreed": pdp_agreed}).eq("id", g.user_id).execute()
        log_activity("update", "pdp_settings", g.user_id, new_data={"pdp_agreed": pdp_agreed}, user_id=g.user_id)
        return jsonify({"status": "success", "message": "Persetujuan PDP berhasil diperbarui."}), 200
    except Exception as e:
        current_app.logger.error(f"Error updating student PDP settings: {e}")
        return jsonify({"status": "error", "message": f"Gagal memperbarui pengaturan PDP: {str(e)}"}), 500

