"""A school and the admin that runs it, created and kept as one unit.

Why this module exists
----------------------
The super-admin schools page could list schools and nothing else. A school could
only be born through the public registration form and its approval; neither the
name, the NPSN, nor the admin's address could be corrected afterwards, and a
school whose admin account was wrong had no door. Meanwhile the admin's *jabatan*
(position) was collected at registration and then dropped — stored on the
registration request and never carried onto the account.

So this is the directory's write side: create a school together with its admin,
edit either half, and **deactivate** a school without deleting it. It reuses
:func:`app.services.account_creation.create_account` for the account, so the
directory inherits the retry, the rollback and the one-time-password rule rather
than spelling out a fourth copy of them.

The two rules that are easy to get wrong
----------------------------------------
* **The account is created after the school, and the school is undone if the
  account fails.** The FK forces that order (``profiles.school_id`` needs a
  school), and leaving the school behind on a failed create would produce exactly
  the orphan the operator was trying to avoid — a school row that runs nothing.
* **Removing a school is a status, never a delete.** ``classes.school_id`` cascades
  on delete and ``profiles.school_id`` is set null, so a real DELETE would take a
  school's classes with it and detach its people. Deactivation keeps every row and
  is reversible.
"""

from __future__ import annotations

import re

from app.services import account_creation
from app.utils.logger import get_logger

logger = get_logger("school_admin")

#: Every reason a write here can refuse, as a key the pages translate.
REFUSALS = (
    "name_required",
    "npsn_required",
    "npsn_taken",
    "email_required",
    "email_invalid",
    "email_taken",
    "not_found",
    "no_admin",
    "write_failed",
)

#: A position is a label, and an operator-facing one: a paragraph pasted into it
#: would follow the person through every screen that names them. Same ceiling as
#: the registration form's own field (``app/routes/auth.py``), so the account the
#: directory edits and the account the form made cannot disagree about the width.
POSITION_MAX_LENGTH = 80

#: Deliberately structural, not RFC-complete — the same shape
#: :mod:`app.services.account_emails` uses, and for the same reason: this rejects a
#: typo, and only a mail server can say whether an address exists.
EMAIL_SHAPE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")

#: The role an `admin_sekolah` is stored under, named once.
ADMIN_ROLE = "admin_sekolah"


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("school_admin: read failed: %s", exc)
        return []


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _email(value) -> str:
    return _text(value).lower()


def _position(value) -> str:
    return _text(value)[:POSITION_MAX_LENGTH]


# ── reading the directory ───────────────────────────────────────────────────

def list_schools(supabase, q: str = "") -> list[dict]:
    """Every school plus the admin that runs it, for the list page.

    The admin's address is read from ``profiles.email`` — the mirror migration 040
    added for exactly this: a directory built in one query instead of paging
    ``auth.admin.list_users()`` fifty accounts at a time.
    """
    schools = _rows(supabase.table("schools")
                    .select("id,name,npsn,status,created_at")
                    .order("name"))
    admins = _admins_by_school(supabase)
    needle = _text(q).lower()
    out = []
    for school in schools:
        admin = admins.get(str(school["id"])) or {}
        row = dict(school)
        row["admin_id"] = admin.get("id")
        row["admin_name"] = admin.get("full_name") or ""
        row["admin_email"] = admin.get("email") or ""
        row["admin_position"] = admin.get("position") or ""
        row["admin_count"] = admin.get("_count", 0)
        if needle and needle not in (row.get("name", "") + row.get("npsn", "")
                                     + row["admin_email"]).lower():
            continue
        out.append(row)
    return out


def _admins_by_school(supabase) -> dict[str, dict]:
    """``{school_id: admin profile}`` — the first admin of each school.

    A school normally has one admin, but nothing in the schema says it cannot have
    two, so the count is kept beside the first: the page can then say "2 admins"
    rather than silently showing one and letting the other be forgotten.
    """
    rows = _rows(supabase.table("profiles")
                 .select("id,school_id,full_name,position,email,status")
                 .eq("role", ADMIN_ROLE))
    out: dict[str, dict] = {}
    for row in rows:
        sid = str(row.get("school_id") or "")
        if not sid:
            continue
        entry = out.setdefault(sid, dict(row, _count=0))
        entry["_count"] += 1
    return out


def find_school(supabase, school_id: str) -> dict | None:
    rows = _rows(supabase.table("schools")
                 .select("id,name,npsn,status").eq("id", school_id))
    return rows[0] if rows else None


def find_admin(supabase, school_id: str) -> dict | None:
    """The school's admin account, or ``None`` when it has none."""
    rows = _rows(supabase.table("profiles")
                 .select("id,school_id,full_name,position,email,status")
                 .eq("school_id", school_id).eq("role", ADMIN_ROLE))
    return rows[0] if rows else None


def npsn_taken(supabase, npsn: str, *, exclude_id: str | None = None) -> bool:
    """Whether another school already owns this NPSN.

    The unique index is what actually holds; this exists so the operator reads a
    sentence instead of a constraint error, and so the check can exclude the school
    being edited.
    """
    rows = _rows(supabase.table("schools").select("id").eq("npsn", npsn))
    return any(str(r["id"]) != str(exclude_id or "") for r in rows)


# ── creating a school with its admin ────────────────────────────────────────

def create_school(supabase, *, name: str, npsn: str, admin_name: str = "",
                  admin_email: str, position: str = "", password: str = "",
                  actor_id: str | None = None) -> dict:
    """Create the school row and its admin account, in that order, as one act.

    Returns ``{"ok": True, "school": …, "admin_id": …, "password": …}``. The
    password is the one that was issued and is the caller's to show **once**; it is
    never stored in plaintext anywhere by this module.

    On any account failure the school row is removed again, so a refused create
    leaves the directory exactly as it was.
    """
    name = _text(name)
    npsn = _text(npsn)
    admin_name = _text(admin_name)
    admin_email = _email(admin_email)

    if not name:
        return {"ok": False, "reason": "name_required"}
    if not npsn:
        return {"ok": False, "reason": "npsn_required"}
    if npsn_taken(supabase, npsn):
        return {"ok": False, "reason": "npsn_taken"}
    if not admin_email:
        return {"ok": False, "reason": "email_required"}
    if not EMAIL_SHAPE.match(admin_email):
        return {"ok": False, "reason": "email_invalid"}

    created = _rows(supabase.table("schools").insert(
        {"name": name, "npsn": npsn, "status": "active"}))
    if not created:
        return {"ok": False, "reason": "write_failed"}
    school = created[0]
    school_id = school["id"]

    try:
        admin_id = account_creation.create_account(
            supabase,
            school_id=school_id,
            role=ADMIN_ROLE,
            full_name=admin_name or name,
            email=admin_email,
            password=password,
            profile_fields={"position": _position(position) or None},
            identifier=admin_email,
        )
    except Exception as exc:                                       # noqa: BLE001
        reason = "email_taken" if _looks_taken(exc) else "write_failed"
        logger.error("school create rolled back (npsn=%s): %s", npsn, exc)
        # Undo the school so a failed create leaves nothing behind.
        try:
            supabase.table("schools").delete().eq("id", school_id).execute()
        except Exception as undo:                                  # noqa: BLE001
            logger.error("could not roll back school %s: %s", school_id, undo)
        return {"ok": False, "reason": reason}

    return {"ok": True, "reason": "", "school": school, "admin_id": admin_id,
            "password": password}


def _looks_taken(exc: Exception) -> bool:
    """Whether GoTrue refused because the address already has an account."""
    text = str(exc).lower()
    return "already" in text or "registered" in text or "exists" in text


# ── editing the two halves ──────────────────────────────────────────────────

def update_school(supabase, school_id: str, *, name: str, npsn: str) -> dict:
    """Rename a school and/or change its NPSN. Never touches its users."""
    name = _text(name)
    npsn = _text(npsn)
    if not find_school(supabase, school_id):
        return {"ok": False, "reason": "not_found"}
    if not name:
        return {"ok": False, "reason": "name_required"}
    if not npsn:
        return {"ok": False, "reason": "npsn_required"}
    if npsn_taken(supabase, npsn, exclude_id=school_id):
        return {"ok": False, "reason": "npsn_taken"}
    written = _rows(supabase.table("schools").update(
        {"name": name, "npsn": npsn}).eq("id", school_id))
    if not written:
        return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "reason": "", "school": written[0]}


def set_active(supabase, school_id: str, active: bool) -> dict:
    """Deactivate or reactivate a school. The only way this module removes one.

    ``classes.school_id`` cascades on delete and ``profiles.school_id`` is nulled,
    so deleting a school would take its classes and detach its people. A status
    keeps everything and is one press to undo.
    """
    if not find_school(supabase, school_id):
        return {"ok": False, "reason": "not_found"}
    status = "active" if active else "inactive"
    written = _rows(supabase.table("schools").update(
        {"status": status}).eq("id", school_id))
    if not written:
        return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "reason": "", "status": status}


def update_admin(supabase, school_id: str, *, email: str | None = None,
                 full_name: str | None = None, position: str | None = None) -> dict:
    """Edit the school's admin: its address (Auth first), its name and its jabatan.

    The address is written to Auth **first** and to ``profiles.email`` after, the
    order :mod:`app.services.account_emails` argues for: Auth is what login and
    reset read, so a stale mirror is a cosmetic problem while the other order would
    leave the directory promising an address that cannot sign in.
    """
    admin = find_admin(supabase, school_id)
    if not admin:
        return {"ok": False, "reason": "no_admin"}
    user_id = admin["id"]

    profile = {}
    if full_name is not None:
        profile["full_name"] = _text(full_name)
    if position is not None:
        profile["position"] = _position(position) or None

    if email is not None:
        new_email = _email(email)
        if not new_email:
            return {"ok": False, "reason": "email_required"}
        if not EMAIL_SHAPE.match(new_email):
            return {"ok": False, "reason": "email_invalid"}
        if new_email != _email(admin.get("email")):
            try:
                supabase.auth.admin.update_user_by_id(
                    user_id, {"email": new_email, "email_confirm": True})
            except Exception as exc:                               # noqa: BLE001
                logger.warning("admin email change refused for %s: %s", user_id, exc)
                return {"ok": False,
                        "reason": "email_taken" if _looks_taken(exc) else "write_failed"}
            profile["email"] = new_email

    if profile:
        written = _rows(supabase.table("profiles").update(profile).eq("id", user_id))
        if not written:
            return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "reason": "", "admin_id": user_id}


def set_position(supabase, user_id: str, position: str) -> dict:
    """Set one account's jabatan — the users/manage page's own door.

    Kept separate from :func:`update_admin` because that one is school-scoped (it
    finds the admin by school) while the user list addresses accounts directly.
    """
    written = _rows(supabase.table("profiles").update(
        {"position": _position(position) or None}).eq("id", user_id))
    if not written:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "reason": "", "position": _position(position)}
