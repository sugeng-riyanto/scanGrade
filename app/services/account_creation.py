"""The one place an account is created: the retry, the rollback, the password rule.

Three services used to create accounts — teachers, students, school officials — and
each spelled out the same three obligations in its own file. They are easy to
state and easy to forget one at a time:

1. **Ride the retry.** GoTrue answers ``Database error creating new user`` when
   its insert raised and rolled back. That is the server hiccupping, not the
   school's data being wrong, and repeating a create that rolled back cannot make
   a second account — so the create goes through
   :func:`app.utils.auth_retry.create_user_with_retry`.

2. **Undo a half-made account.** The write order is forced by the foreign keys
   (``auth user -> profiles -> (teachers|students)``) and the *last* write is the
   one the database can reject. Without the undo, a failure leaves an auth user
   plus a profile with no role row: an account that can sign in and belongs to
   nothing. :func:`discard` deletes the auth user, which cascades.

3. **Stamp the issued password.** The password a school admin sets here is a
   one-time one, so the profile must carry the fields
   :func:`app.services.password_change.account_fields` returns — the account has
   to be told to replace it before any other page opens.

Three copies meant three chances to skip one, and a missing stamp fails silently:
the account simply never asks. So the obligations live here, once, and a creator
supplies only what is specific to its role. A fourth creator copied from an old
one now inherits all three instead of whichever one its source happened to keep.
"""

from __future__ import annotations

from app.services import password_change as _password_change
from app.utils import auth_retry
from app.utils.logger import get_logger

logger = get_logger("account_creation")


def discard(supabase, uid, identifier="") -> None:
    """Best-effort removal of an account whose creation did not finish.

    Deleting the auth user cascades to ``profiles`` and from there to the
    role-specific row, so this is the one call that undoes all of the writes a
    create can have made. Public so the one path that owns an undo owns it for
    every caller — see ``create_account`` below, which is where a creator is meant
    to go: a caller that rolls back by hand is a caller that can forget to.
    """
    if not uid:
        return
    try:
        supabase.auth.admin.delete_user(uid)
        logger.warning("Rolled back a half-created account (identifier=%s)", identifier)
    except Exception as e:  # never mask the original failure
        logger.error("Could not roll back half-created account uid=%s (%s): %s",
                     uid, identifier, e)


def create_account(supabase, *, school_id, role, full_name, email, password,
                   status="active", phone="", profile_fields=None,
                   role_table=None, role_fields=None, identifier=""):
    """Create one account: auth user -> profile -> optional role row.

    ``profile_fields`` carries the role-specific columns that live on ``profiles``
    (a pupil's NISN and class, for instance). ``role_table`` and ``role_fields``
    carry the row that only some roles have (``teachers``, ``students``); an
    official passes neither, because an official's whole record is the profile
    that names the role.

    If any write fails, the auth user is deleted so nothing half-created is left
    behind, and the original exception is re-raised (wrapped by the caller if it
    has a sentence of its own to show).

    Returns the new user's id.
    """
    profile_fields = dict(profile_fields or {})
    uid = None
    try:
        # Retried for the reason app/utils/auth_retry.py spells out: this answer
        # is a hiccup that rolled back, so repeating the create is safe.
        created = auth_retry.create_user_with_retry(
            lambda: supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "user_metadata": {"role": role, "full_name": full_name},
                "email_confirm": True,
            }))
        uid = created.user.id

        profile = {
            "id": uid, "full_name": full_name, "role": role,
            "status": status, "school_id": school_id,
        }
        if phone:
            profile["phone"] = phone
        profile.update(profile_fields)
        # The generated password is a one-time one: see
        # app/services/password_change.py for the rule it is held to.
        profile.update(_password_change.account_fields(email))
        supabase.table("profiles").upsert(profile).execute()

        if role_table:
            row = {"id": uid, "school_id": school_id}
            row.update(role_fields or {})
            supabase.table(role_table).upsert(row).execute()
    except Exception:
        discard(supabase, uid, identifier)
        raise
    return uid
