from flask import Blueprint, render_template, g
from app.utils.auth import admin_required, get_supabase

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    supabase = get_supabase()
    profiles = supabase.table("profiles").select("*").execute().data or []
    exams = supabase.table("exams").select("id, title, subject, total_questions, question_types, question_audio, question_canvas, status, created_at").execute().data or []
    submissions = supabase.table("submissions").select("id").execute().data or []

    total_users = len(profiles)
    total_teachers = sum(1 for p in profiles if p.get("role") == "teacher")
    total_students = sum(1 for p in profiles if p.get("role") == "student")
    total_submissions = len(submissions)
    active_exams = sum(1 for e in exams if e.get("status") == "active" and e.get("is_published"))

    return render_template("admin/dashboard.html",
        total_users=total_users,
        total_teachers=total_teachers,
        total_students=total_students,
        total_exams=len(exams),
        total_submissions=total_submissions,
        active_exams=active_exams,
        users=profiles,
        exams=exams,
    )


@admin_bp.route("/users")
@admin_required
def users():
    supabase = get_supabase()
    profiles = supabase.table("profiles").select("*").execute().data or []
    return render_template("admin/users.html", users=profiles)
