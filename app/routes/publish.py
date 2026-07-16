from flask import Blueprint, jsonify, request, redirect, g
from app.utils.auth import teacher_or_admin_required, get_supabase
from app.decorators.security import require_school_access

publish_bp = Blueprint("publish", __name__)


@publish_bp.route("/", methods=["POST"])
@teacher_or_admin_required
def publish_scores():
    """Publish scores for an exam — expects exam_id in request body."""
    data = request.get_json()
    exam_id = (data or {}).get("exam_id", "")
    if not exam_id:
        return jsonify({"ok": False, "error": "exam_id diperlukan"}), 400
    from app.routes.teacher import _recalculate_scores
    supabase = get_supabase()
    _recalculate_scores(exam_id)
    supabase.table("submissions") \
        .update({"is_published": True, "status": "published"}) \
        .eq("exam_id", exam_id) \
        .execute()
    return jsonify({"ok": True, "exam_id": exam_id})
