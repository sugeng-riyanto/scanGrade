"""Bulk student import route — CSV upload endpoint."""

import io
from flask import Blueprint, render_template, request, jsonify, g, flash, redirect
from app.utils.auth import guru_required, get_supabase
from app.utils.responses import error_response
from app.errors import ValidationError
from app.services.student_import import validate_csv, import_students_from_csv
from app.utils.logger import get_logger

student_bp = Blueprint("students", __name__, url_prefix="/students")
logger = get_logger("students")


@student_bp.route("/import", methods=["GET"])
@guru_required
def import_page():
    return render_template("teacher/import_students.html")


@student_bp.route("/import/csv", methods=["POST"])
@guru_required
def import_csv():
    if "csv_file" not in request.files:
        return error_response("MISSING_FILE", "File CSV diperlukan", status_code=400)

    file = request.files["csv_file"]
    if file.filename == "":
        return error_response("MISSING_FILE", "Pilih file terlebih dahulu", status_code=400)

    if not file.filename.lower().endswith(".csv"):
        return error_response("INVALID_FILE", "File harus berformat .csv", status_code=422)

    class_id = request.form.get("class_id") or None

    # Validate
    file.stream.seek(0)
    errors, headers = validate_csv(file)
    if errors:
        return jsonify({"success": False, "validation_errors": errors, "total_errors": len(errors)}), 422

    # Import
    file.stream.seek(0)
    school_id = g.get("user_school_id")
    if not school_id:
        return error_response("NO_SCHOOL", "Sekolah tidak terdaftar", status_code=400)

    results = import_students_from_csv(file, school_id, class_id)

    return jsonify({
        "success": True,
        "results": results,
        "message": f"Berhasil: {results['success']}, Gagal: {results['failed']}",
    })


@student_bp.route("/template/csv", methods=["GET"])
@guru_required
def download_template():
    """Download CSV template for student import."""
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nama", "nisn", "email", "kelas", "password"])
    writer.writerow(["Contoh Siswa", "1234567890", "siswa@sekolah.id", "X IPA 1", "siswa123"])
    writer.writerow(["Contoh Lain", "1234567891", "", "X IPA 1", ""])
    mem = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    from flask import send_file
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name="template_import_siswa.csv",
    )
