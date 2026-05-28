from flask import Blueprint, request, jsonify, g, render_template, redirect, url_for, make_response
from app.utils.auth import login_required, get_supabase, get_auth_client

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    email = request.form.get("email")
    password = request.form.get("password")
    role = request.form.get("role", "student")
    full_name = request.form.get("full_name", "")

    if not email or not password:
        return render_template("auth/register.html", error="Email dan password wajib diisi")

    supabase = get_supabase()
    try:
        res = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": role, "full_name": full_name},
            "email_confirm": True,
        })
        uid = res.user.id
        supabase.table("profiles").insert({
            "id": uid,
            "full_name": full_name or email.split("@")[0],
            "role": role,
        }).execute()
        return redirect("/auth/login?registered=1")
    except Exception as e:
        return render_template("auth/register.html", error=str(e))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form.get("email")
    password = request.form.get("password")

    if not email or not password:
        return render_template("auth/login.html", error="Email dan password wajib diisi")

    supabase = get_auth_client()
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        role = res.user.user_metadata.get("role")
        redirect_url = "/teacher/dashboard" if role == "teacher" else "/student/exams"
        resp = make_response(redirect(redirect_url))
        resp.set_cookie("access_token", res.session.access_token, httponly=True, samesite="Lax", path="/")
        resp.set_cookie("refresh_token", res.session.refresh_token, httponly=True, samesite="Lax", path="/")
        return resp
    except Exception as e:
        return render_template("auth/login.html", error="Email atau password salah")


@auth_bp.route("/logout")
def logout():
    resp = make_response(redirect("/auth/login"))
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token")
    return resp


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify({
        "user_id": g.user_id,
        "role": g.user_role,
    })
