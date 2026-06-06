from flask import Blueprint, request, jsonify, current_app

webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/midtrans", methods=["POST"])
def midtrans_callback():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400
    from app.services.midtrans_service import handle_payment_notification
    ok = handle_payment_notification(data)
    return jsonify({"ok": ok})


@webhook_bp.route("/fonnte", methods=["POST"])
def fonnte_callback():
    data = request.get_json()
    current_app.logger.info(f"Fonnte callback: {data}")
    return jsonify({"ok": True})
