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


#: Roles that may DECIDE a join request (approve or reject) in the destination
#: school. Read side by side with `admin_sekolah_required` on purpose: approving a
#: new request is a decision THREE roles may take, and one is enough.
APPROVER_ROLES = ("admin_sekolah", "principal", "vice_principal")

#: Roles that may REOPEN a closed membership. ONE role — and that asymmetry is the
#: product decision, not an oversight: approving a new request is academic, while
#: reopening a membership that was closed is administrative and is kept to the
#: school admin alone. `reopen_membership` enforces it at the service, so the rule
#: holds for every caller and not only for the route that remembers a decorator.
REOPEN_ROLES = ("admin_sekolah",)

#: The single canonical status for ANY closure, since migration 064. The 044 value
#: `inactive` is migrated to this and is no longer written by any code path — a
#: second name for one state is how a page ends up showing two kinds of closure.
CLOSED_STATUS = "closed"


def may_approve(role) -> bool:
    return role in APPROVER_ROLES


def may_reopen(role) -> bool:
    return role in REOPEN_ROLES


def deactivate(supabase, school_id, user_id, actor_id=None) -> dict:
    """Close a membership: the canonical closure, `closed`, never `inactive`.

    `closed_by`/`closed_at` are written so the closure has an author — a closure
    with no author is the record a school cannot act on. Scoped to the
    `(school, user)` pair, so a caller cannot close a membership in another school
    by passing an id alone.
    """
    from datetime import datetime, timezone
    res = (supabase.table("teacher_school_membership")
           .update({"status": CLOSED_STATUS,
                    "closed_by": actor_id,
                    "closed_at": datetime.now(timezone.utc).isoformat()})
           .eq("school_id", school_id)
           .eq("user_id", user_id)
           .execute())
    return (res.data or [{}])[0]


def reopen_membership(supabase, school_id, user_id, actor_id=None,
                      actor_role=None) -> dict:
    """Reopen a closed membership — and refuse anyone but `REOPEN_ROLES`.

    The role is checked HERE, not only in the route. A route decorator protects the
    one door it is written on; this function protects every caller, including a
    future one that forgets the decorator. The refusal happens BEFORE any write, so
    an unauthorised attempt leaves no row changed and no field cleared.

    The closure's own history (`closed_by`/`closed_at`) is KEPT: reopening is a new
    fact, not an erasure of the old one.
    """
    from datetime import datetime, timezone
    if not may_reopen(actor_role):
        return {"ok": False, "reason": "not_authorised"}
    res = (supabase.table("teacher_school_membership")
           .update({"status": "active",
                    "reopened_by": actor_id,
                    "reopened_at": datetime.now(timezone.utc).isoformat()})
           .eq("school_id", school_id)
           .eq("user_id", user_id)
           .eq("status", CLOSED_STATUS)
           .execute())
    rows = res.data or []
    if not rows:
        return {"ok": False, "reason": "not_closed_or_missing"}
    return {"ok": True, "membership": rows[0]}


def grant_membership(supabase, school_id, user_id, actor_id=None,
                     role="guru") -> dict:
    """Give an active membership — what an approved request amounts to.

    Upserts on the `(user_id, school_id)` pair, so a teacher who re-applies and is
    approved again re-activates their existing row instead of creating a second one
    for the same pair.
    """
    from datetime import datetime, timezone
    res = (supabase.table("teacher_school_membership").upsert({
        "user_id": user_id,
        "school_id": school_id,
        "school_role": role,
        "status": "active",
        "invited_by": actor_id,
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id,school_id").execute())
    return (res.data or [{}])[0]


def decide_request(supabase, school_id, user_id_request, decision,
                   actor_id=None, actor_role=None, reason=None) -> dict:
    """Approve or reject a join request — in the destination school only.

    Three checks, each a refusal BEFORE any write:

    1. the caller's role may approve at all (`APPROVER_ROLES`);
    2. the request exists AND its `target_school_id` is the caller's own school —
       a request in another school reads as **not found**, not as forbidden, so
       the caller cannot even learn that it exists;
    3. the request is still `pending` — an already-decided request is not decided
       twice.

    Approval also grants the membership in the same call, so a request can never
    read `approved` while the access it promised does not exist.
    """
    from datetime import datetime, timezone
    if not may_approve(actor_role):
        return {"ok": False, "reason": "not_authorised"}
    if decision not in ("approved", "rejected"):
        return {"ok": False, "reason": "bad_decision"}
    try:
        rows = (supabase.table("school_membership_request")
                .select("id,teacher_id,target_school_id,status")
                .eq("id", user_id_request)
                .eq("target_school_id", school_id)
                .eq("status", "pending")
                .limit(1).execute().data or [])
    except Exception:
        logger.debug("request read failed; refusing", exc_info=True)
        return {"ok": False, "reason": "read_failed"}
    if not rows:
        return {"ok": False, "reason": "not_found"}
    row = rows[0]
    (supabase.table("school_membership_request")
     .update({"status": decision,
              "decided_by": actor_id,
              "decided_role": actor_role,
              "decision_reason": reason,
              "decided_at": datetime.now(timezone.utc).isoformat()})
     .eq("id", row["id"])
     .eq("target_school_id", school_id)
     .eq("status", "pending")
     .execute())
    if decision == "approved":
        grant_membership(supabase, school_id, row["teacher_id"], actor_id=actor_id)
    return {"ok": True, "request_id": row["id"], "decision": decision}
