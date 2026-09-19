import io
import json
import os
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, g, jsonify, current_app, make_response, flash
from app.utils.auth import login_required, get_supabase
from app.utils.cache import cache_get, cache_set
from app.utils.helpers import read_with_retry, row_or_none
from app.utils.exam_access import (
    class_assignment_allows, exam_sitting_allowed, result_released,
)
from app.utils.exam_recovery import issue_code, redeem_code
from app.services.audit_service import log_activity
from app.services.pdf_service import ensure_page_thumbs
from app.services.question_types import (
    default_weights, earned_points, is_objective, objective_result, public_options,
)
from app.services.submission_service import finish_sitting, open_sitting
from app.utils.rate_limiter import limiter
from app.utils.req_cache import (active_whiteboards_for, class_row, school_features,
                                 school_subject_count)

student_bp = Blueprint("student", __name__)

# Read once, use everywhere: the dashboard needs both the available list and the
# result cards, and it used to fetch the same submissions row set three times.
SUBMISSION_COLUMNS = ("id, exam_id, student_id, score, max_score, violations, penalty, "
                      "final_score, status, is_published, submitted_at, graded_at, "
                      "exams(id, title, question_types, total_questions)")


def _student_submissions(supabase, student_id):
    """Every submission this student has, in one round-trip.

    Retracted rows are included on purpose: the page derives both the
    "already answered, so hide it from the list" set and the result cards from
    this one read, and `status` is on the row.
    """
    try:
        return (supabase.table("submissions").select(SUBMISSION_COLUMNS)
                .eq("student_id", student_id)
                .order("submitted_at", desc=True).execute().data) or []
    except Exception as e:
        current_app.logger.error(f"Submissions query error: {e}")
        return []


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
    # The session already carries both of these columns, so the page does not ask
    # for the profile again — it used to, three separate times on this page.
    student_class_id = g.get("user_class_id")
    student_school_id = g.get("user_school_id")

    # One submissions read for the whole page. This was three — one for the
    # available list, one for retracted, one for the result cards — over the same
    # rows, at ~100-165 ms each, when the row already carries `status`.
    subs = _student_submissions(supabase, g.user_id)
    submitted_ids = {s["exam_id"] for s in subs
                     if s.get("status") in ("submitted", "graded", "published")}
    submitted_ids -= {s["exam_id"] for s in subs if s.get("status") == "retracted"}

    try:
        query = supabase.table("exams").select("id,title,subject,start_at,class_ids,question_types,total_questions,duration_minutes").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        # Same rule as the door the pupil walks through (`exam_sitting_allowed`),
        # from the same function: an exam is offered to the classes the teacher
        # assigned it to, and to nobody else.
        all_exams = read_with_retry(query.execute).data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        for e in all_exams:
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            if class_assignment_allows(e, student_class_id):
                available_exams.append(e)
    except Exception as e:
        current_app.logger.error(f"Dashboard query error: {e}")
    available_exams = [e for e in available_exams if e["id"] not in submitted_ids]
    completed_exams = []
    all_scores = []
    for s in subs:
        # Only show submitted/graded/published in dashboard (hide drafts, and
        # retracted rows, which now arrive in this same read)
        if s.get("status") in ("draft", "retracted"):
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

    # Class and subject count come out of the shared cache: the class row is the
    # same for every student in the class, and the count is the same for the whole
    # school, so neither is worth a query per page view. (The count was asked
    # *twice* here with an identical filter, and the second answer overwrote the
    # first.)
    student_class = None
    subject_count = 0
    if student_class_id:
        student_class = class_row(student_class_id) or None
    if student_school_id:
        subject_count = school_subject_count(student_school_id)

    # Active whiteboards for student's class (only if the school has it enabled).
    # The feature flag rides on the cached school row; the board list is cached
    # per class for 30 s, so a class of 30 students costs one query.
    active_whiteboards = []
    if student_class_id and student_school_id:
        try:
            if school_features(student_school_id).get("whiteboard_enabled", True):
                active_whiteboards = active_whiteboards_for(student_class_id, student_school_id)
        except Exception:
            pass

    template_data = {
        "available_exams": available_exams,
        "completed_exams": completed_exams[:5],
        "avg_score": avg_score,
        "user_name": user_name,
        "student_class": student_class,
        "subject_count": subject_count,
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

    # Class and school come from the session: this page is opened repeatedly
    # during an exam, and it used to spend a profile round-trip on two columns
    # the token check had already read.
    student_class_id = g.get("user_class_id")
    student_school_id = g.get("user_school_id")

    exams = []
    try:
        # Only the columns the card renders: `select("*")` shipped the whole exam
        # row — which carries question_canvas JSON — to every student's browser
        # context, and made the response bigger to parse for nothing.
        # `class_ids` belongs in this column list: the filter below reads it, and
        # leaving it out made every row look unassigned. `not cids` then matched
        # every exam in the school, so a pupil in X-B was shown the papers for X-A
        # and XI-A and had all three refused one click later.
        query = supabase.table("exams").select("id,title,subject,class_ids,question_types,total_questions,duration_minutes").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        all_exams = read_with_retry(lambda: query.order("created_at", desc=True).execute()).data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        # Filter by class_id if student has one, AND check scheduling
        for e in all_exams:
            # `question_types` is jsonb, and a jsonb value written with
            # `json.dumps` arrives as a JSON *string*. The card asks it whether the
            # exam has an essay question; on a string that question is a 500, and
            # the student loses the whole exam list rather than one badge. The exam
            # page already normalises its own copy of this column — the list was
            # the one place that did not.
            if isinstance(e.get("question_types"), str):
                try:
                    e["question_types"] = json.loads(e["question_types"])
                except (json.JSONDecodeError, TypeError):
                    e["question_types"] = {}
            # Skip exams with future start_at
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            # The same predicate the access guard applies. `not cids` — an exam
            # the teacher never assigned — is no longer a match for everyone.
            if class_assignment_allows(e, student_class_id):
                exams.append(e)
    except Exception as e:
        current_app.logger.error(f"Exam list query error: {e}")

    submitted_ids = set()
    try:
        # One read where there were two: `status` is on the row, so the
        # "answered" set and the retracted set come from the same query. Only
        # submitted/graded/published hide an exam (a draft still shows), and a
        # retracted one comes back into the list.
        subs = supabase.table("submissions").select("exam_id, status").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published", "retracted"]).execute().data or []
        submitted_ids = {s["exam_id"] for s in subs
                         if s.get("status") in ("submitted", "graded", "published")}
        submitted_ids -= {s["exam_id"] for s in subs if s.get("status") == "retracted"}
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
    # What a matching, drag-and-drop or ordering question needs in order to be
    # *answerable*: the two columns, or the items to arrange. The pairing itself
    # stays behind, and it is `public_options()` that decides what "the pairing
    # itself" means — one place, so no route can send a key by accident. An MCQ and
    # a true/false question need nothing extra, so they are absent from the map
    # rather than empty in it.
    _qt = exam.get("question_types") or {}
    _ak = exam.get("answer_key") or {}
    question_options = {}
    for _i in range(exam.get("total_questions") or 0):
        _public = public_options(_qt.get(str(_i)), _ak.get(str(_i)))
        if _public:
            question_options[str(_i)] = _public
    # The page rail renders every page at once, so it is given thumbnails rather
    # than the full pages. Idempotent and cheap when they already exist; it only
    # does work for an exam uploaded before thumbnails were generated.
    ensure_page_thumbs(exam_id, exam.get("pdf_page_urls"))
    # Persist the exam start time so the timer survives a refresh. This is the
    # row the sitting lives in — one per (student, exam) for *every* status, so a
    # re-sit after an approved retraction is reopened here instead of colliding on
    # the unique constraint. One call where there were three reads and a blind
    # INSERT (see app/services/submission_service.py for what that cost a student).
    exam_started_at = None
    try:
        sitting, _opened = open_sitting(supabase, exam_id, g.user_id)
        exam_started_at = sitting.get("started_at") or None
    except Exception:
        current_app.logger.exception(
            "Could not open a sitting for exam %s user %s", exam_id, g.user_id
        )
    anti_cheat_config = json.dumps({k: exam.get(k, v) for k, v in ac_defaults.items()})
    # The way back in when the phone dies or the WiFi does. Issued with the
    # session and shown in the exam topbar; never a precondition for opening.
    recovery_code = issue_code(supabase, g.user_id, exam_id)
    resp = make_response(render_template("student/take_exam.html", exam=safe_exam, anti_cheat_config=anti_cheat_config, exam_started_at=exam_started_at, recovery_code=recovery_code, question_options=question_options))
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
    question_weights = exam.get("question_weights") or {}
    if not question_weights and total_q > 0:
        question_weights = default_weights(question_types, total_q)
    # The weighted objective marks. The *final* score is built from these, and the
    # essay marks a teacher enters are added on top of them. One rule for every
    # objective type, so a true/false or a matching question is marked here exactly
    # as it is marked by the sync route and the scan task.
    earned, _graded = earned_points(
        question_types, exam.get("answer_key"), answers, question_weights, total_q)
    # The stored objective score is one rule for the whole app now
    # (`question_types.objective_result`), and it is a percentage **of the paper's
    # objective questions**. This route used to store the weighted marks here — a
    # different number on a different scale from the one the teacher's "recalculate
    # scores" wrote to the same column, so a pupil's own "MCQ:" figure moved the
    # moment a teacher recalculated.
    score = objective_result(
        question_types, exam.get("answer_key"), answers, total_q).score

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
            if is_objective(question_types.get(str(i))) and str(i) in timestamps:
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
    from app.services.anti_cheat_service import (
        calculate_graduated_penalty, count_penalized_violations,
    )
    violation_count = count_penalized_violations(supabase, g.user_id, exam_id)
    penalty_info = calculate_graduated_penalty(violation_count, exam)
    penalty = penalty_info["penalty"]

    # `earned`, not `score`: the stored objective score is a percentage of the
    # paper, while the final mark is the weighted total the essay marks are added
    # to. Keeping the arithmetic here means this route's `final_score` is the value
    # it has always written.
    final_score = max(0.0, round(earned - penalty, 2))
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
        # ── Query 5: write into the row this (student, exam) already owns ──
        # Not "update the draft, else INSERT": the unique constraint counts every
        # status, so a row that is neither decides nothing and the INSERT collides.
        # `all_subs` is the read from Query 3 — no extra round trip.
        outcome = finish_sitting(supabase, exam_id, g.user_id, submission, all_subs)
        if outcome == "already_submitted":
            current_app.logger.info(
                "Concurrent submit for exam %s user %s — one recorded", exam_id, g.user_id
            )
            if request.is_json:
                return jsonify({"success": True, "note": "already_submitted"})
            return redirect("/student/results")
        log_activity("submit", "submission", None, new_data={"exam_id": exam_id, "score": score}, user_id=g.user_id)
    except Exception:
        # The student sees a sentence, not the database's own words: a raw
        # constraint name in an alert is unreadable and tells them nothing they
        # can act on. The detail goes to the log, where it can be acted on.
        current_app.logger.exception(
            "Submit failed for exam %s user %s", exam_id, g.user_id
        )
        return jsonify({"error": "Gagal menyimpan jawaban. Coba lagi."}), 500
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


@student_bp.route("/results/<submission_id>/print")
@login_required
def print_result_card(submission_id):
    """The report card as a document to print, not a picture of the app.

    The screen page can be printed too, but it prints the interface around the
    result: toolbars, navigation and controls the paper has no use for. This
    renders the same result as a plain document.

    It follows the release rule for the same reason the screen and the PDF do —
    the sheet carries the answer key and the per-question marks.
    """
    from app.services.report_card_service import load_report_card, print_stamp

    card = load_report_card(get_supabase(), submission_id, student_id=g.user_id)
    if not card:
        flash("Hasil tidak ditemukan.", "error")
        return redirect("/student/results")
    if not card["released"]:
        flash("Hasil ujian belum dirilis oleh guru.", "error")
        return redirect("/student/results")
    return render_template("print/report_card.html", printed_on=print_stamp(),
                           show_key=True, **card)


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
            # The key *is* the page index: the exam page writes `canvasData[i][p]`
            # with `p = this.page - 1`. This used to subtract one whenever the map
            # happened to lack a '0' key, which moved any drawing whose first page
            # was not page 1 onto the page before it — so a student who drew on
            # page 3 got the overlay composed onto page 2 in their own report. The
            # guess is gone because it was measurably wrong: across every stored
            # submission no key was at or beyond the exam's page count, which a
            # page number would have to reach for a drawing on the last page.
            pdf_idx = p_idx
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

