from flask import current_app
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


# ── Schools ──────────────────────────────────────
def get_school(school_id: str) -> dict | None:
    res = get_supabase().table("schools").select("*").eq("id", school_id).single().execute()
    return res.data


def list_schools() -> list:
    res = get_supabase().table("schools").select("*").order("name").execute()
    return res.data


def get_school_by_npsn(npsn: str) -> dict | None:
    res = get_supabase().table("schools").select("*").eq("npsn", npsn).single().execute()
    return res.data


# ── School Registration Requests ────────────────
def list_registration_requests(status: str | None = None) -> list:
    q = get_supabase().table("school_registration_requests").select("*").order("created_at", desc=True)
    if status:
        q = q.eq("status", status)
    res = q.execute()
    return res.data


def create_registration_request(data: dict) -> dict:
    res = get_supabase().table("school_registration_requests").insert(data).execute()
    return res.data[0] if res.data else {}


# ── Registration Codes ──────────────────────────
def get_registration_code(code: str) -> dict | None:
    res = get_supabase().table("registration_codes").select("*").eq("code", code).single().execute()
    return res.data


def list_registration_codes(school_id: str) -> list:
    res = get_supabase().table("registration_codes").select("*").eq("school_id", school_id).order("created_at", desc=True).execute()
    return res.data


def create_registration_code(data: dict) -> dict:
    res = get_supabase().table("registration_codes").insert(data).execute()
    return res.data[0] if res.data else {}


def increment_code_use(code_id: str) -> None:
    get_supabase().rpc("increment_code_use", {"code_id": code_id}).execute()


# ── School Years ────────────────────────────────
def list_school_years(school_id: str) -> list:
    res = get_supabase().table("school_years").select("*").eq("school_id", school_id).order("name", desc=True).execute()
    return res.data


def get_active_school_year(school_id: str) -> dict | None:
    res = get_supabase().table("school_years").select("*").eq("school_id", school_id).eq("is_active", True).single().execute()
    return res.data


def create_school_year(data: dict) -> dict:
    res = get_supabase().table("school_years").insert(data).execute()
    return res.data[0] if res.data else {}


# ── Classes ─────────────────────────────────────
def list_classes(school_id: str, school_year_id: str | None = None) -> list:
    q = get_supabase().table("classes").select("*").eq("school_id", school_id)
    if school_year_id:
        q = q.eq("school_year_id", school_year_id)
    res = q.order("name").execute()
    return res.data


def get_class(class_id: str) -> dict | None:
    res = get_supabase().table("classes").select("*").eq("id", class_id).single().execute()
    return res.data


# ── Subjects ────────────────────────────────────
def list_subjects(school_id: str) -> list:
    res = get_supabase().table("subjects").select("*").eq("school_id", school_id).order("name").execute()
    return res.data


def get_subject(subject_id: str) -> dict | None:
    res = get_supabase().table("subjects").select("*").eq("id", subject_id).single().execute()
    return res.data


# ── Teachers ────────────────────────────────────
def list_teachers(school_id: str) -> list:
    res = (
        get_supabase()
        .table("teachers")
        .select("*, profiles!inner(id, full_name, email, phone, status)")
        .eq("school_id", school_id)
        .execute()
    )
    return res.data


def get_teacher(profile_id: str) -> dict | None:
    res = get_supabase().table("teachers").select("*, profiles(*)").eq("id", profile_id).single().execute()
    return res.data


# ── Students ────────────────────────────────────
def list_students(school_id: str, class_id: str | None = None) -> list:
    q = (
        get_supabase()
        .table("students")
        .select("*, profiles!inner(id, full_name, email, phone, status), classes(name)")
        .eq("school_id", school_id)
    )
    if class_id:
        q = q.eq("class_id", class_id)
    res = q.order("profiles(full_name)").execute()
    return res.data


def get_student(profile_id: str) -> dict | None:
    res = get_supabase().table("students").select("*, profiles(*), classes(name)").eq("id", profile_id).single().execute()
    return res.data


# ── Audit Logs ──────────────────────────────────
def create_audit_log(data: dict) -> dict:
    res = get_supabase().table("audit_logs").insert(data).execute()
    return res.data[0] if res.data else {}


def list_audit_logs(limit: int = 100) -> list:
    res = get_supabase().table("audit_logs").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data


# ── Existing helpers (preserved) ────────────────
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


# ── Teacher Assignments ──────────────────────────
def list_teacher_assignments(teacher_id: str, school_id: str) -> list:
    res = get_supabase().table("teacher_assignments") \
        .select("*, classes(id, name, grade_level), subjects(id, name, code)") \
        .eq("teacher_id", teacher_id) \
        .eq("school_id", school_id) \
        .execute()
    return res.data


def create_teacher_assignment(teacher_id: str, class_id: str, subject_id: str, school_id: str) -> dict:
    res = get_supabase().table("teacher_assignments").upsert({
        "teacher_id": teacher_id,
        "class_id": class_id,
        "subject_id": subject_id,
        "school_id": school_id,
    }).execute()
    return res.data[0] if res.data else {}


def delete_teacher_assignment(assignment_id: str) -> None:
    get_supabase().table("teacher_assignments").delete().eq("id", assignment_id).execute()
