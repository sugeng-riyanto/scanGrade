"""Super Admin — full access to all schools, teachers, exams, submissions."""
import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, g, request, jsonify, redirect
from app.utils.auth import login_required, get_supabase
from app.services.audit_service import log_activity

super_bp = Blueprint("super_admin", __name__, url_prefix="/super-admin")


def _sa_required(f):
    """Super admin only — can access ALL data across all schools."""
    from functools import wraps
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "super_admin":
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return wrapper


@super_bp.route("/dashboard")
@_sa_required
def dashboard():
    supabase = get_supabase()

    # ── Stats ──
    total_schools = supabase.table("schools").select("id", count="exact").execute().count or 0
    total_users = supabase.table("profiles").select("id", count="exact").execute().count or 0
    total_teachers = supabase.table("profiles").select("id", count="exact").eq("role", "guru").execute().count or 0
    total_students = supabase.table("profiles").select("id", count="exact").eq("role", "murid").execute().count or 0
    total_exams = supabase.table("exams").select("id", count="exact").execute().count or 0
    total_subs = supabase.table("submissions").select("id", count="exact").execute().count or 0
    pending_requests = supabase.table("school_registration_requests").select("id", count="exact").eq("status", "pending").execute().count or 0

    # ── Schools ──
    schools = supabase.table("schools").select("id, name, status, created_at").order("created_at", desc=True).limit(10).execute().data or []

    # ── Recent activity ──
    recent_logs = []
    try:
        recent_logs = supabase.table("audit_logs").select("*, profiles!inner(full_name)").order("created_at", desc=True).limit(20).execute().data or []
    except Exception:
        pass

    # ── Registration requests ──
    requests = []
    try:
        requests = supabase.table("school_registration_requests").select("*").order("created_at", desc=True).limit(20).execute().data or []
    except Exception:
        pass

    return render_template("super_admin/dashboard.html",
        total_schools=total_schools, total_users=total_users,
        total_teachers=total_teachers, total_students=total_students,
        total_exams=total_exams, total_subs=total_subs,
        pending_requests=pending_requests,
        schools=schools, recent_logs=recent_logs, requests=requests,
    )


@super_bp.route("/schools")
@_sa_required
def schools():
    supabase = get_supabase()
    q = request.args.get("q", "")
    data = supabase.table("schools").select("*").order("created_at", desc=True).execute().data or []
    if q:
        data = [s for s in data if q.lower() in (s.get("name", "") + s.get("npsn", "")).lower()]
    for s in data:
        s["teacher_count"] = supabase.table("profiles").select("id", count="exact").eq("role", "guru").eq("school_id", s["id"]).execute().count or 0
        s["student_count"] = supabase.table("profiles").select("id", count="exact").eq("role", "murid").eq("school_id", s["id"]).execute().count or 0
        s["exam_count"] = supabase.table("exams").select("id", count="exact").eq("school_id", s["id"]).execute().count or 0
    return render_template("super_admin/schools.html", schools=data, q=q)


@super_bp.route("/users")
@_sa_required
def users():
    supabase = get_supabase()
    role = request.args.get("role", "")
    q = request.args.get("q", "")
    query = supabase.table("profiles").select("*, schools!left(name)").order("created_at", desc=True)
    if role in ("super_admin", "admin_sekolah", "guru", "murid"):
        query = query.eq("role", role)
    data = query.execute().data or []
    if q:
        data = [u for u in data if q.lower() in (u.get("full_name", "") + (u.get("schools") or {}).get("name", "")).lower()]
    return render_template("super_admin/users.html", users=data, role=role, q=q)


@super_bp.route("/exams")
@_sa_required
def exams():
    supabase = get_supabase()
    data = supabase.table("exams").select("*, profiles!inner(full_name)").order("created_at", desc=True).limit(50).execute().data or []
    for e in data:
        sub_count = supabase.table("submissions").select("id", count="exact").eq("exam_id", e["id"]).execute().count or 0
        e["submission_count"] = sub_count
        e["teacher_name"] = (e.pop("profiles", None) or {}).get("full_name", "-")
    return render_template("super_admin/exams.html", exams=data)


@super_bp.route("/logs")
@_sa_required
def logs():
    supabase = get_supabase()
    days = request.args.get("days", 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    data = []
    try:
        data = supabase.table("audit_logs").select("*, profiles!left(full_name)").gte("created_at", since.isoformat()).order("created_at", desc=True).limit(100).execute().data or []
    except Exception:
        pass
    return render_template("super_admin/logs.html", logs=data, days=days)
