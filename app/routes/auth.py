from flask import Blueprint, request, jsonify, g
from app.utils.auth import login_required, get_supabase

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    role = data.get("role", "student")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    supabase = get_supabase()
    try:
        res = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": role},
            "email_confirm": True,
        })
        return jsonify({"user": res.user.id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    supabase = get_supabase()
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return jsonify({
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "user": res.user.id,
            "role": res.user.user_metadata.get("role"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 401


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify({
        "user_id": g.user_id,
        "role": g.user_role,
    })
