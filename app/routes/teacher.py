import json
from flask import Blueprint, jsonify, render_template, request, redirect, g, send_file
from app.utils.auth import teacher_or_admin_required, get_supabase
from app.services.export_service import export_to_xlsx, export_to_pdf
from app.services.ljk_generator import generate_bubble_sheet_pdf
from app.services.pdf_service import upload_pdf

teacher_bp = Blueprint("teacher", __name__)


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

    return render_template("teacher/dashboard.html", exams=exams, total_students=total_students, avg_score=avg_score)


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
    answer_key = {}
    question_audio = {}
    question_canvas = {}
    for i in range(total_questions):
        qtype = question_types.get(str(i), "mcq")
        if qtype == "mcq":
            answer_key[str(i)] = request.form.get(f"answer_{i}", "A")
        elif qtype == "essay_text":
            answer_key[str(i)] = "essay_text"
        else:
            answer_key[str(i)] = "essay_canvas"
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
        "question_audio": question_audio,
        "question_canvas": question_canvas,
    }
    res = supabase.table("exams").insert(data).execute()
    exam_id = res.data[0]["id"]
    return redirect(f"/teacher/exams/{exam_id}")


@teacher_bp.route("/exams/<exam_id>", methods=["GET", "POST", "DELETE"])
@teacher_or_admin_required
def exam_detail(exam_id):
    supabase = get_supabase()
    if request.method == "DELETE":
        supabase.table("exams").delete().eq("id", exam_id).execute()
        return jsonify({"success": True})
    if request.method == "GET":
        res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
        return render_template("teacher/exam_form.html", exam=res.data)

    title = request.form.get("title")
    subject = request.form.get("subject")
    total_questions = int(request.form.get("total_questions", 10))
    duration_minutes = int(request.form.get("duration_minutes", 60))
    passing_score = int(request.form.get("passing_score", 70))
    description = request.form.get("description", "")
    action = request.form.get("action", "save_draft")
    question_types = json.loads(request.form.get("question_types", "{}"))
    answer_key = {}
    question_audio = {}
    question_canvas = {}
    for i in range(total_questions):
        qtype = question_types.get(str(i), "mcq")
        if qtype == "mcq":
            answer_key[str(i)] = request.form.get(f"answer_{i}", "A")
        elif qtype == "essay_text":
            answer_key[str(i)] = "essay_text"
        else:
            answer_key[str(i)] = "essay_canvas"
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
        "question_audio": question_audio,
        "question_canvas": question_canvas,
    }
    supabase.table("exams").update(data).eq("id", exam_id).execute()
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
    return redirect(f"/teacher/preview/{exam_id}")


@teacher_bp.route("/exams")
@teacher_or_admin_required
def my_exams():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    return jsonify(res.data)


@teacher_bp.route("/scan")
@teacher_or_admin_required
def scan_page():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("teacher_id", g.user_id).execute()
    students = supabase.table("profiles").select("id,full_name,phone").eq("role", "student").execute()
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
