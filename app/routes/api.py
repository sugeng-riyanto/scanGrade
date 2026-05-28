import io
import time
from flask import Blueprint, request, jsonify, g, render_template, redirect, send_file
from app.utils.auth import login_required, get_supabase
from app.services.anti_cheat_service import validate_violation_log

api_bp = Blueprint("api", __name__)


@api_bp.route("/violation/log", methods=["POST"])
@login_required
def log_violation():
    data = request.get_json()
    logs = data if isinstance(data, list) else [data]

    supabase = get_supabase()
    results = []
    for log in logs:
        valid = validate_violation_log(
            g.user_id,
            log.get("exam_id", ""),
            log.get("timestamp", 0),
        )
        if valid["valid"]:
            supabase.table("violation_logs").insert({
                "exam_id": log.get("exam_id"),
                "user_id": g.user_id,
                "violation_type": log.get("violation_type", "unknown"),
                "metadata": log.get("metadata", {}),
            }).execute()
            results.append({"logged": True})
        else:
            results.append({"logged": False, "reason": valid.get("reason")})

    return jsonify({"violations": results})


@api_bp.route("/violation/count", methods=["GET"])
@login_required
def violation_count():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    res = supabase.table("violation_logs") \
        .select("count", count="exact") \
        .eq("user_id", g.user_id) \
        .eq("exam_id", exam_id) \
        .execute()
    return jsonify({"count": res.count})


@api_bp.route("/scan/process", methods=["POST"])
@login_required
def scan_process():
    """Process a scanned bubble sheet image and return detected answers."""
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    exam_id = request.form.get("exam_id", "")
    total_questions = int(request.form.get("total_questions", 50))

    image_file = request.files["image"]
    image_data = image_file.read()

    from app.services.omr_service import process_scan, draw_debug_image

    result = process_scan(image_data, total_questions=total_questions)

    # If no error, grade against answer key if exam_id provided
    if "error" not in result and exam_id:
        supabase = get_supabase()
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        if exam and exam.get("answer_key"):
            key = exam["answer_key"]
            detected = result.get("answers", {})
            correct = 0
            for k, v in key.items():
                if k in detected and detected[k] == v and v not in ("essay", "essay_text", "essay_canvas"):
                    correct += 1
            mcq_count = sum(1 for v in key.values() if v not in ("essay", "essay_text", "essay_canvas"))
            result["score"] = round((correct / max(mcq_count, 1)) * 100, 2)
            result["correct"] = correct

    # Generate debug image
    if "error" not in result:
        from app.services.omr_service import load_image, find_registration_marks
        img = load_image(image_data)
        if img is not None:
            corners = find_registration_marks(img)
            debug_jpg = draw_debug_image(img, corners, result.get("answers"))
            import base64
            result["debug_image"] = base64.b64encode(debug_jpg).decode()

    return jsonify(result)


@api_bp.route("/student/auto-save", methods=["POST"])
@login_required
def student_auto_save():
    """Auto-save student's in-progress exam draft."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    # Store draft in a submissions draft table or log it
    # For now, just acknowledge (frontend uses localStorage anyway)
    return jsonify({"saved": True, "at": int(time.time())})


@api_bp.route("/grade/auto-save/<submission_id>", methods=["POST"])
@login_required
def grade_auto_save(submission_id):
    """Auto-save teacher's in-progress grading."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    supabase = get_supabase()
    # Save teacher_feedback draft into submission
    feedback = data.get("teacher_feedback", {})
    supabase.table("submissions") \
        .update({"teacher_feedback": feedback}) \
        .eq("id", submission_id) \
        .execute()
    return jsonify({"saved": True, "at": int(time.time())})


@api_bp.route("/scan/save", methods=["POST"])
@login_required
def scan_save():
    """Save scanned answers as a submission."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    exam_id = data.get("exam_id")
    student_id = data.get("student_id")
    answers = data.get("answers")

    if not all([exam_id, student_id, answers]):
        return jsonify({"error": "exam_id, student_id, and answers are required"}), 400

    supabase = get_supabase()

    # Verify exam belongs to this teacher
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    if str(exam.get("teacher_id")) != g.user_id:
        return jsonify({"error": "Forbidden"}), 403

    # Grade MCQ answers
    key = exam.get("answer_key", {})
    detected = answers
    correct = 0
    for k, v in key.items():
        if k in detected and detected[k] == v and v not in ("essay", "essay_text", "essay_canvas"):
            correct += 1
    mcq_count = sum(1 for v in key.values() if v not in ("essay", "essay_text", "essay_canvas"))
    score = round((correct / max(mcq_count, 1)) * 100, 2) if mcq_count > 0 else 0

    # Check for existing submission and update or create
    existing = supabase.table("submissions") \
        .select("*") \
        .eq("exam_id", exam_id) \
        .eq("student_id", student_id) \
        .execute().data

    if existing:
        sub = supabase.table("submissions") \
            .update({
                "answers": answers,
                "score": score,
                "max_score": mcq_count,
                "status": "graded",
            }) \
            .eq("id", existing[0]["id"]) \
            .execute().data
    else:
        sub = supabase.table("submissions") \
            .insert({
                "exam_id": exam_id,
                "student_id": student_id,
                "answers": answers,
                "score": score,
                "max_score": mcq_count,
                "status": "graded",
            }) \
            .execute().data

    return jsonify({
        "success": True,
        "score": score,
        "correct": correct,
        "total": mcq_count,
        "submission": sub[0] if sub else None,
    })
