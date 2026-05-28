from flask import Blueprint, render_template, request, redirect, g, jsonify, current_app
from app.utils.auth import login_required, get_supabase

student_bp = Blueprint("student", __name__)


@student_bp.route("/exams")
@login_required
def exam_list():
    supabase = get_supabase()
    if g.get("user_role") != "student":
        return redirect("/teacher/dashboard")
    res = supabase.table("exams").select("*").eq("is_published", True).execute()
    return render_template("student/dashboard.html", exams=res.data or [])


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

    mcq_count = sum(1 for v in (exam.get("answer_key") or {}).values() if v not in ("essay", "essay_text", "essay_canvas"))
    correct = 0
    for i in range(exam["total_questions"]):
        qtype = (exam.get("question_types") or {}).get(str(i), "mcq")
        key = exam.get("answer_key", {}).get(str(i))
        if qtype == "mcq" and key and answers.get(str(i)) == key:
            correct += 1

    score = round((correct / max(mcq_count, 1)) * 100, 2) if mcq_count > 0 else 0

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
        .select("*, exams(id, title, answer_key, question_types, total_questions, pdf_page_urls)") \
        .eq("student_id", g.user_id) \
        .order("submitted_at", desc=True) \
        .execute()
    submissions = res.data or []
    for s in submissions:
        if s.get("exams"):
            s["exam"] = s.pop("exams")
    return render_template("student/results.html", submissions=submissions)
