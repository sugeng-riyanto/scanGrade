from flask import Blueprint, request, jsonify, current_app
from app.utils.auth import login_required, teacher_required, get_supabase
from app.services.pdf_service import upload_pdf
from app.errors import FileTooLargeError, InvalidPDFError, NotFoundError, ValidationError

exam_bp = Blueprint("exam", __name__)
MAX_PDF_SIZE = 50 * 1024 * 1024


@exam_bp.route("/", methods=["GET"])
@login_required
def list_exams():
    supabase = get_supabase()
    exams = supabase.table("exams").select("*").execute()
    return jsonify(exams.data)


@exam_bp.route("/<exam_id>/upload-pdf", methods=["POST"])
@teacher_required
def upload_exam_pdf(exam_id):
    if "pdf" not in request.files:
        return jsonify({"error": "File PDF diperlukan"}), 400
    file = request.files["pdf"]
    if file.filename == "":
        raise ValidationError("pdf", "Pilih file terlebih dahulu")

    # Check file size
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_PDF_SIZE:
        raise FileTooLargeError(file_size, MAX_PDF_SIZE)

    if not file.filename.lower().endswith(".pdf"):
        raise InvalidPDFError("File harus berformat .pdf")

    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    if not exam.data:
        raise NotFoundError("Ujian", exam_id)

    result = upload_pdf(file, exam_id)
    supabase.table("exams").update({
        "pdf_url": result["pdf_path"],
        "pdf_page_urls": result["page_urls"],
    }).eq("id", exam_id).execute()

    current_app.logger.info("PDF uploaded for exam %s: %s pages", exam_id, len(result.get("page_urls", [])),
                            extra={"exam_id": exam_id, "file_size": file_size})
    return jsonify(result)


@exam_bp.route("/", methods=["POST"])
@teacher_required
def create_exam():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Data diperlukan"}), 400
    allowed = {"title", "subject", "total_questions", "duration_minutes", "mcq_percentage",
               "essay_percentage", "passing_score", "is_active", "school_id", "class_ids",
               "question_types", "answer_key", "question_weights", "question_pages",
               "penalty_per_violation", "max_violations", "allow_calculator", "exam_rules",
               "publish_mode", "show_results", "allow_retraction"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    filtered["teacher_id"] = g.user_id
    filtered["school_id"] = filtered.get("school_id") or g.get("user_school_id")
    supabase = get_supabase()
    res = supabase.table("exams").insert(filtered).execute()
    return jsonify(res.data[0]), 201


@exam_bp.route("/<exam_id>", methods=["GET"])
@login_required
def get_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    if not res.data:
        raise NotFoundError("Ujian", exam_id)
    exam_data = res.data
    # Strip answer_key if user is not the teacher/admin
    role = g.get("user_role", "")
    if role not in ("admin_sekolah", "super_admin") and exam_data.get("teacher_id") != g.user_id:
        exam_data.pop("answer_key", None)
    return jsonify(exam_data)


@exam_bp.route("/<exam_id>", methods=["PUT"])
@teacher_required
def update_exam(exam_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Data diperlukan"}), 400
    supabase = get_supabase()
    existing = supabase.table("exams").select("teacher_id,school_id").eq("id", exam_id).single().execute().data
    if not existing:
        return jsonify({"error": "Ujian tidak ditemukan"}), 404
    # Only allow owner teacher or admin_sekolah to update
    if existing.get("teacher_id") != g.user_id and g.get("user_role") not in ("admin_sekolah", "super_admin"):
        if existing.get("school_id") != g.get("user_school_id"):
            return jsonify({"error": "Tidak punya akses"}), 403
    allowed = {"title", "subject", "total_questions", "duration_minutes", "mcq_percentage",
               "essay_percentage", "passing_score", "is_active", "school_id", "class_ids",
               "question_types", "answer_key", "question_weights", "question_pages",
               "penalty_per_violation", "max_violations", "allow_calculator", "exam_rules",
               "publish_mode", "show_results", "allow_retraction"}
    filtered = {k: v for k, v in data.items() if k in allowed}
    res = supabase.table("exams").update(filtered).eq("id", exam_id).execute()
    return jsonify(res.data[0])


@exam_bp.route("/<exam_id>", methods=["DELETE"])
@teacher_required
def delete_exam(exam_id):
    supabase = get_supabase()
    existing = supabase.table("exams").select("teacher_id,school_id").eq("id", exam_id).single().execute().data
    if not existing:
        return jsonify({"error": "Ujian tidak ditemukan"}), 404
    # Only allow owner teacher or admin_sekolah/super_admin to delete
    if existing.get("teacher_id") != g.user_id and g.get("user_role") not in ("admin_sekolah", "super_admin"):
        if existing.get("school_id") != g.get("user_school_id"):
            return jsonify({"error": "Tidak punya akses"}), 403
    supabase.table("exams").delete().eq("id", exam_id).execute()
    return jsonify({"ok": True})
