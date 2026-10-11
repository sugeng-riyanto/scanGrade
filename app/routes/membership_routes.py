"""Keanggotaan lintas sekolah: keputusan, penutupan, dan pembukaan kembali.

Modul ini ada sebagai berkas sendiri dengan alasan yang praktis dan bukan selera:
rute-rutenya dulu ditambahkan ke `admin_sekolah.py`, dan saat akan dikomit berkas
itu ternyata sedang memuat pekerjaan sesi agent lain yang belum dikomit
(pemindahan `redirect("/auth/login")` ke `login_door_for`). Menambahkan milik kita
ke berkas yang sedang diubah orang lain berarti salah satu dari dua hal: kita
mengomit pekerjaan mereka sebagai milik kita, atau mereka menimpa milik kita.
Berkas terpisah menghapus pilihan buruk itu, dan tetap memakai blueprint dengan
prefix yang sama sehingga URL-nya tidak berubah.

Aturan perannya SENGAJA tidak seragam, dan asimetri itu keputusan produk:

    mengajukan bergabung (sisi guru)    -> guru, principal, vice_principal
    persetujuan / penolakan permintaan  -> admin_sekolah, principal, vice_principal
    penutupan keanggotaan               -> admin_sekolah, principal, vice_principal
    PEMBUKAAN kembali keanggotaan       -> HANYA admin_sekolah

Empat permukaan, dan masing-masing punya alasannya sendiri:

* `GET  /teacher/membership`                layar cari sekolah tujuan (halaman)
* `POST /teacher/membership/request`        satu pengajuan, dengan bukti persetujuan
* `GET  /admin-sekolah/membership`          layar sekolah tujuan (antrean + anggota)
* `GET  /admin-sekolah/membership-requests` antrean sekolah tujuan (JSON)
* `POST /admin-sekolah/membership-requests/<id>/<decision>` keputusan
* `POST /admin-sekolah/memberships/<uid>/close`   penutupan TOTAL
* `POST /admin-sekolah/memberships/<uid>/reopen`  HANYA admin_sekolah

Layar cari sekolah itu sendiri adalah aturan, bukan hiasan: sekolah yang belum
punya kelas terdaftar TETAP muncul, dengan jenjang yang dikatakan belum terdata
alih-alih disembunyikan. Sekolah tanpa kelas justru tujuan baru yang paling mungkin,
dan sekolah yang tidak muncul di hasil tidak bisa diajukan sama sekali.

Kenapa asimetri: menyetujui permintaan baru dan menutup akses adalah tindakan
operasional yang boleh dibagi tiga peran; membuka kembali akses yang sudah ditutup
adalah keputusan administratif yang sengaja dipersempit ke satu peran.

Dua lapis, bukan satu. Dekorator menjaga rute ini; service-nya
(`school_membership.reopen_membership` / `decide_request`) memeriksa peran SEKALI
LAGI sebelum menulis, sehingga pemanggil masa depan yang lupa dekorator tetap tidak
bisa membuka kembali. Setiap aksi dicatat ke audit log, dan setiap penolakan terjadi
sebelum satu baris pun berubah.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for

from app.services.audit_service import log_activity
from app.services import school_membership as membership
from app.utils.auth import (admin_sekolah_required, get_supabase,
                            login_door_for, role_required)

membership_bp = Blueprint("membership", __name__)
#: The teacher-facing half lives under `/teacher`, and it is a second blueprint rather
#: than a second prefix on the first: one blueprint cannot be registered under two URL
#: prefixes, and renaming the admin URLs to satisfy that would break every existing
#: link to them.
teacher_membership_bp = Blueprint("membership_teacher", __name__)

#: Roles that may hold a membership in more than one school, and therefore the only
#: roles that can have anything to do with this module's teacher half.
_MEMBERSHIP_ROLES = ("guru", "principal", "vice_principal")


def _wants_json() -> bool:
    """Whether the caller is an API client rather than a form on one of our pages.

    Decided by the request, not by the route: the same endpoint answers a form post
    with a redirect back to the page (so the reader sees the outcome where they acted,
    which is what a school with spotty Wi-Fi needs) and answers an API client with the
    verdict as JSON.
    """
    if request.is_json or request.path.startswith("/api/"):
        return True
    return "application/json" in (request.headers.get("Accept") or "")


def _back(outcome: str):
    """Return the reader to the page they acted on, with the outcome named."""
    return redirect(url_for("membership.membership_home", status=outcome))


def _outcome(out: dict, ok_code: str) -> str:
    return ok_code if out.get("ok") else (out.get("reason") or "failed")


def _school_id() -> str | None:
    """The session's school — never a school named by the client.

    The same helper `admin_sekolah.py` uses, repeated here rather than imported:
    importing a private name from a route module couples this file to that one's
    internals, and the whole point of the file is to not depend on it.
    """
    sid = g.get("user_school_id")
    if not sid or sid == "None":
        return None
    return sid


@membership_bp.route("/membership", methods=["GET"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_home():
    """Sekolah tujuan: siapa yang meminta bergabung, dan siapa yang bekerja di sini.

    Satu halaman untuk tiga peran penyetuju, karena menyetujui adalah satu pekerjaan.
    Yang TIDAK sama adalah tombol buka-kembali: ia hanya dirender untuk
    `admin_sekolah`. Asimetrinya karena itu terlihat di halaman, bukan hanya ditegakkan
    di service — seorang principal yang tidak berwenang tidak perlu melihat tombol yang
    akan menolaknya.

    Status `closed` ikut ditampilkan pada daftar anggota: baris itulah yang punya
    tombol buka-kembali, dan daftar yang hanya memuat `active` tidak punya apa pun
    untuk dibuka.
    """
    sid = _school_id()
    if not sid:
        # A role that may decide, with no school in its session, has nothing to
        # decide about: send it back to *its own* door rather than render an empty
        # page. `login_door_for` and not the admin path, because a principal or a
        # deputy landing on the admin group's page is told the wrong thing about
        # themselves (`test_login_door.py`).
        return redirect(login_door_for(g.get("user_role"), request.path))
    supabase = get_supabase()
    role = g.get("user_role")
    queue = membership.pending_requests(supabase, sid, role)
    members = membership.school_members(supabase, sid, role)
    return render_template(
        "admin_sekolah/membership.html",
        join_requests=queue.get("requests") or [],
        members=members.get("members") or [],
        may_reopen=membership.may_reopen(role),
        may_approve=membership.may_approve(role),
        status_code=(request.args.get("status") or "").strip(),
    )


@membership_bp.route("/membership-requests", methods=["GET"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_request_queue():
    """Antrean sekolah TUJUAN, tempat keputusan diambil.

    Dibaca dari sekolah sesi dan bukan dari parameter: sekolah lain tidak punya
    antrean untuk dilihat, jadi "tidak ditemukan" adalah seluruh jawabannya.

    Bentuk JSON karena halaman `/admin-sekolah/membership` yang *merender* antrean ini
    membacanya dari service yang sama, dan daftar ini tidak boleh punya mesin render
    kedua yang bisa menyimpang dari halaman guru. Pemisahannya karena itu tentang
    bentuk, bukan tentang data: satu pembacaan, dua penyajian.
    """
    sid = _school_id()
    if not sid:
        return jsonify({"error": "no_school"}), 403
    out = membership.pending_requests(get_supabase(), sid, g.get("user_role"))
    return jsonify(out), (200 if out.get("ok") else 403)


@membership_bp.route("/membership-requests/<request_id>/<decision>", methods=["POST"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_request_decide(request_id, decision):
    """Setujui atau tolak satu permintaan bergabung — di sekolah tujuan saja.

    Sekolah yang dipakai adalah sekolah SESI (`_school_id()`), tidak pernah yang
    dikirim klien: permintaan sekolah lain tidak ditemukan, bukan dilayani.
    """
    if decision not in ("approve", "reject"):
        if _wants_json():
            return jsonify({"error": "not_found"}), 404
        return _back("not_found")
    sid = _school_id()
    if not sid:
        if _wants_json():
            return jsonify({"error": "no_school"}), 403
        return redirect(login_door_for(g.get("user_role"), request.path))
    out = membership.decide_request(
        get_supabase(), sid, request_id,
        "approved" if decision == "approve" else "rejected",
        actor_id=g.get("user_id"), actor_role=g.get("user_role"),
        reason=(request.form.get("reason") or None))
    log_activity("update", "school_membership_request", request_id,
                 new_data={"decision": decision, "ok": bool(out.get("ok"))},
                 user_id=g.get("user_id"))
    if _wants_json():
        return jsonify(out), (200 if out.get("ok") else 403)
    return _back(_outcome(out, "ok"))


@membership_bp.route("/memberships/<user_id>/close", methods=["POST"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_close(user_id):
    """Tutup keanggotaan seorang guru di sekolah ini — penutupan TOTAL.

    `closed`, bukan `inactive`: sejak migrasi 064 hanya ada SATU nama untuk
    penutupan, sehingga tidak ada halaman yang bisa menampilkan dua jenis penutupan
    untuk satu peristiwa.

    Penutupannya berlaku SEKETIKA untuk semua jalan baca: sekolah aktif diresolusi
    ulang tiap request, jadi guru yang sedang membuka halaman kehilangan sekolahnya di
    request berikutnya — bukan setelah cache sesi kedaluwarsa.
    """
    sid = _school_id()
    if not sid:
        if _wants_json():
            return jsonify({"error": "no_school"}), 403
        return redirect(login_door_for(g.get("user_role"), request.path))
    membership.deactivate(get_supabase(), sid, user_id, actor_id=g.get("user_id"))
    # The table's name comes from the service, not from this file: the audit trail's
    # entity *is* that table, and a second spelling of it here is the one the
    # single-writer guard exists to refuse (`test_only_the_service_names_the_
    # membership_table`).
    log_activity("update", membership.MEMBERSHIPS_TABLE, user_id,
                 new_data={"status": membership.CLOSED_STATUS},
                 user_id=g.get("user_id"))
    if _wants_json():
        return jsonify({"ok": True, "status": membership.CLOSED_STATUS}), 200
    return _back("closed")


@membership_bp.route("/memberships/<user_id>/reopen", methods=["POST"])
@admin_sekolah_required
def membership_reopen(user_id):
    """Buka kembali keanggotaan yang ditutup. HANYA admin_sekolah.

    Dekorator `admin_sekolah_required` menolak principal dan vice_principal DI SINI;
    `reopen_membership` menolaknya sekali lagi sebelum menulis. Dua lapis itu
    disengaja: satu dekorator yang terlupa pada rute kembar tidak boleh cukup untuk
    membuka kembali akses yang sudah ditutup.
    """
    sid = _school_id()
    if not sid:
        if _wants_json():
            return jsonify({"error": "no_school"}), 403
        return redirect(login_door_for(g.get("user_role"), request.path))
    out = membership.reopen_membership(get_supabase(), sid, user_id,
                                       actor_id=g.get("user_id"),
                                       actor_role=g.get("user_role"))
    log_activity("update", membership.MEMBERSHIPS_TABLE, user_id,
                 new_data={"status": membership.STATUS_ACTIVE,
                           "reopened": bool(out.get("ok"))},
                 user_id=g.get("user_id"))
    if _wants_json():
        return jsonify(out), (200 if out.get("ok") else 403)
    return _back(_outcome(out, "reopened"))


# ── sisi guru: cari sekolah tujuan, lalu ajukan bergabung ────────────────────

@teacher_membership_bp.route("/membership", methods=["GET"])
@role_required(*_MEMBERSHIP_ROLES)
def teacher_membership():
    """Layar cari sekolah tujuan, untuk guru yang ingin mengajar di NPSN lain.

    Hasil pencariannya memuat **jenjang** yang diturunkan dari kelas sekolah itu,
    dan sekolah yang belum punya kelas terdaftar tetap muncul dengan jenjang kosong —
    halaman yang menyembunyikannya akan menyembunyikan sekolah yang paling mungkin
    menjadi tujuan baru. Teks persetujuan yang ditampilkan dan hash yang dikirim ke
    rute pengajuan berasal dari satu sumber yang sama (`consent_texts()` dan
    `consent_sha256()`), sehingga bukti yang tersimpan benar-benar tentang teks yang
    dibaca guru.
    """
    supabase = get_supabase()
    q = (request.args.get("q") or "").strip()
    ind, eng = membership.consent_texts()
    return render_template(
        "teacher/membership.html",
        q=q,
        schools=membership.school_search(
            supabase, q, member_ids=membership.member_school_ids(supabase, g.get("user_id"))),
        my_requests=membership.my_requests(supabase, g.get("user_id")),
        consent_id=ind,
        consent_en=eng,
        consent_version=membership.CONSENT_DOCUMENT_VERSION,
        consent_sha256=membership.consent_sha256(),
        # The refusal a redirect came back with. A code, not a sentence: the message
        # is the template's, so the language toggle decides it.
        status_code=(request.args.get("status") or "").strip(),
    )


@teacher_membership_bp.route("/membership/request", methods=["POST"])
@role_required(*_MEMBERSHIP_ROLES)
def teacher_membership_request():
    """Ajukan bergabung ke satu sekolah tujuan.

    Sekolah tujuannya datang dari formulir (itu memang pilihannya), sedangkan
    *pengajunya* dari sesi: `create_request` membaca ulang identitas dan peran dari
    `g`, jadi satu formulir tidak bisa mengajukan atas nama orang lain.
    """
    out = membership.create_request(
        get_supabase(), g.get("user_id"), request.form.get("school_id"),
        g.get("user_role"),
        document_version=request.form.get("document_version"),
        document_sha256=request.form.get("document_sha256"))
    log_activity("create", "school_membership_request", out.get("request_id"),
                 new_data={"ok": bool(out.get("ok")), "reason": out.get("reason")},
                 user_id=g.get("user_id"))
    if _wants_json():
        return jsonify(out), (200 if out.get("ok") else 403)
    outcome = "ok" if out.get("ok") else (out.get("reason") or "failed")
    return redirect(url_for("membership_teacher.teacher_membership",
                            q=request.form.get("q", ""), status=outcome))
