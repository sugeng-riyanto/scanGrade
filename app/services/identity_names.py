"""Keep the display name one value, not two.

A person's name lives in two places: ``profiles.full_name`` — the row the school
reads, edits and joins against — and ``auth.users.user_metadata.full_name``, which
is what the account was *created* with. Every creation path writes both, and then
every rename updates only the first. So an account renamed after it was made (or
created from a pasted message, as one vice principal's was) keeps the old value in
Auth forever, and any reader that trusts Auth shows the stale name.

The session no longer trusts Auth (`app/utils/auth.py` reads the profile first),
but the copy still drifts — and drift is how the glitch reappeared the first time.
This module closes the loop: a rename mirrors the new name into Auth, merging into
the existing metadata so the role and everything else there survives.

Best-effort on purpose. The profile write is the one the school asked for and the
one every page reads; a refusal from the Auth service must not fail it. The
refusal is logged, and the repair script (`deploy/repair_identity_names.py`) can
sweep up whatever a failed mirror left behind.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def mirror_display_name(supabase, user_id: str, full_name: str) -> bool:
    """Write ``full_name`` into the Auth metadata of ``user_id``.

    Returns ``True`` only when a write happened. A blank name, an unchanged name,
    or a refusal from the Auth service all return ``False`` and change nothing —
    the first two deliberately, the last because the caller has already done the
    write that matters and must not fail over a copy.
    """
    name = (full_name or "").strip()
    if not user_id or not name:
        return False
    try:
        admin = supabase.auth.admin
        current: dict = {}
        try:
            got = admin.get_user_by_id(user_id)
            user = getattr(got, "user", None) or got
            current = dict(getattr(user, "user_metadata", {}) or {})
        except Exception:
            # A read that fails is not a reason to stop: the write below carries
            # only the name, and GoTrue merges it into whatever is already there.
            current = {}
        if current.get("full_name") == name:
            return False
        current["full_name"] = name
        admin.update_user_by_id(user_id, {"user_metadata": current})
        return True
    except Exception as exc:  # noqa: BLE001 — the caller's write already happened
        logger.warning("could not mirror display name for %s: %s", user_id, exc)
        return False
