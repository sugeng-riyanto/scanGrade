from flask import Blueprint, jsonify
from app.utils.auth import admin_required, get_supabase

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    supabase = get_supabase()
    total_exams = supabase.table("exams").select("count", count="exact").execute()
    total_users = supabase.table("profiles").select("count", count="exact").execute()
    return jsonify({
        "total_exams": total_exams.count,
        "total_users": total_users.count,
    })
