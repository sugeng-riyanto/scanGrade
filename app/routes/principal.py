"""Kepala sekolah dan wakil kepala sekolah: satu dashboard, dua pembaca.

Kedua peran ini ada untuk **mengawasi**, bukan mengelola. Karena itu blueprint ini
sengaja hanya punya GET: tidak ada satu pun route di sini yang menulis. Wewenang
tulis tetap di `admin_sekolah` (data dan akun), dan memisahkannya seperti ini
membuat "read-only" menjadi sifat struktural halaman — bukan janji di dokumen yang
bisa dilanggar satu tombol baru tanpa ada yang gagal.

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

import logging

from flask import Blueprint, g, redirect, render_template

from app.utils.auth import get_supabase, school_official_required

logger = logging.getLogger(__name__)

principal_bp = Blueprint("principal", __name__)

#: How many of the school's newest papers the page lists. Deliberately small: this
#: is an oversight page, not the results list, and a reader who wants the list has
#: `admin_sekolah` for it.
RECENT_EXAMS = 8


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
    school = {}
    try:
        # The columns the schema actually has: `schools` carries no `level`, and
        # naming one that does not exist makes PostgREST refuse the whole request
        # (PGRST205) — which the `except` below would turn into an empty header
        # rather than an error anyone could see.
        school = (supabase.table("schools").select("name, npsn, city, province")
                  .eq("id", school_id).single().execute().data) or {}
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("official dashboard: could not read the school row: %s", exc)

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
                           stats=stats, exams=exams)


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
