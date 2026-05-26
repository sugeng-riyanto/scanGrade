from flask import Blueprint, jsonify
from app.utils.auth import login_required, get_supabase

student_bp = Blueprint("student", __name__)


@student_bp.route("/exams")
@login_required
def available_exams():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("status", "active").execute()
    return jsonify(res.data)
