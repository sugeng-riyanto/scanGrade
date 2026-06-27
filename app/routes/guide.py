from flask import Blueprint, render_template, current_app, flash, redirect
from app.utils.auth import login_required

guide_bp = Blueprint("guide", __name__)


@guide_bp.route("/skor")
@login_required
def guide_skor():
    try:
        return render_template("guide/skor.html")
    except Exception as e:
        current_app.logger.error("Guide skor error: %s", str(e), exc_info=True)
        flash("Terjadi kesalahan: " + str(e)[:100], "error")
        return redirect("/")
