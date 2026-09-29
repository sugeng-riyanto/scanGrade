"""Akun pejabat sekolah: kepala sekolah dan wakil kepala sekolah.

Dua peran ini **tidak punya tabelnya sendiri**, dan itu disengaja. Guru butuh baris
`teachers` (NIP, mata pelajaran) dan murid butuh baris `students` (NISN, kelas);
pejabat sekolah tidak membawa atribut apa pun di luar identitas akunnya. Menambah
tabel `officials` yang hanya berisi `id` dan `school_id` akan menambah satu join
ke setiap pembacaan peran tanpa menyimpan satu fakta pun — jadi peran cukup hidup
di `profiles.role`, tempat seluruh aplikasi sudah membacanya.

Urutan penulisannya sama dengan `teacher_import.create_teacher_account`, dan
alasannya sama: `auth` dulu (karena `profiles.id` mereferensikan `auth.users`),
lalu profil. Kalau profil gagal, akun auth yang sudah jadi **dihapus** — akun yang
bisa login tanpa punya peran adalah akun yang tidak bisa ditolong siapa pun:
pembuatnya melihat error, pemiliknya melihat halaman kosong.
"""
from __future__ import annotations

import logging

from app.services import password_change as _password_change

logger = logging.getLogger(__name__)

#: The roles this module owns, and the whole vocabulary the school admin may hand
#: it. A tuple set, not a string check, so `create_official` cannot be talked into
#: writing `admin_sekolah` by a form field.
OFFICIAL_ROLES = ("principal", "vice_principal")

PROFILE_FIELDS = "id, full_name, role, phone, status, school_id"

#: What migration 040 adds, asked for apart from the rest.
#:
#: PostgREST refuses a select naming a column it cannot find **in full**, so asking for
#: `email` unconditionally would make this page empty on a database that has not run 040
#: yet — and an empty roster reads as "this school has no head teacher", which is a worse
#: lie than a dash in the email column.
PROFILE_FIELDS_040 = "email, must_change_password"


def _read(supabase, build):
    """``build(columns)``, with the newest columns first and without them if refused."""
    try:
        return build(PROFILE_FIELDS + ", " + PROFILE_FIELDS_040)
    except Exception:                                         # noqa: BLE001
        return build(PROFILE_FIELDS)


class OfficialError(Exception):
    """A refusal a route may show to the operator.

    `user_message` is the Indonesian sentence the page flashes; the technical
    detail stays in the log. Routes already read `getattr(e, 'user_message', ...)`
    for the teacher importer, so this follows that contract.
    """

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message


def validate_role(role: str) -> str:
    """The role, or a refusal — never a role this module does not own."""
    role = (role or "").strip()
    if role not in OFFICIAL_ROLES:
        raise ValueError(
            f"{role!r} is not a school official role; expected one of "
            f"{', '.join(OFFICIAL_ROLES)}")
    return role


def list_officials(supabase, school_id: str) -> list[dict]:
    """This school's officials, newest head first, then by name.

    Scoped by `school_id` in the query rather than filtered afterwards: a school
    admin's page must not learn that another school's officials exist, and a
    filter after the read is one `return` away from leaking them.
    """
    if not school_id:
        return []
    try:
        rows = _read(supabase, lambda cols: (
            supabase.table("profiles").select(cols)
            .eq("school_id", school_id)
            .in_("role", list(OFFICIAL_ROLES))
            .order("full_name").execute().data)) or []
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("could not list officials for school %s: %s", school_id, exc)
        return []
    order = {role: index for index, role in enumerate(OFFICIAL_ROLES)}
    return sorted(rows, key=lambda row: (order.get(row.get("role"), 9),
                                         (row.get("full_name") or "").lower()))


def create_official(supabase, *, school_id: str, role: str, full_name: str,
                    email: str, password: str, phone: str = ""):
    """Create one official: auth user, then the profile that names the role.

    Returns the new user's id. Raises ``ValueError`` for a role this module does
    not own, and ``OfficialError`` for a refusal the operator should read.
    """
    validate_role(role)
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()
    if not full_name:
        raise OfficialError("Nama pejabat sekolah wajib diisi.")
    if not email:
        raise OfficialError("Email wajib diisi.")
    if not password:
        raise OfficialError("Password wajib diisi.")

    uid = None
    try:
        created = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": role, "full_name": full_name},
            # Confirmed on creation, like every other account this app makes: the
            # address is typed by the school admin, and an unconfirmed account
            # cannot sign in at all.
            "email_confirm": True,
        })
        uid = created.user.id

        profile = {
            "id": uid,
            "full_name": full_name,
            "role": role,
            "status": "active",
            "school_id": school_id,
        }
        if phone:
            profile["phone"] = phone
        # Same two fields as a pupil's or a teacher's account, for the same reasons:
        # `profiles.email` is the mirror migration 040 describes (the page printed
        # `o.email` before the column existed, so it always showed a dash), and the
        # generated password is a one-time one that must be replaced on first login.
        profile.update(_password_change.account_fields(email))
        supabase.table("profiles").upsert(profile).execute()
    except Exception as exc:                                  # noqa: BLE001
        _discard(supabase, uid)
        raise OfficialError(
            f"Gagal membuat akun {role}: {str(exc)[:120]}", detail=str(exc)) from exc
    return uid


def update_official(supabase, official_id: str, school_id: str, *,
                    full_name: str | None = None,
                    phone: str | None = None) -> None:
    """Rename one of this school's officials.

    The role is deliberately not editable: it is the difference between the two
    accounts, and a mis-click that turned a vice principal into the principal
    would silently change who may sign in as which.
    """
    _assert_own(supabase, official_id, school_id)
    patch: dict = {}
    if full_name is not None and full_name.strip():
        patch["full_name"] = full_name.strip()
    if phone is not None:
        patch["phone"] = phone.strip()
    if not patch:
        return
    supabase.table("profiles").update(patch).eq("id", official_id).execute()


def delete_official(supabase, official_id: str, school_id: str) -> None:
    """Remove one of this school's officials — the profile and the auth account.

    The order matters: the profile goes first, so a failure between the two leaves
    an auth user with no role rather than a profile pointing at a deleted account.
    """
    _assert_own(supabase, official_id, school_id)
    supabase.table("profiles").delete().eq("id", official_id).execute()
    supabase.auth.admin.delete_user(official_id)


def _assert_own(supabase, official_id: str, school_id: str) -> dict:
    """The row, if it is this school's official. Refuses otherwise.

    Read first and refuse, rather than write and hope: `profiles` holds every role
    in the platform, and the school admin's own id is in this table too — without
    this check, "delete official <id>" would delete whatever row you named.
    """
    if not school_id:
        raise OfficialError("Akun ini tidak terhubung ke sekolah mana pun.")
    try:
        row = _read(supabase, lambda cols: (
            supabase.table("profiles").select(cols)
            .eq("id", official_id).single().execute().data)) or {}
    except Exception as exc:                                  # noqa: BLE001
        raise OfficialError("Akun tidak ditemukan.", detail=str(exc)) from exc
    if not row:
        raise OfficialError("Akun tidak ditemukan.")
    if str(row.get("school_id") or "") != str(school_id):
        raise OfficialError("Akun ini bukan milik sekolah Anda.")
    if row.get("role") not in OFFICIAL_ROLES:
        raise OfficialError("Akun ini bukan pejabat sekolah.")
    return row


def _discard(supabase, uid: str | None) -> None:
    """Undo the auth account a failed create left behind."""
    if not uid:
        return
    try:
        supabase.auth.admin.delete_user(uid)
    except Exception as exc:                                  # noqa: BLE001
        logger.error("could not discard half-made official account %s: %s", uid, exc)
