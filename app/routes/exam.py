from flask import Blueprint, request, jsonify
from app.utils.auth import login_required, teacher_required, get_supabase

exam_bp = Blueprint("exam", __name__)


@exam_bp.route("/", methods=["GET"])
@login_required
def list_exams():
    supabase = get_supabase()
    exams = supabase.table("exams").select("*").execute()
    return jsonify(exams.data)


@exam_bp.route("/", methods=["POST"])
@teacher_required
def create_exam():
    data = request.get_json()
    supabase = get_supabase()
    res = supabase.table("exams").insert(data).execute()
    return jsonify(res.data[0]), 201


@exam_bp.route("/<exam_id>", methods=["GET"])
@login_required
def get_exam(exam_id):
    supabase = get_supabase()
    res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
    return jsonify(res.data)


@exam_bp.route("/<exam_id>", methods=["PUT"])
@teacher_required
def update_exam(exam_id):
    data = request.get_json()
    supabase = get_supabase()
    res = supabase.table("exams").update(data).eq("id", exam_id).execute()
    return jsonify(res.data[0])


@exam_bp.route("/<exam_id>", methods=["DELETE"])
@teacher_required
def delete_exam(exam_id):
    supabase = get_supabase()
    supabase.table("exams").delete().eq("id", exam_id).execute()
    return jsonify({"ok": True})
