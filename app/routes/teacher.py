import json
from flask import Blueprint, jsonify, render_template, request, redirect, g, send_file
from app.utils.auth import teacher_or_admin_required, get_supabase
from app.services.export_service import export_to_xlsx, export_to_pdf
from app.services.ljk_generator import generate_bubble_sheet_pdf
from app.services.pdf_service import upload_pdf
from app.services.audit_service import log_activity

teacher_bp = Blueprint("teacher", __name__)


def _is_mcq_correct(student_ans, key_val):
    if key_val == "bonus":
        return bool(student_ans and student_ans.strip())
    if isinstance(key_val, list):
        if not student_ans:
            return False
        return student_ans in key_val
    return student_ans == key_val


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
        if mcq_count > 0:
            each = round(100 / mcq_count, 2)
            for i in range(total_q):
                if question_types.get(str(i), "mcq") == "mcq":
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
    return render_template("teacher/dashboard.html", exams=exams, total_students=total_students,
                           avg_score=avg_score, all_scores=all_scores, user_name=user_name,
                           assignments=assignments, classes=classes, subjects=subjects)


@teacher_bp.route("/exams/new", methods=["GET", "POST"])
@teacher_or_admin_required
def exam_form():
    supabase = get_supabase()
    if request.method == "GET":
        return render_template("teacher/exam_form.html", exam=None)

    title = request.form.get("title")
    subject = request.form.get("subject")
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
        "title": title,
        "subject": subject,
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
    }
    try:
        res = supabase.table("exams").insert(data).execute()
    except Exception:
        data.pop("question_weights", None)
        res = supabase.table("exams").insert(data).execute()
    exam_id = res.data[0]["id"]
    log_activity("create", "exam", exam_id, new_data={"title": title, "subject": subject, "total_questions": total_questions}, user_id=g.user_id)
    return redirect(f"/teacher/exams/{exam_id}")


@teacher_bp.route("/exams/<exam_id>", methods=["GET", "POST", "DELETE"])
@teacher_or_admin_required
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
        return render_template("teacher/exam_form.html", exam=exam_data)

    title = request.form.get("title")
    subject = request.form.get("subject")
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
    }
    try:
        supabase.table("exams").update(data).eq("id", exam_id).execute()
    except Exception:
        data.pop("question_weights", None)
        supabase.table("exams").update(data).eq("id", exam_id).execute()
    _recalculate_scores(exam_id)
    log_activity("update", "exam", exam_id, new_data={"title": title, "status": data.get("status")}, user_id=g.user_id)
    return redirect(f"/teacher/exams/{exam_id}")


@teacher_bp.route("/preview/<exam_id>")
@teacher_or_admin_required
def preview_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    return render_template("teacher/preview_exam.html", exam=res.data)


@teacher_bp.route("/exams/<exam_id>/publish-exam", methods=["POST"])
@teacher_or_admin_required
def publish_exam(exam_id):
    supabase = get_supabase()
    supabase.table("exams").update({
        "is_published": True,
        "status": "active",
    }).eq("id", exam_id).execute()
    log_activity("publish", "exam", exam_id, user_id=g.user_id)
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams/<exam_id>/upload-pdf", methods=["GET", "POST"])
@teacher_or_admin_required
def upload_exam_pdf(exam_id):
    supabase = get_supabase()
    if request.method == "GET":
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam)

    if "pdf" not in request.files:
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        return render_template("teacher/upload_pdf.html", exam=exam, error="Pilih file PDF")

    file = request.files["pdf"]
    result = upload_pdf(file, exam_id)
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
@teacher_or_admin_required
def toggle_exam_status(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("status").eq("id", exam_id).single().execute().data
    new_status = "draft" if exam["status"] == "active" else "active"
    supabase.table("exams").update({"status": new_status}).eq("id", exam_id).execute()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "status": new_status})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/toggle-visibility", methods=["POST"])
@teacher_or_admin_required
def toggle_exam_visibility(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("is_published").eq("id", exam_id).single().execute().data
    new_val = not exam["is_published"]
    supabase.table("exams").update({"is_published": new_val}).eq("id", exam_id).execute()
    if request.headers.get("Accept", "") == "application/json" or request.is_json:
        return jsonify({"success": True, "is_published": new_val})
    return redirect(request.referrer or "/teacher/exams")


@teacher_bp.route("/exams/<exam_id>/delete", methods=["POST"])
@teacher_or_admin_required
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


@teacher_bp.route("/scan")
@teacher_or_admin_required
def scan_page():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    students = supabase.table("profiles").select("id,full_name,phone").eq("role", "murid").execute()
    return render_template("teacher/scan.html", exams=res.data, students=students.data)


@teacher_bp.route("/results")
@teacher_or_admin_required
def results():
    exam_id = request.args.get("exam_id")
    if not exam_id:
        supabase = get_supabase()
        exams = supabase.table("exams").select("id,title").eq("teacher_id", g.user_id).execute().data
        return render_template("teacher/results.html", submissions=[], stats={}, exam_id="", exams=exams)

    supabase = get_supabase()
    subs = supabase.table("submissions").select("*, profiles(full_name)").eq("exam_id", exam_id).execute().data or []
    exams = supabase.table("exams").select("id,title").eq("teacher_id", g.user_id).execute().data or []

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
def grade_detail(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("*").eq("id", submission_id).single().execute().data
    exam = supabase.table("exams").select("*").eq("id", sub["exam_id"]).single().execute().data
    exam.setdefault("question_weights", {})
    student = supabase.table("profiles").select("id,full_name,phone").eq("id", sub["student_id"]).single().execute().data or {}
    return render_template("teacher/grade_detail.html", submission=sub, exam=exam, exam_id=sub["exam_id"], student=student)


@teacher_bp.route("/grade/<submission_id>/override", methods=["POST"])
@teacher_or_admin_required
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
    supabase.table("submissions").update({
        "final_score": float(new_score) if new_score else None,
        "status": "graded",
        "teacher_feedback": feedback,
    }).eq("id", submission_id).execute()
    if request.is_json:
        return jsonify({"success": True, "final_score": float(new_score) if new_score else None})
    return redirect(request.referrer or "/teacher/results")


@teacher_bp.route("/publish/<exam_id>", methods=["POST"])
@teacher_or_admin_required
def publish_scores(exam_id):
    supabase = get_supabase()
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("exam_id", exam_id) \
        .execute()
    return redirect("/teacher/results?exam_id=" + exam_id)


@teacher_bp.route("/publish/submission/<submission_id>", methods=["POST"])
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
    exam = supabase.table("exams").select("title").eq("id", exam_id).single().execute().data
    subs = supabase.table("submissions").select("*").eq("exam_id", exam_id).execute().data or []
    buf = export_to_xlsx(subs, exam.get("title", "Hasil"))
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"nilai_{exam_id[:8]}.xlsx")


@teacher_bp.route("/export/pdf")
@teacher_or_admin_required
def export_pdf():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    exam = supabase.table("exams").select("title").eq("id", exam_id).single().execute().data
    subs = supabase.table("submissions").select("*").eq("exam_id", exam_id).execute().data or []
    buf = export_to_pdf(subs, exam.get("title", "Hasil"))
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"nilai_{exam_id[:8]}.pdf")


@teacher_bp.route("/export/bubble-sheet/<exam_id>")
@teacher_or_admin_required
def bubble_sheet(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    qtypes = exam.get("question_types") or {}
    mcq_count = sum(1 for i in range(exam["total_questions"]) if qtypes.get(str(i), "mcq") == "mcq")
    buf = generate_bubble_sheet_pdf(
        title=exam["title"],
        total_questions=mcq_count,
        subject=exam.get("subject", ""),
    )
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"LJK_{exam_id[:8]}.pdf")


@teacher_bp.route("/grading")
@teacher_or_admin_required
def grading_center():
    supabase = get_supabase()
    exam_ids = [e["id"] for e in supabase.table("exams").select("id").eq("teacher_id", g.user_id).execute().data or []]
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


@teacher_bp.route("/classes")
@teacher_or_admin_required
def teacher_classes():
    supabase = get_supabase()
    school_id = None
    try:
        profile = supabase.table("profiles").select("*").eq("id", g.user_id).single().execute().data or {}
        school_id = profile.get("school_id")
    except Exception:
        pass

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

    return render_template("teacher/classes.html", assignments=assignments, classes=classes, subjects=subjects)


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


@teacher_bp.route("/students")
@teacher_or_admin_required
def students():
    supabase = get_supabase()
    students = supabase.table("profiles").select("id,full_name,phone,role").eq("role", "murid").execute().data or []
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
