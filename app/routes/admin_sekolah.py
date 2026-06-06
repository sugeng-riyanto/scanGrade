import io
import re
import secrets
import string
from datetime import datetime, timezone, date

from flask import Blueprint, render_template, g, request, jsonify, redirect, flash, send_file, current_app
from openpyxl import load_workbook, Workbook
from app.utils.auth import admin_sekolah_required, get_supabase
from app.services.audit_service import log_activity, log_create, log_update, log_delete

def _gen_password(length=12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))

admin_sekolah_bp = Blueprint("admin_sekolah", __name__)


def _school_id() -> str:
    return g.get("user_school_id")


def _gen_password(length=8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


# ─── DASHBOARD ───────────────────────────────────────

@admin_sekolah_bp.route("/dashboard")
@admin_sekolah_required
def dashboard():
    sid = _school_id()
    supabase = get_supabase()

    school = supabase.table("schools").select("*").eq("id", sid).single().execute().data or {}
    students = supabase.table("students").select("id", count="exact").eq("school_id", sid).eq("status", "active").execute()
    teachers = supabase.table("teachers").select("id", count="exact").eq("school_id", sid).execute()
    classes = supabase.table("classes").select("id", count="exact").eq("school_id", sid).execute()
    years = supabase.table("school_years").select("*").eq("school_id", sid).eq("is_active", True).execute().data or []
    active_year = years[0] if years else None

    ctx = {
        "school": school,
        "student_count": students.count or 0,
        "teacher_count": teachers.count or 0,
        "class_count": classes.count or 0,
        "active_year": active_year,
    }
    return render_template("admin_sekolah/dashboard.html", **ctx)


# ─── SCHOOL PROFILE ──────────────────────────────────

@admin_sekolah_bp.route("/profile", methods=["GET", "POST"])
@admin_sekolah_required
def profile():
    sid = _school_id()
    supabase = get_supabase()

    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "npsn": request.form.get("npsn", "").strip(),
            "address": request.form.get("address", "").strip(),
            "province": request.form.get("province", "").strip(),
            "city": request.form.get("city", "").strip(),
            "district": request.form.get("district", "").strip(),
            "postal_code": request.form.get("postal_code", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "email": request.form.get("email", "").strip(),
            "website": request.form.get("website", "").strip(),
            "principal_name": request.form.get("principal_name", "").strip(),
            "principal_nip": request.form.get("principal_nip", "").strip(),
            "tz_offset": int(request.form.get("tz_offset", 7)),
        }
        try:
            supabase.table("schools").update(data).eq("id", sid).execute()
            log_activity("update", "school", sid, new_data=data, user_id=g.user_id)
            flash("Profil sekolah berhasil diperbarui", "success")
        except Exception as e:
            flash(f"Gagal: {e}", "error")
        return redirect("/admin-sekolah/profile")

    school = supabase.table("schools").select("*").eq("id", sid).single().execute().data or {}
    return render_template("admin_sekolah/profile.html", school=school)


# ─── IMPORT EXCEL ────────────────────────────────────

@admin_sekolah_bp.route("/import", methods=["GET", "POST"])
@admin_sekolah_required
def import_excel():
    if request.method == "GET":
        return render_template("admin_sekolah/import.html")

    file = request.files.get("file")
    if not file:
        flash("File tidak ditemukan", "error")
        return redirect("/admin-sekolah/import")

    sid = _school_id()
    supabase = get_supabase()

    try:
        wb = load_workbook(filename=io.BytesIO(file.read()))
    except Exception as e:
        flash(f"Gagal membaca file: {e}", "error")
        return redirect("/admin-sekolah/import")

    results = {"students": 0, "teachers": 0, "subjects": 0, "errors": []}
    sheet_names_lower = {s.lower(): s for s in wb.sheetnames}

    # ── Sheet: Murid / Students ──
    for key in ("murid", "siswa", "students", "student"):
        if key in sheet_names_lower:
            ws = wb[sheet_names_lower[key]]
            _import_students(ws, sid, supabase, results)
            break

    # ── Sheet: Guru / Teachers ──
    for key in ("guru", "teachers", "teacher"):
        if key in sheet_names_lower:
            ws = wb[sheet_names_lower[key]]
            _import_teachers(ws, sid, supabase, results)
            break

    # ── Sheet: Mata Pelajaran / Subjects ──
    for key in ("mata pelajaran", "pelajaran", "subjects", "subject", "mapel"):
        if key in sheet_names_lower:
            ws = wb[sheet_names_lower[key]]
            _import_subjects(ws, sid, supabase, results)
            break

    log_activity("import", "school", sid, new_data={"students": results["students"], "teachers": results["teachers"], "subjects": results["subjects"], "errors": len(results["errors"])}, user_id=g.user_id)
    flash(
        f"Impor selesai: {results['students']} murid, {results['teachers']} guru, {results['subjects']} mapel. "
        f"{len(results['errors'])} error.",
        "success" if not results["errors"] else "warning",
    )
    return redirect("/admin-sekolah/import")


def _import_students(ws, sid, supabase, results):
    classes_cache = {}
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        try:
            nisn = str(row[0] or "").strip()
            nama = str(row[1] or "").strip()
            kelas = str(row[2] or "").strip()
            level = str(row[3] or "").strip() if len(row) > 3 else ""
            email = str(row[4] or "").strip().lower() if len(row) > 4 else ""

            if not nisn or not nama:
                continue

            # Resolve class_id
            class_id = None
            if kelas:
                if kelas not in classes_cache:
                    c = supabase.table("classes").select("id").eq("school_id", sid).eq("name", kelas).maybe_single().execute()
                    classes_cache[kelas] = c.data["id"] if c.data else None
                class_id = classes_cache.get(kelas)

            user_email = email or f"siswa.{nisn}@school.local"
            user_pw = _gen_password()

            res = supabase.auth.admin.create_user({
                "email": user_email,
                "password": user_pw,
                "user_metadata": {"role": "murid", "full_name": nama},
                "email_confirm": True,
            })
            uid = res.user.id
            supabase.table("profiles").upsert({
                "id": uid, "full_name": nama, "phone": "", "role": "murid",
                "nisn": nisn, "class_id": class_id, "status": "active", "school_id": sid,
            }).execute()
            supabase.table("students").upsert({
                "id": uid, "school_id": sid, "class_id": class_id, "nisn": nisn,
                "status": "active",
            }).execute()
            results["students"] += 1
        except Exception as e:
            results["errors"].append(f"Baris {row_idx}: {e}")


def _import_teachers(ws, sid, supabase, results):
    subjects_cache = {}
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        try:
            nip = str(row[0] or "").strip()
            nama = str(row[1] or "").strip()
            mapel = str(row[2] or "").strip() if len(row) > 2 else ""
            email = str(row[3] or "").strip().lower() if len(row) > 3 else ""
            hp = str(row[4] or "").strip() if len(row) > 4 else ""

            if not nip or not nama:
                continue

            # Resolve subject_id
            subject_id = None
            if mapel:
                if mapel not in subjects_cache:
                    s = supabase.table("subjects").select("id").eq("school_id", sid).eq("name", mapel).maybe_single().execute()
                    subjects_cache[mapel] = s.data["id"] if s.data else None
                subject_id = subjects_cache.get(mapel)

            user_email = email or f"guru.{nip}@school.local"
            user_pw = _gen_password()

            res = supabase.auth.admin.create_user({
                "email": user_email,
                "password": user_pw,
                "user_metadata": {"role": "guru", "full_name": nama},
                "email_confirm": True,
            })
            uid = res.user.id
            supabase.table("profiles").upsert({
                "id": uid, "full_name": nama, "phone": hp, "role": "guru",
                "status": "active", "school_id": sid,
            }).execute()
            supabase.table("teachers").upsert({
                "id": uid, "school_id": sid, "employee_id": nip,
                "subject_id": subject_id,
            }).execute()
            results["teachers"] += 1
        except Exception as e:
            results["errors"].append(f"Baris {row_idx}: {e}")


def _import_subjects(ws, sid, supabase, results):
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        try:
            name = str(row[0] or "").strip()
            if not name:
                continue
            code = str(row[1] or "").strip() if len(row) > 1 else ""
            supabase.table("subjects").upsert({
                "school_id": sid, "name": name, "code": code,
            }).execute()
            results["subjects"] += 1
        except Exception as e:
            results["errors"].append(f"Baris {row_idx}: {e}")


# ─── EXPORT EXCEL ────────────────────────────────────

@admin_sekolah_bp.route("/export")
@admin_sekolah_required
def export_excel():
    sid = _school_id()
    supabase = get_supabase()

    wb = Workbook()
    # Students sheet
    ws1 = wb.active
    ws1.title = "Murid"
    ws1.append(["NISN", "Nama", "Kelas", "Level", "Email"])
    students = supabase.table("students").select("*, profiles!inner(full_name, email), classes(name)").eq("school_id", sid).execute().data or []
    for s in students:
        ws1.append([s.get("nisn", ""), (s.get("profiles") or {}).get("full_name", ""),
                     (s.get("classes") or {}).get("name", ""), "", (s.get("profiles") or {}).get("email", "")])

    # Teachers sheet
    ws2 = wb.create_sheet("Guru")
    ws2.append(["Nomor Pegawai", "Nama", "Mapel", "Email", "No HP"])
    teachers = supabase.table("teachers").select("*, profiles!inner(full_name, email, phone), subjects(name)").eq("school_id", sid).execute().data or []
    for t in teachers:
        ws2.append([t.get("employee_id", ""), (t.get("profiles") or {}).get("full_name", ""),
                     (t.get("subjects") or {}).get("name", ""), (t.get("profiles") or {}).get("email", ""),
                     (t.get("profiles") or {}).get("phone", "")])

    # Subjects sheet
    ws3 = wb.create_sheet("Mata Pelajaran")
    ws3.append(["Mata Pelajaran", "Kode"])
    subjects = supabase.table("subjects").select("*").eq("school_id", sid).execute().data or []
    for s in subjects:
        ws3.append([s.get("name", ""), s.get("code", "")])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="data_sekolah.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ─── SCHOOL YEARS ────────────────────────────────────

@admin_sekolah_bp.route("/school-years", methods=["GET", "POST"])
@admin_sekolah_required
def school_years():
    sid = _school_id()
    supabase = get_supabase()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        is_active = request.form.get("is_active") == "1"

        if not name or not start_date or not end_date:
            flash("Nama, tanggal mulai, dan tanggal berakhir wajib diisi", "error")
            return redirect("/admin-sekolah/school-years")

        try:
            if is_active:
                supabase.table("school_years").update({"is_active": False}).eq("school_id", sid).execute()
            res = supabase.table("school_years").insert({
                "school_id": sid, "name": name,
                "start_date": start_date, "end_date": end_date,
                "is_active": is_active,
            }).execute()
            new_id = res.data[0]["id"] if res.data else None
            log_activity("create", "school_year", new_id, new_data={"name": name, "start_date": start_date, "end_date": end_date, "is_active": is_active}, user_id=g.user_id)
            flash("Tahun ajaran berhasil ditambahkan", "success")
        except Exception as e:
            flash(f"Gagal: {e}", "error")
        return redirect("/admin-sekolah/school-years")

    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/school_years.html", years=years)


@admin_sekolah_bp.route("/school-years/<year_id>/toggle", methods=["POST"])
@admin_sekolah_required
def toggle_school_year(year_id):
    sid = _school_id()
    supabase = get_supabase()
    supabase.table("school_years").update({"is_active": False}).eq("school_id", sid).execute()
    supabase.table("school_years").update({"is_active": True}).eq("id", year_id).execute()
    return jsonify({"success": True})


@admin_sekolah_bp.route("/school-years/<year_id>/delete", methods=["POST"])
@admin_sekolah_required
def delete_school_year(year_id):
    supabase = get_supabase()
    try:
        supabase.table("school_years").delete().eq("id", year_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── CLASSES ─────────────────────────────────────────

@admin_sekolah_bp.route("/classes")
@admin_sekolah_required
def classes():
    sid = _school_id()
    supabase = get_supabase()
    classes_list = supabase.table("classes").select("*, profiles(full_name)").eq("school_id", sid).order("name").execute().data or []
    for c in classes_list:
        c["wali_kelas"] = (c.get("profiles") or {}).get("full_name") if c.get("profiles") else None
    teachers = supabase.table("profiles").select("id, full_name").eq("role", "guru").eq("school_id", sid).execute().data or []
    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/classes.html", classes=classes_list, teachers=teachers, years=years)


@admin_sekolah_bp.route("/classes/create", methods=["POST"])
@admin_sekolah_required
def create_class():
    sid = _school_id()
    supabase = get_supabase()
    name = request.form.get("name", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    wali_id = request.form.get("wali_kelas_id") or None
    year_id = request.form.get("school_year_id") or None
    if not name:
        flash("Nama kelas wajib diisi", "error")
        return redirect("/admin-sekolah/classes")
    try:
        res = supabase.table("classes").insert({
            "name": name, "grade_level": grade_level, "school_id": sid,
            "teacher_id": wali_id, "school_year_id": year_id,
        }).execute()
        cid = res.data[0]["id"] if res.data else None
        log_activity("create", "class", cid, new_data={"name": name, "grade_level": grade_level}, user_id=g.user_id)
        flash("Kelas berhasil ditambahkan", "success")
    except Exception as e:
        flash(f"Gagal: {e}", "error")
    return redirect("/admin-sekolah/classes")


@admin_sekolah_bp.route("/classes/<class_id>/edit", methods=["POST"])
@admin_sekolah_required
def edit_class(class_id):
    supabase = get_supabase()
    data = {}
    for key in ("name", "grade_level"):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    wali = request.form.get("wali_kelas_id")
    data["teacher_id"] = wali if wali else None
    year_id = request.form.get("school_year_id")
    data["school_year_id"] = year_id if year_id else None
    try:
        supabase.table("classes").update(data).eq("id", class_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/classes/<class_id>/delete", methods=["POST"])
@admin_sekolah_required
def delete_class(class_id):
    supabase = get_supabase()
    try:
        supabase.table("students").update({"class_id": None}).eq("class_id", class_id).execute()
        supabase.table("classes").delete().eq("id", class_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── PROMOTE (Naik Kelas) ────────────────────────────

@admin_sekolah_bp.route("/promote", methods=["GET", "POST"])
@admin_sekolah_required
def promote():
    sid = _school_id()
    supabase = get_supabase()

    if request.method == "POST":
        source_class_id = request.form.get("source_class_id")
        target_class_id = request.form.get("target_class_id")
        create_new = request.form.get("create_new") == "1"

        if not source_class_id:
            flash("Pilih kelas asal", "error")
            return redirect("/admin-sekolah/promote")

        # Get source class info for level inference
        src = supabase.table("classes").select("*").eq("id", source_class_id).single().execute().data

        if create_new:
            new_name = request.form.get("new_class_name", "").strip()
            new_level = request.form.get("new_grade_level", "").strip()
            year_id = request.form.get("school_year_id") or None
            if not new_name:
                flash("Nama kelas baru wajib diisi", "error")
                return redirect("/admin-sekolah/promote")
            res = supabase.table("classes").insert({
                "name": new_name, "grade_level": new_level or src.get("grade_level", ""),
                "school_id": sid, "school_year_id": year_id,
            }).execute()
            target_class_id = res.data[0]["id"] if res.data else None

        if not target_class_id:
            flash("Pilih atau buat kelas tujuan", "error")
            return redirect("/admin-sekolah/promote")

        # Move students
        students = supabase.table("students").select("id").eq("class_id", source_class_id).eq("status", "active").execute().data or []
        moved = 0
        for s in students:
            try:
                supabase.table("students").update({"class_id": target_class_id}).eq("id", s["id"]).execute()
                supabase.table("profiles").update({"class_id": target_class_id}).eq("id", s["id"]).execute()
                moved += 1
            except Exception:
                pass
        log_activity("promote", "class", source_class_id, new_data={"target_class_id": target_class_id, "moved": moved, "school_year_id": request.form.get("school_year_id")}, user_id=g.user_id)
        flash(f"{moved} murid berhasil dipindahkan ke kelas tujuan", "success")
        return redirect("/admin-sekolah/promote")

    classes_list = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
    teachers = supabase.table("profiles").select("id, full_name").eq("role", "guru").eq("school_id", sid).execute().data or []
    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/promote.html", classes=classes_list, teachers=teachers, years=years)


# ─── TEACHERS CRUD ───────────────────────────────────

@admin_sekolah_bp.route("/teachers")
@admin_sekolah_required
def teachers():
    sid = _school_id()
    supabase = get_supabase()
    subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
    teachers_list = supabase.table("teachers").select(
        "*, profiles!inner(id, full_name, email, phone, status), subjects(name)"
    ).eq("school_id", sid).order("employee_id").execute().data or []
    return render_template("admin_sekolah/teachers.html", teachers=teachers_list, subjects=subjects)


@admin_sekolah_bp.route("/teachers/create", methods=["POST"])
@admin_sekolah_required
def create_teacher():
    sid = _school_id()
    supabase = get_supabase()
    nip = request.form.get("employee_id", "").strip()
    nama = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    hp = request.form.get("phone", "").strip()
    subject_id = request.form.get("subject_id") or None
    password = request.form.get("password", "").strip() or _gen_password()

    if not nama:
        flash("Nama guru wajib diisi", "error")
        return redirect("/admin-sekolah/teachers")

    try:
        user_email = email or f"guru.{_gen_password(6)}@school.local"
        res = supabase.auth.admin.create_user({
            "email": user_email, "password": password,
            "user_metadata": {"role": "guru", "full_name": nama},
            "email_confirm": True,
        })
        uid = res.user.id
        supabase.table("profiles").upsert({
            "id": uid, "full_name": nama, "phone": hp, "role": "guru",
            "status": "active", "school_id": sid,
        }).execute()
        supabase.table("teachers").upsert({
            "id": uid, "school_id": sid, "employee_id": nip, "subject_id": subject_id,
        }).execute()
        log_activity("create", "teacher", uid, new_data={"full_name": nama, "employee_id": nip}, user_id=g.user_id)
        flash(f"Guru berhasil ditambahkan. Email: {user_email}, Password: {password}", "success")
    except Exception as e:
        flash(f"Gagal: {e}", "error")
    return redirect("/admin-sekolah/teachers")


@admin_sekolah_bp.route("/teachers/<teacher_id>/edit", methods=["POST"])
@admin_sekolah_required
def edit_teacher(teacher_id):
    supabase = get_supabase()
    data = {}
    for key in ("employee_id",):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    subj = request.form.get("subject_id")
    data["subject_id"] = subj if subj else None

    profile_data = {}
    for key in ("full_name", "phone"):
        val = request.form.get(key)
        if val is not None:
            profile_data[key] = val.strip()
    email = request.form.get("email", "").strip().lower()
    if email:
        profile_data["email_note"] = email  # not updateable via API

    try:
        if data:
            supabase.table("teachers").update(data).eq("id", teacher_id).execute()
        if profile_data:
            supabase.table("profiles").update(profile_data).eq("id", teacher_id).execute()
        log_activity("update", "teacher", teacher_id, new_data={**data, **profile_data}, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/teachers/<teacher_id>/delete", methods=["POST"])
@admin_sekolah_required
def delete_teacher(teacher_id):
    supabase = get_supabase()
    try:
        supabase.table("teachers").delete().eq("id", teacher_id).execute()
        supabase.table("profiles").delete().eq("id", teacher_id).execute()
        supabase.auth.admin.delete_user(teacher_id)
        log_activity("delete", "teacher", teacher_id, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/teachers/<teacher_id>/reset-password", methods=["POST"])
@admin_sekolah_required
def reset_teacher_password(teacher_id):
    supabase = get_supabase()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        supabase.auth.admin.update_user_by_id(teacher_id, {"password": password})
        log_activity("reset_password", "teacher", teacher_id, user_id=g.user_id)
        return jsonify({"success": True, "password": password})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── STUDENTS CRUD ───────────────────────────────────

@admin_sekolah_bp.route("/students")
@admin_sekolah_required
def students():
    sid = _school_id()
    supabase = get_supabase()
    classes_list = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []
    students_list = supabase.table("students").select(
        "*, profiles!inner(id, full_name, email, phone, status, nisn), classes(name)"
    ).eq("school_id", sid).order("nisn").execute().data or []
    return render_template("admin_sekolah/students.html", students=students_list, classes=classes_list)


@admin_sekolah_bp.route("/students/create", methods=["POST"])
@admin_sekolah_required
def create_student():
    sid = _school_id()
    supabase = get_supabase()
    nisn = request.form.get("nisn", "").strip()
    nama = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    class_id = request.form.get("class_id") or None
    password = request.form.get("password", "").strip() or _gen_password()

    if not nama:
        flash("Nama murid wajib diisi", "error")
        return redirect("/admin-sekolah/students")

    try:
        user_email = email or f"siswa.{nisn or _gen_password(6)}@school.local"
        res = supabase.auth.admin.create_user({
            "email": user_email, "password": password,
            "user_metadata": {"role": "murid", "full_name": nama},
            "email_confirm": True,
        })
        uid = res.user.id
        supabase.table("profiles").upsert({
            "id": uid, "full_name": nama, "role": "murid", "nisn": nisn,
            "class_id": class_id, "status": "active", "school_id": sid,
        }).execute()
        supabase.table("students").upsert({
            "id": uid, "school_id": sid, "class_id": class_id, "nisn": nisn,
            "status": "active",
        }).execute()
        log_activity("create", "student", uid, new_data={"full_name": nama, "nisn": nisn, "class_id": class_id}, user_id=g.user_id)
        flash(f"Murid berhasil ditambahkan. Email: {user_email}, Password: {password}", "success")
    except Exception as e:
        flash(f"Gagal: {e}", "error")
    return redirect("/admin-sekolah/students")


@admin_sekolah_bp.route("/students/<student_id>/edit", methods=["POST"])
@admin_sekolah_required
def edit_student(student_id):
    supabase = get_supabase()
    data = {}
    for key in ("nisn",):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    cls = request.form.get("class_id")
    data["class_id"] = cls if cls else None

    profile_data = {}
    for key in ("full_name",):
        val = request.form.get(key)
        if val is not None:
            profile_data[key] = val.strip()

    try:
        if data:
            supabase.table("students").update(data).eq("id", student_id).execute()
            supabase.table("profiles").update({"nisn": data.get("nisn"), "class_id": data.get("class_id")}).eq("id", student_id).execute()
        if profile_data:
            supabase.table("profiles").update(profile_data).eq("id", student_id).execute()
        log_activity("update", "student", student_id, new_data={**data, **profile_data}, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/students/<student_id>/delete", methods=["POST"])
@admin_sekolah_required
def delete_student(student_id):
    supabase = get_supabase()
    try:
        supabase.table("students").delete().eq("id", student_id).execute()
        supabase.table("profiles").delete().eq("id", student_id).execute()
        supabase.auth.admin.delete_user(student_id)
        log_activity("delete", "student", student_id, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/students/<student_id>/reset-password", methods=["POST"])
@admin_sekolah_required
def reset_student_password(student_id):
    supabase = get_supabase()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        supabase.auth.admin.update_user_by_id(student_id, {"password": password})
        log_activity("reset_password", "student", student_id, user_id=g.user_id)
        return jsonify({"success": True, "password": password})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
