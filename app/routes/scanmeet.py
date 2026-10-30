"""Authenticated offers for every role; only school staff can request a package."""
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, render_template, request

from app.services import scanmeet_packages as packages
from app.services.audit_service import log_activity
from app.utils.auth import get_supabase, login_required

scanmeet_bp = Blueprint("scanmeet", __name__, url_prefix="/scanmeet")
REQUEST_ROLES = {"guru", "principal", "vice_principal", "admin_sekolah", "super_admin"}


@scanmeet_bp.route("", methods=["GET"])
@login_required
def offers():
    school = license_row = pending = None
    error = ""
    if g.get("user_school_id"):
        try:
            db = get_supabase()
            rows = db.table("schools").select("id,npsn,name").eq("id", g.user_school_id).limit(1).execute().data or []
            school = rows[0] if rows else None
            rows = db.table("scanmeet_school_entitlements").select("*").eq("school_id", g.user_school_id).limit(1).execute().data or []
            license_row = packages.describe(rows[0] if rows else None)
            rows = db.table("scanmeet_plan_requests").select("id,plan_code,status,created_at").eq("school_id", g.user_school_id).eq("status", "pending").limit(1).execute().data or []
            pending = rows[0] if rows else None
        except Exception:
            error = "Status sekolah belum dapat dimuat. Penawaran tetap dapat dilihat; coba lagi sebelum mengajukan."
    return render_template("scanmeet/offers.html", plans=packages.PLANS, school=school,
                           license=license_row, pending=pending, error=error,
                           can_request=g.get("user_role") in REQUEST_ROLES and bool(school) and not error)


@scanmeet_bp.route("/requests", methods=["POST"])
@login_required
def request_package():
    if g.get("user_role") not in REQUEST_ROLES or not g.get("user_school_id"):
        return jsonify({"error": "Pengajuan paket dilakukan oleh guru atau pengelola sekolah."}), 403
    payload = (request.get_json(silent=True) if request.is_json else request.form) or {}
    package = packages.plan(payload.get("plan_code"))
    note = str(payload.get("note") or "").strip()
    if not package or len(note) > 500:
        return jsonify({"error": "Pilih paket yang tersedia dan gunakan catatan maksimal 500 karakter."}), 400
    try:
        db = get_supabase()
        rows = db.table("scanmeet_plan_requests").select("id").eq("school_id", g.user_school_id).eq("status", "pending").limit(1).execute().data or []
        if rows:
            return jsonify({"error": "Sekolah sudah memiliki pengajuan yang menunggu tinjauan."}), 409
        if package["code"] == "trial":
            rows = db.table("scanmeet_school_entitlements").select("trial_started_at").eq("school_id", g.user_school_id).limit(1).execute().data or []
            if rows and rows[0].get("trial_started_at"):
                return jsonify({"error": "Sekolah sudah memakai trial. Pilih paket lanjutan."}), 409
        row = db.table("scanmeet_plan_requests").insert({
            "school_id": g.user_school_id, "requester_id": g.user_id,
            "plan_code": package["code"], "note": note, "status": "pending",
        }).execute().data or []
        log_activity("create", "scanmeet_plan_requests", row[0]["id"] if row else None,
                     new_data={"school_id": g.user_school_id, "plan_code": package["code"]})
        return jsonify({"ok": True, "message": "Pengajuan tersimpan. Pengelola akan meninjau kapasitas dan mengonfirmasi aktivasi; belum ada biaya yang ditarik."}), 201
    except Exception:
        current_app.logger.warning("ScanMeet package request unavailable", exc_info=True)
        return jsonify({"error": "Pengajuan belum dapat dikonfirmasi. Muat ulang untuk memeriksa status sebelum mencoba lagi."}), 503


def reject_request(db, request_id, actor_id):
    rows = db.table("scanmeet_plan_requests").update({
        "status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", request_id).eq("status", "pending").execute().data or []
    if not rows:
        return {"error": "Pengajuan tidak ditemukan atau sudah ditinjau."}, 409
    log_activity("reject", "scanmeet_plan_requests", request_id, user_id=actor_id,
                 new_data={"school_id": rows[0]["school_id"]})
    return {"ok": True, "message": "Pengajuan ditolak. Lisensi tidak berubah."}, 200
