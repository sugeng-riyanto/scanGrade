from flask import Blueprint, render_template, g
from app.utils.auth import login_required

whiteboard_student_bp = Blueprint("whiteboard_student", __name__)


@whiteboard_student_bp.route("/whiteboard")
@login_required
def whiteboard_list():
    return render_template("student/whiteboard_list.html")


@whiteboard_student_bp.route("/whiteboard/<whiteboard_id>")
@login_required
def whiteboard_canvas(whiteboard_id):
    return render_template("student/whiteboard_canvas.html", whiteboard_id=whiteboard_id)


@whiteboard_student_bp.route("/whiteboard/<whiteboard_id>/download")
@login_required
def whiteboard_download(whiteboard_id):
    # TODO: PDF export
    return "", 501
