import io
from flask import Blueprint, render_template, request, redirect, g, jsonify, current_app, make_response
from app.utils.auth import login_required, get_supabase
from app.services.audit_service import log_activity

student_bp = Blueprint("student", __name__)


@student_bp.route("/dashboard")
@login_required
def dashboard():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")

    available_exams = supabase.table("exams").select("*").eq("is_published", True).eq("status", "active").execute().data or []
    subs = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published", "draft"]).execute().data or []
    submitted_ids = {s["exam_id"] for s in subs}
    available_exams = [e for e in available_exams if e["id"] not in submitted_ids]

    subs = supabase.table("submissions").select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, submitted_at, graded_at, teacher_feedback, exams(id, title, answer_key, question_types, total_questions, pdf_page_urls)").eq("student_id", g.user_id).order("submitted_at", desc=True).execute().data or []
    completed_exams = []
    all_scores = []
    for s in subs:
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        s.setdefault("is_hidden", False)
        completed_exams.append(s)
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None:
            all_scores.append(float(sc))
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else "-"
    user_name = g.user_name or g.user_email or ""

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

    return render_template("student/dashboard.html", available_exams=available_exams,
                           completed_exams=completed_exams[:5], avg_score=avg_score,
                           user_name=user_name, student_class=student_class,
                           subject_count=subject_count)


@student_bp.route("/exams")
@login_required
def exam_list():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")
    res = supabase.table("exams").select("*").eq("is_published", True).eq("status", "active").order("created_at", desc=True).execute()
    exams = res.data or []
    subs = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published", "draft"]).execute().data or []
    submitted_ids = {s["exam_id"] for s in subs}
    exams = [e for e in exams if e["id"] not in submitted_ids]
    return render_template("student/exam_list.html", exams=exams)


@student_bp.route("/exams/<exam_id>")
@login_required
def take_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    return render_template("student/take_exam.html", exam=res.data)


@student_bp.route("/exams/<exam_id>/submit", methods=["POST"])
@login_required
def submit_exam(exam_id):
    import json
    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data

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

    mcq_count = sum(1 for v in (exam.get("answer_key") or {}).values() if v not in ("essay", "essay_text", "essay_canvas", None))
    question_weights = exam.get("question_weights") or {}
    question_types = exam.get("question_types") or {}
    total_q = exam["total_questions"]
    if not question_weights and mcq_count > 0:
        each = round(100 / mcq_count, 2)
        for i in range(total_q):
            if question_types.get(str(i), "mcq") == "mcq":
                question_weights[str(i)] = each
    earned = 0.0
    for i in range(exam["total_questions"]):
        qtype = question_types.get(str(i), "mcq")
        key = exam.get("answer_key", {}).get(str(i))
        w = float(question_weights.get(str(i), 0))
        if qtype == "mcq" and key and w > 0:
            ans = answers.get(str(i))
            if key == "bonus":
                if ans and str(ans).strip():
                    earned += w
            elif isinstance(key, list):
                if ans in key:
                    earned += w
            elif ans == key:
                earned += w

    score = round(min(earned, 100), 2)

    submission = {
        "exam_id": exam_id,
        "student_id": g.user_id,
        "answers": {k: v for k, v in answers.items() if v is not None},
        "score": score,
        "max_score": 100,
        "status": "submitted",
    }
    try:
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
    res = supabase.table("submissions") \
        .select("id, status, score, final_score, penalty, submitted_at, exams(id, title, subject)") \
        .eq("student_id", g.user_id) \
        .order("submitted_at", desc=True) \
        .execute()
    submissions = res.data or []
    for s in submissions:
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        s.setdefault("is_hidden", False)
    return render_template("student/results.html", submissions=submissions)


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
    student_name = g.user_name or g.user_email or ""
    return render_template("student/result_detail.html", submission=submission, student_name=student_name)


@student_bp.route("/results/<submission_id>/download-pdf")
@login_required
def download_result_pdf(submission_id):
    from xhtml2pdf import pisa
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
    student_name = g.user_name or g.user_email or ""
    html_string = render_template("student/result_detail_pdf.html", submission=submission, student_name=student_name)
    buf = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buf)
    if pisa_status.err:
        current_app.logger.error("PDF generation error: %s", pisa_status.err)
    buf.seek(0)
    filename = f"hasil_{submission.get('exam', {}).get('title', 'ujian').replace(' ', '_')}_{student_name.replace(' ', '_')}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@student_bp.route("/submissions/<submission_id>/retract", methods=["POST"])
@login_required
def retract_submission(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("*, exams(id, teacher_id)").eq("id", submission_id).eq("student_id", g.user_id).single().execute().data
    if not sub:
        if request.is_json:
            return jsonify({"error": "Not found"}), 404
        return redirect("/student/results")
    supabase.table("submissions").update({"status": "retracted"}).eq("id", submission_id).execute()
    log_activity("retract", "submission", submission_id, user_id=g.user_id)
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")


@student_bp.route("/submissions/<submission_id>/toggle-visibility", methods=["POST"])
@login_required
def toggle_submission_visibility(submission_id):
    supabase = get_supabase()
    try:
        sub = supabase.table("submissions").select("is_hidden").eq("id", submission_id).eq("student_id", g.user_id).single().execute().data
    except Exception:
        sub = {"is_hidden": False}
    if not sub:
        if request.is_json:
            return jsonify({"error": "Not found"}), 404
        return redirect("/student/results")
    new_val = not sub.get("is_hidden", False)
    try:
        supabase.table("submissions").update({"is_hidden": new_val}).eq("id", submission_id).execute()
    except Exception:
        pass
    if request.is_json:
        return jsonify({"success": True, "is_hidden": new_val})
    return redirect("/student/results")


@student_bp.route("/submissions/<submission_id>/delete", methods=["POST"])
@login_required
def delete_submission(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("id, status").eq("id", submission_id).eq("student_id", g.user_id).single().execute().data
    if not sub:
        if request.is_json:
            return jsonify({"error": "Not found"}), 404
        return redirect("/student/results")
    if sub.get("status") not in ("submitted", "draft", "retracted"):
        if request.is_json:
            return jsonify({"error": "Cannot delete graded submission"}), 403
        return redirect("/student/results")
    supabase.table("submissions").delete().eq("id", submission_id).execute()
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")
