from flask import Blueprint, request, jsonify, current_app
from app.utils.auth import login_required, teacher_required, get_supabase
from app.services.pdf_service import upload_pdf
from app.errors import FileTooLargeError, InvalidPDFError, NotFoundError

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
    supabase = get_supabase()
    res = supabase.table("exams").insert(data).execute()
    return jsonify(res.data[0]), 201


@exam_bp.route("/<exam_id>", methods=["GET"])
@login_required
def get_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    if not res.data:
        raise NotFoundError("Ujian", exam_id)
    return jsonify(res.data)


@exam_bp.route("/<exam_id>", methods=["PUT"])
@teacher_required
def update_exam(exam_id):
    data = request.get_json()
    supabase = get_supabase()
    res = supabase.table("exams").update(data).eq("id", exam_id).execute()
    return jsonify(res.data[0])


@exam_bp.route("/<exam_id>", methods=["DELETE"])
@teacher_required
def delete_exam(exam_id):
    supabase = get_supabase()
    supabase.table("exams").delete().eq("id", exam_id).execute()
    return jsonify({"ok": True})
