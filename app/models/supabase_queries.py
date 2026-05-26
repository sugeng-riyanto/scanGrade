from flask import current_app
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


def find_exam_by_id(exam_id: str) -> dict | None:
    res = get_supabase().table("exams").select("*").eq("id", exam_id).single().execute()
    return res.data


def find_submissions_by_exam(exam_id: str) -> list:
    res = get_supabase().table("submissions").select("*").eq("exam_id", exam_id).execute()
    return res.data


def find_violations_by_user(user_id: str, exam_id: str) -> list:
    res = get_supabase().table("violation_logs") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("exam_id", exam_id) \
        .execute()
    return res.data
