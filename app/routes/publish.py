from flask import Blueprint, jsonify
from app.utils.auth import teacher_required

publish_bp = Blueprint("publish", __name__)


@publish_bp.route("/", methods=["POST"])
@teacher_required
def publish_scores():
    return jsonify({"ok": True})
