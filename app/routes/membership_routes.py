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

    persetujuan / penolakan permintaan  -> admin_sekolah, principal, vice_principal
    penutupan keanggotaan               -> admin_sekolah, principal, vice_principal
    PEMBUKAAN kembali keanggotaan       -> HANYA admin_sekolah

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

from flask import Blueprint, g, jsonify, request

from app.decorators.security import require_school_access  # noqa: F401  (re-exported for callers)
from app.services.audit_service import log_activity
from app.services import school_membership as membership
from app.utils.auth import (admin_sekolah_required, get_supabase,
                            login_door_for, role_required)

membership_bp = Blueprint("membership", __name__)

#: The two official roles that may decide and close, beside the school admin.
_OFFICIAL_ROLES = ("principal", "vice_principal")


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


@membership_bp.route("/membership-requests/<request_id>/<decision>", methods=["POST"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_request_decide(request_id, decision):
    """Setujui atau tolak satu permintaan bergabung — di sekolah tujuan saja.

    Sekolah yang dipakai adalah sekolah SESI (`_school_id()`), tidak pernah yang
    dikirim klien: permintaan sekolah lain tidak ditemukan, bukan dilayani.
    """
    if decision not in ("approve", "reject"):
        return jsonify({"error": "not_found"}), 404
    sid = _school_id()
    if not sid:
        return jsonify({"error": "no_school"}), 403
    out = membership.decide_request(
        get_supabase(), sid, request_id,
        "approved" if decision == "approve" else "rejected",
        actor_id=g.get("user_id"), actor_role=g.get("user_role"),
        reason=(request.form.get("reason") or None))
    log_activity("update", "school_membership_request", request_id,
                 new_data={"decision": decision, "ok": bool(out.get("ok"))},
                 user_id=g.get("user_id"))
    return jsonify(out), (200 if out.get("ok") else 403)


@membership_bp.route("/memberships/<user_id>/close", methods=["POST"])
@role_required("admin_sekolah", "principal", "vice_principal")
def membership_close(user_id):
    """Tutup keanggotaan seorang guru di sekolah ini — penutupan TOTAL.

    `closed`, bukan `inactive`: sejak migrasi 064 hanya ada SATU nama untuk
    penutupan, sehingga tidak ada halaman yang bisa menampilkan dua jenis penutupan
    untuk satu peristiwa.
    """
    sid = _school_id()
    if not sid:
        return jsonify({"error": "no_school"}), 403
    membership.deactivate(get_supabase(), sid, user_id, actor_id=g.get("user_id"))
    log_activity("update", "teacher_school_membership", user_id,
                 new_data={"status": membership.CLOSED_STATUS},
                 user_id=g.get("user_id"))
    return jsonify({"ok": True, "status": membership.CLOSED_STATUS}), 200


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
        return jsonify({"error": "no_school"}), 403
    out = membership.reopen_membership(get_supabase(), sid, user_id,
                                       actor_id=g.get("user_id"),
                                       actor_role=g.get("user_role"))
    log_activity("update", "teacher_school_membership", user_id,
                 new_data={"status": "active", "reopened": bool(out.get("ok"))},
                 user_id=g.get("user_id"))
    return jsonify(out), (200 if out.get("ok") else 403)
