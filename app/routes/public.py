"""Public-facing routes — landing, pricing, demo request."""

from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, flash, redirect
from app.utils.auth import get_supabase
from app.utils.logger import get_logger

public_bp = Blueprint("public", __name__)
logger = get_logger("public")


@public_bp.route("/loaderio-51ecf273210e88abe9f24d4eb2dba2a8.html")
def loaderio_verify():
    return "loaderio-51ecf273210e88abe9f24d4eb2dba2a8", 200, {"Content-Type": "text/plain"}


@public_bp.route("/pricing")
def pricing():
    try:
        from app.utils.auth import get_supabase
        supabase = get_supabase()
        plans = supabase.table("subscription_plans").select("*").eq("is_active", True).order("sort_order").execute().data or []
    except Exception:
        plans = []
    return render_template("pricing.html", plans=plans)


@public_bp.route("/api/demo-request", methods=["POST"])
def demo_request():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Data tidak boleh kosong"}), 400

    school_name = (data.get("school_name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()

    if not school_name:
        return jsonify({"success": False, "message": "Nama sekolah wajib diisi"}), 400
    if not email or "@" not in email:
        return jsonify({"success": False, "message": "Email tidak valid"}), 400

    supabase = get_supabase()
    try:
        supabase.table("audit_logs").insert({
            "action": "demo_request",
            "entity_type": "lead",
            "new_data": {"school_name": school_name, "email": email, "phone": phone, "message": message},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info("Demo request from %s (%s)", school_name, email)
        return jsonify({"success": True, "message": "Terima kasih! Tim kami akan menghubungi Anda."})
    except Exception as e:
        logger.warning("Failed to save demo request: %s", e)
        return jsonify({"success": True, "message": "Terima kasih! Kami akan menghubungi Anda."})
