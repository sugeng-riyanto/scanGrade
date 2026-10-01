import io
import json
import re
import secrets
import string
import time
from datetime import datetime, timezone, date
from urllib.parse import urlencode
from app.decorators.year_lock import open_year_required

from flask import Blueprint, render_template, g, request, jsonify, redirect, flash, send_file, current_app
from openpyxl import load_workbook, Workbook
from app.utils.auth import (admin_sekolah_required, get_supabase,
                            subscription_write_required, list_all_auth_users)
from app.utils import failure
from app.utils.cache import cache_get, cache_set
from app.utils.helpers import row_or_none
from app.decorators.security import require_school_access
from app.decorators.subscription import require_subscription
from app.services.audit_service import log_activity, log_create, log_update, log_delete
from app.services import trial_settings as trial_cfg
from app.services.student_import import create_student_account
from app.utils.req_cache import (invalidate_class, invalidate_school,
                                 invalidate_teacher_assignments)
from app.services import teacher_assignments as ta_service
from app.services.teacher_import import create_teacher_account
from app.services import school_officials as officials_service
from app.services.subject_service import (subject_usage, usage_confirmation_needed,
                                          usage_message)
from app.services import analysis_scope
from app.services import login_cards
from app.services import account_emails
from app.services import enrollment
from app.services import academic_year

def _gen_password(length=12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


# ── Header-driven import columns ─────────────────────────────────────────────
#
# The importer used to guess a sheet's layout from the *shape of its values*: a
# teacher row was read as "NIP, Nama, Email, Mapel" only when `cols[0].isdigit()`.
# Measured, that is wrong for every school whose employee number is not pure
# digits (`GT-001`, `1987.0101`, a NIP with a space): the row fell through to the
# last branch, `email` read the subject column and `create_user` was handed a
# subject name as an address, so the whole teacher sheet failed row after row
# while the (numeric-NISN) student sheet imported fine.
#
# Resolve a column by its **header** instead, and keep the positional heuristics
# only as a fallback for a sheet with no recognisable header row (the oldest
# templates). The aliases are the header spellings this repo has shipped across
# the student/teacher/officials templates and the `/export` workbook.
_IMPORT_ALIASES = {
    "nip": ("nip", "nipnuptk", "nomorpegawai", "nopegawai",
            "nomorindukpegawai", "employeeid", "employeenumber", "npk", "nik",
            "nomorinduk"),
    "nuptk": ("nuptk",),
    "name": ("namalengkap", "nama", "name", "fullname", "namaguru", "namamurid",
             "namapejabat"),
    "email": ("email", "alamatemail", "emailaktif"),
    "recovery_email": ("emailpemulihan", "recoveryemail", "emailalternatif", "emailcadangan"),
    "subject": ("matapelajaran", "mapel", "subject", "pelajaran", "mataajar"),
    "phone": ("nohp", "nomorhp", "hp", "phone", "telepon", "whatsapp", "wa",
              "nohandphone", "nohandpone", "kontak"),
    "password": ("password", "pw", "katasandi", "pass"),
    "nisn": ("nisn", "nomorinduksiswa"),
    "class": ("kelas", "class", "rombel", "ruangkelas"),
    "level": ("level", "tingkat", "jenjang"),
    "role": ("peran", "jabatan", "role", "position", "posisi"),
}


def _norm_header(value) -> str:
    """A header reduced to lowercase alphanumerics: `"No. HP"` -> `"nohp"`."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _header_columns(ws) -> dict:
    """Map canonical field -> column index from the worksheet's first row."""
    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not first:
        return {}
    lookup = {}
    for canonical, aliases in _IMPORT_ALIASES.items():
        for alias in aliases:
            lookup.setdefault(alias, canonical)
    found = {}
    for index, value in enumerate(first):
        canonical = lookup.get(_norm_header(value))
        if canonical and canonical not in found:
            found[canonical] = index
    return found


def _cell(row, columns, field) -> str:
    """The cell for `field`, or "". An integral float is written without `.0`."""
    index = columns.get(field)
    if index is None or index >= len(row):
        return ""
    value = row[index]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value or "").strip()


def _row_is_blank(row) -> bool:
    return not row or not any(str(c or "").strip() for c in row)


# ── List-page paging, sorting and filtering ──────────────────────────────────
#
# Shared by /teachers and /students so the two pages behave the same way: one
# spelling of "show all", of "asc/desc", and of the filter links the pagination
# carries forward. The rows are read once and sorted here rather than by the
# database, because the useful keys (a teacher's name, a pupil's class *name*) live
# on embedded rows, which PostgREST cannot order by.
_PAGE_SIZES = (20, 50, 100)


def _per_page_arg(raw) -> int:
    """A page size, or ``0`` meaning "all" — never an error for a bad value."""
    raw = str(raw or "").strip().lower()
    if raw in ("all", "semua", "0"):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 50
    return value if value in _PAGE_SIZES else 50


def _sort_dir(raw) -> str:
    return "desc" if str(raw or "").strip().lower() == "desc" else "asc"


def _filter_qs(**params) -> str:
    """The filter query string the pagination links must carry to stay filtered."""
    return urlencode({k: v for k, v in params.items() if v not in (None, "", 0)})


def _apply_sort_page(items, *, sort, direction, page, per_page, keys):
    """Sort ``items`` by one of ``keys`` then slice the requested page.

    Returns ``(page_items, total, total_pages, page)``. ``per_page == 0`` is the
    "show all" choice: one page holding everything.
    """
    key_fn = keys.get(sort) or next(iter(keys.values()))
    items = sorted(items, key=lambda row: key_fn(row),
                   reverse=(direction == "desc"))
    total = len(items)
    if not per_page:
        return items, total, 1, 1
    total_pages = max(1, -(-total // per_page))
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return items[start:start + per_page], total, total_pages, page


# Cached email map — avoids slow list_users() call on every page load
_email_cache = {"data": {}, "ts": 0}

def _get_email_map(supabase):
    """Return dict of user_id → email, cached for 60 seconds.

    ``auth.admin.list_users()`` returns a **page** — 50 by default — and this project
    holds 811 accounts, so reading it once produced addresses for the first fifty and
    nothing for the rest. Every login card past them therefore printed an empty Email
    column, which is the half of the credential the school is handing over: the pupil
    has a password and no address to use it with. ``list_all_auth_users`` walks the
    pages until one comes back empty — the same reader the user-management page
    needed, for the same reason.

    The address is read from Auth rather than from ``profiles.email`` because Auth is
    what ``/auth/login-user`` matches; the mirror is the fallback, used by the card
    builder when this listing cannot be read at all.
    """
    # `time` is the *module* here (`import time` at the top of this file), so `time()`
    # raised ``TypeError: 'module' object is not callable`` on the first line of this
    # function — every call, since it was written. Four pages guard the call and so
    # showed a blank Email column forever, and `/admin-sekolah/export/excel` does not
    # guard it and answered 500. Measured: the map was empty for all 811 accounts.
    now = time.time()
    if now - _email_cache["ts"] < 60 and _email_cache["data"]:
        return _email_cache["data"]
    try:
        m = {u.id: u.email for u in list_all_auth_users()}
        _email_cache["data"] = m
        _email_cache["ts"] = now
        return m
    except Exception:
        return _email_cache["data"]

admin_sekolah_bp = Blueprint("admin_sekolah", __name__)


def _generate_email(full_name: str, domain: str) -> str:
    """Generate email from full name: Budi Santoso → budi.santoso@domain"""
    if not full_name or not full_name.strip():
        return f"user.{_gen_password(6)}@{domain or 'school.local'}"
    parts = full_name.strip().lower().split()
    if len(parts) == 1:
        return f"{parts[0]}@{domain or 'school.local'}"
    elif len(parts) == 2:
        return f"{parts[0]}.{parts[1]}@{domain or 'school.local'}"
    else:
        # first.middle_initial.last
        first = parts[0]
        middle = parts[1][0] if len(parts[1]) > 0 else ""
        last = parts[-1]
        return f"{first}.{middle}.{last}@{domain or 'school.local'}"


def _get_email_domain(sid) -> str:
    """Get custom email domain for a school."""
    if not sid or sid == "None":
        return "scan-grade.app"
    return "scan-grade.app"


def _school_row(supabase, sid) -> dict:
    """Baris sekolah ini untuk kepala halaman, atau ``{}`` kalau tidak terbaca.

    Best-effort dengan sengaja: halaman email & aktivasi tetap harus terbuka walaupun
    satu bacaan nama sekolah gagal, sebab yang dikerjakan di sana bukan namanya.
    """
    if not sid:
        return {}
    try:
        return (supabase.table("schools").select("name, npsn").eq("id", sid)
                .single().execute().data) or {}
    except Exception:
        return {}


def _school_id() -> str | None:
    sid = g.get("user_school_id")
    if not sid or sid == "None":
        return None
    return sid


def _wants_json() -> bool:
    """A browser form gets flash + redirect; an API caller gets the answer.

    The same test `admin_subject_delete` already applies inline — named here
    because the class CRUD answers four routes with it.
    """
    return request.is_json or "application/json" in (request.headers.get("Accept") or "")


def _back_to(default: str) -> str:
    r"""Where a form goes once it has answered.

    Only this blueprint's own pages, and only as a path: `next` comes off the
    wire, so `https://evil.example` or `//evil.example` would make every one of
    these writes an open redirect, and a browser reads `\` as `/` before it
    parses the URL — hence both are refused rather than only the obvious one.
    Anything else falls back to *default*, so a caller that sends nothing still
    lands somewhere real instead of about:blank.
    """
    from urllib.parse import urlsplit

    target = request.form.get("next") or request.args.get("next") or ""
    if "\\" in target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return default
    if not parts.path.startswith("/admin-sekolah/"):
        return default
    return target


def _class_in_school(supabase, class_id, sid) -> dict | None:
    """The class row when it belongs to *this* school, otherwise nothing.

    Every id in a form decides which class is read and whose students are moved.
    Without `school_id` in the very same query, the promote form would move
    **another school's** students into a class of ours — a write, across the one
    boundary this app's RBAC is built on — and the check is also what stops the
    crash it used to be: `.single()` on a class that is not ours has no row, and
    the page answered 500.
    """
    if not class_id or not sid:
        return None
    rows = (supabase.table("classes")
            .select("id, name, grade_level, school_id, teacher_id, school_year_id")
            .eq("id", class_id).eq("school_id", sid)
            .limit(1).execute().data or [])
    return rows[0] if rows else None


def _teacher_in_school(supabase, teacher_id, sid) -> bool:
    """Whether `classes.teacher_id` may point at this id.

    The column references **profiles**, so that is the table asked — a teacher id
    typed in from outside the school would otherwise hang off our class in a
    dropdown another admin reads.
    """
    if not teacher_id or not sid:
        return not teacher_id
    rows = (supabase.table("profiles").select("id")
            .eq("id", teacher_id).eq("role", "guru").eq("school_id", sid)
            .limit(1).execute().data or [])
    return bool(rows)


def _year_in_school(supabase, year_id, sid) -> bool:
    """Whether `classes.school_year_id` may point at this id."""
    if not year_id or not sid:
        return not year_id
    rows = (supabase.table("school_years").select("id")
            .eq("id", year_id).eq("school_id", sid)
            .limit(1).execute().data or [])
    return bool(rows)





# ─── DASHBOARD ───────────────────────────────────────

@admin_sekolah_bp.route("/dashboard")
@admin_sekolah_required
def dashboard():
    sid = _school_id()
    if not sid:
        flash("Sekolah belum terdaftar. Hubungi Super Admin.", "error")
        return redirect("/auth/login")

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
        "subject_count": len(supabase.table("subjects").select("id", count="exact").eq("school_id", sid).execute().data or []),
        "active_year": active_year,
        "setup_done": bool(active_year and (classes.count or 0) > 0),
    }
    return render_template("admin_sekolah/dashboard.html", **ctx)


# ─── SCHOOL PROFILE ──────────────────────────────────

@admin_sekolah_bp.route("/profile", methods=["GET", "POST"])
@admin_sekolah_required
def profile():
    sid = _school_id()
    supabase = get_supabase()

    if request.method == "POST":
        # Handle logo upload
        logo_file = request.files.get("logo")
        logo_url = None
        if logo_file and logo_file.filename:
            import uuid, os
            ext = logo_file.filename.rsplit(".", 1)[-1].lower() if "." in logo_file.filename else "png"
            logo_name = f"logo_{sid[:8]}.{ext}"
            logo_dir = os.path.join(current_app.root_path, "static", "uploads", "logos")
            os.makedirs(logo_dir, exist_ok=True)
            logo_path = os.path.join(logo_dir, logo_name)
            logo_file.save(logo_path)
            logo_url = f"/static/uploads/logos/{logo_name}"

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
            "email_domain": request.form.get("email_domain", "").strip(),
        }
        if logo_url:
            data["logo_url"] = logo_url
        try:
            supabase.table("schools").update(data).eq("id", sid).execute()
            # The school row (name, logo, feature flags) is cached school-wide.
            invalidate_school(sid)
            log_activity("update", "school", sid, new_data=data, user_id=g.user_id)
            flash("Profil sekolah berhasil diperbarui", "success")
        except Exception as e:
            flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/profile")

    school = supabase.table("schools").select("*").eq("id", sid).single().execute().data or {}
    return render_template("admin_sekolah/profile.html", school=school)


# ─── IMPORT EXCEL ────────────────────────────────────

@admin_sekolah_bp.route("/generate-email")
@admin_sekolah_required
def generate_email_preview():
    """Preview generated email + password for a given name."""
    name = request.args.get("name", "").strip()
    sid = _school_id()
    domain = _get_email_domain(sid)
    email = _generate_email(name, domain) if name else ""
    pw = _gen_password() if name else ""
    return jsonify({"email": email, "password": pw, "domain": domain})


@admin_sekolah_bp.route("/download-template/murid")
@admin_sekolah_required
def download_template_murid():
    """Download XLSX template for students with generated passwords."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    sid = _school_id()
    domain = _get_email_domain(sid)
    school = get_supabase().table("schools").select("name, npsn").eq("id", sid).single().execute().data or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Murid"
    headers = ["NISN", "Nama Lengkap", "Kelas", "Email", "No. HP", "Password"]
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hf; cell.fill = hfill; cell.alignment = Alignment(horizontal="center")
    for i, (nama, nisn, kelas) in enumerate([("Ahmad Budiman", "1234567801", "VII-A"), ("Citra Dewi", "1234567802", "VII-B")], 2):
        email = _generate_email(nama, domain)
        pw = _gen_password()
        ws.cell(row=i, column=1, value=nisn)
        ws.cell(row=i, column=2, value=nama)
        ws.cell(row=i, column=3, value=kelas)
        ws.cell(row=i, column=4, value=email)
        ws.cell(row=i, column=5, value="")
        ws.cell(row=i, column=6, value=pw)
    for col in range(1, 7):
        ws.column_dimensions[chr(64+col)].width = 22
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_murid.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_sekolah_bp.route("/download-template/guru")
@admin_sekolah_required
def download_template_guru():
    """Download XLSX template for teachers with generated passwords."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    sid = _school_id()
    domain = _get_email_domain(sid)
    supabase = get_supabase()
    school = supabase.table("schools").select("name, npsn").eq("id", sid).single().execute().data or {}
    subjects = supabase.table("subjects").select("name").eq("school_id", sid).execute().data or []
    subj_names = [s["name"] for s in subjects]
    wb = Workbook()
    ws = wb.active
    ws.title = "Guru"
    headers = ["NIP", "Nama Lengkap", "Email", "Mata Pelajaran", "No. HP", "Email Pemulihan", "Password"]
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hf; cell.fill = hfill; cell.alignment = Alignment(horizontal="center")
    for i, (nama, nip, mapel) in enumerate([("Budi Santoso", "19870101", subj_names[0] if subj_names else ""), ("Siti Rahma", "19900202", subj_names[1] if len(subj_names) > 1 else "")], 2):
        email = _generate_email(nama, domain)
        pw = _gen_password()
        ws.cell(row=i, column=1, value=nip)
        ws.cell(row=i, column=2, value=nama)
        ws.cell(row=i, column=3, value=email)
        ws.cell(row=i, column=4, value=mapel)
        ws.cell(row=i, column=5, value="")
        ws.cell(row=i, column=6, value="")
        ws.cell(row=i, column=7, value=pw)
    for col in range(1, 8):
        ws.column_dimensions[chr(64+col)].width = 22
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_guru.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_sekolah_bp.route("/download-template/pejabat")
@admin_sekolah_required
def download_template_pejabat():
    """Download the XLSX template for head teachers and vice head teachers.

    One sheet, one Role column, because the two accounts differ by exactly that
    column (see app/services/school_officials.py). A school that prefers separate
    sheets may instead name one "Kepala Sekolah" and another "Wakil Kepala
    Sekolah" — the importer accepts both shapes.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    sid = _school_id()
    domain = _get_email_domain(sid)
    wb = Workbook()
    ws = wb.active
    ws.title = "Pejabat"
    headers = ["Jabatan", "Nama Lengkap", "Email", "No. HP"]
    hf = Font(bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = hf; cell.fill = hfill; cell.alignment = Alignment(horizontal="center")
    examples = [("Kepala Sekolah", "Drs. Hasan Basri"),
                ("Wakil Kepala Sekolah", "Rina Marlina")]
    for i, (jabatan, nama) in enumerate(examples, 2):
        ws.cell(row=i, column=1, value=jabatan)
        ws.cell(row=i, column=2, value=nama)
        ws.cell(row=i, column=3, value=_generate_email(nama, domain))
        ws.cell(row=i, column=4, value="")
    for col in range(1, 5):
        ws.column_dimensions[chr(64 + col)].width = 26
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_pejabat.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_sekolah_bp.route("/download-template/semua")
@admin_sekolah_required
def download_template_semua():
    """One workbook, one tab per roster.

    A school fills a teacher list, a pupil list and a head-teacher list, and the
    three templates were three separate downloads — so the natural move is to
    copy the teacher rows into the workbook that already holds the pupils. The
    importer has always read every named sheet in one file; this template is the
    file that makes that the easy path instead of the surprising one. The sheets
    carry the same headers as the single-kind templates, so either route imports
    identically.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    sid = _school_id()
    domain = _get_email_domain(sid)
    supabase = get_supabase()
    subjects = supabase.table("subjects").select("name").eq("school_id", sid).execute().data or []
    subj_names = [s["name"] for s in subjects]

    def add_sheet(wb, title, headers, rows):
        ws = wb.create_sheet(title)
        hf = Font(bold=True, color="FFFFFF", size=11)
        hfill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = hf; cell.fill = hfill; cell.alignment = Alignment(horizontal="center")
        for i, row in enumerate(rows, 2):
            for c, value in enumerate(row, 1):
                ws.cell(row=i, column=c, value=value)
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[chr(64 + col)].width = 24
        return ws

    wb = Workbook()
    wb.remove(wb.active)  # only the four named sheets, so nothing is unnamed
    add_sheet(
        wb, "Murid",
        ["NISN", "Nama Lengkap", "Kelas", "Email", "No. HP", "Password"],
        [("1234567801", "Ahmad Budiman", "VII-A", _generate_email("Ahmad Budiman", domain), "", _gen_password()),
         ("1234567802", "Citra Dewi", "VII-B", _generate_email("Citra Dewi", domain), "", _gen_password())],
    )
    add_sheet(
        wb, "Guru",
        ["NIP", "Nama Lengkap", "Email", "Mata Pelajaran", "No. HP", "Email Pemulihan", "Password"],
        [("19870101", "Budi Santoso", _generate_email("Budi Santoso", domain), subj_names[0] if subj_names else "", "", "", _gen_password()),
         ("19900202", "Siti Rahma", _generate_email("Siti Rahma", domain), subj_names[1] if len(subj_names) > 1 else "", "", "", _gen_password())],
    )
    add_sheet(
        wb, "Pejabat",
        ["Jabatan", "Nama Lengkap", "Email", "No. HP"],
        [("Kepala Sekolah", "Drs. Hasan Basri", _generate_email("Drs. Hasan Basri", domain), ""),
         ("Wakil Kepala Sekolah", "Rina Marlina", _generate_email("Rina Marlina", domain), "")],
    )
    add_sheet(
        wb, "Mata Pelajaran",
        ["Nama", "Kode"],
        [(subj_names[0] if subj_names else "Matematika", "MTK"),
         ("Bahasa Indonesia", "BIN")],
    )
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_semua.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
        flash(f"Gagal membaca file: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/import")

    results = {"students": 0, "teachers": 0, "officials": 0, "subjects": 0,
               "subjects_updated": 0, "errors": []}

    # A sheet is matched by its *normalised* name, not its exact spelling. A
    # school that copies this workbook gets "Guru (2)", "Mata Pelajaran " or
    # "Sheet1" for reasons that have nothing to do with the data, and matching
    # the raw name turned that into a silent zero: the upload reported
    # "0 murid, 0 guru, 0 mapel, 0 error", which reads as a clean run while
    # nothing was imported at all. `_norm_header` already strips case, spaces
    # and punctuation; reuse it here so the two readers agree on what a name is.
    sheet_lookup = {}
    for name in wb.sheetnames:
        base = _norm_header(name)
        sheet_lookup.setdefault(base, name)
        # A copied tab is "Guru (2)" / "Guru2"; the digits are Excel's, not the
        # school's, so the trimmed spelling points at the same worksheet.
        trimmed = re.sub(r"\d+$", "", base)
        if trimmed and trimmed != base:
            sheet_lookup.setdefault(trimmed, name)
    used_sheets = set()

    def _find(keys):
        """The worksheet whose normalised title is one of `keys`, else None."""
        for key in keys:
            actual = sheet_lookup.get(_norm_header(key))
            if actual is not None:
                used_sheets.add(actual)
                return wb[actual]
        return None

    # ── Sheet: Murid / Students ──
    ws = _find(("murid", "siswa", "students", "student"))
    if ws is not None:
        _import_students(ws, sid, supabase, results)

    # ── Sheet: Guru / Teachers ──
    ws = _find(("guru", "teachers", "teacher"))
    if ws is not None:
        _import_teachers(ws, sid, supabase, results)

    # ── Sheet: Pejabat sekolah (kepala sekolah & wakil kepala sekolah) ──
    #
    # Three spellings, because a school writes one sheet per role: a dedicated
    # "Kepala Sekolah" (role fixed for the sheet), a "Wakil Kepala Sekolah" (same),
    # or one combined "Pejabat" sheet whose Role column decides. An officials sheet
    # had no importer at all before this — the only way to create a head teacher was
    # the Officials page, one account per form submit.
    ws = _find(("kepala sekolah", "kepalasekolah", "principal", "kepala"))
    if ws is not None:
        _import_officials(ws, sid, supabase, results, default_role="principal")
    ws = _find(("wakil kepala sekolah", "wakilkepalasekolah", "vice principal",
                "vice_principal", "wakil"))
    if ws is not None:
        _import_officials(ws, sid, supabase, results, default_role="vice_principal")
    ws = _find(("pejabat", "pejabat sekolah", "officials", "official"))
    if ws is not None:
        _import_officials(ws, sid, supabase, results, default_role=None)

    # ── Sheet: Mata Pelajaran / Subjects ──
    ws = _find(("mata pelajaran", "pelajaran", "subjects", "subject", "mapel"))
    if ws is not None:
        _import_subjects(ws, sid, supabase, results)

    # Never let a workbook that reached no importer read as success. Only the
    # sheets present are processed, and a sheet this build does not recognise is
    # named back to the uploader — "0 murid, 0 guru, 0 error" must not be the
    # only answer to "why did my file do nothing".
    ignored = [n for n in wb.sheetnames if n not in used_sheets]
    if not used_sheets:
        found = ", ".join(wb.sheetnames) or "(kosong)"
        results["errors"].append(
            "Tidak ada sheet yang dikenali. Sheet di file ini: "
            f"{found}. Beri nama tab Murid, Guru, Pejabat, atau Mata Pelajaran."
        )
    elif ignored:
        results["errors"].append(
            "Sheet diabaikan (nama tidak dikenali): " + ", ".join(ignored)
        )

    log_activity("import", "school", sid, new_data={"students": results["students"], "teachers": results["teachers"], "officials": results["officials"], "subjects": results["subjects"], "errors": len(results["errors"])}, user_id=g.user_id)
    subj_msg = f"{results['subjects']} mapel"
    if results.get("subjects_updated"):
        subj_msg += f" ({results['subjects_updated']} diperbarui)"
    off_msg = f", {results['officials']} pejabat" if results["officials"] else ""
    msg = f"Impor selesai: {results['students']} murid, {results['teachers']} guru{off_msg}, {subj_msg}. {len(results['errors'])} error."
    if results["errors"]:
        msg += " " + results["errors"][0]
    flash(msg, "success" if not results["errors"] else "warning")
    return redirect("/admin-sekolah/import")


def _import_students(ws, sid, supabase, results):
    # The student cap is enforced here, not on the whole `/import` route: the same
    # upload can carry teacher and subject sheets, and those must not be refused
    # because the student quota is full.
    from app.services.subscription_service import check_feature_limit
    incoming = sum(1 for row in ws.iter_rows(min_row=2, values_only=True)
                   if row and row[0])
    allowed, message = check_feature_limit(sid, "add_student", extra=incoming)
    if not allowed:
        results["errors"].append(message)
        return

    classes_cache = {}
    columns = _header_columns(ws)
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if _row_is_blank(row):
            continue
        try:
            cols = [str(c or "").strip() for c in row]
            if columns.get("nisn") is not None or columns.get("name") is not None:
                # Header row understood: read by name, so column order and an
                # alphanumeric identifier cannot derail the mapping.
                nisn = _cell(row, columns, "nisn")
                nama = _cell(row, columns, "name")
                kelas = _cell(row, columns, "class")
                email = _cell(row, columns, "email")
                hp = _cell(row, columns, "phone")
            # OLD 9-col template: NPSN(0), ThnAjaran(1), NISN(2), Email(3), Nama(4), Kelas(5), Pw(6)
            elif len(cols) >= 7 and cols[0].isdigit() and cols[2].isdigit() and len(cols[0]) >= 5 and len(cols[2]) >= 8:
                nisn = cols[2]; nama = cols[4]; kelas = cols[5]; email = cols[3]; hp = ""
            # New 6-col template: NISN(0), Nama(1), Kelas(2), Email(3), No.HP(4), Pw(5)
            elif len(cols) >= 6 and cols[0].isdigit() and len(cols[0]) >= 8:
                nisn = cols[0]; nama = cols[1]; kelas = cols[2]; email = cols[3]; hp = cols[4]
            # Old 5-col: NISN(0), Nama(1), Kelas(2), Level(3), Email(4)
            elif len(cols) >= 5 and cols[0].isdigit() and len(cols[0]) >= 8:
                nisn = cols[0]; nama = cols[1]; kelas = cols[2]; email = cols[4]; hp = ""
            else:
                nisn = cols[0]; nama = cols[1]; kelas = cols[2] if len(cols) > 2 else ""; email = cols[4] if len(cols) > 4 else ""; hp = ""

            if not nisn or not nama:
                continue

            # Resolve class_id
            class_id = None
            if kelas:
                if kelas not in classes_cache:
                    c = row_or_none(
                        supabase.table("classes").select("id").eq("school_id", sid)
                        .eq("name", kelas).maybe_single().execute()
                    )
                    classes_cache[kelas] = c["id"] if c else None
                class_id = classes_cache.get(kelas)

            user_email = email or _generate_email(nama, _get_email_domain(sid))
            user_pw = _gen_password()

            # This sheet used to have no duplicate-NISN check at all, and the
            # writes ran straight through: create_user, then profiles, then
            # students -- so a NISN already used elsewhere was only rejected by
            # the global index *after* the account existed. The helper checks the
            # NISN globally first and rolls the account back if a write fails.
            create_student_account(
                supabase, school_id=sid, nisn=nisn, full_name=nama,
                email=user_email, password=user_pw, class_id=class_id, phone=hp,
            )
            results["students"] += 1
        except Exception as e:
            results["errors"].append(
                f"Baris {row_idx}: {getattr(e, 'user_message', str(e))}"
            )


def _import_teachers(ws, sid, supabase, results):
    subjects_cache = {}
    columns = _header_columns(ws)
    seen = 0
    imported_before = results.setdefault("teachers", 0)
    errors_before = len(results.setdefault("errors", []))
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if _row_is_blank(row):
            continue
        seen += 1
        try:
            cols = [str(c or "").strip() for c in row]
            # A header row is understood when it *names a person*. The employee
            # number is optional: many schools do not hold every teacher's NIP,
            # and demanding a NIP *column* before reading the header dropped the
            # whole sheet to the positional reader and made it vanish -- the live
            # report was "Sheet 'Guru': 29 baris terbaca tetapi tidak ada yang
            # dikenali" while the officials sheet beside it imported fine.
            by_header = columns.get("name") is not None
            if by_header:
                # Header row understood. This is what fixes the alphanumeric NIP:
                # the subject column is read as the subject, not as the email.
                nip = _cell(row, columns, "nip") or _cell(row, columns, "nuptk")
                nama = _cell(row, columns, "name")
                email = _cell(row, columns, "email")
                mapel_name = _cell(row, columns, "subject")
                mapels = [mapel_name] if mapel_name else []
                hp = _cell(row, columns, "phone")
                recovery_email = _cell(row, columns, "recovery_email")
                nuptk = _cell(row, columns, "nuptk") or None
            else:
                nuptk = None
                # OLD 9-col template: NPSN(0), ThnAjaran(1), NIP(2), Email(3), Nama(4), Mapel1-3(5-7), Pw(8)
                if len(cols) >= 9 and cols[2].isdigit() and len(cols[2]) >= 5:
                    nip = cols[2]; nama = cols[4]; email = cols[3]
                    mapels = [cols[i] for i in range(5, min(8, len(cols))) if cols[i]]
                    hp = ""; recovery_email = ""
                # NEW 7-col template: NIP(0), Nama(1), Email(2), Mapel(3), No.HP(4), EmailPemulihan(5), Pw(6)
                elif len(cols) >= 7 and cols[0].isdigit() and len(cols[0]) >= 5:
                    nip = cols[0]; nama = cols[1]; email = cols[2]
                    mapels = [cols[3]] if cols[3] else []
                    hp = cols[4]; recovery_email = cols[5]
                # OLD 5-col format: NIP(0), Nama(1), Mapel(2), Email(3), HP(4)
                elif len(cols) >= 5 and cols[0].isdigit() and len(cols[0]) >= 5:
                    nip = cols[0]; nama = cols[1]; mapels = [cols[2]] if cols[2] else []
                    email = cols[3] if len(cols) > 3 else ""; hp = cols[4] if len(cols) > 4 else ""; recovery_email = ""
                else:
                    nip = cols[0]; nama = cols[1]; mapels = [cols[2]] if len(cols) > 2 and cols[2] else []
                    email = cols[3] if len(cols) > 3 else ""; hp = cols[4] if len(cols) > 4 else ""; recovery_email = ""

            # A header-driven row needs a name, and nothing more: a blank NIP is
            # written as an empty employee id, not read as a reason to skip the
            # row. Only the positional fallback still needs an employee number,
            # because there it is the token that tells a teacher row from a
            # student one.
            if not nama or (not by_header and not nip):
                continue

            # Resolve subject_id (use first mapel from the list)
            subject_id = None
            for mn in mapels:
                if mn and mn not in subjects_cache:
                    s = row_or_none(
                        supabase.table("subjects").select("id").eq("school_id", sid)
                        .eq("name", mn).maybe_single().execute()
                    )
                    subjects_cache[mn] = s["id"] if s else None
                if mn and subjects_cache.get(mn):
                    subject_id = subjects_cache[mn]
                    break

            user_email = email or _generate_email(nama, _get_email_domain(sid))
            user_pw = _gen_password()

            # The helper checks the NIP (and NUPTK) globally -- teachers.employee_id
            # carries no unique index, and auth.login resolves a NIP with .limit(1),
            # so a duplicate would silently make one of the two teachers unable to
            # sign in. It also rolls the account back if a write fails.
            create_teacher_account(
                supabase, school_id=sid, full_name=nama, email=user_email,
                password=user_pw, employee_id=nip, subject_id=subject_id,
                phone=recovery_email or hp, nuptk=nuptk,
            )
            results["teachers"] += 1
        except Exception as e:
            results["errors"].append(
                f"Baris {row_idx}: {getattr(e, 'user_message', str(e))}"
            )

    # A sheet with rows that all vanish is the silent version of a failed import:
    # the upload says "0 guru, 0 error", and the operator has nothing to act on.
    # Name the sheet and the header it expected instead.
    # Only when the sheet produced *nothing at all* — not one account and not one
    # named row error. A sheet whose rows were each rejected for a real reason
    # (a duplicate NIP, a bad email) already says why; adding "unrecognised" on top
    # would bury the cause it worked hard to name.
    if seen and results.get("teachers", 0) == imported_before \
            and len(results["errors"]) == errors_before:
        results["errors"].append(
            f"Sheet '{ws.title}': {seen} baris terbaca tetapi tidak ada yang dikenali. "
            "Pastikan baris pertama adalah header, minimal 'Nama Lengkap'. "
            "Kolom 'NIP' opsional; 'Email' dan 'Mata Pelajaran' dipakai bila ada."
        )


def _import_officials(ws, sid, supabase, results, default_role=None):
    """Import a head-teacher / vice-head-teacher sheet.

    A school official is a ``profiles`` row with role ``principal`` or
    ``vice_principal`` — there is no officials table — so this calls the same
    :func:`school_officials.create_official` the Officials page calls, and the
    account is identical whichever way it was made.

    ``default_role`` is the role a dedicated sheet fixes for every row ("Kepala
    Sekolah" -> principal). A combined sheet passes ``None`` and reads the role
    from its Role/Jabatan column, accepting the spellings a school actually
    writes (`Kepala Sekolah`, `Wakil Kepala Sekolah`, `Principal`, `Vice Principal`).
    """
    from app.services import school_officials

    role_words = {
        "principal": ("kepalasekolah", "kepala", "principal", "headmaster",
                      "headteacher"),
        "vice_principal": ("wakilkepalasekolah", "wakilkepala", "wakil",
                           "viceprincipal", "vice", "deputyprincipal", "deputy"),
    }

    def resolve_role(value):
        word = _norm_header(value)
        for role, aliases in role_words.items():
            if word in aliases:
                return role
        return default_role

    columns = _header_columns(ws)
    seen = 0
    imported_before = results.setdefault("officials", 0)
    errors_before = len(results.setdefault("errors", []))
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if _row_is_blank(row):
            continue
        seen += 1
        role_raw = ""
        try:
            if columns.get("name") is not None:
                role_raw = _cell(row, columns, "role")
                nama = _cell(row, columns, "name")
                email = _cell(row, columns, "email")
                hp = _cell(row, columns, "phone")
            else:
                # Positional sheet with no header: Peran/Jabatan(0), Nama(1),
                # Email(2), No. HP(3).
                cols = [str(c or "").strip() for c in row]
                role_raw = cols[0] if cols else ""
                nama = cols[1] if len(cols) > 1 else ""
                email = cols[2] if len(cols) > 2 else ""
                hp = cols[3] if len(cols) > 3 else ""

            role = resolve_role(role_raw)
            if not role or not nama:
                continue

            user_email = email or _generate_email(nama, _get_email_domain(sid))
            user_pw = _gen_password()
            school_officials.create_official(
                supabase, school_id=sid, role=role, full_name=nama,
                email=user_email, password=user_pw, phone=hp,
            )
            results["officials"] += 1
        except Exception as e:
            results["errors"].append(
                f"Baris {row_idx}: {getattr(e, 'user_message', str(e))}"
            )

    if seen and results.get("officials", 0) == imported_before \
            and len(results["errors"]) == errors_before:
        results["errors"].append(
            f"Sheet '{ws.title}': {seen} baris terbaca tetapi tidak ada yang dikenali. "
            "Pastikan kolom Jabatan berisi Kepala Sekolah atau Wakil Kepala Sekolah."
        )


def _import_subjects(ws, sid, supabase, results):
    """Import the "Mata Pelajaran" sheet, updating subjects that already exist.

    Two traps, both reproduced against the live database before being fixed:

    1. A blank `code` must become NULL, not "". The unique index is
       `(school_id, code) WHERE code IS NOT NULL` and `'' IS NOT NULL`, so an
       empty string sits *inside* the index and the second code-less subject for
       one school is rejected:
       ``409 23505 duplicate key value violates unique constraint
       "idx_subjects_school_code"``.
    2. The payload carried no `id` while the primary key *is* `id`, so the
       default conflict target could never match -- this "upsert" was a plain
       INSERT, and every re-import duplicated the whole sheet.
       ``on_conflict="school_id,code"`` is NOT the fix: PostgREST answers 42P10
       because a *partial* index cannot serve as a conflict arbiter. Resolving
       the existing row's id instead makes the target correct by construction.
    """
    by_code = {}  # code -> existing row
    by_name = {}  # lowercased name -> existing row
    try:
        existing_rows = (
            supabase.table("subjects").select("id,name,code")
            .eq("school_id", sid).execute().data or []
        )
        for s in existing_rows:
            by_name[(s.get("name") or "").strip().lower()] = s
            if s.get("code"):
                by_code[s["code"]] = s
    except Exception as e:
        # Non-fatal: without the lookup a re-import inserts instead of updating.
        results["errors"].append(f"Gagal membaca daftar mapel: {e}")

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        try:
            name = str(row[0] or "").strip()
            if not name:
                continue
            # "" -> None, see note 1 above
            code = (str(row[1] or "").strip() if len(row) > 1 else "") or None

            target = by_code.get(code) if code else None
            if target is None:
                target = by_name.get(name.lower())

            payload = {"school_id": sid, "name": name, "code": code}
            if target:
                payload["id"] = target["id"]
                # the sheet said nothing about the code, so keep the stored one
                # instead of wiping it with a NULL
                if code is None and target.get("code"):
                    payload.pop("code")

            saved = supabase.table("subjects").upsert(payload).execute().data or []
            current = saved[0] if saved else dict(payload)
            # remember it so a repeated row inside this same sheet updates too
            by_name[name.lower()] = current
            if current.get("code"):
                by_code[current["code"]] = current
            results["subjects"] += 1
            if target:
                results["subjects_updated"] = results.get("subjects_updated", 0) + 1
        except Exception as e:
            results["errors"].append(f"Baris {row_idx}: {e}")


# ─── EXPORT EXCEL ────────────────────────────────────

@admin_sekolah_bp.route("/export")
@admin_sekolah_required
def export_excel():
    sid = _school_id()
    supabase = get_supabase()
    school = supabase.table("schools").select("npsn, name, email_domain").eq("id", sid).single().execute().data or {}
    npsn = school.get("npsn", "")
    years = supabase.table("school_years").select("name").eq("school_id", sid).eq("is_active", True).execute().data or []
    academic_year = years[0]["name"] if years else "2025/2026"
    domain = _get_email_domain(sid)
    _email_map = _get_email_map(supabase)

    wb = Workbook()
    # Students sheet (sama format dengan template download)
    ws1 = wb.active
    ws1.title = "Murid"
    ws1.append(["NPSN", "Tahun Ajaran", "NISN", "Email", "Nama Lengkap", "Kelas"])
    students = supabase.table("students").select("*, profiles!inner(id, full_name), classes(name)").eq("school_id", sid).execute().data or []
    # Ordered class-by-class, name-by-name — the same order the roster page and the
    # login-card sheet use. The database cannot order by the embedded class *name*.
    students.sort(key=lambda s: (((s.get("classes") or {}).get("name") or "").casefold(),
                                 ((s.get("profiles") or {}).get("full_name") or "").casefold()))
    for s in students:
        prof = s.get("profiles") or {}
        uid = prof.get("id", "")
        ws1.append([npsn, academic_year, s.get("nisn", ""), _email_map.get(uid, ""),
                     prof.get("full_name", ""), (s.get("classes") or {}).get("name", "")])

    # Teachers sheet (sama format dengan template download)
    ws2 = wb.create_sheet("Guru")
    ws2.append(["NPSN", "Tahun Ajaran", "NIP", "Email", "Nama Lengkap", "Mapel"])
    teachers = supabase.table("teachers").select("*, profiles!inner(id, full_name), subjects(name)").eq("school_id", sid).execute().data or []
    teachers.sort(key=lambda t: ((t.get("profiles") or {}).get("full_name") or "").casefold())
    for t in teachers:
        prof = t.get("profiles") or {}
        uid = prof.get("id", "")
        ws2.append([npsn, academic_year, t.get("employee_id", ""), _email_map.get(uid, ""),
                     prof.get("full_name", ""), (t.get("subjects") or {}).get("name", "")])

    # Subjects sheet
    ws3 = wb.create_sheet("Mata Pelajaran")
    ws3.append(["Nama Mata Pelajaran", "Kode"])
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
            flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/school-years")

    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/school_years.html", years=years)


@admin_sekolah_bp.route("/school-years/<year_id>/toggle", methods=["POST"])
@admin_sekolah_required
@require_school_access("school_years", "year_id")
def toggle_school_year(year_id):
    sid = _school_id()
    supabase = get_supabase()
    try:
        supabase.table("school_years").update({"is_active": False}).eq("school_id", sid).execute()
        supabase.table("school_years").update({"is_active": True}).eq("id", year_id).execute()
        log_activity("update", "school_year", year_id, new_data={"is_active": True}, user_id=g.user_id)
        flash("Tahun ajaran berhasil diaktifkan", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/school-years")


@admin_sekolah_bp.route("/school-years/<year_id>/delete", methods=["POST"])
@admin_sekolah_required
@require_school_access("school_years", "year_id")
def delete_school_year(year_id):
    supabase = get_supabase()
    try:
        supabase.table("school_years").delete().eq("id", year_id).execute()
        log_activity("delete", "school_year", year_id, user_id=g.user_id)
        flash("Tahun ajaran berhasil dihapus", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/school-years")


# ─── YEAR CLOSE (tutup tahun ajaran & buka tahun baru) ─
#
# One screen, because the three things that happen at the turn of a year belong
# together: the old year is closed (and becomes read-only), the new one is
# created as a draft, and every pupil gets an outcome. Doing them by hand, on
# separate pages, is how a class pointer gets overwritten with no record of where
# the pupil was — the defect `student_enrollment` (migration 046) exists to fix.

@admin_sekolah_bp.route("/school-years/close", methods=["GET", "POST"])
@admin_sekolah_required
def close_school_year():
    sid = _school_id()
    supabase = get_supabase()

    years = (supabase.table("school_years").select("*")
             .eq("school_id", sid).order("name", desc=True).execute().data or [])
    active_year = next((y for y in years if y.get("is_active")), None)
    drafts = [y for y in years if (y.get("status") or "") == "draft"]

    if request.method == "GET":
        plan = None
        classes = (supabase.table("classes")
                   .select("id, name, grade_level, school_year_id")
                   .eq("school_id", sid).execute().data or [])
        if active_year and drafts:
            plan = academic_year.plan_close(supabase, sid, active_year["id"],
                                            drafts[0]["id"], classes=classes)
        # `classes` is passed even when there is no plan yet: the template's
        # per-pupil "next class" dropdown reads it, and an empty list would render
        # a plan whose target options are all blank.
        return render_template("admin_sekolah/year_close.html",
                               years=years, active_year=active_year, drafts=drafts,
                               plan=plan, classes=classes, report=None)

    wants_json = _wants_json()

    def refuse(message, code=400):
        if wants_json:
            return jsonify({"error": message}), code
        flash(message, "error")
        return redirect("/admin-sekolah/school-years/close")

    old_id = request.form.get("old_year_id") or (active_year or {}).get("id")
    if not old_id:
        return refuse("Tidak ada tahun ajaran aktif untuk ditutup")
    if not _year_in_school(supabase, old_id, sid):
        return refuse("Tahun ajaran bukan milik sekolah ini", 403)

    # The new year: an existing draft, or one created from the form. A year is
    # never reused across schools, and its name must be new here.
    new_id = request.form.get("new_year_id") or None
    if not new_id:
        name = (request.form.get("name") or "").strip()
        start_date = (request.form.get("start_date") or "").strip()
        end_date = (request.form.get("end_date") or "").strip()
        if not (name and start_date and end_date):
            return refuse("Nama, tanggal mulai, dan tanggal berakhir tahun baru wajib diisi")
        if any((y.get("name") or "") == name for y in years):
            return refuse(f"Tahun ajaran '{name}' sudah ada")
        if not request.form.get("confirmed"):
            # Preview first: build the plan against a year that does not exist yet
            # by planning outcomes without a target (`to_year=None`), so the admin
            # sees who graduates and who advances before anything is written.
            classes = (supabase.table("classes")
                       .select("id, name, grade_level, school_year_id")
                       .eq("school_id", sid).execute().data or [])
            plan = academic_year.plan_close(supabase, sid, old_id, None, classes=classes)
            return render_template("admin_sekolah/year_close.html",
                                   years=years, active_year=active_year, drafts=drafts,
                                   plan=plan, classes=classes, report=None,
                                   pending_new={"name": name, "start_date": start_date,
                                                "end_date": end_date})
        new_year = academic_year.create_draft_year(supabase, sid, name, start_date, end_date)
        new_id = new_year.get("id")
    if not _year_in_school(supabase, new_id, sid):
        return refuse("Tahun ajaran baru bukan milik sekolah ini", 403)

    classes = (supabase.table("classes")
               .select("id, name, grade_level, school_year_id")
               .eq("school_id", sid).execute().data or [])
    plan = academic_year.plan_close(supabase, sid, old_id, new_id, classes=classes)

    # First pass (no `confirmed`): show the plan with each pupil's outcome and
    # target, so a person decides — the wizard never closes a year on its own.
    if not request.form.get("confirmed"):
        return render_template("admin_sekolah/year_close.html",
                               years=years, active_year=active_year, drafts=drafts,
                               plan=plan, classes=classes, report=None)

    # Per-pupil overrides: `outcome_<student_id>` and `to_class_<student_id>`.
    overrides = {}
    for row in plan["rows"]:
        sid_ = row["student_id"]
        chosen = request.form.get(f"outcome_{sid_}")
        target = request.form.get(f"to_class_{sid_}")
        if chosen or target:
            overrides[sid_] = {"outcome": chosen or row["outcome"],
                               "to_class_id": target or row.get("to_class_id")}

    report = academic_year.apply_close(supabase, sid, plan, overrides)
    # Only after the pupils are safely recorded does the old year close and the new
    # one start: closing first and failing halfway would leave nobody enrolled.
    academic_year.close_year(supabase, sid, old_id)
    academic_year.activate_year(supabase, sid, new_id)
    log_activity("close_year", "school_year", old_id,
                 new_data={"opened": new_id, "moved": report["moved"],
                           "recorded": report["recorded"],
                           "errors": len(report["errors"])}, user_id=g.user_id)

    if wants_json:
        return jsonify({"success": True, **report})
    if report["errors"]:
        flash(f"Tahun ajaran ditutup, tetapi {len(report['errors'])} murid gagal diproses. "
              f"Lihat laporan di bawah.", "warning")
    else:
        flash(f"Tahun ajaran ditutup. {report['moved']} murid dipindahkan, "
              f"{report['recorded']} riwayat dicatat.", "success")
    return render_template("admin_sekolah/year_close.html",
                           years=years, active_year=active_year, drafts=drafts,
                           plan=plan, classes=classes, report=report)


# ─── CLASSES ─────────────────────────────────────────

@admin_sekolah_bp.route("/classes")
@admin_sekolah_required
def classes():
    sid = _school_id()
    supabase = get_supabase()
    classes_list = supabase.table("classes").select("*, profiles!classes_teacher_id_fkey(full_name)").eq("school_id", sid).order("name").execute().data or []
    # Batch fetch student counts per class
    class_ids = [c["id"] for c in classes_list]
    if class_ids:
        all_students = supabase.table("profiles").select("class_id", count="exact").eq("role", "murid").eq("school_id", sid).in_("class_id", class_ids).execute().data or []
        from collections import Counter
        counts = Counter(s.get("class_id") for s in all_students)
    else:
        counts = {}
    for c in classes_list:
        c["wali_kelas"] = (c.get("profiles") or {}).get("full_name")
        c["wali_kelas_id"] = c.get("teacher_id")
        c["student_count"] = counts.get(c["id"], 0)
    teachers = supabase.table("profiles").select("id, full_name").eq("role", "guru").eq("school_id", sid).execute().data or []
    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/classes.html", classes=classes_list, teachers=teachers, years=years)


@admin_sekolah_bp.route("/classes/create", methods=["POST"])
@admin_sekolah_required
def create_class():
    sid = _school_id()
    supabase = get_supabase()
    wants_json = _wants_json()
    back = _back_to("/admin-sekolah/classes")

    def refuse(message, code=400):
        if wants_json:
            return jsonify({"error": message}), code
        flash(message, "error")
        return redirect(back)

    name = request.form.get("name", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    wali_id = request.form.get("wali_kelas_id") or None
    year_id = request.form.get("school_year_id") or None
    if not name:
        return refuse("Nama kelas wajib diisi")
    # Check duplicate across all roles
    dup = supabase.table("classes").select("id").eq("school_id", sid).eq("name", name).limit(1).execute()
    if dup.data:
        return refuse(f"Kelas '{name}' sudah ada")
    # The two ids the form may attach are checked against *this* school: a teacher
    # or a school year chosen from anywhere else would hang our class off somebody
    # else's roster, and nothing downstream re-checks it.
    if not _teacher_in_school(supabase, wali_id, sid):
        return refuse("Guru tersebut bukan milik sekolah ini", 403)
    if not _year_in_school(supabase, year_id, sid):
        return refuse("Tahun ajaran bukan milik sekolah ini", 403)
    try:
        res = supabase.table("classes").insert({
            "name": name, "grade_level": grade_level, "school_id": sid,
            "teacher_id": wali_id, "school_year_id": year_id, "created_by": g.user_id,
        }).execute()
        cid = res.data[0]["id"] if res.data else None
        invalidate_school(sid)          # the class list is cached per school
        log_activity("create", "class", cid, new_data={"name": name, "grade_level": grade_level}, user_id=g.user_id)
        if wants_json:
            return jsonify({"success": True, "id": cid})
        flash("Kelas berhasil ditambahkan", "success")
    except Exception as e:
        return refuse(f"Gagal: {e}")
    return redirect(back)


@admin_sekolah_bp.route("/classes/<class_id>/edit", methods=["POST"])
@admin_sekolah_required
@require_school_access("classes", "class_id")
@open_year_required("class_id")
def edit_class(class_id):
    supabase = get_supabase()
    wants_json = _wants_json()
    sid = _school_id()
    back = _back_to("/admin-sekolah/classes")

    def refuse(message, code=400):
        if wants_json:
            return jsonify({"error": message}), code
        flash(message, "error")
        return redirect(back)

    name = (request.form.get("name") or "").strip()
    if not name:
        return refuse("Nama kelas wajib diisi")
    wali_id = request.form.get("wali_kelas_id") or None
    year_id = request.form.get("school_year_id") or None
    if not _teacher_in_school(supabase, wali_id, sid):
        return refuse("Guru tersebut bukan milik sekolah ini", 403)
    if not _year_in_school(supabase, year_id, sid):
        return refuse("Tahun ajaran bukan milik sekolah ini", 403)
    # Duplicate name (excluding this row), the rule `/classes/create` already
    # applies: two "7A" rows would split one class in every dropdown on this page.
    dup = (supabase.table("classes").select("id").eq("school_id", sid)
           .eq("name", name).neq("id", class_id).limit(1).execute())
    if dup.data:
        return refuse(f"Kelas '{name}' sudah ada")
    data = {
        "name": name,
        "grade_level": (request.form.get("grade_level") or "").strip(),
        "teacher_id": wali_id,
        "school_year_id": year_id,
    }
    try:
        # `school_id` in the update as well as in the decorator: the row was
        # checked a moment ago, and this keeps the write on the same row the check
        # saw rather than on whatever the id points at now.
        supabase.table("classes").update(data).eq("id", class_id).eq("school_id", sid).execute()
        invalidate_class(class_id)
        invalidate_school(g.get("user_school_id"))
        log_activity("update", "class", class_id, new_data={"name": name}, user_id=g.user_id)
        if wants_json:
            return jsonify({"success": True})
        flash("Kelas berhasil diperbarui", "success")
        return redirect(back)
    except Exception as e:
        return refuse(f"Gagal: {e}")


@admin_sekolah_bp.route("/classes/<class_id>/delete", methods=["POST"])
@admin_sekolah_required
@require_school_access("classes", "class_id")
@open_year_required("class_id")
def delete_class(class_id):
    supabase = get_supabase()
    wants_json = _wants_json()
    back = _back_to("/admin-sekolah/classes")

    # What this delete takes with it — the question `subject_service` already
    # answers for subjects. The dialog is drawn from this count on the page, but
    # a bare POST carries no dialog, so the server counts it again: an unconfirmed
    # delete of a class holding pupils is refused with the number in the sentence.
    measured = True
    try:
        occupants = (supabase.table("students").select("id", count="exact")
                     .eq("class_id", class_id).execute().count or 0)
    except Exception:
        # A read that fails is **not** a count of zero — that is the difference
        # between "nobody is in it" and "we could not tell", and only the first
        # one may be deleted without asking. Observed live: a dropped connection
        # during the count returned 0 here and walked straight into the delete of
        # a class holding a pupil.
        measured = False
        occupants = 0
    if request.form.get("confirm") != "1":
        if not measured:
            message = ("Jumlah murid di kelas ini tidak bisa dibaca, jadi tidak "
                       "diketahui apa yang akan ikut terhapus — ulangi dengan "
                       "konfirmasi bila Anda yakin.")
            if wants_json:
                return jsonify({"error": message, "needs_confirmation": True}), 409
            flash(message, "warning")
            return redirect(back)
        if occupants:
            message = (f"Kelas ini masih berisi {occupants} murid. Menghapusnya akan "
                       "melepas semua murid dari kelas ini — bukan memindahkannya — "
                       "ulangi untuk menghapus.")
            if wants_json:
                return jsonify({"error": message, "needs_confirmation": True}), 409
            flash(message, "warning")
            return redirect(back)
    try:
        # Both tables carry a pupil's class — `students.class_id` is what the
        # promote form counts, `profiles.class_id` is what the roster reads — so
        # nulling one and leaving the other leaves a class id pointing at a row
        # that no longer exists.
        supabase.table("students").update({"class_id": None}).eq("class_id", class_id).execute()
        supabase.table("profiles").update({"class_id": None}).eq("class_id", class_id).execute()
        supabase.table("classes").delete().eq("id", class_id).execute()
        invalidate_class(class_id)
        invalidate_school(g.get("user_school_id"))
        log_activity("delete", "class", str(class_id), user_id=g.user_id)
        if wants_json:
            return jsonify({"success": True})
        flash("Kelas berhasil dihapus", "success")
        return redirect(back)
    except Exception as e:
        if wants_json:
            return jsonify({"error": str(e)}), 400
        flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect(back)


# ─── SUBJECTS CRUD ────────────────────────────────────

@admin_sekolah_bp.route("/subjects")
@admin_sekolah_required
def admin_subjects():
    sid = _school_id()
    supabase = get_supabase()
    sort = request.args.get("sort", "asc")
    q = request.args.get("q", "")
    data = supabase.table("subjects").select("*").eq("school_id", sid).order("name", desc=(sort == "desc")).execute().data or []
    if q:
        data = [s for s in data if q.lower() in s.get("name", "").lower()]
    # What each subject is carrying, so the delete dialog can name it. Two
    # batched reads rather than one count per card.
    subject_ids = [s["id"] for s in data]
    assign_counts, exam_counts = {}, {}
    if subject_ids:
        for r in (supabase.table("teacher_assignments").select("subject_id")
                  .in_("subject_id", subject_ids).execute().data or []):
            assign_counts[r["subject_id"]] = assign_counts.get(r["subject_id"], 0) + 1
        for r in (supabase.table("exams").select("subject_id")
                  .in_("subject_id", subject_ids).execute().data or []):
            key = r.get("subject_id")
            if key:
                exam_counts[key] = exam_counts.get(key, 0) + 1
    for s in data:
        s["assignment_count"] = assign_counts.get(s["id"], 0)
        s["exam_count"] = exam_counts.get(s["id"], 0)
    return render_template("admin_sekolah/subjects.html", subjects=data, sort=sort, q=q)


@admin_sekolah_bp.route("/subjects/create", methods=["POST"])
@admin_sekolah_required
def admin_subject_create():
    sid = _school_id()
    supabase = get_supabase()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Nama mapel wajib diisi", "error")
        return redirect("/admin-sekolah/subjects")
    # Check duplicate across all roles
    dup = supabase.table("subjects").select("id").eq("school_id", sid).eq("name", name).limit(1).execute()
    if dup.data:
        flash(f"Mapel '{name}' sudah ada", "error")
        return redirect("/admin-sekolah/subjects")
    try:
        supabase.table("subjects").insert({"school_id": sid, "name": name, "is_active": True, "created_by": g.user_id}).execute()
        log_activity("create", "subject", name, new_data={"name": name}, user_id=g.user_id)
        flash("Mapel berhasil ditambahkan", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/subjects")


@admin_sekolah_bp.route("/subjects/<subject_id>/edit", methods=["POST"])
@admin_sekolah_required
@require_school_access("subjects", "subject_id")
def admin_subject_edit(subject_id):
    supabase = get_supabase()
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip() or None
    if not name:
        flash("Nama mapel wajib diisi", "error")
        return redirect("/admin-sekolah/subjects")
    sid = _school_id()
    # Check duplicate name (excluding self)
    dup = supabase.table("subjects").select("id").eq("school_id", sid).eq("name", name).neq("id", subject_id).limit(1).execute()
    if dup.data:
        flash(f"Mapel '{name}' sudah ada", "error")
        return redirect("/admin-sekolah/subjects")
    try:
        supabase.table("subjects").update({"name": name, "code": code}).eq("id", subject_id).execute()
        log_activity("update", "subject", subject_id, new_data={"name": name, "code": code}, user_id=g.user_id)
        invalidate_school(sid)
        flash("Mapel berhasil diperbarui", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/subjects")


@admin_sekolah_bp.route("/subjects/<subject_id>/delete", methods=["POST"])
@admin_sekolah_required
@require_school_access("subjects", "subject_id")
def admin_subject_delete(subject_id):
    supabase = get_supabase()
    # A browser form post gets flash + redirect so the page comes back with the
    # outcome; an API/HTMX caller gets the same answer as JSON.
    wants_json = request.is_json or "application/json" in (request.headers.get("Accept") or "")

    # A delete that breaks something has to be shown first. The dialog names the
    # counts; this gate enforces it, because a bare POST carries no such promise
    # — and `exams.subject_id` is ON DELETE SET NULL, so the exams that lose the
    # subject do so without an error.
    usage = subject_usage(supabase, subject_id)
    if request.form.get("confirm") != "1" and usage_confirmation_needed(usage):
        if wants_json:
            return jsonify({"error": usage_message(usage), "needs_confirmation": True}), 409
        flash(usage_message(usage), "warning")
        return redirect("/admin-sekolah/subjects")
    try:
        supabase.table("teacher_assignments").delete().eq("subject_id", subject_id).execute()
        supabase.table("subjects").delete().eq("id", subject_id).execute()
        # Deleting a subject removes its assignments, so the school's subject
        # count moves too. The per-teacher assignment lists expire on their own
        # 60 s TTL — their keys name a teacher this route never saw.
        invalidate_school(g.get("user_school_id"))
        log_activity("delete", "subject", str(subject_id), user_id=g.user_id)
        if wants_json:
            return jsonify({"success": True})
        flash("Mapel berhasil dihapus", "success")
        return redirect("/admin-sekolah/subjects")
    except Exception as e:
        if wants_json:
            return jsonify({"error": str(e)}), 400
        flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/subjects")


# ─── PROMOTE (Naik Kelas) ────────────────────────────

@admin_sekolah_bp.route("/promote", methods=["GET", "POST"])
@admin_sekolah_required
def promote():
    sid = _school_id()
    supabase = get_supabase()

    if request.method == "POST":
        wants_json = _wants_json()
        source_class_id = request.form.get("source_class_id")
        target_class_id = request.form.get("target_class_id")
        create_new = request.form.get("create_new") == "1"
        confirmed = request.form.get("confirmed") == "1"
        year_id = request.form.get("school_year_id") or None

        def refuse(message, code=400):
            """One refusal, in the shape the caller asked for."""
            if wants_json:
                return jsonify({"error": message}), code
            flash(message, "error")
            return redirect("/admin-sekolah/promote")

        if not source_class_id:
            return refuse("Pilih kelas asal")

        # Whose class is this? Answered **before** the preview reads it, because
        # the preview is the read: a form field was the only thing deciding which
        # school's students got listed, then moved. Both ids are checked here, on
        # the same tick, whether or not the request says it is confirmed.
        src = _class_in_school(supabase, source_class_id, sid)
        if src is None:
            return refuse("Kelas asal bukan milik sekolah ini", 403)
        if not create_new:
            if not target_class_id:
                return refuse("Pilih atau buat kelas tujuan")
            tgt = _class_in_school(supabase, target_class_id, sid)
            if tgt is None:
                return refuse("Kelas tujuan bukan milik sekolah ini", 403)
        elif not _year_in_school(supabase, year_id, sid):
            return refuse("Tahun ajaran bukan milik sekolah ini", 403)

        # The same read-only rule the teacher write paths carry, at the school
        # admin's door: promoting INTO a closed year would add rows to a year
        # whose marks are already history. Refused before the preview, so a new
        # class is never created inside a closed year either.
        target_year = year_id if create_new else ((tgt or {}).get("school_year_id") or year_id)
        closed_reason = academic_year.write_refusal(supabase, target_year)
        if closed_reason:
            return refuse(closed_reason, 403)

        # Preview mode: show students before executing
        if not confirmed:
            students_to_move = supabase.table("students").select("id, profiles!inner(full_name)").eq("class_id", source_class_id).eq("status", "active").execute().data or []
            preview_students = []
            for s in students_to_move:
                prof = s.get("profiles") or {}
                preview_students.append({"name": prof.get("full_name", "?"), "id": s["id"]})
            # Get target class info — both rows were read from *this* school above,
            # so the names on this screen are ours to show.
            target_info = {"name": "Kelas Baru", "id": ""}
            if create_new:
                target_info["name"] = request.form.get("new_class_name", "Kelas Baru")
            else:
                target_info = {"name": tgt.get("name", "?"), "id": target_class_id}
            return render_template("admin_sekolah/promote_confirm.html",
                                   source_name=src.get("name", "?"),
                                   target_name=target_info["name"],
                                   students=preview_students,
                                   source_class_id=source_class_id,
                                   target_class_id=target_class_id,
                                   create_new="1" if create_new else "0",
                                   new_class_name=request.form.get("new_class_name", ""),
                                   school_year_id=request.form.get("school_year_id", ""))

        # Confirmed: execute the promotion
        if create_new:
            new_name = request.form.get("new_class_name", "").strip()
            new_level = request.form.get("new_grade_level", "").strip()
            if not new_name:
                return refuse("Nama kelas baru wajib diisi")
            # The same duplicate rule `/classes/create` applies: a second "7A"
            # would split one class across two rows in every dropdown below.
            dup = (supabase.table("classes").select("id")
                   .eq("school_id", sid).eq("name", new_name).limit(1).execute())
            if dup.data:
                return refuse(f"Kelas '{new_name}' sudah ada")
            res = supabase.table("classes").insert({
                "name": new_name, "grade_level": new_level or src.get("grade_level", ""),
                "school_id": sid, "school_year_id": year_id,
                "created_by": g.user_id,
            }).execute()
            invalidate_school(sid)
            target_class_id = res.data[0]["id"] if res.data else None
            if not target_class_id:
                return refuse("Gagal membuat kelas tujuan")

        # Move students
        students = supabase.table("students").select("id").eq("class_id", source_class_id).eq("status", "active").execute().data or []

        # Which year the pupil moves INTO, and which they came FROM. The target
        # year is the target class's own year (or the new class's, for `create_new`);
        # the source year is the source class's, if it has one. Both are needed
        # because the membership history is per year, not per class.
        tgt_year_id = year_id if create_new else ((tgt or {}).get("school_year_id") or year_id)
        src_year_id = src.get("school_year_id")

        # The outcome is what this year *was* for the pupil. It defaults by rule —
        # a school's last grade graduates, every other grade advances — and the
        # form may override it. `enrollment.default_outcome` owns that rule.
        levels = [c.get("grade_level") for c in
                  (supabase.table("classes").select("grade_level").eq("school_id", sid).execute().data or [])]
        outcome = enrollment.normalise_status(
            request.form.get("outcome"),
            default=enrollment.default_outcome(src.get("grade_level"), levels))

        moved = 0
        enrolled = 0
        for s in students:
            try:
                supabase.table("students").update({"class_id": target_class_id}).eq("id", s["id"]).execute()
                supabase.table("profiles").update({"class_id": target_class_id}).eq("id", s["id"]).execute()
                moved += 1
            except Exception:
                continue
            # The record promotion used to erase. A pupil moving within one year
            # (a class change, not a year change) has ONE row for that year, so
            # the outcome is written only when the years actually differ.
            try:
                if src_year_id and str(src_year_id) == str(tgt_year_id):
                    enrollment.record(supabase, sid, s["id"], src_year_id,
                                      target_class_id, status=outcome)
                else:
                    if src_year_id:
                        enrollment.record(supabase, sid, s["id"], src_year_id,
                                          source_class_id, status=outcome)
                    if tgt_year_id:
                        enrollment.record(supabase, sid, s["id"], tgt_year_id,
                                          target_class_id, status="aktif")
                enrolled += 1
            except Exception:
                current_app.logger.warning("enrollment record failed for %s", s["id"])
        log_activity("promote", "class", source_class_id, new_data={"target_class_id": target_class_id, "moved": moved, "enrolled": enrolled, "outcome": outcome, "school_year_id": request.form.get("school_year_id")}, user_id=g.user_id)
        flash(f"{moved} murid berhasil dipindahkan ke kelas tujuan", "success")
        return redirect("/admin-sekolah/promote")

    classes_list = (supabase.table("classes")
                    .select("*, school_years!left(name), profiles!classes_teacher_id_fkey(full_name)")
                    .eq("school_id", sid).order("name").execute().data or [])
    for c in classes_list:
        try:
            sc = supabase.table("profiles").select("id", count="exact").eq("role", "murid").eq("school_id", sid).eq("class_id", c["id"]).execute()
            c["student_count"] = sc.count or 0
        except Exception:
            c["student_count"] = 0
        c["school_year_name"] = (c.get("school_years") or {}).get("name", "")
        c["wali_kelas"] = (c.get("profiles") or {}).get("full_name", "")
    teachers = supabase.table("profiles").select("id, full_name").eq("role", "guru").eq("school_id", sid).execute().data or []
    years = supabase.table("school_years").select("*").eq("school_id", sid).order("name", desc=True).execute().data or []
    return render_template("admin_sekolah/promote.html", classes=classes_list, teachers=teachers, years=years)


# ─── TEACHERS CRUD ───────────────────────────────────

@admin_sekolah_bp.route("/teachers")
@admin_sekolah_required
def teachers():
    sid = _school_id()
    supabase = get_supabase()
    q = request.args.get("q", "").strip()
    subject_id = request.args.get("subject_id", "").strip()
    sort = request.args.get("sort", "employee_id")
    direction = _sort_dir(request.args.get("dir"))
    per_page = _per_page_arg(request.args.get("per_page", "50"))
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    subjects = supabase.table("subjects").select("*").eq("school_id", sid).order("name").execute().data or []
    # The create/edit forms pair subjects with classes, so the roster needs the
    # class list too — read once here, not per row.
    classes = (supabase.table("classes").select("id, name, grade_level")
               .eq("school_id", sid).order("name").execute().data or [])

    data_q = supabase.table("teachers").select(
        "*, profiles!inner(id, full_name, phone), subjects(name)"
    ).eq("school_id", sid)
    if q:
        data_q = data_q.ilike("employee_id", f"%{q}%")
    if subject_id:
        data_q = data_q.eq("subject_id", subject_id)
    teachers_raw = data_q.execute().data or []

    # Fetch auth emails once for all users. Only the rows on this page need an
    # address, but the map is cached and one read is cheaper than a page-worth of
    # per-user calls.
    _email_map = {}
    try:
        _email_map = _get_email_map(supabase)
    except Exception:
        pass

    teachers_list = []
    for t in teachers_raw:
        prof = t.get("profiles") or {}
        subj = t.get("subjects") or {}
        uid = t["id"]
        teachers_list.append({
            "id": uid,
            "name": prof.get("full_name", "-") or "-",
            "employee_number": t.get("employee_id", "") or "",
            "email": _email_map.get(uid, ""),
            "subject_name": subj.get("name", "-") or "-",
            "subject_id": t.get("subject_id"),
            "phone": prof.get("phone", ""),
            "employee_id": t.get("employee_id", "") or "",
        })

    page_rows, total, total_pages, page = _apply_sort_page(
        teachers_list, sort=sort, direction=direction, page=page, per_page=per_page,
        keys={
            "employee_id": lambda r: r["employee_number"].lower(),
            "name": lambda r: r["name"].lower(),
            "subject": lambda r: r["subject_name"].lower(),
        })

    base_qs = _filter_qs(q=q, subject_id=subject_id, sort=sort, dir=direction,
                         per_page=("all" if not per_page else per_page))
    # `pending_activation` rides along so THIS page answers "who has not activated
    # yet" — the question the admin is already asking while looking at the roster.
    # Until now it was only on `/accounts`, so the answer meant leaving the list of
    # people it is about. It is `None` when migration 040 has not run, which the
    # template shows as "cannot be read", never as zero.
    #
    # The assignment counts are read once for the whole school, not once per row:
    # the matrix itself is a modal, but the roster must be able to say "3 kelas, 2
    # mapel" without the admin opening 50 modals to find out.
    year = ta_service.active_school_year(supabase, sid)
    year_name = (year or {}).get("name")
    try:
        assign_detail = ta_service.school_pairs_detail(supabase, sid, year_name)
    except Exception:
        assign_detail = {}
    subject_names = {str(s["id"]): s.get("name") or "-" for s in subjects}
    for t in page_rows:
        c = assign_detail.get(str(t["id"]),
                              {"classes": 0, "subjects": 0, "subject_ids": [], "pairs": []})
        t["assign_classes"] = c["classes"]
        t["assign_subjects"] = c["subjects"]
        # The names, so a teacher with two subjects shows two — not just the one
        # legacy `teachers.subject_id`. Falls back to that column when the matrix
        # has no row for them (older data, a school that never opened the matrix).
        names = [subject_names.get(sid_) for sid_ in c["subject_ids"]]
        t["assign_subject_names"] = [n for n in names if n]
        if not t["assign_subject_names"] and t.get("subject_name"):
            t["assign_subject_names"] = [t["subject_name"]]
        # Pre-tick keys for the edit form: `class|subject`, the same pair key the
        # service stores, so the form opens on exactly the stored selection.
        t["assign_pairs"] = set(c["pairs"])
    return render_template("admin_sekolah/teachers.html", teachers=page_rows, subjects=subjects,
                           classes=classes,
                           q=q, subject_id=subject_id, sort=sort, dir=direction,
                           page=page, total=total, total_pages=total_pages, per_page=per_page,
                           base_qs=base_qs, page_sizes=_PAGE_SIZES,
                           pending_activation=_pending_activation(supabase, sid),
                           teacher_ids=[t["id"] for t in page_rows],
                           active_year=year)


@admin_sekolah_bp.route("/teachers/<teacher_id>/assignments", methods=["GET", "POST"])
@admin_sekolah_required
def teacher_assignments(teacher_id):
    """The many-to-many matrix for one teacher, read and written in one place.

    A GET answers the modal: the school's classes and subjects, the pairs this
    teacher holds for the active year, and the year's own name so the screen can
    say which year is being edited. A POST is the *whole selection*, not a delta —
    the modal owns the matrix, so sending only what changed would make two open
    tabs silently disagree. The diff is computed server-side (see
    `teacher_assignments.save`).

    Every id is checked against the caller's school: a manipulated `class_id` from
    another school is refused with 403, never quietly dropped, because a silent
    drop is indistinguishable from success to the admin who tried it.
    """
    sid = _school_id()
    supabase = get_supabase()

    # The teacher must be one of this school's — a 404 for anyone else's id, not a
    # matrix that would let the admin read a rival school's class list.
    if not ta_service.teacher_in_school(supabase, sid, teacher_id):
        return jsonify({"error": "Guru tidak ditemukan di sekolah ini"}), 404

    year = ta_service.active_school_year(supabase, sid)
    year_name = (year or {}).get("name")

    if request.method == "GET":
        classes = (supabase.table("classes").select("id, name, grade_level")
                   .eq("school_id", sid).order("name").execute().data or [])
        subjects = (supabase.table("subjects").select("id, name, code")
                    .eq("school_id", sid).order("name").execute().data or [])
        pairs = sorted(ta_service.current_pairs(supabase, sid, teacher_id, year_name))
        return jsonify({
            "year": year,
            "classes": classes,
            "subjects": subjects,
            "pairs": [{"class_id": c, "subject_id": s} for c, s in pairs],
        })

    payload = request.get_json(silent=True) or {}
    raw = payload.get("pairs") or []
    pairs = [(p.get("class_id"), p.get("subject_id"))
             for p in raw if isinstance(p, dict) and p.get("class_id") and p.get("subject_id")]
    confirm = bool(payload.get("confirm_remove"))

    ok, result = ta_service.save(supabase, sid, teacher_id, pairs,
                                 year_name=year_name, confirm_remove=confirm)
    if not ok:
        return jsonify(result), result.get("status", 400)

    # Both caches the door is read through: the teacher's own list and the
    # school's, so the exam builder's dropdown reflects the new matrix at once.
    invalidate_teacher_assignments(teacher_id, sid)
    invalidate_school(sid)
    added = result.get("added_count", 0)
    removed = result.get("removed_count", 0)
    if added or removed:
        log_activity("update", "teacher_assignment", teacher_id,
                     new_data={"added": result.get("added", []),
                               "removed": result.get("removed", []),
                               "school_year": year_name},
                     user_id=g.user_id)
    return jsonify({"success": True, "added": added, "removed": removed,
                    "pairs": [{"class_id": c, "subject_id": s} for c, s in pairs]})


@admin_sekolah_bp.route("/teachers/create", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def create_teacher():
    sid = _school_id()
    supabase = get_supabase()
    nip = request.form.get("employee_number", request.form.get("employee_id", "")).strip()
    nama = request.form.get("name", request.form.get("full_name", "")).strip()
    email = request.form.get("email", "").strip().lower()
    hp = request.form.get("phone", "").strip()
    recovery_email = request.form.get("recovery_email", "").strip().lower()
    # The assignment section carries one `assign_<subject_id>` field per subject,
    # each holding the classes that subject is taught in. It is the same write the
    # matrix makes, so a teacher created here and one edited there are identical.
    pairs = ta_service.pairs_from_form(request.form)
    # The legacy single column keeps the first chosen subject, so the older
    # `subjects(name)` join and the subject filter still answer something.
    subject_id = request.form.get("subject_id") or (pairs[0][1] if pairs else None)
    password = request.form.get("password", "").strip() or _gen_password()

    if not nama:
        flash("Nama guru wajib diisi", "error")
        return redirect("/admin-sekolah/teachers")

    try:
        user_email = email or _generate_email(nama, _get_email_domain(sid))
        # One call, because the order matters: auth -> profiles -> teachers. The
        # old code wrote a `status` column to `teachers`, which does not exist,
        # so it created the account and then failed every time.
        uid = create_teacher_account(
            supabase, school_id=sid, full_name=nama, email=user_email,
            password=password, employee_id=nip, subject_id=subject_id,
            phone=recovery_email or hp,
        )
        if pairs:
            year = ta_service.active_school_year(supabase, sid)
            ok, result = ta_service.save(supabase, sid, uid, pairs,
                                         year_name=(year or {}).get("name"))
            if not ok:
                flash(f"Guru dibuat, tetapi penugasan gagal: {result.get('reason')}",
                      "warning")
            invalidate_teacher_assignments(uid, sid)
            invalidate_school(sid)
        log_activity("create", "teacher", uid, new_data={"full_name": nama, "employee_id": nip}, user_id=g.user_id)
        flash(f"Guru berhasil ditambahkan. Email: {user_email}, Password: {password}", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/teachers")


@admin_sekolah_bp.route("/teachers/<teacher_id>/edit", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("teachers", "teacher_id")
def edit_teacher(teacher_id):
    supabase = get_supabase()
    sid = _school_id()
    data = {}
    emp_id = request.form.get("employee_number", request.form.get("employee_id", ""))
    if emp_id:
        data["employee_id"] = emp_id.strip()
    # `assign_present` separates "the admin unchecked every box" from "another
    # caller posted only a name": without it, an empty selection is
    # indistinguishable from no selection, and the first would silently wipe the
    # teacher's assignment on any unrelated edit.
    assignment_submitted = bool(request.form.get("assignment_present"))
    pairs = ta_service.pairs_from_form(request.form) if assignment_submitted else None
    # The legacy single column follows the first chosen subject — but only when
    # this edit actually carried an assignment section. Writing `subject_id=None`
    # on every unrelated edit would erase the one piece of assignment the older
    # join still reads, which is the opposite of an unrelated edit's job.
    if assignment_submitted:
        data["subject_id"] = pairs[0][1] if pairs else None
    elif "subject_id" in request.form:
        data["subject_id"] = request.form.get("subject_id") or None

    profile_data = {}
    nama = request.form.get("name", request.form.get("full_name", ""))
    if nama:
        profile_data["full_name"] = nama.strip()
    hp = request.form.get("phone", "")
    if hp:
        profile_data["phone"] = hp.strip()
    # Email is stored in auth.users, not profiles table — cannot update via this API

    try:
        if data:
            supabase.table("teachers").update(data).eq("id", teacher_id).execute()
        if profile_data:
            supabase.table("profiles").update(profile_data).eq("id", teacher_id).execute()
        if assignment_submitted:
            year = ta_service.active_school_year(supabase, sid)
            ok, result = ta_service.save(supabase, sid, teacher_id, pairs,
                                         year_name=(year or {}).get("name"),
                                         confirm_remove=True)
            if not ok:
                flash(f"Penugasan tidak tersimpan: {result.get('reason')}", "warning")
            else:
                invalidate_teacher_assignments(teacher_id, sid)
                invalidate_school(sid)
        log_activity("update", "teacher", teacher_id, new_data={**data, **profile_data}, user_id=g.user_id)
        flash("Guru berhasil diperbarui", "success")
        return redirect("/admin-sekolah/teachers")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/teachers")


@admin_sekolah_bp.route("/teachers/<teacher_id>/delete", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("teachers", "teacher_id")
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
@require_school_access("teachers", "teacher_id")
def reset_teacher_password(teacher_id):
    supabase = get_supabase()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        supabase.auth.admin.update_user_by_id(teacher_id, {"password": password})
        log_activity("reset_password", "teacher", teacher_id, user_id=g.user_id)
        return jsonify({"success": True, "password": password})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── PEJABAT SEKOLAH (kepala sekolah & wakil kepala sekolah) ──────
#
# Dua akun yang *membaca* sekolah dan tidak mengubahnya. Sekolah yang membuatnya
# sendiri — sama seperti guru dan murid — karena yang tahu siapa kepala sekolahnya
# adalah sekolahnya, bukan super admin.

@admin_sekolah_bp.route("/officials")
@admin_sekolah_required
def officials():
    """The two official accounts, as a page the school admin owns."""
    sid = _school_id()
    supabase = get_supabase()
    rows = officials_service.list_officials(supabase, sid)
    try:
        email_map = _get_email_map(supabase)
    except Exception:
        email_map = {}
    listed = [{**row, "email": email_map.get(row["id"], "")} for row in rows]
    school = {}
    try:
        school = (supabase.table("schools").select("name, npsn")
                  .eq("id", sid).single().execute().data) or {}
    except Exception:
        pass
    return render_template("admin_sekolah/officials.html", officials=listed,
                           school=school)


@admin_sekolah_bp.route("/officials/create", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def create_official():
    """Create one official from the school admin's form.

    The role is read from a *closed* list, not from free text: `validate_role`
    refuses anything but the two official roles, so a form that posted
    `admin_sekolah` (or a school admin's own role) cannot mint a second admin.
    """
    sid = _school_id()
    role = request.form.get("role", "").strip()
    nama = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    hp = request.form.get("phone", "").strip()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        officials_service.validate_role(role)
        user_email = email or _generate_email(nama, _get_email_domain(sid))
        uid = officials_service.create_official(
            get_supabase(), school_id=sid, role=role, full_name=nama,
            email=user_email, password=password, phone=hp)
        log_activity("create", role, uid,
                     new_data={"full_name": nama, "email": user_email},
                     user_id=g.user_id)
        # The password is flashed once, because the school admin is the only one
        # who can see it: nothing else in the app can hand it out afterwards.
        flash(f"Akun berhasil dibuat. Email: {user_email}, Password: {password}",
              "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/officials")


@admin_sekolah_bp.route("/officials/<official_id>/edit", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("profiles", "official_id")
def edit_official(official_id):
    """Rename one of this school's officials (never their role)."""
    try:
        officials_service.update_official(
            get_supabase(), official_id, _school_id(),
            full_name=request.form.get("name"), phone=request.form.get("phone"))
        log_activity("update", "official", official_id, user_id=g.user_id)
        flash("Akun pejabat sekolah berhasil diperbarui", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/officials")


@admin_sekolah_bp.route("/officials/<official_id>/delete", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("profiles", "official_id")
def delete_official(official_id):
    """Remove the account — the profile first, then the auth user."""
    try:
        officials_service.delete_official(get_supabase(), official_id, _school_id())
        log_activity("delete", "official", official_id, user_id=g.user_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": getattr(e, "user_message", str(e))}), 400


@admin_sekolah_bp.route("/officials/<official_id>/reset-password", methods=["POST"])
@admin_sekolah_required
@require_school_access("profiles", "official_id")
def reset_official_password(official_id):
    supabase = get_supabase()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        supabase.auth.admin.update_user_by_id(official_id, {"password": password})
        log_activity("reset_password", "official", official_id, user_id=g.user_id)
        return jsonify({"success": True, "password": password})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── STUDENTS CRUD ───────────────────────────────────

@admin_sekolah_bp.route("/students")
@admin_sekolah_required
def students():
    sid = _school_id()
    supabase = get_supabase()
    q = request.args.get("q", "").strip()
    class_id = request.args.get("class_id", "").strip()
    sort = request.args.get("sort", "class")
    direction = _sort_dir(request.args.get("dir", "asc"))
    per_page = _per_page_arg(request.args.get("per_page", "50"))
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    classes_list = supabase.table("classes").select("*").eq("school_id", sid).order("name").execute().data or []

    data_q = supabase.table("students").select(
        "*, profiles!inner(id, full_name, phone), classes(name)"
    ).eq("school_id", sid)
    if q:
        data_q = data_q.ilike("nisn", f"%{q}%")
    if class_id:
        data_q = data_q.eq("class_id", class_id)
    students_raw = data_q.execute().data or []

    _email_map = {}
    try:
        _email_map = _get_email_map(supabase)
    except Exception:
        pass

    students_list = []
    for s in students_raw:
        prof = s.get("profiles") or {}
        cls = s.get("classes") or {}
        uid = s["id"]
        students_list.append({
            "id": uid,
            "nisn": s.get("nisn", "") or prof.get("nisn", "") or "",
            "name": prof.get("full_name", "-") or "-",
            "class_name": cls.get("name", "-") or "-",
            "class_id": s.get("class_id"),
            "phone": prof.get("phone", ""),
            "email": _email_map.get(uid, ""),
        })

    # Default is the class order, ascending: a school's roster reads class by class,
    # and this is the same order the downloadable login-card sheet uses (see
    # app/services/login_cards.py), so the list and the file agree.
    page_rows, total, total_pages, page = _apply_sort_page(
        students_list, sort=sort, direction=direction, page=page, per_page=per_page,
        keys={
            "class": lambda r: (r["class_name"].lower(), r["name"].lower()),
            "name": lambda r: r["name"].lower(),
            "nisn": lambda r: r["nisn"],
        })

    base_qs = _filter_qs(q=q, class_id=class_id, sort=sort, dir=direction,
                         per_page=("all" if not per_page else per_page))
    # Same as `/teachers`: the "who has not activated" count belongs on the page
    # that lists the people, not two clicks away on `/accounts`.
    return render_template("admin_sekolah/students.html", students=page_rows, classes=classes_list,
                           q=q, class_id=class_id, sort=sort, dir=direction,
                           page=page, total=total, total_pages=total_pages, per_page=per_page,
                           pending_activation=_pending_activation(supabase, sid),
                           base_qs=base_qs, page_sizes=_PAGE_SIZES)


@admin_sekolah_bp.route("/students/create", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_subscription("add_student")
def create_student():
    sid = _school_id()
    supabase = get_supabase()
    nisn = request.form.get("nisn", "").strip()
    nama = request.form.get("name", request.form.get("full_name", "")).strip()
    email = request.form.get("email", "").strip().lower()
    recovery_email = request.form.get("phone", "").strip().lower()
    class_id = request.form.get("class_id") or None
    password = request.form.get("password", "").strip() or _gen_password()

    if not nama:
        flash("Nama murid wajib diisi", "error")
        return redirect("/admin-sekolah/students")

    try:
        user_email = email or _generate_email(nama, _get_email_domain(sid))
        uid = create_student_account(
            supabase, school_id=sid, nisn=nisn, full_name=nama,
            email=user_email, password=password, class_id=class_id,
            phone=recovery_email,
        )
        log_activity("create", "student", uid, new_data={"full_name": nama, "nisn": nisn, "class_id": class_id}, user_id=g.user_id)
        flash(f"Murid berhasil ditambahkan. Email: {user_email}, Password: {password}", "success")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
    return redirect("/admin-sekolah/students")


@admin_sekolah_bp.route("/students/<student_id>/edit", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("students", "student_id")
def edit_student(student_id):
    supabase = get_supabase()
    data = {}
    nisn = request.form.get("nisn", "")
    if nisn:
        data["nisn"] = nisn.strip()
    cls = request.form.get("class_id")
    data["class_id"] = cls if cls else None

    profile_data = {}
    nama = request.form.get("name", request.form.get("full_name", ""))
    if nama:
        profile_data["full_name"] = nama.strip()

    try:
        if data:
            supabase.table("students").update(data).eq("id", student_id).execute()
        if profile_data:
            supabase.table("profiles").update(profile_data).eq("id", student_id).execute()
        log_activity("update", "student", student_id, new_data={**data, **profile_data}, user_id=g.user_id)
        flash("Murid berhasil diperbarui", "success")
        return redirect("/admin-sekolah/students")
    except Exception as e:
        flash(f"Gagal: {failure.sentence(e)}", "error")
        return redirect("/admin-sekolah/students")


@admin_sekolah_bp.route("/teachers/bulk-reset-password", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def bulk_reset_teachers_password():
    sid = _school_id()
    supabase = get_supabase()
    data = request.get_json() if request.is_json else request.form
    user_ids = data.get("user_ids", [])
    if isinstance(user_ids, str):
        user_ids = json.loads(user_ids)
    if not user_ids:
        return jsonify({"error": "Tidak ada user dipilih"}), 400
    results = []
    for uid in user_ids:
        try:
            prof = supabase.table("profiles").select("school_id").eq("id", uid).single().execute().data or {}
            if prof.get("school_id") != sid:
                results.append({"id": uid, "error": "Not in school"})
                continue
            new_pw = _gen_password()
            supabase.auth.admin.update_user_by_id(uid, {"password": new_pw})
            log_activity("reset_password", "user", uid, user_id=g.user_id)
            results.append({"id": uid, "password": new_pw, "success": True})
        except Exception as e:
            results.append({"id": uid, "error": str(e)})
    return jsonify({"results": results, "total": len(results)})


@admin_sekolah_bp.route("/teachers/bulk-delete", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def bulk_delete_teachers():
    sid = _school_id()
    supabase = get_supabase()
    data = request.get_json() if request.is_json else request.form
    user_ids = data.get("user_ids", [])
    if isinstance(user_ids, str):
        user_ids = json.loads(user_ids)
    if not user_ids:
        return jsonify({"error": "Tidak ada user dipilih"}), 400
    results = []
    for uid in user_ids:
        try:
            prof = supabase.table("profiles").select("school_id").eq("id", uid).single().execute().data or {}
            if prof.get("school_id") != sid:
                continue
            supabase.table("teachers").delete().eq("id", uid).execute()
            supabase.table("profiles").delete().eq("id", uid).execute()
            supabase.auth.admin.delete_user(uid)
            results.append({"id": uid, "success": True})
        except:
            pass
    return jsonify({"results": results, "total": len(results)})


@admin_sekolah_bp.route("/students/bulk-reset-password", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def bulk_reset_students_password():
    sid = _school_id()
    supabase = get_supabase()
    data = request.get_json() if request.is_json else request.form
    user_ids = data.get("user_ids", [])
    if isinstance(user_ids, str):
        user_ids = json.loads(user_ids)
    if not user_ids:
        return jsonify({"error": "Tidak ada user dipilih"}), 400
    results = []
    for uid in user_ids:
        try:
            # Whose row is it? The page only ever offers this school's pupils, and a
            # list drawn by a page is not a guard: these ids arrive in the request
            # body. The single-id twin is safe because it carries
            # `@require_school_access("students", "student_id")`; a bulk route has no
            # id in the path for that decorator to read, so the check is per id,
            # here — the same one the teacher reset already makes.
            prof = supabase.table("profiles").select("school_id").eq("id", uid).single().execute().data or {}
            if prof.get("school_id") != sid:
                results.append({"id": uid, "error": "Not in school"})
                continue
            new_pw = _gen_password()
            supabase.auth.admin.update_user_by_id(uid, {"password": new_pw})
            log_activity("reset_password", "user", uid, user_id=g.user_id)
            results.append({"id": uid, "password": new_pw, "success": True})
        except Exception as e:
            results.append({"id": uid, "error": str(e)})
    return jsonify({"results": results, "total": len(results)})


# ─── LOGIN CARDS ─────────────────────────────────────
#
# The download half of "reset the password": the two bulk-reset routes below do
# return a new password per account, and their pages print a count and drop the rest,
# so the one thing a school needs — a sheet it can hand out — was the one thing it
# could not get. See `app/services/login_cards.py` for why a card has to be issued
# rather than exported.
#
# A plain form POST, not `fetch`: the reply *is* the file, so the browser downloads
# it and stays on the page without any blob plumbing, and `base.html` already injects
# the CSRF field into every `form[method="POST"]`.

def _login_cards(kind: str):
    """Issue cards for the posted ids and answer with the file. Shared by both pages."""
    sid = _school_id()
    supabase = get_supabase()

    raw = request.form.get("user_ids")
    if raw is None and request.is_json:
        raw = (request.get_json(silent=True) or {}).get("user_ids")
    if isinstance(raw, str):
        try:
            user_ids = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            user_ids = []
    elif isinstance(raw, list):
        user_ids = raw
    else:
        user_ids = []

    back = {"teacher": "/admin-sekolah/teachers",
            "official": "/admin-sekolah/officials"}.get(kind, "/admin-sekolah/students")
    if not user_ids:
        flash("Pilih dulu akun yang kartu loginnya ingin diunduh.", "warning")
        return redirect(back)

    emails = {}
    try:
        emails = _get_email_map(supabase)
    except Exception:
        pass

    # Scope first, write second — in that order, and in two calls rather than one,
    # because a password that is set and then not handed over does not exist anywhere
    # (the sheet was never produced, and the account's old one is gone). A request
    # naming an account from another school is refused before anything is written.
    found = login_cards.collect(supabase, sid, user_ids, kind, emails=emails)
    if found["missing"]:
        # Refused, not skipped: a sheet quietly short of three pupils is one the
        # school hands out and then has to answer for.
        flash(f"{len(found['missing'])} akun bukan milik sekolah ini — tidak ada yang "
              f"diunduh, dan belum ada password yang diubah.", "error")
        return redirect(back)
    result = {"rows": login_cards.set_passwords(supabase, found["rows"]),
              "missing": []}

    issued = sum(1 for row in result["rows"] if row["password"])
    failed = sum(1 for row in result["rows"] if row["error"])
    school_name = ""
    try:
        row = row_or_none(supabase.table("schools").select("name").eq("id", sid)
                          .maybe_single().execute())
        school_name = (row or {}).get("name", "") or ""
    except Exception:
        pass

    meta = login_cards.meta_for(school_name, len(result["rows"]), issued, failed)
    payload, mimetype, name = login_cards.render(
        result["rows"], kind, meta, request.form.get("fmt", "xlsx"))
    # An export of credentials is exactly the kind of access the retention policy
    # wants a record of — and the per-account reset is recorded by the same reason.
    log_activity("export", "login_cards", kind,
                 new_data={"requested": len(result["rows"]), "issued": issued,
                           "failed": failed},
                 user_id=g.user_id)
    return send_file(io.BytesIO(payload), as_attachment=True,
                     download_name=name, mimetype=mimetype)


@admin_sekolah_bp.route("/students/login-cards", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def student_login_cards():
    return _login_cards("student")


@admin_sekolah_bp.route("/teachers/login-cards", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def teacher_login_cards():
    return _login_cards("teacher")


@admin_sekolah_bp.route("/officials/login-cards", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def official_login_cards():
    """The same sheet as pupils and teachers get, for the head and deputy head.

    One route for both official roles rather than one each: they are one list on the
    page, one page of cards in the school's hands, and nothing about the sheet differs
    between them — the role is a column, and `login_cards.collect` refuses any id that
    is not one of this school's two official roles.
    """
    return _login_cards("official")


# ─── EMAIL AKUN & AKTIVASI ─────────────────────────────────────────────
#
# Dua hal yang tidak bisa didapat sekolah dari mana pun: memperbaiki email akun
# secara massal (emailnya dibuat dari nama saat impor, dan ia yang menerima kode
# reset), dan melihat berapa akun yang masih memakai password dari kartu login.
# Yang pertama menulis, jadi ia dijaga `subscription_write_required` seperti setiap
# tulis lain di halaman ini.

def _pending_activation(supabase, sid) -> dict | None:
    """``{role: count}`` akun yang masih memakai password terbitan, atau ``None``.

    ``None`` dan bukan ``{}`` ketika kolomnya belum ada (basis data yang belum
    menjalankan migrasi 040): nol berarti "semua akun sudah memakai password
    sendiri", dan itu klaim yang berbeda dari "tidak bisa dibaca".
    """
    counts = {"murid": 0, "guru": 0, "principal": 0, "vice_principal": 0}
    try:
        rows = (supabase.table("profiles")
                .select("role").eq("school_id", sid)
                .eq("must_change_password", True).execute().data) or []
    except Exception:
        return None
    for row in rows:
        role = row.get("role")
        if role in counts:
            counts[role] += 1
    return counts


@admin_sekolah_bp.route("/accounts")
@admin_sekolah_required
def accounts():
    """Halaman email & aktivasi: satu unggahan, satu unduhan, satu hitungan."""
    supabase = get_supabase()
    sid = _school_id()
    return render_template("admin_sekolah/accounts.html",
                           pending=_pending_activation(supabase, sid),
                           report=None, school=_school_row(supabase, sid))


@admin_sekolah_bp.route("/emails/template")
@admin_sekolah_required
def email_template():
    """Template unggahan email — kolomnya sama dengan yang dibaca `read_rows`."""
    school = _school_row(get_supabase(), _school_id())
    payload = account_emails.template_bytes((school or {}).get("name", ""))
    return send_file(io.BytesIO(payload), as_attachment=True,
                     download_name="template-email-akun.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@admin_sekolah_bp.route("/emails/upload", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def upload_emails():
    """Pasang atau perbaiki email akun yang **sudah ada**, berlingkup sekolah ini.

    Membaca, merencanakan, lalu menulis — tiga langkah terpisah, bukan satu loop:
    baris yang salah harus ketahuan sebelum alamat pertama ditulis, supaya satu
    lembar dengan dua murid di satu alamat email tidak setengah-terpasang.
    """
    supabase = get_supabase()
    sid = _school_id()
    file = request.files.get("file")

    def back(report=None, error=None):
        if error:
            flash(error, "error")
        return render_template("admin_sekolah/accounts.html",
                               pending=_pending_activation(supabase, sid),
                               report=report, school=_school_row(supabase, sid))

    if not file or not (file.filename or "").strip():
        return back(error="Pilih berkasnya dulu.")

    try:
        rows = account_emails.read_rows(file.stream, file.filename)
    except Exception as exc:
        return back(error=f"Berkas tidak bisa dibaca: {str(exc)[:80]}")
    if not rows:
        return back(error="Tidak ada baris yang bisa dibaca di berkas itu.")

    planned = account_emails.apply(account_emails.plan(supabase, sid, rows), supabase)
    counts = account_emails.summarise(planned)
    # Mengubah email akun mengubah tempat kode reset dikirim — persis bentuk akses
    # yang kebijakan retensi ingin tercatat, seperti unduhan kartu login.
    log_activity("update", "account_email", str(sid),
                 new_data={"updated": counts["updated"], "errors": counts["errors"],
                           "rows": counts["total"]}, user_id=g.user_id)
    return back(report={"rows": planned, **counts})


@admin_sekolah_bp.route("/students/bulk-delete", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
def bulk_delete_students():
    sid = _school_id()
    supabase = get_supabase()
    data = request.get_json() if request.is_json else request.form
    user_ids = data.get("user_ids", [])
    if isinstance(user_ids, str):
        user_ids = json.loads(user_ids)
    if not user_ids:
        return jsonify({"error": "Tidak ada user dipilih"}), 400
    results = []
    for uid in user_ids:
        try:
            # The same per-id check the teacher delete makes, and for the same
            # reason: without it the list in the request body decides whose
            # students row, profiles row and auth user are destroyed. Skipped ids
            # are left out of `results`, exactly as the teacher route leaves them.
            prof = supabase.table("profiles").select("school_id").eq("id", uid).single().execute().data or {}
            if prof.get("school_id") != sid:
                continue
            supabase.table("students").delete().eq("id", uid).execute()
            supabase.table("profiles").delete().eq("id", uid).execute()
            supabase.auth.admin.delete_user(uid)
            results.append({"id": uid, "success": True})
        except:
            pass
    return jsonify({"results": results, "total": len(results)})


@admin_sekolah_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@admin_sekolah_required
@require_school_access("profiles", "user_id")
def admin_reset_user_password(user_id):
    """Admin sekolah reset password untuk guru/murid di sekolahnya."""
    sid = _school_id()
    supabase = get_supabase()
    try:
        # Verify user belongs to this school
        prof = supabase.table("profiles").select("school_id, role").eq("id", user_id).single().execute().data or {}
        if prof.get("school_id") != sid:
            return jsonify({"error": "User tidak berada di sekolah Anda"}), 403
        new_pw = _gen_password()
        supabase.auth.admin.update_user_by_id(user_id, {"password": new_pw})
        log_activity("reset_password", "user", user_id, user_id=g.user_id)
        return jsonify({"success": True, "password": new_pw})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sekolah_bp.route("/students/<student_id>/delete", methods=["POST"])
@subscription_write_required
@admin_sekolah_required
@require_school_access("students", "student_id")
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
@subscription_write_required
@admin_sekolah_required
@require_school_access("students", "student_id")
def reset_student_password(student_id):
    supabase = get_supabase()
    password = request.form.get("password", "").strip() or _gen_password()
    try:
        supabase.auth.admin.update_user_by_id(student_id, {"password": password})
        log_activity("reset_password", "student", student_id, user_id=g.user_id)
        return jsonify({"success": True, "password": password})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── Langganan / Subscription ─────────────────────────────────────────

@admin_sekolah_bp.route("/subscription")
@admin_sekolah_required
def subscription():
    supabase = get_supabase()
    from app.services.midtrans_service import get_school_subscription

    school_id = g.user_school_id
    sub = get_school_subscription(school_id) if school_id else None

    plans = []
    try:
        plans = supabase.table("subscription_plans").select("*").eq("is_active", True).neq("duration_days", 0).order("sort_order").limit(10).execute().data or []
    except Exception:
        pass

    transactions = []
    try:
        transactions = supabase.table("payment_transactions").select("*, subscription_plans!left(name, duration_label)").eq("school_id", school_id).order("created_at", desc=True).limit(10).execute().data or []
    except Exception:
        pass

    # The same read the grant sites make, so the sentence this page prints is the
    # length a new school in this school's position would actually receive.
    trial_days = trial_cfg.get_trial_days(supabase)

    from app.services.midtrans_service import get_pricing_config, calculate_plan_price, get_student_count_for_school, get_payment_fee_config
    pricing_config = get_pricing_config()
    student_count = get_student_count_for_school(school_id) if school_id else 0
    scaled_active = pricing_config.get("model") == "scaled"
    scaled_tiers = pricing_config.get("tiers", [])
    fee_config = get_payment_fee_config()

    return render_template("admin_sekolah/subscription.html",
        sub=sub, plans=plans, transactions=transactions, trial_days=trial_days,
        pricing_config=pricing_config, student_count=student_count,
        scaled_active=scaled_active, scaled_tiers=scaled_tiers,
        fee_config=fee_config)


@admin_sekolah_bp.route("/subscription/subscribe", methods=["POST"])
@admin_sekolah_required
def subscribe():
    supabase = get_supabase()
    plan_id = request.form.get("plan_id", type=int)
    if not plan_id:
        flash("Pilih plan terlebih dahulu", "error")
        return redirect("/admin-sekolah/subscription")

    school_id = g.user_school_id
    if not school_id:
        flash("Sekolah tidak terdaftar", "error")
        return redirect("/admin-sekolah/subscription")

    # Get school info
    school_name = ""
    admin_email = ""
    try:
        sch = supabase.table("schools").select("name").eq("id", school_id).single().execute()
        if sch.data:
            school_name = sch.data.get("name", "")
        # Email is in Auth, not profiles table
        user_info = supabase.auth.admin.get_user_by_id(g.user_id)
        if user_info and user_info.user:
            admin_email = user_info.user.email or ""
    except Exception:
        pass
    # Ensure email is valid for Midtrans
    if not admin_email or "@" not in admin_email:
        admin_email = "srphysics04@gmail.com"

    from app.services.midtrans_service import create_snap_transaction
    result, error = create_snap_transaction(school_id, plan_id, school_name, admin_email)

    if error:
        flash(error, "error")
        return redirect("/admin-sekolah/subscription")

    settings = {}
    try:
        res = supabase.table("midtrans_settings").select("*").limit(1).execute()
        if res.data:
            settings = res.data[0]
    except Exception:
        pass

    from app.services.midtrans_service import get_payment_fee_config, calculate_total_with_fee
    _total, fee_info = calculate_total_with_fee(result.get("base_amount", result["gross_amount"]))
    base_price = result.get("base_amount", result["gross_amount"])

    return render_template("admin_sekolah/payment.html",
        token=result["token"],
        redirect_url=result["redirect_url"],
        order_id=result["order_id"],
        gross_amount=result["gross_amount"],
        base_price=base_price,
        fee_info=fee_info,
        settings=settings,
    )


@admin_sekolah_bp.route("/payment/success")
@admin_sekolah_required
def payment_success():
    order_id = request.args.get("order_id", "")
    supabase = get_supabase()
    tx = None
    try:
        res = supabase.table("payment_transactions").select("*, subscription_plans!left(name)").eq("order_id", order_id).single().execute()
        tx = res.data
    except Exception:
        pass
    if not tx:
        flash("Transaksi tidak ditemukan", "error")
        return redirect("/admin-sekolah/subscription")
    return render_template("admin_sekolah/payment_success.html", tx=tx)


@admin_sekolah_bp.route("/payment/failure")
@admin_sekolah_required
def payment_failure():
    order_id = request.args.get("order_id", "")
    flash("Pembayaran gagal atau dibatalkan", "error")
    return redirect("/admin-sekolah/subscription")


@admin_sekolah_bp.route("/invoices")
@admin_sekolah_required
def invoices():
    supabase = get_supabase()
    sid = _school_id()
    invs = []
    school_info = {}
    if sid:
        invs = supabase.table("invoices").select("*, subscription_plans!left(name)").eq("school_id", sid).order("created_at", desc=True).execute().data or []
        for inv in invs:
            if isinstance(inv.get("created_at"), str):
                inv["created_at"] = inv["created_at"][:19].replace("T", " ")
            if isinstance(inv.get("paid_at"), str):
                inv["paid_at"] = inv["paid_at"][:19].replace("T", " ")
        try:
            sch = supabase.table("schools").select("name, address, npsn").eq("id", sid).single().execute()
            school_info = sch.data or {}
        except:
            pass
    return render_template("admin_sekolah/invoices.html", invoices=invs, school_info=school_info)


@admin_sekolah_bp.route("/invoices/<invoice_id>/download-pdf")
@admin_sekolah_required
@require_school_access("invoices", "invoice_id")
def download_invoice_pdf(invoice_id):
    supabase = get_supabase()
    sid = _school_id()
    inv = supabase.table("invoices").select("*, subscription_plans!left(name, duration_label)") \
        .eq("id", invoice_id).eq("school_id", sid).single().execute()
    if not inv.data:
        flash("Invoice tidak ditemukan", "error")
        return redirect("/admin-sekolah/invoices")
    inv = inv.data

    school = supabase.table("schools").select("name, address, npsn, city, province").eq("id", sid).single().execute().data or {}
    plan = inv.get("subscription_plans") or {}

    amount = int(inv.get("amount", 0))
    amount_fmt = f"Rp {amount:,}".replace(",", ".")

    period_start = (inv.get("period_start") or "")[:10] if inv.get("period_start") else "-"
    period_end = (inv.get("period_end") or "")[:10] if inv.get("period_end") else "Selamanya"
    paid_at = (str(inv.get("paid_at") or inv.get("created_at", "")))[:19] if inv.get("paid_at") or inv.get("created_at") else "-"
    address = school.get("address", "")
    city = school.get("city", "")
    province = school.get("province", "")
    full_addr = f"{address}, {city}, {province}".strip(", ")

    from xhtml2pdf import pisa
    import io
    from flask import make_response
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 2cm; }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; color: #1e293b; }}
.watermark {{ position: absolute; left: 15%; top: 38%; width: 70%; text-align: center; z-index: -1;
  font-size: 48pt; font-weight: 900; color: #e2e8f0; letter-spacing: 8px; white-space: nowrap; }}  
.invoice {{ max-width: 100%; }}
.header {{ text-align: center; padding-bottom: 10px; margin-bottom: 18px; }}
.header h1 {{ font-size: 22pt; color: #1e293b; margin: 0 0 2px; }}
.header .inv-num {{ font-size: 12pt; color: #64748b; font-weight: bold; letter-spacing: 0.5px; }}
.info-table {{ width: 100%; margin-bottom: 12px; }}
.info-table td {{ padding: 2px 4px; vertical-align: top; font-size: 9pt; }}
.info-table .label {{ color: #64748b; font-weight: bold; width: 100px; }}
.info-table .value {{ font-weight: bold; color: #1e293b; }}
.detail-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
.detail-table th {{ background: #2563eb; color: white; padding: 7px 10px; text-align: left; font-size: 9pt; font-weight: 800; }}
.detail-table td {{ padding: 7px 10px; font-size: 9pt; border: 1px solid #e2e8f0; }}
.total-row {{ text-align: right; font-size: 13pt; font-weight: 900; color: #2563eb; padding: 6px 0; }}
.footer {{ margin-top: 20px; padding-top: 8px; font-size: 7pt; color: #94a3b8; text-align: center; }}
.status-badge {{ display: inline-block; padding: 2px 10px; background: #059669; color: white; font-weight: bold; font-size: 8pt; border-radius: 3px; }}
</style></head><body>

<div class="watermark">ScanGrade</div>

<div class="invoice">
<div class="header">
<h1>INVOICE</h1>
<div class="inv-num">{inv.get('invoice_number', '-')}</div>
</div>

<table class="info-table">
<tr><td class="label">Kepada:</td><td class="value">{school.get('name', '-')}</td></tr>
<tr><td class="label">NPSN:</td><td class="value">{school.get('npsn', '-')}</td></tr>
<tr><td class="label">Alamat:</td><td class="value">{full_addr}</td></tr>
<tr><td class="label">Tanggal:</td><td class="value">{paid_at}</td></tr>
<tr><td class="label">Status:</td><td class="value"><span class="status-badge">LUNAS</span></td></tr>
</table>

<table class="detail-table">
<tr><th>Deskripsi</th><th>Periode</th><th>Jumlah</th></tr>
<tr>
<td>Langganan ScanGrade - {plan.get('name', '-')}</td>
<td>{period_start} s/d {period_end}</td>
<td style="text-align:right; font-weight:bold;">{amount_fmt}</td>
</tr>
</table>

<div class="total-row">Total: {amount_fmt}</div>

<table class="info-table">
<tr><td class="label">Pembayaran:</td><td class="value">{inv.get('payment_method', '-')}</td></tr>
<tr><td class="label">Kode Aktivasi:</td><td class="value" style="font-family:monospace;">{inv.get('activation_code', '-')}</td></tr>
<tr><td class="label">Invoice #:</td><td class="value" style="font-family:monospace;">{inv.get('invoice_number', '-')}</td></tr>
</table>

<div class="footer">
Invoice ini sah dan diterbitkan oleh ScanGrade. Data disimpan di database dan dapat diverifikasi kapan saja.<br>
Dicetak: {paid_at} &mdash; Terima kasih telah menggunakan ScanGrade.
</div>
</div></body></html>"""
    result = io.BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result)
    if pisa_status.err:
        flash("Gagal generate PDF", "error")
        return redirect("/admin-sekolah/invoices")
    resp = make_response(result.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="INVOICE-{inv.get("invoice_number", "unknown")}.pdf"'
    resp.headers['Content-Length'] = len(result.getvalue())
    return resp


@admin_sekolah_bp.route("/comms")
@admin_sekolah_required
def admin_comms():
    return render_template("shared/comms.html")


# ── the school's own reports ─────────────────────────────────────────────────
#
# The same two pages a teacher reads, at the school admin's own addresses. Shared
# rather than copied: `teacher/reports.html` and `teacher/analytics.html` are
# rendered with their base paths pointed at this blueprint, so the index's filter
# form, the CSV, the PDF and the print view all stay inside `/admin-sekolah/`.
# Two implementations of one report is how the school's numbers and the teacher's
# numbers start to differ, and the reader would have no way to tell which one to
# believe.
#
# The scope is the shared one: `analysis_scope.report` with the role
# `admin_sekolah` reads every exam of the reader's **own school** and nothing
# else. The school id comes from the session and never from the query string —
# the one rule that keeps a report page from reading another school's children.

#: The two addresses this school reads its reports at. One constant each, so the
#: sidebar, the index's own form and its three exports cannot name two areas.
ADMIN_REPORTS_BASE = "/admin-sekolah/reports"
ADMIN_ANALYTICS_BASE = "/admin-sekolah/analytics"

#: How long one school's report may be reused — the same five minutes the
#: teacher's page uses. The report is rebuilt from every exam in scope with its
#: item analysis, so it is worth caching; a quarter of an hour of staleness would
#: be worse than the cost the cache exists to avoid.
REPORT_TTL = 300


def _scope_report(supabase, lang, *, date_from=None, date_to=None,
                  school_filter=None, teacher_filter=None):
    """The school's report, cached per reader, school, language and range.

    The range is in the key for the same reason it is on the teacher's page: a
    report narrowed to one term is not the report for the year, and serving one
    as the other prints the wrong totals beside the right rows. The teacher
    choice is in it for the same reason — the index's list is narrowed by it, and
    a table narrowed while the totals still count the school is two sizes of one
    scope printed on one page.
    """
    key = (f"admin-analytics:{g.get('user_id')}:"
           f"{g.get('user_school_id') or '-'}:{lang}:"
           f"{date_from or ''}:{date_to or ''}:"
           f"{school_filter or ''}:{teacher_filter or ''}")
    cached = cache_get(key)
    if cached:
        return analysis_scope.from_payload(cached)
    data = analysis_scope.report(supabase, "admin_sekolah", g.get("user_id"),
                                 g.get("user_school_id"), lang=lang,
                                 date_from=date_from, date_to=date_to,
                                 school_filter=school_filter,
                                 teacher_filter=teacher_filter)
    try:
        cache_set(key, analysis_scope.as_payload(data), ttl=REPORT_TTL)
    except Exception:                                          # noqa: BLE001
        pass
    return data


def _kpis(data) -> dict:
    """The four headline numbers, from the report's own totals."""
    totals = data["totals"]
    return {"total_exams": totals["exams"],
            "total_submissions": totals["participants"],
            "avg_score": totals["mean"] or 0,
            "pass_rate": totals["pass_rate"] or 0,
            "std_dev": totals["sd"] or 0}


def _analytics_page(*, print_mode: bool = False):
    """The school's statistics, read-only, at the school's own address."""
    supabase = get_supabase()
    lang = analysis_scope.language(request.args.get("lang"))
    data = _scope_report(supabase, lang,
                         date_from=request.args.get("date_from") or None,
                         date_to=request.args.get("date_to") or None)
    return render_template(
        "teacher/analytics.html", report=data, stats=_kpis(data),
        dist_bins=data["bins"], exam_breakdown=data["rows"],
        exam_labels=[row["title"][:20] for row in data["rows"]],
        exam_avgs=[row["mean"] or 0 for row in data["rows"]],
        exam_medians=[row["median"] or 0 for row in data["rows"]],
        analysis_base=ADMIN_ANALYTICS_BASE,
        **({"print_mode": True} if print_mode else {}))


def _analytics_file(kind: str):
    """The school's report as the document a school files or hands on."""
    supabase = get_supabase()
    lang = analysis_scope.language(request.args.get("lang"))
    data = _scope_report(supabase, lang,
                         date_from=request.args.get("date_from") or None,
                         date_to=request.args.get("date_to") or None)
    if kind == "csv":
        payload = analysis_scope.report_csv(data).encode("utf-8-sig")
        return send_file(io.BytesIO(payload), mimetype="text/csv", as_attachment=True,
                         download_name=analysis_scope.filename(data, "csv"))
    pdf = analysis_scope.report_pdf(data)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=analysis_scope.filename(data, "pdf"))


def _reports_page():
    """The index of this school's own class and learner reports."""
    supabase = get_supabase()
    lang = analysis_scope.language(request.args.get("lang"))
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    school_filter = (request.args.get("school_id") or "").strip()
    teacher_filter = (request.args.get("teacher_id") or "").strip()
    choices = analysis_scope.scope_choices(
        supabase, "admin_sekolah", g.get("user_id"), g.get("user_school_id"),
        date_from=date_from, date_to=date_to)
    data = _scope_report(supabase, lang, date_from=date_from, date_to=date_to,
                         school_filter=school_filter,
                         teacher_filter=teacher_filter)
    learners = analysis_scope.learners_in_scope(supabase, data["rows"])
    return render_template(
        "teacher/reports.html", report=data, learners=learners,
        learner_keys=[f"{row['name']} {row['exam_title']} {row['school']} "
                      f"{row['teacher']}".lower() for row in learners],
        learner_cap=analysis_scope.MAX_LEARNERS,
        learners_truncated=len(learners) >= analysis_scope.MAX_LEARNERS,
        scope_choices=choices,
        # A choice the scope does not offer is not a filter at all — the page
        # shows "all" beside it and prints the ordinary empty state.
        school_filter=school_filter if any(o["id"] == school_filter
                                           for o in choices["schools"]) else "",
        teacher_filter=teacher_filter if any(o["id"] == teacher_filter
                                             for o in choices["teachers"]) else "",
        reports_base=ADMIN_REPORTS_BASE)


@admin_sekolah_bp.route("/reports")
@admin_sekolah_required
def admin_reports():
    """Laporan sekolah: setiap dokumen kelas dan murid yang boleh diterbitkan."""
    return _reports_page()


@admin_sekolah_bp.route("/analytics")
@admin_sekolah_required
def admin_analytics():
    """Statistik seluruh ujian sekolah, baca saja."""
    return _analytics_page()


@admin_sekolah_bp.route("/analytics/download.csv")
@admin_sekolah_required
def admin_analytics_csv():
    return _analytics_file("csv")


@admin_sekolah_bp.route("/analytics/download.pdf")
@admin_sekolah_required
def admin_analytics_pdf():
    return _analytics_file("pdf")


@admin_sekolah_bp.route("/analytics/print")
@admin_sekolah_required
def admin_analytics_print():
    return _analytics_page(print_mode=True)
