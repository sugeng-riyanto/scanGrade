from flask import Blueprint, request, jsonify

webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/midtrans", methods=["POST"])
def midtrans_callback():
    data = request.get_json()
    return jsonify({"ok": True})


@webhook_bp.route("/fonnte", methods=["POST"])
def fonnte_callback():
    data = request.get_json()
    return jsonify({"ok": True})
