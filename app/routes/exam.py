from flask import Blueprint, request, jsonify, current_app, g
from app.utils.auth import (login_required, teacher_required, get_supabase,
                            filing_school_for_request)
from app.services.pdf_service import upload_pdf
from app.errors import FileTooLargeError, InvalidPDFError, NotFoundError, ValidationError
from app.utils import denials
from app.utils.exam_access import can_read_exam
from app.decorators.year_lock import open_year_required

exam_bp = Blueprint("exam", __name__)
MAX_PDF_SIZE = 50 * 1024 * 1024


#: The columns a written paper may carry. `school_id` is deliberately **not** in
#: this set: the school a paper belongs to is decided by the server
#: (`filing_school_for_request`) and never by the body, because that column is what
#: scopes the paper's weighting, its pupils, its marks and its reports — a body that
#: could set it could file one school's paper into another's results.
WRITABLE_EXAM_FIELDS = {
    "title", "subject", "total_questions", "duration_minutes", "mcq_percentage",
    "essay_percentage", "passing_score", "is_active", "class_ids", "question_types",
    "answer_key", "question_weights", "question_pages", "penalty_per_violation",
    "max_violations", "allow_calculator", "exam_rules", "publish_mode",
    "show_results", "allow_retraction",
}


@exam_bp.route("/", methods=["GET"])
@login_required
def list_exams():
    """This caller's school's papers, with each answer key only for who may read it.

    The read was `select(*)` with no filter at all: every paper of every school, keys
    included, handed to any signed-in account. The write side of this blueprint is
    now pinned to one school, and a read that still crosses schools would hand back
    exactly what the write side refuses to create — another school's paper, its key
    among them. So the list is scoped to the school this request is for, and a row's
    key travels only when `can_read_exam` says the caller may read that paper (its
    owner, the admin of the school, or the platform operator).
    """
    supabase = get_supabase()
    query = supabase.table("exams").select("*")
    school_id = g.get("user_school_id")
    if school_id and g.get("user_role") != "super_admin":
        query = query.eq("school_id", school_id)
    rows = query.execute().data or []
    readable = []
    for row in rows:
        row = dict(row)
        if not can_read_exam(g.get("user_id"), g.get("user_role"), school_id, row):
            row.pop("answer_key", None)
        readable.append(row)
    return jsonify(readable)


@exam_bp.route("/<exam_id>/upload-pdf", methods=["POST"])
@teacher_required
@open_year_required("exam_id")
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
    """Mint a paper, filed under the school this request is acting in.

    This door used to take `school_id` straight out of the body —
    `filtered.get("school_id") or g.get("user_school_id")` — so a caller could file a
    paper into *any* school, its own home school among them, whether or not they were
    a member. That is the one column whose value decides which school weights the
    paper and whose pupils' marks come back under it, and it is now the server's to
    answer: `filing_school_for_request` returns the resolved active school (verified
    against an active membership in this same request) or a refusal, and a body that
    names a different school is refused rather than obeyed.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Data diperlukan"}), 400
    school_id, error = filing_school_for_request(requested_school_id=data.get("school_id"))
    if error:
        return jsonify({"error": error}), 400
    filtered = {k: v for k, v in data.items() if k in WRITABLE_EXAM_FIELDS}
    filtered["teacher_id"] = g.user_id
    filtered["school_id"] = school_id
    supabase = get_supabase()
    # Date the paper to the running year, so the pupil dashboard can scope scores
    # to the class a pupil is in this year.
    from app.services import teacher_assignments as _ta
    filtered["school_year_id"] = (_ta.active_school_year(
        supabase, filtered["school_id"]) or {}).get("id")
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
@open_year_required("exam_id")
def update_exam(exam_id):
    """Edit a paper. **A paper's school is not part of what an edit may change.**

    The row kept its `school_id` while the body was allowed to name another one, so an
    owner teacher — the ownership branch skips the school comparison — could move a
    paper into a school they were not acting in, and every reader of that column (the
    school's weighting, its exam list, its pupils' marks, its reports) followed the
    paper. The school is out of `WRITABLE_EXAM_FIELDS` now, and a body that asks for a
    different one is refused rather than silently ignored, so a client is told instead
    of guessing why its field did not stick.
    """
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
            return jsonify({"error": denials.NO_EXAM_ACCESS}), 403
    school_id, error = filing_school_for_request(requested_school_id=data.get("school_id"))
    if error:
        return jsonify({"error": error}), 400
    filtered = {k: v for k, v in data.items() if k in WRITABLE_EXAM_FIELDS}
    res = supabase.table("exams").update(filtered).eq("id", exam_id).execute()
    return jsonify(res.data[0])


@exam_bp.route("/<exam_id>", methods=["DELETE"])
@teacher_required
@open_year_required("exam_id")
def delete_exam(exam_id):
    supabase = get_supabase()
    existing = supabase.table("exams").select("teacher_id,school_id").eq("id", exam_id).single().execute().data
    if not existing:
        return jsonify({"error": "Ujian tidak ditemukan"}), 404
    # Only allow owner teacher or admin_sekolah/super_admin to delete
    if existing.get("teacher_id") != g.user_id and g.get("user_role") not in ("admin_sekolah", "super_admin"):
        if existing.get("school_id") != g.get("user_school_id"):
            return jsonify({"error": denials.NO_EXAM_ACCESS}), 403
    supabase.table("exams").delete().eq("id", exam_id).execute()
    return jsonify({"ok": True})
