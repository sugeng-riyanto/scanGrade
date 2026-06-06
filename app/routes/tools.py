"""Tools blueprint: Answer Sheet Generator and other utility tools."""
from flask import Blueprint, render_template, request, jsonify, send_file
from app.utils.auth import login_required
from app.services.answer_sheet_generator import generate_answer_sheet

tools_bp = Blueprint("tools", __name__, url_prefix="/tools")


@tools_bp.route("/generate-answer-sheet", methods=["GET", "POST"])
@login_required
def generate_answer_sheet_route():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form.to_dict()

        mark_type = data.get("mark_type", "circle")
        if mark_type not in ("circle", "square"):
            return jsonify({"error": "Mark type must be 'circle' or 'square'"}), 400

        exam_version = data.get("exam_version", "A").upper()
        if exam_version not in ("A", "B", "C", "D", "E"):
            return jsonify({"error": "Exam version must be A, B, C, D, or E"}), 400

        try:
            total_questions = int(data.get("total_questions", 50))
            total_questions = max(1, min(200, total_questions))
        except (ValueError, TypeError):
            total_questions = 50

        try:
            options = int(data.get("options", 5))
            options = max(2, min(8, options))
        except (ValueError, TypeError):
            options = 5

        pdf = generate_answer_sheet(
            total_questions=total_questions,
            mark_type=mark_type,
            student_name=data.get("student_name", ""),
            class_name=data.get("class_name", ""),
            subject=data.get("subject", ""),
            date=data.get("date", ""),
            exam_version=exam_version,
            school_name=data.get("school_name", ""),
            options=options,
        )

        return send_file(
            pdf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"answer_sheet_{total_questions}Q_v{exam_version}.pdf",
        )

    return render_template("tools/generate_answer_sheet.html")
