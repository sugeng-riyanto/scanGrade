"""Tools blueprint: Answer Sheet Generator and other utility tools."""
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, send_file, g, current_app
from app.utils.auth import login_required
from app.services.answer_sheet_generator import generate_answer_sheet
from app.services.device_preview import (
    DEVICE_NAMES,
    DEVICE_WIDTHS,
    preview_exclusions,
    preview_sections,
)

tools_bp = Blueprint("tools", __name__, url_prefix="/tools")

# Internal tools are for the people who run the school's account, not for the
# students sitting an exam. `login_required` alone let any pupil open them.
STAFF_ROLES = ("super_admin", "admin_sekolah", "guru")


def staff_required(f):
    """Staff only: no student reaches an internal tool."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") not in STAFF_ROLES:
            return jsonify({"error": "Not available for this role"}), 403
        return f(*args, **kwargs)
    return wrapper


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


@tools_bp.route("/device-preview")
@staff_required
def device_preview():
    """Every main page at 320 / 375 / 768 px, side by side.

    The list comes from the app's own URL map, so this page never needs editing
    when a page is added. The frames load with the *viewer's* session, so a page
    belonging to another role redirects — which the page says out loud instead of
    quietly showing something else.
    """
    # `_get_current_object()` because the URL map belongs to the app, not to the
    # request-scoped proxy.
    app = current_app._get_current_object()
    groups = preview_sections(app)
    first = next((p["url"] for grp in groups for p in grp["pages"]), "/")
    return render_template(
        "tools/device_preview.html",
        groups=groups,
        # The URLs this preview does not render, with the reason — the page says
        # what it covers *and* what it leaves out, so an omission is a decision on
        # screen rather than a gap in the list.
        exclusions=preview_exclusions(app),
        widths=DEVICE_WIDTHS,
        names=DEVICE_NAMES,
        selected=request.args.get("page") or first,
    )
