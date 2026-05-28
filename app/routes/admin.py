import json
import io
from flask import Blueprint, render_template, g, request, jsonify, redirect, send_file
from app.utils.auth import admin_required, get_supabase, get_auth_client

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    supabase = get_supabase()
    profiles = supabase.table("profiles").select("*").execute().data or []
    exams = supabase.table("exams").select("id, title, subject, total_questions, question_types, question_audio, question_canvas, status, is_published, created_at").execute().data or []
    submissions = supabase.table("submissions").select("id").execute().data or []

    total_users = len(profiles)
    total_teachers = sum(1 for p in profiles if p.get("role") == "teacher")
    total_students = sum(1 for p in profiles if p.get("role") == "student")
    total_submissions = len(submissions)
    active_exams = sum(1 for e in exams if e.get("status") == "active" and e.get("is_published"))

    for e in exams:
        teacher = next((p for p in profiles if p["id"] == e.get("teacher_id")), None)
        e["teacher_name"] = (teacher or {}).get("full_name", "-")

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


@admin_bp.route("/teachers")
@admin_required
def teachers():
    supabase = get_supabase()
    teachers = supabase.table("profiles").select("*").eq("role", "teacher").execute().data or []
    for t in teachers:
        count = supabase.table("exams").select("id", count="exact").eq("teacher_id", t["id"]).execute().count or 0
        t["exam_count"] = count
    return render_template("admin/teachers.html", teachers=teachers)


@admin_bp.route("/students")
@admin_required
def students():
    supabase = get_supabase()
    students = supabase.table("profiles").select("*").eq("role", "student").execute().data or []
    for s in students:
        count = supabase.table("submissions").select("id", count="exact").eq("student_id", s["id"]).execute().count or 0
        s["submission_count"] = count
    return render_template("admin/students.html", students=students)


@admin_bp.route("/classes")
@admin_required
def classes():
    supabase = get_supabase()
    try:
        classes = supabase.table("classes").select("*").execute().data or []
    except Exception:
        classes = []
    for c in classes:
        if c.get("teacher_id"):
            t = supabase.table("profiles").select("full_name").eq("id", c["teacher_id"]).execute().data
            c["teacher_name"] = (t[0]["full_name"] if t else "-")
        else:
            c["teacher_name"] = "-"
        try:
            count = supabase.table("profiles").select("id", count="exact").eq("class_id", c["id"]).execute().count or 0
        except Exception:
            count = 0
        c["student_count"] = count
    return render_template("admin/classes.html", classes=classes)


@admin_bp.route("/exams")
@admin_required
def exams():
    supabase = get_supabase()
    exams = supabase.table("exams").select("*").order("created_at", desc=True).execute().data or []
    profiles = supabase.table("profiles").select("id,full_name").eq("role", "teacher").execute().data or []
    teacher_map = {p["id"]: p["full_name"] for p in profiles}
    for e in exams:
        e["teacher_name"] = teacher_map.get(e.get("teacher_id"), "-")
        try:
            count = supabase.table("submissions").select("id", count="exact").eq("exam_id", e["id"]).execute().count or 0
        except Exception:
            count = 0
        e["submission_count"] = count
    return render_template("admin/exams.html", exams=exams)


@admin_bp.route("/exams/<exam_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_exam_status(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("status").eq("id", exam_id).single().execute().data
    new_status = "draft" if exam["status"] == "active" else "active"
    supabase.table("exams").update({"status": new_status}).eq("id", exam_id).execute()
    if request.is_json:
        return jsonify({"success": True, "status": new_status})
    return redirect(request.referrer or "/admin/exams")


@admin_bp.route("/exams/<exam_id>/toggle-visibility", methods=["POST"])
@admin_required
def toggle_exam_visibility(exam_id):
    supabase = get_supabase()
    exam = supabase.table("exams").select("is_published").eq("id", exam_id).single().execute().data
    new_val = not exam["is_published"]
    supabase.table("exams").update({"is_published": new_val}).eq("id", exam_id).execute()
    if request.is_json:
        return jsonify({"success": True, "is_published": new_val})
    return redirect(request.referrer or "/admin/exams")


@admin_bp.route("/exams/<exam_id>/delete", methods=["POST"])
@admin_required
def delete_exam(exam_id):
    supabase = get_supabase()
    supabase.table("violation_logs").delete().eq("exam_id", exam_id).execute()
    supabase.table("exam_access_codes").delete().eq("exam_id", exam_id).execute()
    supabase.table("analytics_cache").delete().eq("exam_id", exam_id).execute()
    supabase.table("submissions").delete().eq("exam_id", exam_id).execute()
    supabase.table("exams").delete().eq("id", exam_id).execute()
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/admin/exams")


@admin_bp.route("/teachers/<teacher_id>/delete", methods=["POST"])
@admin_required
def delete_teacher(teacher_id):
    supabase = get_supabase()
    supabase.table("exams").delete().eq("teacher_id", teacher_id).execute()
    supabase.auth.admin.delete_user(teacher_id)
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/admin/teachers")


@admin_bp.route("/students/<student_id>/delete", methods=["POST"])
@admin_required
def delete_student(student_id):
    supabase = get_supabase()
    supabase.table("submissions").delete().eq("student_id", student_id).execute()
    supabase.auth.admin.delete_user(student_id)
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/admin/students")


@admin_bp.route("/classes/create", methods=["POST"])
@admin_required
def create_class():
    supabase = get_supabase()
    data = request.get_json() if request.is_json else request.form.to_dict()
    try:
        supabase.table("classes").insert({
            "name": data.get("name", ""),
            "grade_level": data.get("grade_level", ""),
            "teacher_id": data.get("teacher_id") or None,
            "school_id": data.get("school_id", 1),
        }).execute()
    except Exception as e:
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        return redirect("/admin/classes")
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/admin/classes")


@admin_bp.route("/classes/<class_id>/delete", methods=["POST"])
@admin_required
def delete_class(class_id):
    supabase = get_supabase()
    supabase.table("profiles").update({"class_id": None}).eq("class_id", class_id).execute()
    supabase.table("classes").delete().eq("id", class_id).execute()
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/admin/classes")


@admin_bp.route("/school", methods=["GET", "POST"])
@admin_required
def school():
    supabase = get_supabase()
    if request.method == "GET":
        try:
            settings = supabase.table("school_settings").select("*").eq("id", 1).single().execute().data
        except Exception:
            settings = {}
        return render_template("admin/school.html", settings=settings)
    data = {
        "school_name": request.form.get("school_name", ""),
        "npsn": request.form.get("npsn", ""),
        "address": request.form.get("address", ""),
        "province": request.form.get("province", ""),
        "city": request.form.get("city", ""),
        "district": request.form.get("district", ""),
        "academic_year": request.form.get("academic_year", "2025/2026"),
        "principal_name": request.form.get("principal_name", ""),
        "tz_offset": int(request.form.get("tz_offset", 7)),
    }
    try:
        supabase.table("school_settings").update(data).eq("id", 1).execute()
    except Exception:
        supabase.table("school_settings").insert({**data, "id": 1}).execute()
    return redirect("/admin/school")


@admin_bp.route("/students/export")
@admin_required
def export_students():
    from openpyxl import Workbook
    supabase = get_supabase()
    rows = supabase.table("profiles").select("id, full_name, nisn, nis, phone, class_id, role").eq("role", "student").execute().data or []
    classes = supabase.table("classes").select("id, name").execute().data or []
    class_map = {c["id"]: c["name"] for c in classes}
    wb = Workbook()
    ws = wb.active
    ws.title = "Siswa"
    ws.append(["No", "Nama Lengkap", "NISN", "NIS", "No. HP", "Kelas", "ID"])
    for idx, s in enumerate(rows, 1):
        ws.append([idx, s.get("full_name", ""), s.get("nisn", ""), s.get("nis", ""), s.get("phone", ""), class_map.get(s.get("class_id"), ""), s.get("id", "")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="data_siswa.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_bp.route("/teachers/export")
@admin_required
def export_teachers():
    from openpyxl import Workbook
    supabase = get_supabase()
    rows = supabase.table("profiles").select("id, full_name, phone, role").eq("role", "teacher").execute().data or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Guru"
    ws.append(["No", "Nama Lengkap", "No. HP", "ID"])
    for idx, t in enumerate(rows, 1):
        ws.append([idx, t.get("full_name", ""), t.get("phone", ""), t.get("id", "")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="data_guru.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_bp.route("/students/import", methods=["POST"])
@admin_required
def import_students():
    from openpyxl import load_workbook
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "error": "File tidak ditemukan"}), 400
    try:
        wb = load_workbook(filename=io.BytesIO(file.read()))
        ws = wb.active
    except Exception as e:
        return jsonify({"success": False, "error": f"Gagal membaca file: {e}"}), 400
    supabase = get_supabase()
    created = 0
    errors = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        full_name = str(row[0] or "").strip()
        nisn = str(row[1] or "").strip() if len(row) > 1 else ""
        nis = str(row[2] or "").strip() if len(row) > 2 else ""
        phone = str(row[3] or "").strip() if len(row) > 3 else ""
        default_email = f"siswa.{nisn or nis or row_idx}@school.local"
        default_pw = nisn or nis or "siswa123"
        try:
            res = supabase.auth.admin.create_user({
                "email": default_email,
                "password": default_pw,
                "user_metadata": {"role": "student", "full_name": full_name},
                "email_confirm": True,
            })
            uid = res.user.id
            profile_data = {"id": uid, "full_name": full_name, "role": "student"}
            if nisn:
                profile_data["nisn"] = nisn
            if nis:
                profile_data["nis"] = nis
            if phone:
                profile_data["phone"] = phone
            supabase.table("profiles").insert(profile_data).execute()
            created += 1
        except Exception as e:
            errors.append(f"Baris {row_idx} ({full_name}): {e}")
    return jsonify({"success": True, "created": created, "errors": errors})


@admin_bp.route("/teachers/import", methods=["POST"])
@admin_required
def import_teachers():
    from openpyxl import load_workbook
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "error": "File tidak ditemukan"}), 400
    try:
        wb = load_workbook(filename=io.BytesIO(file.read()))
        ws = wb.active
    except Exception as e:
        return jsonify({"success": False, "error": f"Gagal membaca file: {e}"}), 400
    supabase = get_supabase()
    created = 0
    errors = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        full_name = str(row[0] or "").strip()
        phone = str(row[1] or "").strip() if len(row) > 1 else ""
        default_email = f"guru.{full_name.lower().replace(' ', '.')}@school.local"
        default_pw = "guru123"
        try:
            res = supabase.auth.admin.create_user({
                "email": default_email,
                "password": default_pw,
                "user_metadata": {"role": "teacher", "full_name": full_name},
                "email_confirm": True,
            })
            uid = res.user.id
            profile_data = {"id": uid, "full_name": full_name, "role": "teacher"}
            if phone:
                profile_data["phone"] = phone
            supabase.table("profiles").insert(profile_data).execute()
            created += 1
        except Exception as e:
            errors.append(f"Baris {row_idx} ({full_name}): {e}")
    return jsonify({"success": True, "created": created, "errors": errors})
