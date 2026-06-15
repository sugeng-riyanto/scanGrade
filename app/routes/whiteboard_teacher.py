from flask import Blueprint, render_template, g
from app.utils.auth import login_required, guru_required

whiteboard_teacher_bp = Blueprint("whiteboard_teacher", __name__)


@whiteboard_teacher_bp.route("/whiteboard")
@login_required
@guru_required
def whiteboard_list():
    return render_template("teacher/whiteboard_list.html")


@whiteboard_teacher_bp.route("/whiteboard/<whiteboard_id>")
@login_required
@guru_required
def whiteboard_canvas(whiteboard_id):
    return render_template("teacher/whiteboard_canvas.html", whiteboard_id=whiteboard_id)


@whiteboard_teacher_bp.route("/whiteboard/<whiteboard_id>/download")
@login_required
@guru_required
def whiteboard_download(whiteboard_id):
    # TODO: PDF export
    return "", 501
