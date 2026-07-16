import hashlib
import hmac as hmac_lib
from flask import Blueprint, request, jsonify, current_app
from app.config import Config

webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/midtrans", methods=["POST"])
def midtrans_callback():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400

    # Verify Midtrans signature
    server_key = Config.MIDTRANS_SERVER_KEY
    if server_key:
        order_id = data.get("order_id", "")
        status_code = str(data.get("status_code", ""))
        gross_amount = str(data.get("gross_amount", ""))
        signature_key = data.get("signature_key", "")
        payload = f"{order_id}{status_code}{gross_amount}{server_key}"
        expected = hashlib.sha512(payload.encode()).hexdigest()
        if not hmac_lib.compare_digest(expected, signature_key):
            current_app.logger.warning("Midtrans signature mismatch for order %s", order_id)
            return jsonify({"ok": False, "error": "Invalid signature"}), 403

    from app.services.midtrans_service import handle_payment_notification
    ok = handle_payment_notification(data)
    return jsonify({"ok": ok})


@webhook_bp.route("/fonnte", methods=["POST"])
def fonnte_callback():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400

    # Verify Fonnte token from header
    api_key = Config.FONNTE_API_KEY
    if api_key:
        token = request.headers.get("X-Fonnte-Token", "")
        if not hmac_lib.compare_digest(api_key, token):
            current_app.logger.warning("Fonnte token mismatch")
            return jsonify({"ok": False, "error": "Invalid token"}), 403

    current_app.logger.info(f"Fonnte callback: {data}")
    return jsonify({"ok": True})
