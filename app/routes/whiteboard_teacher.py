import json
from flask import Blueprint, render_template, g, request, jsonify, send_file, current_app
from app.utils.auth import login_required, guru_required
from app.decorators.security import require_school_access
from app.services.whiteboard_service import (
    create_whiteboard, get_whiteboard, list_whiteboards, update_whiteboard, delete_whiteboard,
    add_members, remove_member, get_members, set_permission, bulk_set_permission,
    add_slide, delete_slide, reorder_slides, list_slides, upload_slide_background,
    log_op, get_ops, log_reaction, log_anti_cheat, save_snapshot, list_snapshots,
    export_pdf, is_member, can_annotate, UPLOAD_DIR,
)
from app.utils.auth import get_supabase

whiteboard_teacher_bp = Blueprint("whiteboard_teacher", __name__)


@whiteboard_teacher_bp.route("/whiteboard")
@login_required
@guru_required
def whiteboard_list():
    whiteboards = []
    classes = []
    try:
        whiteboards = list_whiteboards(role="teacher")
        supabase = get_supabase()
        for wb in whiteboards:
            try:
                cnt = supabase.table("whiteboard_members").select("id", count="exact").eq("whiteboard_id", wb["id"]).execute()
                wb["member_count"] = cnt.count if hasattr(cnt, "count") else 0
            except Exception:
                wb["member_count"] = 0
        sid = g.get("user_school_id")
        if sid:
            classes = supabase.table("classes").select("id,name").eq("school_id", sid).order("name").execute().data or []
    except Exception as e:
        current_app.logger.error("whiteboard list error: %s", str(e)[:200])
    return render_template("teacher/whiteboard_list.html", whiteboards=whiteboards, classes=classes)


@whiteboard_teacher_bp.route("/whiteboard/<whiteboard_id>")
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def whiteboard_canvas(whiteboard_id):
    try:
        wb = get_whiteboard(whiteboard_id)
        if not wb:
            return "Whiteboard not found", 404
        ds = wb.get("display_settings")
        if isinstance(ds, str):
            try: wb["display_settings"] = json.loads(ds)
            except: wb["display_settings"] = {}
        elif not isinstance(ds, dict):
            wb["display_settings"] = {}
        slides = list_slides(whiteboard_id)
        members = get_members(whiteboard_id)
        return render_template("teacher/whiteboard_canvas.html", whiteboard=wb, slides=slides, members=members)
    except Exception as e:
        current_app.logger.error("whiteboard canvas error: %s", str(e)[:300])
        return f"Error loading whiteboard: {str(e)[:100]}", 500


@whiteboard_teacher_bp.route("/whiteboard/<whiteboard_id>/download")
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
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

@whiteboard_teacher_bp.route("/api/whiteboard/create", methods=["POST"])
@login_required
@guru_required
def api_create():
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    class_id = data.get("class_id", "").strip()
    student_ids = data.get("student_ids", [])
    if not title or not class_id:
        return jsonify({"error": "Judul dan kelas harus diisi"}), 400
    try:
        wb = create_whiteboard(title, class_id, student_ids)
        return jsonify({"success": True, "whiteboard": wb})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>", methods=["PUT"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_update(whiteboard_id):
    data = request.get_json() or {}
    if "status" in data and data["status"] == "ended":
        data["ended_at"] = "now()"
    update_whiteboard(whiteboard_id, data)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>", methods=["DELETE"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_delete(whiteboard_id):
    delete_whiteboard(whiteboard_id)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/members", methods=["GET"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_get_members(whiteboard_id):
    members = get_members(whiteboard_id)
    return jsonify({"members": members})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/members", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_add_members(whiteboard_id):
    data = request.get_json() or {}
    student_ids = data.get("student_ids", [])
    add_members(whiteboard_id, student_ids)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/members/<student_id>", methods=["DELETE"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_remove_member(whiteboard_id, student_id):
    remove_member(whiteboard_id, student_id)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/permission", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_set_permission(whiteboard_id):
    data = request.get_json() or {}
    student_id = data.get("student_id")
    can_annotate_val = data.get("can_annotate", True)
    if student_id:
        set_permission(whiteboard_id, student_id, can_annotate_val)
    elif data.get("bulk") == "all":
        bulk_set_permission(whiteboard_id, can_annotate_val)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/slides", methods=["GET"])
@login_required
@guru_required
def api_get_slides(whiteboard_id):
    slides = list_slides(whiteboard_id)
    return jsonify({"slides": slides})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/slides", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_create_slide(whiteboard_id):
    data = request.get_json() or {}
    background_url = data.get("background_url")
    slide = add_slide(whiteboard_id, background_url=background_url)
    return jsonify({"success": True, "slide": slide})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/slides/upload", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_upload_slides(whiteboard_id):
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "File tidak ditemukan"}), 400

    try:
        result = upload_slide_background(whiteboard_id, file)
        # Insert slides into DB
        for page in result["pages"]:
            add_slide(whiteboard_id, page["slide_number"], page["background_url"])
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/slides/<int:slide_number>", methods=["DELETE"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_delete_slide(whiteboard_id, slide_number):
    delete_slide(whiteboard_id, slide_number)
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/slides/reorder", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_reorder_slides(whiteboard_id):
    data = request.get_json() or {}
    reorder_slides(whiteboard_id, data.get("order", []))
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/ops", methods=["GET"])
@login_required
@guru_required
def api_get_ops(whiteboard_id):
    slide_number = request.args.get("slide", 1, type=int)
    since_seq = request.args.get("since", None, type=int)
    ops = get_ops(whiteboard_id, slide_number, since_seq)
    return jsonify({"ops": ops})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/snapshots", methods=["GET"])
@login_required
@guru_required
def api_get_snapshots(whiteboard_id):
    snapshots = list_snapshots(whiteboard_id)
    return jsonify({"snapshots": snapshots})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/snapshots", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_save_snapshot(whiteboard_id):
    data = request.get_json() or {}
    slide_number = data.get("slide_number", 1)
    image_data = data.get("image_data", "")
    if not image_data:
        return jsonify({"error": "Image data required"}), 400
    result = save_snapshot(whiteboard_id, slide_number, image_data)
    return jsonify({"success": True, "snapshot": result})


@whiteboard_teacher_bp.route("/api/whiteboard/<whiteboard_id>/display-settings", methods=["POST"])
@login_required
@guru_required
@require_school_access("whiteboards", "whiteboard_id")
def api_display_settings(whiteboard_id):
    data = request.get_json() or {}
    settings = {
        "board_mode": data.get("board_mode", "white"),
        "grid_enabled": data.get("grid_enabled", False),
        "grid_spacing": data.get("grid_spacing", 50),
        "grid_logarithmic": data.get("grid_logarithmic", False),
    }
    get_supabase().table("whiteboards").update({"display_settings": settings}).eq("id", whiteboard_id).execute()
    return jsonify({"success": True})


@whiteboard_teacher_bp.route("/api/class/<class_id>/students")
@login_required
@guru_required
def api_class_students(class_id):
    supabase = get_supabase()
    try:
        students = supabase.table("profiles").select("id,full_name").eq("class_id", class_id).eq("role", "murid").eq("status", "active").order("full_name").execute()
        return jsonify({"students": students.data or []})
    except Exception as e:
        return jsonify({"error": str(e), "students": []}), 500
