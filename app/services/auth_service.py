from flask import current_app
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


def create_user(email: str, password: str, role: str = "murid"):
    supabase = get_supabase()
    return supabase.auth.admin.create_user({
        "email": email,
        "password": password,
        "user_metadata": {"role": role},
        "email_confirm": True,
    })


def sign_in(email: str, password: str):
    supabase = get_supabase()
    return supabase.auth.sign_in_with_password({"email": email, "password": password})
