import json
from flask import Blueprint, render_template, g, request, jsonify, send_file
from app.utils.auth import login_required
from app.services.whiteboard_service import (
    get_whiteboard, list_whiteboards, get_members, get_ops, can_annotate,
    log_op, log_reaction, log_anti_cheat, save_snapshot, export_pdf,
)
from app.utils.auth import get_supabase

whiteboard_student_bp = Blueprint("whiteboard_student", __name__)


@whiteboard_student_bp.route("/whiteboard")
@login_required
def whiteboard_list():
    whiteboards = list_whiteboards(role="student")
    return render_template("student/whiteboard_list.html", whiteboards=whiteboards)


@whiteboard_student_bp.route("/whiteboard/<whiteboard_id>")
@login_required
def whiteboard_canvas(whiteboard_id):
    try:
        wb = get_whiteboard(whiteboard_id)
        if not wb:
            return "Whiteboard not found", 404

        supabase = get_supabase()
        profile = supabase.table("profiles").select("class_id").eq("id", g.user_id).single().execute().data
        if not profile or str(profile.get("class_id")) != str(wb.get("class_id")):
            return "Not authorized", 403

        slides = supabase.table("whiteboard_slides").select("*").eq("whiteboard_id", whiteboard_id).order("slide_number").execute().data or []
        return render_template("student/whiteboard_canvas.html", whiteboard=wb, slides=slides)
    except Exception as e:
        return f"Error: {str(e)[:100]}", 500


@whiteboard_student_bp.route("/whiteboard/<whiteboard_id>/download")
@login_required
def whiteboard_download(whiteboard_id):
    wb = get_whiteboard(whiteboard_id)
    if not wb:
        return jsonify({"error": "Whiteboard not found"}), 404
    pdf_buf = export_pdf(whiteboard_id)
    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{wb.get('title', 'papan-tulis')}.pdf",
    )


# ── API endpoints ──

@whiteboard_student_bp.route("/api/whiteboard/<whiteboard_id>/can-annotate")
@login_required
def api_can_annotate(whiteboard_id):
    allowed = can_annotate(whiteboard_id, g.user_id)
    return jsonify({"can_annotate": allowed})


@whiteboard_student_bp.route("/api/whiteboard/<whiteboard_id>/ops", methods=["GET"])
@login_required
def api_get_ops(whiteboard_id):
    slide_number = request.args.get("slide", 1, type=int)
    since_seq = request.args.get("since", None, type=int)
    ops = get_ops(whiteboard_id, slide_number, since_seq)
    return jsonify({"ops": ops})


@whiteboard_student_bp.route("/api/whiteboard/<whiteboard_id>/reaction", methods=["POST"])
@login_required
def api_reaction(whiteboard_id):
    data = request.get_json() or {}
    emoji = data.get("emoji", "")
    if emoji:
        log_reaction(whiteboard_id, emoji)
    return jsonify({"success": True})


@whiteboard_student_bp.route("/api/whiteboard/<whiteboard_id>/anti-cheat", methods=["POST"])
@login_required
def api_anti_cheat(whiteboard_id):
    data = request.get_json() or {}
    log_anti_cheat(whiteboard_id, data.get("event_type", "unknown"), data.get("event_data"))
    return jsonify({"success": True})
