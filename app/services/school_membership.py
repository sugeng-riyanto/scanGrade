"""One account, many schools: a teacher's membership, and which one is active.

Until migration 044 an account was one `profiles.school_id`. A teacher who taught
at two schools needed two accounts, two emails and two activations, and there was
no way to move between them without signing out and back in. `teacher_school_membership`
separates **identity** (one `profiles` row, one email) from **membership** (many
rows, one per school).

The security half is here too. The backend uses the service key and passes through
RLS, so the tenant boundary is decided in Python, not by the database — which means
the *active* school must be re-checked on every request rather than trusted because
it was chosen once. `resolve_active_school` is that check, and it is the only place
allowed to answer "which school is this request for?".

Roles: only `guru`, `principal` and `vice_principal` may hold more than one
membership. A pupil and a school admin stay single-school through
`profiles.school_id`, unchanged — their behaviour must not shift because a feature
for teachers was added.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Roles that may belong to more than one school.
MEMBERSHIP_ROLES = ("guru", "principal", "vice_principal")

#: The active school is kept server-side, never in the browser, so a client cannot
#: put itself in a school it is not a member of. The key is per session token.
ACTIVE_SCHOOL_KEY = "active_school:{token}"


def is_cross_school_role(role) -> bool:
    return role in MEMBERSHIP_ROLES


def memberships_for(supabase, user_id, status="active") -> list:
    """The user's membership rows, active ones by default."""
    if not user_id:
        return []
    # The whole chain is inside the try, not just `execute()`: supabase-py raises
    # while *building* the request too (and a table the database does not have yet
    # fails at the first `.select()`), so a try that only wrapped the execute would
    # let the exception escape — which is the opposite of "fails closed".
    try:
        query = (supabase.table("teacher_school_membership")
                 .select("id, user_id, school_id, school_role, status, invited_by, joined_at")
                 .eq("user_id", user_id))
        if status:
            query = query.eq("status", status)
        return query.execute().data or []
    except Exception:
        # A database that has not run migration 044 has no table. Returning [] is
        # the same answer as "no membership", which fails closed: the caller falls
        # back to the single `profiles.school_id`, exactly as before this feature.
        logger.debug("membership read failed; treating as no membership", exc_info=True)
        return []


def member_school_ids(supabase, user_id) -> set:
    return {str(m["school_id"]) for m in memberships_for(supabase, user_id)
            if m.get("school_id")}


def is_active_member(supabase, user_id, school_id) -> bool:
    """Whether `user_id` is an active member of `school_id` *right now*.

    Asked on every request that resolves an active school, because a membership
    can be revoked mid-session and a cached answer must not outlive the row.
    """
    if not user_id or not school_id:
        return False
    try:
        rows = (supabase.table("teacher_school_membership")
                .select("id")
                .eq("user_id", user_id)
                .eq("school_id", school_id)
                .eq("status", "active")
                .limit(1).execute().data or [])
    except Exception:
        return False
    return bool(rows)


def resolve_from_memberships(home_school_id, chosen_school_id, member_ids, role) -> str | None:
    """The pure resolution, given the caller's active memberships already in hand.

    Order, and each step is a *narrowing*:

    1. a role that cannot be cross-school keeps its `profiles.school_id` — the
       membership table is never consulted, so pupils and admins behave exactly
       as they did before;
    2. for a cross-school role, the school chosen in this session, **re-verified**
       against an active membership (a revoked membership drops the choice);
    3. otherwise the home school, if it is a real membership.

    Returns `None` when there is nothing valid to resolve, and a caller with no
    school is refused by `require_school_access` rather than defaulted somewhere.

    Kept separate from `resolve_active_school` so a caller that already read the
    memberships (the per-request session apply, which reads them once) does not
    pay two more round-trips to ask the same question again.
    """
    if not is_cross_school_role(role):
        return home_school_id
    ids = {str(s) for s in (member_ids or set())}
    if chosen_school_id and str(chosen_school_id) in ids:
        return chosen_school_id
    if home_school_id and str(home_school_id) in ids:
        return home_school_id
    return None


def resolve_active_school(supabase, user_id, home_school_id, chosen_school_id,
                          role) -> str | None:
    """`resolve_from_memberships`, reading the memberships itself.

    For callers outside a request — the school switcher, the merge tool — where a
    fresh read is the point.
    """
    if not is_cross_school_role(role):
        return home_school_id
    return resolve_from_memberships(home_school_id, chosen_school_id,
                                    member_school_ids(supabase, user_id), role)


def set_active_school(token, school_id) -> None:
    """Remember the chosen school server-side for this session.

    Kept in the same cache the session itself uses, keyed by the token, so the
    choice dies with the session and never travels in a cookie or a form the
    browser can edit.
    """
    if not token:
        return
    from app.utils.kv_cache import cache_set
    cache_set(ACTIVE_SCHOOL_KEY.format(token=token), {"school_id": school_id},
              _ttl())


def get_active_school(token):
    if not token:
        return None
    from app.utils.kv_cache import cache_get
    found = cache_get(ACTIVE_SCHOOL_KEY.format(token=token))
    return (found or {}).get("school_id")


def _ttl():
    from app.utils.auth import _session_ttl
    return _session_ttl()


def invite(supabase, school_id, user_id, role="guru", invited_by=None) -> dict:
    """Offer (or re-offer) a membership, status `invited`.

    `on_conflict` names the `(user_id, school_id)` unique constraint, so inviting
    someone who already holds a row *updates* that row instead of tripping it.
    """
    # The column is `school_role`, not `role`: this is the role the person holds
    # *in this school*, and naming it `role` made the schema gate read it as the
    # application's own role vocabulary (`profiles.role`) — so the gate began
    # refusing every `role == 'murid'` in the codebase as "a name the database
    # cannot hold". Two different questions deserve two different columns.
    res = (supabase.table("teacher_school_membership").upsert({
        "user_id": user_id,
        "school_id": school_id,
        "school_role": role,
        "status": "invited",
        "invited_by": invited_by,
    }, on_conflict="user_id,school_id").execute())
    return (res.data or [{}])[0]


def accept_invite(supabase, user_id, school_id) -> dict:
    """Turn the caller's own invitation into an active membership.

    Scoped to `user_id` in the same query that flips the status: a teacher may
    accept an invitation addressed to them, never activate one addressed to
    somebody else.
    """
    from datetime import datetime, timezone
    res = (supabase.table("teacher_school_membership")
           .update({"status": "active", "joined_at": datetime.now(timezone.utc).isoformat()})
           .eq("user_id", user_id)
           .eq("school_id", school_id)
           .eq("status", "invited")
           .execute())
    return (res.data or [{}])[0]


def deactivate(supabase, school_id, user_id) -> None:
    (supabase.table("teacher_school_membership")
     .update({"status": "inactive"})
     .eq("school_id", school_id)
     .eq("user_id", user_id)
     .execute())
