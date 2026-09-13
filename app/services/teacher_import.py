"""Teacher account creation — one place that knows the write order and its undo.

Mirrors ``app/services/student_import.py``. The order is forced by foreign keys
(``teachers.id -> profiles.id -> auth.users.id``) and the *last* write is the one
that can be rejected, so a failure used to leave an auth user plus a profile with
no ``teachers`` row: an account that can sign in but is not a teacher anywhere.

Two traps this module exists to close, both measured against the live database:

1. ``teachers.employee_id`` has **no** unique index. The only unique constraints
   are ``teachers_pkey (id)`` and ``teachers_nuptk_key (nuptk)``:

   ==========================================  ====================
   column                                      constrained?
   ==========================================  ====================
   ``teachers.nuptk``                          UNIQUE, but written by nobody
   ``teachers.employee_id``                    not constrained at all
   ==========================================  ====================

   So the database cannot reject a duplicate NIP, and the check has to happen in
   code. It matters beyond tidiness: ``auth.login`` resolves a non-email login
   with ``.eq("employee_id", login_input).limit(1)``, so two teachers sharing a
   NIP silently make one of them unable to sign in with it.

2. ``teachers`` has **no** ``status`` column, while ``profiles`` does. Writing
   ``status`` to the teachers row is rejected before the row is created::

       42703  column teachers.status does not exist
       PGRST204  Could not find the 'status' column of 'teachers' in the schema cache

   That is why the single-teacher form created the account and then failed.
"""

from app.utils.logger import get_logger
from app.errors import ValidationError

logger = get_logger("teacher_import")


def _clean(value):
    return str(value or "").strip()


def find_teacher_by_employee_id(supabase, employee_id):
    """Look up a NIP/employee id across the **whole** database, not one school.

    Returns the existing row, or ``None``. ``employee_id`` carries no unique
    index, so this is the only check a duplicate NIP can hit.

    ``.limit(1)`` rather than ``.maybe_single()``: ``maybe_single`` raises when a
    query matches more than one row, and this runs before every teacher insert.
    """
    value = _clean(employee_id)
    if not value:
        return None
    rows = (
        supabase.table("teachers").select("id, school_id, employee_id")
        .eq("employee_id", value).limit(1).execute().data or []
    )
    return rows[0] if rows else None


def find_teacher_by_nuptk(supabase, nuptk):
    """Look up a NUPTK globally. ``teachers.nuptk`` is ``TEXT UNIQUE``.

    No importer writes this column yet, so today it always resolves to ``None``
    — but the constraint is real, and a future sheet that carries NUPTK would
    otherwise fail at the index after the account already existed.
    """
    value = _clean(nuptk)
    if not value:
        return None
    rows = (
        supabase.table("teachers").select("id, school_id, nuptk")
        .eq("nuptk", value).limit(1).execute().data or []
    )
    return rows[0] if rows else None


def discard_partial_account(supabase, uid, identifier):
    """Best-effort removal of an account whose creation did not finish.

    Deleting the auth user cascades to ``profiles`` and from there to
    ``teachers``, so this is the one call that undoes all three writes.
    """
    if not uid:
        return
    try:
        supabase.auth.admin.delete_user(uid)
        logger.warning("Rolled back a half-created teacher account (nip=%s)", identifier)
    except Exception as e:  # never mask the original failure
        logger.error("Could not roll back half-created teacher uid=%s nip=%s: %s",
                     uid, identifier, e)


def _reject_if_taken(existing, field, value, school_id):
    if not existing:
        return
    where = "sekolah ini" if str(existing.get("school_id")) == str(school_id) \
        else "sekolah lain"
    raise ValidationError(field, f"{field} {value} sudah terdaftar di {where}")


def create_teacher_account(supabase, *, school_id, full_name, email, password,
                           employee_id="", nuptk=None, subject_id=None,
                           phone="", status="active"):
    """Create one teacher account: auth user -> profiles -> teachers.

    Checks the NIP and NUPTK against the whole database first, and if any of the
    three writes still fails, deletes the auth user so nothing half-created is
    left behind.

    ``status`` goes to ``profiles`` only — ``teachers`` has no such column (see
    the module docstring), and writing it there is what broke the add-teacher
    form.

    Raises ``ValidationError`` when the NIP or NUPTK is already taken.
    """
    employee_id = _clean(employee_id)
    nuptk = _clean(nuptk) or None

    _reject_if_taken(
        find_teacher_by_employee_id(supabase, employee_id), "NIP", employee_id, school_id)
    _reject_if_taken(
        find_teacher_by_nuptk(supabase, nuptk), "NUPTK", nuptk, school_id)

    uid = None
    try:
        created = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": "guru", "full_name": full_name},
            "email_confirm": True,
        })
        uid = created.user.id

        profile = {
            "id": uid, "full_name": full_name, "role": "guru",
            "status": status, "school_id": school_id,
        }
        if phone:
            profile["phone"] = phone
        supabase.table("profiles").upsert(profile).execute()

        # No `status` key here on purpose: teachers.status does not exist.
        teacher = {
            "id": uid, "school_id": school_id,
            "employee_id": employee_id, "subject_id": subject_id,
        }
        if nuptk:
            teacher["nuptk"] = nuptk
        supabase.table("teachers").upsert(teacher).execute()
    except Exception:
        discard_partial_account(supabase, uid, employee_id)
        raise
    return uid
