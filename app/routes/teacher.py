import json
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash, g, send_file
from app.utils.auth import teacher_or_admin_required, get_supabase, login_required, subscription_write_required
from app.decorators.security import require_school_access
from app.services.export_service import export_to_xlsx, export_to_pdf
from app.services.answer_sheet_generator import generate_answer_sheet
from app.services.pdf_service import upload_pdf
from app.services.audit_service import log_activity

teacher_bp = Blueprint("teacher", __name__)


def _extract_mcq_answer(student_ans):
    if isinstance(student_ans, dict):
        return student_ans.get('answer', '')
    return student_ans or ''

def _is_mcq_correct(student_ans, key_val):
    ans = _extract_mcq_answer(student_ans)
    if key_val == "bonus":
        return bool(ans and str(ans).strip())
    if isinstance(key_val, list):
        if not ans:
            return False
        return ans in key_val
    return ans == key_val


def _recalculate_scores(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return
    answer_key = exam.get("answer_key") or {}
    question_types = exam.get("question_types") or {}
    question_weights = exam.get("question_weights") or {}
    total_q = exam.get("total_questions", 0)
    if not question_weights and total_q > 0:
        mcq_count = sum(1 for i in range(total_q) if question_types.get(str(i), "mcq") == "mcq")
        essay_count = total_q - mcq_count
        mcq_pct = 70
        essay_pct = 30
        if mcq_count > 0 and essay_count > 0:
            pass
        elif mcq_count == 0:
            mcq_pct, essay_pct = 0, 100
        else:
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
    subs = supabase.table("submissions").select("id, answers, penalty, teacher_feedback").eq("exam_id", exam_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
    for sub in subs:
        answers = sub.get("answers") or {}
        earned = 0.0
        for i in range(total_q):
            qtype = question_types.get(str(i), "mcq")
            key_val = answer_key.get(str(i))
            w = float(question_weights.get(str(i), 0))
            if w <= 0:
                continue
            if qtype == "mcq":
                if _is_mcq_correct(answers.get(str(i)), key_val):
                    earned += w
        fb = sub.get("teacher_feedback") or {}
        fb_scores = fb.get("scores", {}) or {}
        for qi, sv in fb_scores.items():
            if sv is not None and sv != "":
                ew = float(question_weights.get(str(qi), 0))
                if ew > 0:
                    earned += float(sv) / 100.0 * ew
        final = round(min(earned, 100), 2)
        penalty = float(sub.get("penalty") or 0)
        final = max(0, round(final - penalty, 2))
        mcq_correct = 0
        mcq_count = sum(1 for i in range(total_q) if question_types.get(str(i), "mcq") == "mcq")
        for i in range(total_q):
            qtype = question_types.get(str(i), "mcq")
            key_val = answer_key.get(str(i))
            if qtype == "mcq" and key_val:
                if _is_mcq_correct(answers.get(str(i)), key_val):
                    mcq_correct += 1
        mcq_score = round((mcq_correct / max(mcq_count, 1)) * 100, 2) if mcq_count > 0 else 0
        supabase.table("submissions").update({
            "score": mcq_score,
            "final_score": final,
        }).eq("id", sub["id"]).execute()


@teacher_bp.route("/dashboard")
@teacher_or_admin_required
def dashboard():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    exams = res.data or []

    exam_ids = [e["id"] for e in exams]
    total_students = 0
    all_scores = []
    if exam_ids:
        subs = supabase.table("submissions").select("student_id,score,final_score").in_("exam_id", exam_ids).execute().data or []
        unique_students = set(s["student_id"] for s in subs)
        total_students = len(unique_students)
        all_scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs if s.get("final_score") or s.get("score")]

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else "-"

    # Get teacher's school_id (column may not exist in older schema)
    school_id = None
    try:
        profile = supabase.table("profiles").select("*").eq("id", g.user_id).single().execute().data or {}
        school_id = profile.get("school_id")
    except Exception:
        pass

    # Get assignments and available classes/subjects (tables may not exist)
    assignments = []
    classes = []
    subjects = []
    if school_id:
        try:
            assignments = supabase.table("teacher_assignments") \
                .select("*, classes(id, name, grade_level), subjects(id, name, code)") \
                .eq("teacher_id", g.user_id) \
                .eq("school_id", school_id) \
                .execute().data or []
        except Exception:
            pass
        try:
            classes = supabase.table("classes").select("id, name, grade_level") \
                .eq("school_id", school_id) \
                .order("name").execute().data or []
        except Exception:
            pass
        try:
            subjects = supabase.table("subjects").select("id, name, code") \
                .eq("school_id", school_id) \
                .eq("is_active", True) \
                .order("name").execute().data or []
        except Exception:
            pass

    user_name = g.user_name or g.user_email or ""
    # School info for header
    school_info = {}
    if school_id:
        try:
            school_info = supabase.table("schools").select("name, npsn, logo_url").eq("id", school_id).single().execute().data or {}
        except Exception:
            pass
    return render_template("teacher/dashboard.html", exams=exams, total_students=total_students,
                           avg_score=avg_score, all_scores=all_scores, user_name=user_name,
                           assignments=assignments, classes=classes, subjects=subjects,
                           school_info=school_info)


@teacher_bp.route("/exams/new", methods=["GET", "POST"])
@subscription_write_required
@teacher_or_admin_required
def exam_form():
    supabase = get_supabase()
    if request.method == "GET":
        supabase = get_supabase()
        sid = g.get("user_school_id")
        subjects = []
        classes = []
        if sid:
            subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
            classes = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
        return render_template("teacher/exam_form.html", exam=None, subjects=subjects, classes=classes)

    title = request.form.get("title")
    subject = request.form.get("subject")
    subject_id = request.form.get("subject_id") or None
    class_ids = json.loads(request.form.get("class_ids", "[]"))
    start_at_str = request.form.get("start_at", "").strip()
    start_at = start_at_str if start_at_str else None
    is_template = request.form.get("is_template", "false") == "true"
    source_exam_id = request.form.get("source_exam_id") or None
    max_attempts = int(request.form.get("max_attempts", 1))
    publish_mode = request.form.get("publish_mode", "manual")
    total_questions = int(request.form.get("total_questions", 10))
    duration_minutes = int(request.form.get("duration_minutes", 60))
    passing_score = int(request.form.get("passing_score", 70))
    description = request.form.get("description", "")
    action = request.form.get("action", "save_draft")

    question_types = json.loads(request.form.get("question_types", "{}"))
    answer_key = json.loads(request.form.get("answer_key", "{}"))
    question_weights = json.loads(request.form.get("question_weights", "{}"))
    question_audio = {}
    question_canvas = {}
    anti_cheat_enabled = request.form.get("anti_cheat_enabled", "true") == "true"
    penalty_per_violation = int(request.form.get("penalty_per_violation", 5))
    max_violations = int(request.form.get("max_violations", 5))
    auto_submit_on_max = request.form.get("auto_submit_on_max", "true") == "true"
    fullscreen_required = request.form.get("fullscreen_required", "true") == "true"
    randomize_questions = request.form.get("randomize_questions", "false") == "true"
    randomize_options = request.form.get("randomize_options", "false") == "true"
    watermark_name = request.form.get("watermark_name", "true") == "true"
    block_copy_paste = request.form.get("block_copy_paste", "true") == "true"
    block_right_click = request.form.get("block_right_click", "true") == "true"
    block_screenshot = request.form.get("block_screenshot", "false") == "true"
    allow_calculator = request.form.get("allow_calculator", "false") == "true"
    for i in range(total_questions):
        qtype = question_types.get(str(i), "mcq")
        if qtype != "mcq":
            question_canvas[str(i)] = True
        audio_url = request.form.get(f"audio_{i}", "").strip()
        youtube_url = request.form.get(f"youtube_{i}", "").strip()
        media = {}
        if audio_url:
            media["audio"] = audio_url
        if youtube_url:
            media["youtube"] = youtube_url
        if media:
            question_audio[str(i)] = media

    data = {
        "teacher_id": g.user_id,
        "school_id": g.get("user_school_id"),
        "title": title,
        "subject": subject,
        "subject_id": subject_id,
        "class_ids": class_ids,
        "start_at": start_at,
        "is_template": is_template,
        "source_exam_id": source_exam_id,
        "max_attempts": max_attempts,
        "publish_mode": publish_mode,
        "total_questions": total_questions,
        "duration_minutes": duration_minutes,
        "passing_score": passing_score,
        "description": description,
        "status": "active" if action == "save_active" else "draft",
        "answer_key": answer_key,
        "question_types": question_types,
        "question_weights": question_weights,
        "question_audio": question_audio,
        "question_canvas": question_canvas,
        "anti_cheat_enabled": anti_cheat_enabled,
        "penalty_per_violation": penalty_per_violation,
        "max_violations": max_violations,
        "auto_submit_on_max": auto_submit_on_max,
        "fullscreen_required": fullscreen_required,
        "randomize_questions": randomize_questions,
        "randomize_options": randomize_options,
        "watermark_name": watermark_name,
        "block_copy_paste": block_copy_paste,
        "block_right_click": block_right_click,
        "block_screenshot": block_screenshot,
        "allow_calculator": allow_calculator,
    }
    try:
        res = supabase.table("exams").insert(data).execute()
    except Exception:
        for key in ["question_weights", "anti_cheat_enabled", "penalty_per_violation", "max_violations", "auto_submit_on_max", "fullscreen_required", "randomize_questions", "randomize_options", "watermark_name", "block_copy_paste", "block_right_click", "block_screenshot", "allow_calculator", "subject_id", "class_ids", "start_at", "is_template", "source_exam_id", "max_attempts", "publish_mode"]:
            data.pop(key, None)
        res = supabase.table("exams").insert(data).execute()
    exam_id = res.data[0]["id"]
    log_activity("create", "exam", exam_id, new_data={"title": title, "subject": subject, "total_questions": total_questions}, user_id=g.user_id)
    return redirect(f"/teacher/exams/{exam_id}")


@teacher_bp.route("/exams/<exam_id>", methods=["GET", "POST", "DELETE"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_detail(exam_id):
    supabase = get_supabase()
    if request.method == "DELETE":
        supabase.table("exams").delete().eq("id", exam_id).execute()
        return jsonify({"success": True})
    if request.method == "GET":
        try:
            res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
        except Exception:
            res = supabase.table("exams").select("id,title,subject,total_questions,duration_minutes,passing_score,description,status,answer_key,question_types,question_audio,question_canvas,teacher_id,created_at").eq("id", exam_id).single().execute()
        exam_data = res.data
        exam_data.setdefault("question_weights", {})
        sid = g.get("user_school_id")
        subjects = []
        classes = []
        if sid:
            subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
            classes = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
        return render_template("teacher/exam_form.html", exam=exam_data, subjects=subjects, classes=classes)

    title = request.form.get("title")
    subject = request.form.get("subject")
    subject_id = request.form.get("subject_id") or None
    class_ids = json.loads(request.form.get("class_ids", "[]"))
    start_at_str = request.form.get("start_at", "").strip()
    start_at = start_at_str if start_at_str else None
    is_template = request.form.get("is_template", "false") == "true"
    source_exam_id = request.form.get("source_exam_id") or None
    max_attempts = int(request.form.get("max_attempts", 1))
    publish_mode = request.form.get("publish_mode", "manual")
    total_questions = int(request.form.get("total_questions", 10))
    duration_minutes = int(request.form.get("duration_minutes", 60))
    passing_score = int(request.form.get("passing_score", 70))
    description = request.form.get("description", "")
    action = request.form.get("action", "save_draft")
    question_types = json.loads(request.form.get("question_types", "{}"))
    answer_key = json.loads(request.form.get("answer_key", "{}"))
    question_weights = json.loads(request.form.get("question_weights", "{}"))
    question_audio = {}
    question_canvas = {}
    anti_cheat_enabled = request.form.get("anti_cheat_enabled", "true") == "true"
    penalty_per_violation = int(request.form.get("penalty_per_violation", 5))
    max_violations = int(request.form.get("max_violations", 5))
    auto_submit_on_max = request.form.get("auto_submit_on_max", "true") == "true"
    fullscreen_required = request.form.get("fullscreen_required", "true") == "true"
    randomize_questions = request.form.get("randomize_questions", "false") == "true"
    randomize_options = request.form.get("randomize_options", "false") == "true"
    watermark_name = request.form.get("watermark_name", "true") == "true"
    block_copy_paste = request.form.get("block_copy_paste", "true") == "true"
    block_right_click = request.form.get("block_right_click", "true") == "true"
    block_screenshot = request.form.get("block_screenshot", "false") == "true"
    allow_calculator = request.form.get("allow_calculator", "false") == "true"
    for i in range(total_questions):
        qtype = question_types.get(str(i), "mcq")
        if qtype != "mcq":
            question_canvas[str(i)] = True
        audio_url = request.form.get(f"audio_{i}", "").strip()
        youtube_url = request.form.get(f"youtube_{i}", "").strip()
        media = {}
        if audio_url:
            media["audio"] = audio_url
        if youtube_url:
            media["youtube"] = youtube_url
        if media:
            question_audio[str(i)] = media

    data = {
        "title": title,
        "subject": subject,
        "subject_id": subject_id,
        "class_ids": class_ids,
        "start_at": start_at,
        "is_template": is_template,
        "source_exam_id": source_exam_id,
        "max_attempts": max_attempts,
        "publish_mode": publish_mode,
        "total_questions": total_questions,
        "duration_minutes": duration_minutes,
        "passing_score": passing_score,
        "description": description,
        "status": "active" if action == "save_active" else "draft",
        "answer_key": answer_key,
        "question_types": question_types,
        "question_weights": question_weights,
        "question_audio": question_audio,
        "question_canvas": question_canvas,
        "anti_cheat_enabled": anti_cheat_enabled,
        "penalty_per_violation": penalty_per_violation,
        "max_violations": max_violations,
        "auto_submit_on_max": auto_submit_on_max,
        "fullscreen_required": fullscreen_required,
        "randomize_questions": randomize_questions,
        "randomize_options": randomize_options,
        "watermark_name": watermark_name,
        "block_copy_paste": block_copy_paste,
        "block_right_click": block_right_click,
        "block_screenshot": block_screenshot,
        "allow_calculator": allow_calculator,
    }
    try:
        supabase.table("exams").update(data).eq("id", exam_id).execute()
    except Exception:
        for key in ["question_weights", "anti_cheat_enabled", "penalty_per_violation", "max_violations", "auto_submit_on_max", "fullscreen_required", "randomize_questions", "randomize_options", "watermark_name", "block_copy_paste", "block_right_click", "block_screenshot", "allow_calculator", "subject_id", "class_ids", "start_at", "is_template", "source_exam_id", "max_attempts", "publish_mode"]:
            data.pop(key, None)
        supabase.table("exams").update(data).eq("id", exam_id).execute()
    _recalculate_scores(exam_id)
    log_activity("update", "exam", exam_id, new_data={"title": title, "status": data.get("status")}, user_id=g.user_id)
    return redirect(f"/teacher/exams/{exam_id}")


@teacher_bp.route("/preview/<exam_id>")
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def preview_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
    if not res.data:
        return redirect("/teacher/exams")
    return render_template("teacher/preview_exam.html", exam=res.data)


@teacher_bp.route("/exams/<exam_id>/publish-exam", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def publish_exam(exam_id):
    supabase = get_supabase()
    supabase.table("exams").update({
        "is_published": True,
        "status": "active",
    }).eq("id", exam_id).execute()
    log_activity("publish", "exam", exam_id, user_id=g.user_id)
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams/<exam_id>/upload-pdf", methods=["GET", "POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def upload_exam_pdf(exam_id):
    supabase = get_supabase()
    if request.method == "GET":
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam)

    if "pdf" not in request.files:
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam, error="Pilih file PDF")

    file = request.files["pdf"]
    try:
        result = upload_pdf(file, exam_id)
    except ValueError as e:
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam, error=str(e))
    supabase.table("exams").update({
        "pdf_url": result["pdf_path"],
        "pdf_page_urls": result["page_urls"],
    }).eq("id", exam_id).execute()
    log_activity("upload", "exam", exam_id, new_data={"pages": len(result.get("page_urls", []))}, user_id=g.user_id)
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams")
@teacher_or_admin_required
def my_exams():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).order("created_at", desc=True).execute()
    return render_template("teacher/exams.html", exams=res.data or [])


@teacher_bp.route("/exams/<exam_id>/toggle-status", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def toggle_exam_status(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("status").eq("id", exam_id).single().execute().data
    new_status = "draft" if exam["status"] == "active" else "active"
    supabase.table("exams").update({"status": new_status}).eq("id", exam_id).execute()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "status": new_status})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/toggle-visibility", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def toggle_exam_visibility(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("is_published").eq("id", exam_id).single().execute().data
    new_val = not exam["is_published"]
    supabase.table("exams").update({"is_published": new_val}).eq("id", exam_id).execute()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "is_published": new_val})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/delete", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def delete_exam(exam_id):
    supabase = get_supabase()
    supabase.table("violation_logs").delete().eq("exam_id", exam_id).execute()
    supabase.table("exam_access_codes").delete().eq("exam_id", exam_id).execute()
    supabase.table("analytics_cache").delete().eq("exam_id", exam_id).execute()
    supabase.table("submissions").delete().eq("exam_id", exam_id).execute()
    supabase.table("exams").delete().eq("id", exam_id).execute()
    log_activity("delete", "exam", exam_id, user_id=g.user_id)
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True})
    return redirect("/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/duplicate", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def duplicate_exam(exam_id):
    supabase = get_supabase()
    try:
        res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
        exam = res.data
        if not exam:
            flash("Ujian tidak ditemukan", "error")
            return redirect("/teacher/exams")
        import copy, uuid
        new_data = {k: v for k, v in exam.items() if k not in ("id", "created_at", "updated_at")}
        new_data["title"] = exam["title"] + " (salinan)"
        new_data["status"] = "draft"
        new_data["is_published"] = False
        new_data["is_template"] = False
        new_data["source_exam_id"] = exam_id
        new_exam = supabase.table("exams").insert(new_data).execute()
        new_id = new_exam.data[0]["id"]
        log_activity("duplicate", "exam", new_id, new_data={"source": exam_id, "title": new_data["title"]}, user_id=g.user_id)
        flash("Ujian berhasil digandakan. Silakan edit sesuai kebutuhan.", "success")
        return redirect(f"/teacher/exams/{new_id}")
    except Exception as e:
        flash(f"Gagal menggandakan: {str(e)[:60]}", "error")
        return redirect("/teacher/exams")


@teacher_bp.route("/scan")
@teacher_or_admin_required
def scan_page():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    students = supabase.table("profiles").select("id,full_name,phone").eq("role", "murid").execute()
    return render_template("teacher/scan.html", exams=res.data, students=students.data)


@teacher_bp.route("/retractions")
@teacher_or_admin_required
def retraction_requests():
    supabase = get_supabase()
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    requests = []
    if exam_ids:
        subs = supabase.table("submissions").select("id,student_id,exam_id,answers,submitted_at,exams(title),profiles(full_name)").in_("exam_id", exam_ids).execute().data or []
        for s in subs:
            answers = s.get("answers")
            if isinstance(answers, str):
                try:
                    answers = json.loads(answers)
                except (json.JSONDecodeError, TypeError):
                    answers = {}
            if isinstance(answers, dict) and answers.get("_retract_request", {}).get("status") == "pending":
                s["exam_title"] = (s.get("exams") or {}).get("title", "-")
                s["student_name"] = (s.get("profiles") or {}).get("full_name", "-")
                s["requested_at"] = answers["_retract_request"].get("requested_at", "")
                requests.append(s)
    return render_template("teacher/retractions.html", requests=requests)


@teacher_bp.route("/retractions/<submission_id>/approve", methods=["POST"])
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def approve_retraction(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("answers").eq("id", submission_id).single().execute().data
    if not sub:
        flash("Submission tidak ditemukan", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict) or "_retract_request" not in answers or not isinstance(answers["_retract_request"], dict):
        flash("Tidak ada permintaan retraction", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers["_retract_request"]["status"] = "approved"
    try:
        supabase.table("submissions").update({"answers": json.dumps(answers), "status": "retracted"}).eq("id", submission_id).execute()
    except Exception:
        supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_approve", "submission", submission_id, user_id=g.user_id)
    flash("Retraction berhasil disetujui", "success")
    return redirect(url_for("teacher.retraction_requests"))


@teacher_bp.route("/retractions/<submission_id>/reject", methods=["POST"])
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def reject_retraction(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("answers").eq("id", submission_id).single().execute().data
    if not sub:
        flash("Submission tidak ditemukan", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict) or "_retract_request" not in answers or not isinstance(answers["_retract_request"], dict):
        flash("Tidak ada permintaan retraction", "error")
        return redirect(url_for("teacher.retraction_requests"))
    answers["_retract_request"]["status"] = "rejected"
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_reject", "submission", submission_id, user_id=g.user_id)
    flash("Retraction ditolak", "success")
    return redirect(url_for("teacher.retraction_requests"))


@teacher_bp.route("/results")
@teacher_or_admin_required
def results():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")

    if not exam_id:
        query = supabase.table("exams").select("id,title").eq("teacher_id", g.user_id)
        if user_role == "admin_sekolah" and school_id:
            query = supabase.table("exams").select("id,title").eq("school_id", school_id)
        exams = query.execute().data or []
        return render_template("teacher/results.html", submissions=[], stats={}, exam_id="", exams=exams)

    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    query = supabase.table("exams").select("id,title").eq("teacher_id", g.user_id)
    if user_role == "admin_sekolah" and school_id:
        query = supabase.table("exams").select("id,title").eq("school_id", school_id)
    exams = query.execute().data or []

    for s in subs:
        if s.get("profiles"):
            s["student_name"] = s.pop("profiles").get("full_name", "")

    if subs:
        scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs]
        stats = {
            "avg": round(sum(scores) / len(scores), 1),
            "max": max(scores),
            "min": min(scores),
            "count": len(scores),
        }
    else:
        stats = {"avg": 0, "max": 0, "min": 0, "count": 0}

    return render_template("teacher/results.html", submissions=subs, stats=stats, exam_id=exam_id, exams=exams)


@teacher_bp.route("/grade/<submission_id>")
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def grade_detail(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("*").eq("id", submission_id).maybe_single().execute()
    if not sub.data:
        return redirect("/teacher/grading")
    sub = sub.data
    exam = supabase.table("exams").select("*").eq("id", sub["exam_id"]).single().execute().data
    exam.setdefault("question_weights", {})
    if not exam.get("question_weights") and exam.get("total_questions", 0) > 0:
        qtypes = exam.get("question_types", {})
        tq = exam["total_questions"]
        mcq_n = sum(1 for i in range(tq) if qtypes.get(str(i), "mcq") == "mcq")
        essay_n = tq - mcq_n
        mp, ep = (70, 30)
        if mcq_n == 0: mp, ep = 0, 100
        elif essay_n == 0: mp, ep = 100, 0
        if mcq_n:
            e = round(mp / mcq_n, 2)
            for i in range(tq):
                if qtypes.get(str(i), "mcq") == "mcq":
                    exam["question_weights"][str(i)] = e
        if essay_n:
            e = round(ep / essay_n, 2)
            for i in range(tq):
                if qtypes.get(str(i), "mcq") != "mcq":
                    exam["question_weights"][str(i)] = e
    student = supabase.table("profiles").select("id,full_name,phone").eq("id", sub["student_id"]).single().execute().data or {}
    # Parse JSON string fields
    for field in ("teacher_feedback", "answers"):
        val = sub.get(field)
        if isinstance(val, str):
            try:
                sub[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                sub[field] = {} if field != "answers" else {}
        if not isinstance(sub.get(field), dict):
            sub[field] = {} if field != "answers" else {}
    return render_template("teacher/grade_detail.html", submission=sub, exam=exam, exam_id=sub["exam_id"], student=student)


@teacher_bp.route("/grade/<submission_id>/override", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("submissions", "submission_id", ("exam_id", "exams"))
def override_score(submission_id):
    if request.is_json:
        data = request.get_json()
        new_score = data.get("final_score")
        feedback = data.get("teacher_feedback", {})
    else:
        new_score = request.form.get("final_score")
        feedback_raw = request.form.get("teacher_feedback", "{}")
        try:
            feedback = json.loads(feedback_raw)
        except json.JSONDecodeError:
            feedback = {}
    supabase = get_supabase()
    final_score_val = float(new_score) if new_score is not None and new_score != '' else None
    supabase.table("submissions").update({
        "final_score": final_score_val,
        "status": "graded",
        "teacher_feedback": feedback,
    }).eq("id", submission_id).execute()
    # Recalculate after grading
    try:
        sub_updated = supabase.table("submissions").select("exam_id").eq("id", submission_id).single().execute().data
        if sub_updated:
            _recalculate_scores(sub_updated["exam_id"])
    except Exception:
        pass
    if request.is_json:
        return jsonify({"success": True, "final_score": final_score_val})
    return redirect(request.referrer or "/teacher/results")


@teacher_bp.route("/publish/<exam_id>", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def publish_scores(exam_id):
    supabase = get_supabase()
    _recalculate_scores(exam_id)
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("exam_id", exam_id) \
        .execute()
    return redirect("/teacher/results?exam_id=" + exam_id)


@teacher_bp.route("/publish/submission/<submission_id>", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def publish_single(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("exam_id").eq("id", submission_id).single().execute().data
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("id", submission_id) \
        .execute()
    return redirect("/teacher/results?exam_id=" + sub["exam_id"])


# --- Export ---
@teacher_bp.route("/export/xlsx")
@teacher_or_admin_required
def export_xlsx():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data or {}
    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    for s in subs:
        s["student_name"] = (s.pop("profiles", None) or {}).get("full_name", s.get("student_id", "")[:12])
    buf = export_to_xlsx(subs, exam)
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"nilai_{exam_id[:8]}.xlsx")


@teacher_bp.route("/export/pdf")
@teacher_or_admin_required
def export_pdf():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data or {}
    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    for s in subs:
        s["student_name"] = (s.pop("profiles", None) or {}).get("full_name", s.get("student_id", "")[:12])
    buf = export_to_pdf(subs, exam.get("title", "Hasil"), exam)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"nilai_{exam_id[:8]}.pdf")


@teacher_bp.route("/export/bubble-sheet/<exam_id>")
@teacher_or_admin_required
def bubble_sheet(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    qtypes = exam.get("question_types") or {}
    mcq_count = sum(1 for i in range(exam["total_questions"]) if qtypes.get(str(i), "mcq") == "mcq")
    if mcq_count == 0:
        mcq_count = exam["total_questions"]

    buf = generate_answer_sheet(
        total_questions=mcq_count,
        subject=exam.get("subject", ""),
        school_name="",
    )
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"LJK_{exam_id[:8]}.pdf")


@teacher_bp.route("/grading")
@teacher_or_admin_required
def grading_center():
    supabase = get_supabase()
    user_role = g.get("user_role")
    school_id = g.get("user_school_id")
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    # For admin_sekolah, also get exams from their school
    if not exam_ids and user_role == "admin_sekolah" and school_id:
        exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("school_id", school_id).execute().data or []]
    pending_subs = []
    graded_subs = []
    if exam_ids:
        subs = supabase.table("submissions").select("id,student_id,exam_id,score,final_score,status,submitted_at,exams(title),profiles(full_name)").in_("exam_id", exam_ids).order("submitted_at", desc=True).execute().data or []
        for s in subs:
            s["student_name"] = (s.get("profiles") or {}).get("full_name", "-") if s.get("profiles") else "-"
            s["exam_title"] = (s.get("exams") or {}).get("title", "-") if s.get("exams") else "-"
            if s["status"] == "submitted":
                pending_subs.append(s)
            elif s["status"] in ("graded", "published"):
                graded_subs.append(s)
    return render_template("teacher/grading.html", pending_subs=pending_subs, graded_subs=graded_subs)


@teacher_bp.route("/analytics")
@teacher_or_admin_required
def analytics():
    supabase = get_supabase()
    exams = supabase.table("exams").select("id,title,passing_score").eq("teacher_id", g.user_id).execute().data or []
    exam_ids = [e["id"] for e in exams]
    all_scores = []
    exam_breakdown = []
    dist_bins = [0, 0, 0, 0, 0]
    exam_labels = []
    exam_avgs = []
    total_submissions = 0
    pass_count = 0
    for e in exams:
        subs = supabase.table("submissions").select("score,final_score").eq("exam_id", e["id"]).execute().data or []
        scores = [float(s.get("final_score") or s.get("score") or 0) for s in subs if s.get("final_score") or s.get("score")]
        all_scores.extend(scores)
        total_submissions += len(subs)
        passing = e.get("passing_score") or 70
        pc = sum(1 for sc in scores if sc >= passing)
        pass_count += pc
        if scores:
            exam_breakdown.append({
                "title": e["title"],
                "count": len(scores),
                "avg": round(sum(scores) / len(scores), 1),
                "max": round(max(scores), 1),
                "min": round(min(scores), 1),
                "pass_pct": round(pc / len(scores) * 100),
            })
            exam_labels.append(e["title"][:20])
            exam_avgs.append(round(sum(scores) / len(scores), 1))
    for sc in all_scores:
        if sc < 20: dist_bins[0] += 1
        elif sc < 40: dist_bins[1] += 1
        elif sc < 60: dist_bins[2] += 1
        elif sc < 80: dist_bins[3] += 1
        else: dist_bins[4] += 1
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    pass_rate = round(pass_count / len(all_scores) * 100) if all_scores else 0
    stats = {
        "total_exams": len(exams),
        "total_submissions": total_submissions,
        "avg_score": avg_score,
        "pass_rate": pass_rate,
    }
    return render_template("teacher/analytics.html", stats=stats, exam_breakdown=exam_breakdown, dist_bins=dist_bins, exam_labels=exam_labels, exam_avgs=exam_avgs)


@teacher_bp.route("/reset-password", methods=["POST"])
@login_required
def teacher_reset_password():
    if g.get("user_role") not in ("guru",):
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


@teacher_bp.route("/profile/update", methods=["POST"])
@login_required
def teacher_update_profile():
    if g.get("user_role") not in ("guru",):
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


@teacher_bp.route("/assignments", methods=["GET", "POST"])
@teacher_or_admin_required
def assignments():
    supabase = get_supabase()
    school_id = None
    try:
        profile = supabase.table("profiles").select("*").eq("id", g.user_id).single().execute().data or {}
        school_id = profile.get("school_id")
    except Exception:
        pass
    if not school_id:
        if request.is_json or request.headers.get("HX-Request"):
            return jsonify({"error": "School not found"}), 400
        return redirect("/teacher/dashboard")

    if request.method == "POST":
        class_id = request.form.get("class_id")
        subject_id = request.form.get("subject_id")
        if not class_id or not subject_id:
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"error": "Class and subject required"}), 400
            return redirect("/teacher/dashboard")
        try:
            res = supabase.table("teacher_assignments").upsert({
                "teacher_id": g.user_id,
                "class_id": class_id,
                "subject_id": subject_id,
                "school_id": school_id,
            }).execute()
            aid = res.data[0]["id"] if res.data else None
            log_activity("create", "teacher_assignment", aid, new_data={"class_id": class_id, "subject_id": subject_id}, user_id=g.user_id)
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"success": True})
            return redirect("/teacher/dashboard")
        except Exception as e:
            if request.is_json or request.headers.get("HX-Request"):
                return jsonify({"error": str(e)}), 400
            return redirect("/teacher/dashboard")

    try:
        assignments = supabase.table("teacher_assignments") \
            .select("*, classes(id, name, grade_level), subjects(id, name, code)") \
            .eq("teacher_id", g.user_id) \
            .eq("school_id", school_id) \
            .execute().data or []
    except Exception:
        assignments = []
    return jsonify(assignments)


@teacher_bp.route("/assignments/<assignment_id>", methods=["DELETE"])
@teacher_or_admin_required
def delete_assignment(assignment_id):
    supabase = get_supabase()
    try:
        supabase.table("teacher_assignments").delete().eq("id", assignment_id).eq("teacher_id", g.user_id).execute()
        log_activity("delete", "teacher_assignment", assignment_id, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@teacher_bp.route("/ai-settings")
@teacher_or_admin_required
def ai_settings():
    supabase = get_supabase()
    keys = supabase.table("teacher_ai_keys").select("*").eq("teacher_id", g.user_id).order("created_at", desc=True).execute().data or []
    settings_res = supabase.table("teacher_ai_settings").select("*").eq("teacher_id", g.user_id).limit(1).execute()
    if not settings_res.data:
        default_prompts = [
            {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
            {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        ]
        supabase.table("teacher_ai_settings").insert({
            "teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"
        }).execute()
        settings = {"teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"}
    else:
        settings = settings_res.data[0]
        pr = settings.get("prompts")
        if isinstance(pr, str):
            try:
                settings["prompts"] = json.loads(pr)
            except (json.JSONDecodeError, TypeError):
                settings["prompts"] = []
        elif pr is None:
            settings["prompts"] = []
        # Auto-upgrade if less than 3 prompts (old version)
        try:
            if len(settings.get("prompts", [])) < 3:
                default_prompts = [
                    {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                    {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
                ]
                supabase.table("teacher_ai_settings").update({
                    "prompts": default_prompts, "active_prompt_id": "default"
                }).eq("teacher_id", g.user_id).execute()
                settings["prompts"] = default_prompts
                settings["active_prompt_id"] = "default"
        except Exception as e:
            current_app.logger.error(f"Auto-upgrade prompts error: {e}")
    return render_template("teacher/ai_settings.html", keys=keys, settings=settings)


@teacher_bp.route("/ai-settings/add-key", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_add_key():
    supabase = get_supabase()
    provider = request.form.get("provider", "gemini")
    api_key = request.form.get("api_key", "").strip()
    label = request.form.get("label", "").strip()
    base_url = request.form.get("base_url", "").strip()
    model_name = request.form.get("model_name", "").strip()
    if not api_key:
        flash("API Key wajib diisi", "error")
        return redirect("/teacher/ai-settings")
    supabase.table("teacher_ai_keys").update({"is_active": False}).eq("teacher_id", g.user_id).execute()
    data = {
        "teacher_id": g.user_id, "provider": provider,
        "api_key": api_key, "label": label or provider,
        "is_active": True,
    }
    if provider == "custom":
        data["base_url"] = base_url
        data["model_name"] = model_name or "gpt-4o-mini"
    supabase.table("teacher_ai_keys").insert(data).execute()
    flash("API Key berhasil ditambahkan dan diaktifkan", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/<key_id>/toggle", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_toggle_key(key_id):
    supabase = get_supabase()
    key = supabase.table("teacher_ai_keys").select("is_active").eq("id", key_id).eq("teacher_id", g.user_id).single().execute()
    if key.data:
        if key.data.get("is_active"):
            supabase.table("teacher_ai_keys").update({"is_active": False}).eq("id", key_id).execute()
        else:
            supabase.table("teacher_ai_keys").update({"is_active": False}).eq("teacher_id", g.user_id).execute()
            supabase.table("teacher_ai_keys").update({"is_active": True}).eq("id", key_id).execute()
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/<key_id>/delete", methods=["POST"])
@subscription_write_required
@teacher_or_admin_required
def ai_delete_key(key_id):
    supabase = get_supabase()
    supabase.table("teacher_ai_keys").delete().eq("id", key_id).eq("teacher_id", g.user_id).execute()
    flash("API Key berhasil dihapus", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/save-prompt", methods=["POST"])
@teacher_or_admin_required
def ai_save_prompt():
    supabase = get_supabase()
    prompts_raw = request.form.get("prompts", "[]").strip()
    active_id = request.form.get("active_prompt_id", "default").strip()
    try:
        prompts = json.loads(prompts_raw)
    except json.JSONDecodeError:
        flash("Data prompts tidak valid", "error")
        return redirect("/teacher/ai-settings")
    supabase.table("teacher_ai_settings").upsert({
        "teacher_id": g.user_id, "prompts": prompts, "active_prompt_id": active_id
    }).execute()
    flash("Prompt berhasil disimpan", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/ai-settings/reset-prompt", methods=["POST"])
@teacher_or_admin_required
def ai_reset_prompt():
    default_prompts = [
        {"id": "default", "label": "Default (Semua Mapel)", "template": "Kamu adalah asisten koreksi ujian. Koreksi jawaban esai berikut berdasarkan soal dan bobot maksimal.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}) dan feedback singkat dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ipa", "label": "IPA / Sains", "template": "Kamu adalah asisten koreksi mata pelajaran IPA (Fisika, Kimia, Biologi, Earth Science).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: keakuratan konsep sains, penggunaan istilah ilmiah yang tepat, logika ilmiah, dan kelengkapan jawaban. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "matematika", "label": "Matematika", "template": "Kamu adalah asisten koreksi mata pelajaran Matematika.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran rumus, ketepatan langkah-langkah penyelesaian, keakuratan perhitungan, dan kesimpulan akhir. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "bahasa", "label": "Bahasa (Inggris/Indonesia/Arab/Mandarin)", "template": "Kamu adalah asisten koreksi mata pelajaran Bahasa (Indonesia, Inggris, Arab, Mandarin).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: tata bahasa (grammar/tata bahasa), kosa kata (vocabulary/kosakata), struktur tulisan, kesesuaian konteks, dan kreativitas. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ips", "label": "IPS / Sosial", "template": "Kamu adalah asisten koreksi mata pelajaran IPS (Geografi, Sosiologi, Ekonomi).\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kedalaman analisis, penggunaan data/contoh konkret, argumen logis, dan keterkaitan antar konsep sosial. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ict", "label": "ICT / Coding", "template": "Kamu adalah asisten koreksi mata pelajaran ICT, Coding, dan Computer Science.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: kebenaran logika algoritma, sintaks kode, efisiensi solusi, dan dokumentasi/penjelasan. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "agama", "label": "Agama", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Agama.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman konsep keagamaan, ketepatan dalil/sumber, implementasi dalam kehidupan, dan sikap toleransi. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "penjas", "label": "PJOK / Olahraga", "template": "Kamu adalah asisten koreksi mata pelajaran Pendidikan Jasmani, Olahraga, dan Kesehatan.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman teori olahraga, teknik gerakan, keselamatan, dan kebugaran jasmani. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "iot", "label": "IoT / Teknologi", "template": "Kamu adalah asisten koreksi mata pelajaran Internet of Things dan Teknologi Embedded.\n\nSoal: {question}\nPedoman Penskoran: {rubric}\nBobot Maksimal: {max_score} poin\nJawaban Siswa: \"{answer}\"\n\nBerikan skor (0-{max_score}). Nilai berdasarkan: pemahaman sistem IoT, integrasi sensor, jaringan komunikasi, dan pemecahan masalah teknis. Feedback dalam bahasa Indonesia.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ketat", "label": "Ketat (Semua Mapel)", "template": "Kamu adalah pemeriksa ujian yang sangat ketat. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Jangan mudah memberi nilai tinggi. Feedback harus menyebutkan kekurangan secara spesifik.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
        {"id": "ringan", "label": "Ringan (Semua Mapel)", "template": "Kamu adalah guru yang baik hati dan memotivasi. Koreksi jawaban esai berikut.\n\nSoal: {question}\nBobot Maksimal: {max_score} poin\nJawaban: \"{answer}\"\n\nBerikan skor (0-{max_score}). Beri nilai maksimal jika jawaban mendekati benar. Feedback yang membangun dan memotivasi.\nFormat JSON: {\"score\": <number>, \"feedback\": \"<string>\"}"},
    ]
    supabase = get_supabase()
    supabase.table("teacher_ai_settings").upsert({
        "teacher_id": g.user_id, "prompts": default_prompts, "active_prompt_id": "default"
    }).execute()
    flash("Prompt dikembalikan ke default", "success")
    return redirect("/teacher/ai-settings")


@teacher_bp.route("/students")
@teacher_or_admin_required
def students():
    supabase = get_supabase()
    school_id = g.get("user_school_id")
    query = supabase.table("profiles").select("id,full_name,phone,role").eq("role", "murid")
    if school_id:
        query = query.eq("school_id", school_id)
    students = query.execute().data or []
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
    if exam_ids:
        subs = supabase.table("submissions").select("student_id,score,final_score").in_("exam_id", exam_ids).execute().data or []
        sub_map = {}
        for s in subs:
            sid = s["student_id"]
            if sid not in sub_map:
                sub_map[sid] = []
            sc = float(s.get("final_score") or s.get("score") or 0)
            sub_map[sid].append(sc)
        for st in students:
            st_scores = sub_map.get(st["id"], [])
            st["sub_count"] = len(st_scores)
            st["avg_score"] = round(sum(st_scores) / len(st_scores), 1) if st_scores else None
    return render_template("teacher/students.html", students=students)


@teacher_bp.route("/classes")
@teacher_or_admin_required
def teacher_classes():
    supabase = get_supabase()
    sid = g.get("user_school_id")
    assignments = []
    school_info = {}
    active_year = None
    if sid:
        try:
            assignments = supabase.table("teacher_assignments") \
                .select("*, classes!inner(id, name), subjects!inner(id, name)") \
                .eq("teacher_id", g.user_id) \
                .eq("school_id", sid) \
                .execute().data or []
        except Exception:
            assignments = []
        try:
            sch = supabase.table("schools").select("name, npsn").eq("id", sid).single().execute()
            if sch.data: school_info = sch.data
        except: pass
        try:
            years = supabase.table("school_years").select("*").eq("school_id", sid).eq("is_active", True).limit(1).execute()
            if years.data: active_year = years.data[0]
        except: pass
    return render_template("teacher/classes.html", assignments=assignments,
                           school_info=school_info, active_year=active_year)
