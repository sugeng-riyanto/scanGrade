from flask import Blueprint, jsonify
from app.utils.auth import teacher_required, get_supabase

teacher_bp = Blueprint("teacher", __name__)


@teacher_bp.route("/exams")
@teacher_required
def my_exams():
    supabase = get_supabase()
    res = supabase.table("exams").select("*").execute()
    return jsonify(res.data)
