"""Kepala sekolah dan wakil kepala sekolah: satu dashboard, dua pembaca.

Kedua peran ini ada untuk **mengawasi**, bukan mengelola. Karena itu semua route
`/principal/*` tetap hanya GET: "read-only" menjadi sifat struktural halaman, bukan
janji di dokumen yang bisa dilanggar satu tombol baru tanpa ada yang gagal.

Satu pengecualian, dan hanya satu: **jadwal pengawasan ujian**, yang wewenang
tulisnya didelegasikan kepada wakil kepala sekolah (lihat matriks di docs/RBAC.md).
Karena itu seluruh route tulisnya berada di prefiks `/vice-principal/*` dan tidak
ada satu pun di `/principal/*` — kepala sekolah membuka halaman yang sama dan
melihat jadwalnya, tanpa satu pun tombol yang menulis. Pemisahan itu diuji, bukan
diandaikan: `tests/unit/test_invigilation.py` menolak route POST apa pun di bawah
prefiks kepala sekolah.

Satu fungsi view untuk dua alamat (`/principal/dashboard` dan
`/vice-principal/dashboard`) karena isinya memang sama: angka sekolah yang sama,
ujian yang sama. Yang berbeda hanya siapa yang membaca, dan itu diteruskan sebagai
`role` supaya judul halaman menyebut pembacanya dengan benar — wakil kepala
sekolah yang membaca halaman berjudul "Kepala Sekolah" akan mengira ia meminjam
akun orang lain.

Kenapa tidak memakai ulang dashboard `admin_sekolah`: halaman itu dibangun dari
tombol dan tautan menuju aksi (impor, naik kelas, langganan) yang justru tidak
boleh dijalankan pembaca di sini. Menampilkan pintu yang menolak lebih buruk
daripada tidak menampilkan pintu.
"""
from __future__ import annotations

import io
import logging

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   send_file)

from app.utils.auth import (get_supabase, principal_required,
                            school_official_required, vice_principal_required)
from app.utils.cache import cache_get, cache_set
from app.services import analysis_scope, invigilation, official_insight

logger = logging.getLogger(__name__)

principal_bp = Blueprint("principal", __name__)

#: How many of the school's newest papers the page lists. Deliberately small: this
#: is an oversight page, not the results list, and a reader who wants the list has
#: `admin_sekolah` for it.
RECENT_EXAMS = 8

#: The statistics report is the most expensive page a school official can open, so
#: it is cached for the same five minutes the teacher's own copy is — one number
#: for the whole box, so an official's report cannot be fresher or staler than the
#: teacher's report of the same exams.
ANALYTICS_TTL = 300

#: The progress page counts the same rows but is cheap, and it is the page a head
#: of school leaves open while a sitting runs. A minute is long enough to keep
#: five hundred students off the database and short enough that a paper handed in
#: is on the page before the teacher who marked it has closed the tab.
PROGRESS_TTL = 60

#: Which address each official reads the shared pages at. One table, so the two
#: blueprints cannot drift into serving different paths for the same page — and so
#: a third official role is one line rather than five templates with a path in them.
OFFICIAL_BASES = {
    "principal": "/principal",
    "vice_principal": "/vice-principal",
}


def _base(role: str) -> str:
    """This reader's own address prefix, e.g. `/vice-principal`."""
    return OFFICIAL_BASES.get(role) or "/principal"


def _school_id():
    """The school this reader oversees, or None.

    Read from the session and never from the request: every query below is filtered
    by it, so a page that accepted a school id from the query string would be a page
    that reads another school's children.
    """
    return g.get("user_school_id")


def _school(supabase, school_id) -> dict:
    """The school row, for the page's header. A failed read is an empty header
    rather than an error page: the numbers below are the page's point."""
    try:
        return (supabase.table("schools").select("name, npsn, city, province")
                .eq("id", school_id).single().execute().data) or {}
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("official page: could not read the school row: %s", exc)
        return {}


def _count(supabase, table: str, school_id: str, **filters) -> int:
    """Rows in this school, counted by the database.

    ``count="exact"`` rather than ``len(rows)``: the page only prints the number,
    so fetching the rows to measure them is a table read for a single integer.
    A table this schema may not have yet (a school mid-setup) answers 0 rather
    than failing the whole page — the page is for looking at, not for migrating.
    """
    try:
        query = supabase.table(table).select("id", count="exact").eq("school_id", school_id)
        for column, value in filters.items():
            query = query.eq(column, value)
        return query.execute().count or 0
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("official dashboard: could not count %s: %s", table, exc)
        return 0


def _dashboard(role: str):
    """The page itself, whichever of the two officials is reading it."""
    school_id = g.get("user_school_id")
    if not school_id:
        # An official with no school has nothing to oversee, and every query below
        # would read every school. Refusing is the only safe answer.
        return redirect("/auth/login")

    supabase = get_supabase()
    # The columns the schema actually has: `schools` carries no `level`, and naming
    # one that does not exist makes PostgREST refuse the whole request (PGRST205) —
    # which the reader's `except` would turn into an empty header rather than an
    # error anyone could see.
    school = _school(supabase, school_id)

    stats = {
        "teachers": _count(supabase, "teachers", school_id),
        "students": _count(supabase, "students", school_id),
        "classes": _count(supabase, "classes", school_id),
        "subjects": _count(supabase, "subjects", school_id),
        "exams": _count(supabase, "exams", school_id),
    }

    exams: list[dict] = []
    try:
        exams = (supabase.table("exams")
                 .select("id, title, subject, created_at, status")
                 .eq("school_id", school_id)
                 .order("created_at", desc=True)
                 .limit(RECENT_EXAMS).execute().data) or []
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("official dashboard: could not read recent exams: %s", exc)

    return render_template("principal/dashboard.html", role=role, school=school,
                           stats=stats, exams=exams, base=_base(role))


@principal_bp.route("/principal/dashboard")
@school_official_required
def principal_dashboard():
    """Kepala sekolah: laporan dan pengawasan sekolahnya, tanpa wewenang tulis."""
    return _dashboard("principal")


@principal_bp.route("/vice-principal/dashboard")
@school_official_required
def vice_principal_dashboard():
    """Wakil kepala sekolah: halaman yang sama, dengan wewenang yang sama."""
    return _dashboard("vice_principal")


# ── the school's statistics, read ────────────────────────────────────────────
#
# The same two pages a teacher reads, at the officials' own addresses. Not a copy:
# `teacher/analytics.html` is rendered with `analysis_base` pointing at the reader,
# so the form action, the CSV, the PDF and the print view all stay inside the
# officials' own blueprint. Two implementations of one report is how the head's
# numbers and the teacher's numbers start to differ, and the reader would have no
# way to tell which one to believe.

def _scope_report(supabase, role: str, lang: str, *, date_from=None, date_to=None):
    """The school's statistics report, cached per reader, school, language and range.

    The range is in the key for the same reason it is on the teacher's page: a
    report narrowed to one term is not the report for the year, and serving one as
    the other prints the wrong totals beside the right rows.
    """
    key = (f"official-analytics:{role}:{g.get('user_id')}:"
           f"{g.get('user_school_id') or '-'}:{lang}:"
           f"{date_from or ''}:{date_to or ''}")
    cached = cache_get(key)
    if cached:
        return analysis_scope.from_payload(cached)
    data = analysis_scope.report(supabase, role, g.get("user_id"), _school_id(),
                                 lang=lang, date_from=date_from, date_to=date_to)
    try:
        cache_set(key, analysis_scope.as_payload(data), ttl=ANALYTICS_TTL)
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


def _analytics_html(role: str, *, print_mode: bool = False):
    """The page itself, whichever of the two officials is reading it."""
    if not _school_id():
        return redirect("/auth/login")
    supabase = get_supabase()
    lang = analysis_scope.language(request.args.get("lang"))
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    data = _scope_report(supabase, role, lang, date_from=date_from, date_to=date_to)
    return render_template(
        "teacher/analytics.html", report=data, stats=_kpis(data),
        dist_bins=data["bins"], exam_breakdown=data["rows"],
        exam_labels=[row["title"][:20] for row in data["rows"]],
        exam_avgs=[row["mean"] or 0 for row in data["rows"]],
        exam_medians=[row["median"] or 0 for row in data["rows"]],
        analysis_base=_base(role) + "/analytics",
        **({"print_mode": True} if print_mode else {}))


def _analytics_file(role: str, kind: str):
    """The report as the document a head of school files or hands on."""
    if not _school_id():
        return redirect("/auth/login")
    supabase = get_supabase()
    lang = analysis_scope.language(request.args.get("lang"))
    data = _scope_report(supabase, role, lang,
                         date_from=request.args.get("date_from") or None,
                         date_to=request.args.get("date_to") or None)
    if kind == "csv":
        payload = analysis_scope.report_csv(data).encode("utf-8-sig")
        return send_file(io.BytesIO(payload), mimetype="text/csv", as_attachment=True,
                         download_name=analysis_scope.filename(data, "csv"))
    pdf = analysis_scope.report_pdf(data)
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=analysis_scope.filename(data, "pdf"))


# ── the calendar ─────────────────────────────────────────────────────────────

def _progress(role: str):
    """The month on a calendar, its weeks, the term's months and the teachers.

    Cached for a minute under a key that carries the school, the month in the
    reader's clock **and** the clock itself: a report bucketed in WIB is a
    different report from the same rows bucketed in UTC, and one cache entry for
    both would hand a reader somebody else's day boundaries.
    """
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    supabase = get_supabase()
    tz_offset = g.get("tz_offset", 7)
    month = request.args.get("month") or ""
    key = f"official-progress:{school_id}:{tz_offset}:{month or 'now'}"
    data = cache_get(key)
    if not data:
        data = official_insight.progress(supabase, school_id, month=month or None,
                                         tz_offset_hours=tz_offset)
        try:
            cache_set(key, data, ttl=PROGRESS_TTL)
        except Exception:                                      # noqa: BLE001
            pass
    return render_template("principal/progress.html", role=role, progress=data,
                           school=_school(supabase, school_id),
                           month_key=data["month"]["key"], tz=tz_offset,
                           base=_base(role))


@principal_bp.route("/principal/analytics")
@school_official_required
def principal_analytics():
    """Kepala sekolah: statistik seluruh ujian sekolahnya, baca saja."""
    return _analytics_html("principal")


@principal_bp.route("/vice-principal/analytics")
@school_official_required
def vice_principal_analytics():
    """Wakil kepala sekolah: laporan yang sama, cakupan yang sama."""
    return _analytics_html("vice_principal")


@principal_bp.route("/principal/analytics/download.csv")
@school_official_required
def principal_analytics_csv():
    return _analytics_file("principal", "csv")


@principal_bp.route("/vice-principal/analytics/download.csv")
@school_official_required
def vice_principal_analytics_csv():
    return _analytics_file("vice_principal", "csv")


@principal_bp.route("/principal/analytics/download.pdf")
@school_official_required
def principal_analytics_pdf():
    return _analytics_file("principal", "pdf")


@principal_bp.route("/vice-principal/analytics/download.pdf")
@school_official_required
def vice_principal_analytics_pdf():
    return _analytics_file("vice_principal", "pdf")


@principal_bp.route("/principal/analytics/print")
@school_official_required
def principal_analytics_print():
    """Sama dengan halaman analitik, hanya tanpa chrome layar."""
    return _analytics_html("principal", print_mode=True)


@principal_bp.route("/vice-principal/analytics/print")
@school_official_required
def vice_principal_analytics_print():
    return _analytics_html("vice_principal", print_mode=True)


@principal_bp.route("/principal/progress")
@school_official_required
def principal_progress():
    """Kepala sekolah: kalender bulan dan tren mingguan sekolahnya."""
    return _progress("principal")


@principal_bp.route("/vice-principal/progress")
@school_official_required
def vice_principal_progress():
    return _progress("vice_principal")


# ── the invigilation schedule ────────────────────────────────────────────────
#
# One page for both officials and one function that builds it: the head of school
# and their deputy read the same schedule, and the difference between them is not
# the content but the *verbs* — which is expressed by which routes exist, not by a
# flag inside the template. `can_write` is passed only so the page can decide whether
# to draw a form at all; the POST routes themselves live behind
# `vice_principal_required`, so a page that drew the form anyway would still be
# refused by the route.

def _invigilation_page(role: str):
    """The schedule, its invigilators, and the retake requests waiting on someone."""
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    supabase = get_supabase()
    return render_template(
        "principal/invigilation.html",
        role=role,
        base=_base(role),
        can_write=role == "vice_principal",
        school=_school(supabase, school_id),
        schedules=invigilation.list_schedules(supabase, school_id),
        requests=invigilation.retake_requests(supabase, school_id),
        options=invigilation.form_options(supabase, school_id),
    )


def _invigilation_refused(out: dict):
    """Flash whatever the write refused, as a key the page translates.

    The key rather than a sentence: the language lives in the browser, which the
    server never sees, so the words belong to the template — the same arrangement
    the auth pages use for their messages.
    """
    if not out.get("ok"):
        flash(out.get("reason") or "write_failed", "error")
        return False
    if out.get("reason"):
        flash(out["reason"], "error")
        return False
    return True


@principal_bp.route("/principal/invigilation")
@principal_required
def principal_invigilation():
    """Kepala sekolah: jadwal pengawasan sekolahnya, baca saja."""
    return _invigilation_page("principal")


@principal_bp.route("/vice-principal/invigilation")
@vice_principal_required
def vice_principal_invigilation():
    """Wakil kepala sekolah: halaman yang sama, plus wewenang menyusunnya."""
    return _invigilation_page("vice_principal")


@principal_bp.route("/vice-principal/invigilation/save", methods=["POST"])
@vice_principal_required
def vice_principal_invigilation_save():
    """Create or move one sitting. The school is the session's, never the form's."""
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    out = invigilation.save_schedule(
        get_supabase(), school_id,
        exam_id=request.form.get("exam_id", ""),
        class_id=request.form.get("class_id", ""),
        scheduled_at=request.form.get("scheduled_at", ""),
        room=request.form.get("room", ""),
        notes=request.form.get("notes", ""),
        actor_id=g.get("user_id"),
    )
    _invigilation_refused(out)
    return redirect("/vice-principal/invigilation")


@principal_bp.route("/vice-principal/invigilation/<schedule_id>/assign",
                    methods=["POST"])
@vice_principal_required
def vice_principal_invigilation_assign(schedule_id: str):
    """Put a teacher on a sitting, or make them its lead."""
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    out = invigilation.assign_invigilator(
        get_supabase(), school_id,
        schedule_id=schedule_id,
        teacher_id=request.form.get("teacher_id", ""),
        is_lead=request.form.get("is_lead") in ("1", "true", "on"),
        actor_id=g.get("user_id"),
    )
    _invigilation_refused(out)
    return redirect("/vice-principal/invigilation")


@principal_bp.route("/vice-principal/invigilation/assignments/<assignment_id>/remove",
                    methods=["POST"])
@vice_principal_required
def vice_principal_invigilation_unassign(assignment_id: str):
    """Take a teacher off a sitting."""
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    _invigilation_refused(invigilation.remove_assignment(
        get_supabase(), school_id, assignment_id))
    return redirect("/vice-principal/invigilation")


@principal_bp.route("/vice-principal/retake-requests/<request_id>/decide",
                    methods=["POST"])
@vice_principal_required
def vice_principal_retake_decide(request_id: str):
    """Decide a retake request as the school's delegated authority.

    No `within_exam_ids` here: a vice principal's authority is the whole school, so
    there is nothing to narrow. The race with an invigilator deciding at the same
    moment is closed in the service, not here.
    """
    school_id = _school_id()
    if not school_id:
        return redirect("/auth/login")
    out = invigilation.decide_retake(
        get_supabase(), school_id, request_id,
        decision=request.form.get("decision", ""),
        actor_id=g.get("user_id"),
        note=request.form.get("note", ""),
    )
    _invigilation_refused(out)
    return redirect("/vice-principal/invigilation")
