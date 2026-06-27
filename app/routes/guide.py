from flask import Blueprint, render_template
from app.utils.auth import login_required

guide_bp = Blueprint("guide", __name__)


@guide_bp.route("/guide/skor")
@login_required
def guide_skor():
    return render_template("guide/skor.html")
