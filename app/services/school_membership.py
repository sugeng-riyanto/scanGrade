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

import hashlib
import logging

logger = logging.getLogger(__name__)


def _text(value) -> str:
    return str(value if value is not None else "").strip()

#: Roles that may belong to more than one school.
MEMBERSHIP_ROLES = ("guru", "principal", "vice_principal")

#: The active school is kept server-side, never in the browser, so a client cannot
#: put itself in a school it is not a member of. The key is per session token.
ACTIVE_SCHOOL_KEY = "active_school:{token}"

#: The tables this module owns, named once. A literal typed at each call site is a
#: literal that can be misspelled into an empty answer: every read here treats a
#: failing query as "no rows", so a typo reads as "this school has no classes" or
#: "this teacher has no memberships" instead of as the error it is.
MEMBERSHIPS_TABLE = "teacher_school_membership"
REQUESTS_TABLE = "school_membership_request"
CONSENT_TABLE = "membership_consent_log"
SCHOOLS_TABLE = "schools"
CLASSES_TABLE = "classes"

#: A hanging request expires after this many days unless the caller names another
#: number. The deadline is written onto the row when the request is made, so a policy
#: change never moves the deadline of a request already in flight.
DEFAULT_REQUEST_DAYS = 30

#: The consent document a request is made under, and the exact text of it. The version
#: answers "which document", the hash answers "which text" — both go to
#: `membership_consent_log`, because a document edited without its version being
#: raised is precisely the case that has to be provable.
#:
#: The two halves are the copy the page renders, and `consent_sha256()` hashes both of
#: them in this order. That is what makes the recorded hash a fact about what the
#: teacher was shown rather than a literal somebody typed twice: the page prints these
#: strings, and a guard pins that it prints them from here instead of restating them.
CONSENT_DOCUMENT_VERSION = "membership-cross-school-2026-10"
CONSENT_TEXT_ID = (
    "Guru dan pejabat sekolah dapat menjadi anggota lebih dari satu sekolah, dan "
    "sekolah yang saya tuju hanya menerima nama, email, serta status akun saya — "
    "bukan daftar sekolah tempat saya mengajar. Saya menyetujui hal itu."
)
CONSENT_TEXT_EN = (
    "Teachers and school officials may belong to more than one school, and the "
    "school I am applying to receives my name, my email and my account status only "
    "— never the list of schools I teach in. I agree to that."
)


def consent_texts() -> tuple[str, str]:
    """The consent copy exactly as the page must render it: `(Indonesian, English)`."""
    return CONSENT_TEXT_ID, CONSENT_TEXT_EN


def consent_sha256() -> str:
    """SHA-256 of the consent text as shipped, both halves in this order.

    Computed here rather than written down: a hash typed into a file stops matching
    the moment the text is edited, which is the defect the column exists to catch.
    """
    ind, eng = consent_texts()
    return hashlib.sha256(f"{ind}\n{eng}".encode("utf-8")).hexdigest()


def consent_is_current(version, digest) -> bool:
    """Whether a posted consent is the *current* document, not a remembered one.

    The version is compared exactly and the digest against the shipped text, so a page
    still showing last month's wording cannot consent to today's — and a client cannot
    invent a hash for a document it was never shown.
    """
    return (_text(version) == CONSENT_DOCUMENT_VERSION
            and _text(digest) == consent_sha256())


def is_cross_school_role(role) -> bool:
    return role in MEMBERSHIP_ROLES


def memberships_for(supabase, user_id, status="active") -> list:
    """The user's membership rows, active ones by default.

    `status=None` (or `""`) drops the filter, which is the *display* read: a page
    that lists a school's members wants closed rows too, and they are the ones that
    show a reopen control. No question of ACCESS is answered here — `is_active_member`
    and `member_school_ids` name `"active"` in the query, and `membership_rows`
    returns the rows for `resolve_from_memberships` to classify — because "which rows
    exist" and "which school may this request read" are different questions, and only
    one of them is safely answered by an unfiltered read.
    """
    if not user_id:
        return []
    # The whole chain is inside the try, not just `execute()`: supabase-py raises
    # while *building* the request too (and a table the database does not have yet
    # fails at the first `.select()`), so a try that only wrapped the execute would
    # let the exception escape — which is the opposite of "fails closed".
    try:
        query = (supabase.table(MEMBERSHIPS_TABLE)
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
        rows = (supabase.table(MEMBERSHIPS_TABLE)
                .select("id")
                .eq("user_id", user_id)
                .eq("school_id", school_id)
                .eq("status", "active")
                .limit(1).execute().data or [])
    except Exception:
        return False
    return bool(rows)


def membership_rows(supabase, user_id) -> list[dict] | None:
    """Every membership row this user has — or `None` when it could not be read.

    The `None` is the point of the function, and it is why the rows are returned
    rather than a convenient set of active ids. Two situations a plain `[]` would
    merge are opposite, and the difference is a closure versus an outage:

    * **read succeeded, a row says `closed`** — the account held this school and no
      longer does;
    * **read failed** — nothing is known, and dropping a teacher out of the school
      their profile names because the database hiccuped is the worse failure.

    All rows, and not only the active ones, because "no row at all" and "a row that
    is closed" are different answers about the home school: the first is a school
    nobody ever recorded a membership for (every teacher created after migration 044
    looks like this), the second is a membership somebody closed.
    """
    if not user_id:
        return []
    try:
        return (supabase.table(MEMBERSHIPS_TABLE).select("school_id,status")
                .eq("user_id", user_id).execute().data or [])
    except Exception:
        logger.debug("membership read failed; narrowing nothing", exc_info=True)
        return None


def resolve_from_memberships(home_school_id, chosen_school_id, rows, role) -> str | None:
    """The pure resolution, given the caller's membership rows already in hand.

    Order, and each step is a *narrowing*:

    1. a role that cannot be cross-school keeps its `profiles.school_id` — the
       membership table is never consulted, so pupils and admins behave exactly as
       they did before;
    2. `rows is None` (the read failed) also keeps the home school: an outage may
       not take a school away from anybody;
    3. the school chosen in this session, **re-verified** against an active row — a
       closed membership drops the choice on the very next request;
    4. the home school, when it is active **or has no row at all**. That second
       clause is load-bearing and was nearly a lockout: only a row that exists and
       is not active is a closure, so a teacher whose membership was never recorded
       (any account created after migration 044) keeps working;
    5. otherwise any other active membership, in a **sorted** order so two requests
       cannot disagree about which school that teacher is in.

    Returns `None` when there is nothing valid left, and a caller with no school is
    refused by `require_school_access` rather than defaulted somewhere.

    One function for every caller — the request path (`resolve_for_request`) and the
    switcher (`resolve_active_school`) — because two copies of this order is how a
    page ends up disagreeing with the server about which school a request is for.
    """
    if not is_cross_school_role(role):
        return home_school_id
    if rows is None:
        return home_school_id
    active = {str(r.get("school_id")) for r in rows
              if str(r.get("status") or "") == "active"}
    known = {str(r.get("school_id")) for r in rows}
    home = str(home_school_id) if home_school_id else ""
    if chosen_school_id and str(chosen_school_id) in active:
        return chosen_school_id
    if home and (home in active or home not in known):
        return home_school_id
    return sorted(active)[0] if active else None


def resolve_active_school(supabase, user_id, home_school_id, chosen_school_id,
                          role) -> str | None:
    """`resolve_from_memberships`, reading the memberships itself.

    For callers outside a request — the school switcher, the merge tool — where a
    fresh read is the point.
    """
    if not is_cross_school_role(role):
        return home_school_id
    return resolve_from_memberships(home_school_id, chosen_school_id,
                                    membership_rows(supabase, user_id), role)


def resolve_for_request(token, user_id, home_school_id, role, supabase=None) -> str | None:
    """Which school is THIS request for? The single answer the request path uses.

    Every authenticated request resolves its school here (`app.utils.auth`), and the
    shape of the function is what makes a closure total rather than eventual:

    * a role that cannot be cross-school is returned untouched, so pupils and school
      admins behave exactly as before and pay nothing;
    * the memberships are read **fresh** — once per request, memoised — so a closed
      row is honoured on the very next request instead of at the end of the session
      cache's TTL. The session cache holds the *profile*, whose `school_id` is the
      home school; it never holds the resolved answer, so there is no cached school
      to outlive its row;
    * the chosen school (kept server-side, per token) is only honoured while an
      active row still names it, and a memo that outlived its row is **cleared**
      here — the read path is the only place that sees both the token and the row,
      so it is the only place that can drop the one and keep the other honest.
    """
    if not is_cross_school_role(role):
        return home_school_id
    from app.utils.req_cache import memo
    rows = memo(f"membership:rows:{user_id}",
                lambda: membership_rows(supabase or _client(), user_id))
    chosen = get_active_school(token)
    if chosen and rows is not None and not _still_active(rows, chosen):
        # The choice pointed at a school this account is no longer an active member
        # of. Dropping it here keeps the next request from re-reading a decision that
        # has already been refused — and the read path is the only place that can,
        # because it is the only place holding both the token and the row.
        clear_active_school(token)
        chosen = None
    return resolve_from_memberships(home_school_id, chosen, rows, role)


def _still_active(rows, school_id) -> bool:
    """Whether `rows` holds an ACTIVE membership in `school_id`."""
    wanted = str(school_id)
    return any(str(r.get("school_id")) == wanted
               and str(r.get("status") or "") == "active" for r in rows)


def _client():
    from app.utils.auth import get_supabase
    return get_supabase()


def clear_active_school(token) -> None:
    """Forget the chosen school for this session (a closure drops it, so does logout)."""
    if not token:
        return
    from app.utils.kv_cache import cache_delete
    cache_delete(ACTIVE_SCHOOL_KEY.format(token=token))


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


# ── the destination school: how a teacher finds one ─────────────────────────

#: The stages a grade belongs to, as ranges. Indonesian schools write grades as
#: `1..12` (or `I..XII`), and the stage a school serves is the set of grades its
#: classes carry — so this is the whole vocabulary, and it is stated here rather than
#: read off the school's *name*, which is a label somebody typed.
JENJANG_BY_GRADE = ((1, 6, "SD"), (7, 9, "SMP"), (10, 12, "SMA"))


def jenjang_label(grade_levels) -> str | None:
    """The school stage its classes imply, or `None` when nothing implies one.

    `None` is a real state and not a mistake: a school whose classes have not been
    registered yet has no stage to show, and the page says so in words rather than
    dropping the school from the results. A grade outside 1..12 is reported as itself
    instead of counted as "unknown", so an unusual school still answers.
    """
    from app.services import enrollment

    numbers = set()
    for label in grade_levels or []:
        number = enrollment.grade_number(label)
        if number > 0:
            numbers.add(number)
    if not numbers:
        return None
    stages = [name for low, high, name in JENJANG_BY_GRADE
              if any(low <= n <= high for n in numbers)]
    return "+".join(stages) if stages else str(max(numbers))


def school_search(supabase, q="", member_ids=None, limit=25) -> list[dict]:
    """Schools a teacher may apply to, each with the stage its classes imply.

    Deliberately TWO reads and a join in Python rather than an embedded `classes(...)`
    select, and the reason is the rule this function exists to keep: **a school with no
    registered classes must still appear in the results**, with an empty stage. An
    embed that answers "no classes" as "no row" hides exactly the school a new
    destination is likeliest to be, and a hidden school cannot be applied to.

    The failure direction is the same one: a classes read that fails leaves every
    school listed with no stage — a page missing a *stage* is a page somebody can still
    use, a page missing a *school* is not. `member` is a flag rather than a filter, for
    the same reason: a school this teacher already belongs to is shown as such instead
    of disappearing from a search they may be running to check exactly that.
    """
    try:
        schools = (supabase.table(SCHOOLS_TABLE)
                   .select("id,name,npsn,city,status")
                   .order("name").execute().data or [])
    except Exception:
        logger.debug("school read failed; reporting no results", exc_info=True)
        return []
    try:
        classes = (supabase.table(CLASSES_TABLE)
                   .select("school_id,grade_level").execute().data or [])
    except Exception:
        logger.debug("class read failed; every school loses its stage only",
                     exc_info=True)
        classes = []
    grades: dict[str, list] = {}
    for row in classes:
        grades.setdefault(_text(row.get("school_id")), []).append(
            row.get("grade_level"))
    members = {str(s) for s in (member_ids or set())}
    needle = _text(q).lower()
    out = []
    for school in schools:
        sid = _text(school.get("id"))
        row = {
            "id": sid,
            "name": _text(school.get("name")),
            "npsn": _text(school.get("npsn")),
            "city": _text(school.get("city")),
            "status": _text(school.get("status") or "active"),
            "jenjang": jenjang_label(grades.get(sid)),
            "member": sid in members,
        }
        if needle and needle not in (row["name"] + row["npsn"] + row["city"]).lower():
            continue
        out.append(row)
    return out[:limit] if limit else out


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
    res = (supabase.table(MEMBERSHIPS_TABLE).upsert({
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
    res = (supabase.table(MEMBERSHIPS_TABLE)
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
    res = (supabase.table(MEMBERSHIPS_TABLE)
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
    res = (supabase.table(MEMBERSHIPS_TABLE)
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

    Upserts on the `(user_id, school_id)` pair, so a teacher who is approved again
    re-activates their existing row instead of getting a second one for the same pair.

    A **closed** row is never raised here: an upsert that flips `closed` back to
    `active` is a reopening in disguise, and reopening is the school admin's alone.
    The refusal is the reason this function returns a verdict rather than a row — a
    caller that ignored the shape would silently grant what the product said only one
    role may grant.
    """
    from datetime import datetime, timezone
    state, status = _existing_status(supabase, school_id, user_id)
    if state == "known" and status == CLOSED_STATUS:
        return {"ok": False, "reason": "membership_closed"}
    if state == "error":
        # Granting from a row we could not read could overwrite a closure.
        return {"ok": False, "reason": "read_failed"}
    res = (supabase.table(MEMBERSHIPS_TABLE).upsert({
        "user_id": user_id,
        "school_id": school_id,
        "school_role": role,
        "status": "active",
        "invited_by": actor_id,
        "joined_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id,school_id").execute())
    return {"ok": True, "membership": (res.data or [{}])[0]}


# ── permintaan bergabung: who writes one, and what a write refuses ───────────

ALLOWED_REQUEST_STATUSES = ("pending", "approved", "rejected", "cancelled",
                            "expired")


def _existing_status(supabase, school_id, user_id) -> tuple[str, str | None]:
    """`(state, status)` for this teacher's row in this school.

    Three answers, because two of them are not the same fact: `known` (a row, with its
    status), `missing` (no row at all — a first application, which is allowed), and
    `error` (the read failed). The callers decide what an unreadable row means, and
    they decide differently on purpose: a teacher may still apply when their row cannot
    be read, but an approval never *grants* from an unreadable row, because "cannot
    read" and "not closed" are different states and only one of them is safe to grant
    on.
    """
    try:
        rows = (supabase.table(MEMBERSHIPS_TABLE).select("status")
                .eq("school_id", school_id).eq("user_id", user_id)
                .limit(1).execute().data or [])
    except Exception:
        logger.debug("membership read failed; state unknown", exc_info=True)
        return "error", None
    if not rows:
        return "missing", None
    return "known", _text(rows[0].get("status"))


def _request_days(days) -> int:
    """The deadline in days: the caller's number when it is a usable one.

    A missing, unparseable or non-positive number falls back to the default rather
    than raising: this is a policy value, and a bad policy is not a reason to lose the
    request a teacher just wrote.
    """
    try:
        number = int(days)
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_DAYS
    return number if number >= 1 else DEFAULT_REQUEST_DAYS


def create_request(supabase, user_id, target_school_id, role, *,
                   document_version=None, document_sha256=None,
                   days=None) -> dict:
    """A teacher asks to join another school. The only writer of a request row.

    Every refusal happens BEFORE a row is written, and they are ordered so the answer
    is about the most useful fact first: a role that cannot hold two memberships at all
    (a pupil, an admin) is told that rather than being told its consent is stale.

    A membership this school **closed** cannot be re-applied for. `closed` is answered
    with `membership_closed` because the way back is the school admin's `reopen` and
    not a new application — without that check a principal could undo an admin's
    offboarding by approving a fresh request, and the asymmetry the product decided on
    would be decorative.

    The consent is compared against the *shipped* document, so a page still showing
    last month's wording cannot consent to today's on the teacher's behalf.
    """
    from datetime import datetime, timedelta, timezone

    if not is_cross_school_role(role):
        return {"ok": False, "reason": "not_authorised"}
    target = _text(target_school_id)
    if not target:
        return {"ok": False, "reason": "bad_target"}
    if not consent_is_current(document_version, document_sha256):
        return {"ok": False, "reason": "consent_required"}
    if target in member_school_ids(supabase, user_id):
        return {"ok": False, "reason": "already_member"}
    state, status = _existing_status(supabase, target, user_id)
    if state == "known" and status == CLOSED_STATUS:
        return {"ok": False, "reason": "membership_closed"}
    if state == "error":
        # Unknown is not a licence to write. A request that could never be approved
        # (because the approval would have to guess) is not created at all.
        return {"ok": False, "reason": "read_failed"}
    try:
        school = (supabase.table(SCHOOLS_TABLE).select("id,status")
                  .eq("id", target).limit(1).execute().data or [])
    except Exception:
        logger.debug("target school read failed; refusing", exc_info=True)
        return {"ok": False, "reason": "read_failed"}
    if not school:
        return {"ok": False, "reason": "bad_target"}
    if _text(school[0].get("status") or "active") != "active":
        return {"ok": False, "reason": "school_inactive"}
    try:
        hanging = (supabase.table(REQUESTS_TABLE).select("id")
                   .eq("teacher_id", user_id).eq("target_school_id", target)
                   .eq("status", "pending").limit(1).execute().data or [])
    except Exception:
        logger.debug("pending read failed; refusing", exc_info=True)
        return {"ok": False, "reason": "read_failed"}
    if hanging:
        return {"ok": False, "reason": "already_pending"}

    expires = datetime.now(timezone.utc) + timedelta(days=_request_days(days))
    created = (supabase.table(REQUESTS_TABLE).insert({
        "teacher_id": user_id,
        "target_school_id": target,
        "status": "pending",
        "expires_at": expires.isoformat(),
    }).execute().data or [{}])[0]
    request_id = created.get("id")
    # The consent row is written WITH the request, never instead of it: a request that
    # exists without the record of what was agreed to is the one combination a UU PDP
    # audit cannot accept — and the one nothing would notice.
    (supabase.table(CONSENT_TABLE).insert({
        "request_id": request_id,
        "teacher_id": user_id,
        "document_version": CONSENT_DOCUMENT_VERSION,
        "document_sha256": consent_sha256(),
    }).execute())
    return {"ok": True, "request_id": request_id, "expires_at": expires.isoformat()}


def _teachers_by_id(supabase, ids) -> dict:
    """`{teacher_id: profile}` for the ids given — and for nothing else.

    `profiles` and no other table: the destination school learns the name, the address
    and the account status of whoever applied, and there is no membership read here to
    leak which other schools they work in.
    """
    wanted = [str(i) for i in dict.fromkeys(str(i) for i in (ids or []) if i)]
    if not wanted:
        return {}
    try:
        rows = (supabase.table("profiles")
                .select("id,full_name,email,status").in_("id", wanted)
                .execute().data or [])
    except Exception:
        logger.debug("teacher read failed; the queue keeps its rows", exc_info=True)
        return {}
    return {_text(row.get("id")): row for row in rows}


def school_members(supabase, school_id, role) -> dict:
    """The memberships of ONE school, for the page that closes and reopens them.

    Refused to every role that may not decide, for the same reason the queue is:
    this is the list of who works here, and a teacher has no business enumerating
    it through an endpoint.

    Closed rows are included on purpose — a closed membership is what the reopen
    control acts on, and a page that only showed `active` would have nothing to
    reopen. The memberships are read with no filter and the *page* labels them,
    which is why the query deliberately does not say `status="active"`: this is the
    display read, and it is never an access decision.
    """
    if not may_approve(role):
        return {"ok": False, "reason": "not_authorised"}
    school = _text(school_id)
    if not school:
        return {"ok": False, "reason": "no_school"}
    try:
        rows = (supabase.table(MEMBERSHIPS_TABLE)
                .select("id,user_id,school_role,status,joined_at,closed_at,closed_by,"
                        "reopened_at")
                .eq("school_id", school).order("joined_at", desc=True)
                .execute().data or [])
    except Exception:
        logger.debug("member read failed; reporting empty", exc_info=True)
        return {"ok": True, "members": []}
    people = _teachers_by_id(supabase, [row.get("user_id") for row in rows])
    out = []
    for row in rows:
        person = people.get(_text(row.get("user_id"))) or {}
        out.append({
            "user_id": row.get("user_id"),
            "name": _text(person.get("full_name")),
            "email": _text(person.get("email")),
            "school_role": _text(row.get("school_role")),
            "status": _text(row.get("status")),
            "joined_at": row.get("joined_at"),
            "closed_at": row.get("closed_at"),
            "reopened_at": row.get("reopened_at"),
            "closed": _text(row.get("status")) == CLOSED_STATUS,
        })
    return {"ok": True, "members": out}


def pending_requests(supabase, school_id, role) -> dict:
    """The destination school's queue, newest first, with the applicant's own details.

    Approve and reject share one door (`decide_request`); this is the *reading* that
    door is used from, and it is refused to every role that may not decide, so a
    teacher cannot use it to learn who else applied to a school.

    A request whose profile cannot be read is still listed, with empty fields: the
    application exists, and dropping it would make the queue quietly disagree with the
    rows the school has to answer. What is never read is any other membership.
    """
    if not may_approve(role):
        return {"ok": False, "reason": "not_authorised"}
    school = _text(school_id)
    if not school:
        return {"ok": False, "reason": "no_school"}
    try:
        rows = (supabase.table(REQUESTS_TABLE)
                .select("id,teacher_id,created_at,expires_at")
                .eq("target_school_id", school).eq("status", "pending")
                .order("created_at").execute().data or [])
    except Exception:
        logger.debug("request queue read failed; reporting empty", exc_info=True)
        return {"ok": True, "requests": []}
    people = _teachers_by_id(supabase, [row.get("teacher_id") for row in rows])
    out = []
    for row in rows:
        person = people.get(_text(row.get("teacher_id"))) or {}
        out.append({
            "request_id": row.get("id"),
            "teacher_id": row.get("teacher_id"),
            "name": _text(person.get("full_name")),
            "email": _text(person.get("email")),
            "account_status": _text(person.get("status")),
            "requested_at": row.get("created_at"),
            "expires_at": row.get("expires_at"),
        })
    return {"ok": True, "requests": out}


def my_requests(supabase, user_id) -> list[dict]:
    """The teacher's own applications, newest first, with the destination's name.

    Read by the teacher, about the teacher: the destination school of one's own
    application is not somebody else's information.
    """
    if not user_id:
        return []
    try:
        rows = (supabase.table(REQUESTS_TABLE)
                .select("id,target_school_id,status,created_at,decided_at,decision_reason")
                .eq("teacher_id", user_id).order("created_at", desc=True)
                .execute().data or [])
    except Exception:
        logger.debug("own request read failed; reporting none", exc_info=True)
        return []
    ids = [row.get("target_school_id") for row in rows]
    names = _school_names(supabase, ids)
    for row in rows:
        row["school_name"] = names.get(_text(row.get("target_school_id")), "")
    return rows


def _school_names(supabase, ids) -> dict:
    wanted = [str(i) for i in dict.fromkeys(str(i) for i in (ids or []) if i)]
    if not wanted:
        return {}
    try:
        rows = (supabase.table(SCHOOLS_TABLE).select("id,name").in_("id", wanted)
                .execute().data or [])
    except Exception:
        logger.debug("school name read failed; names left empty", exc_info=True)
        return {}
    return {_text(row.get("id")): _text(row.get("name")) for row in rows}


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
        rows = (supabase.table(REQUESTS_TABLE)
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
    # The membership is read BEFORE the decision is written, so an approval that would
    # be a covert reopen is refused while nothing has changed yet. Writing `approved`
    # first and discovering the refusal after would leave a request that promises
    # access nobody has — the one state this function exists not to create.
    if decision == "approved":
        state, status = _existing_status(supabase, school_id, row["teacher_id"])
        if state == "known" and status == CLOSED_STATUS:
            return {"ok": False, "reason": "membership_closed"}
        if state == "error":
            return {"ok": False, "reason": "read_failed"}
    (supabase.table(REQUESTS_TABLE)
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
        granted = grant_membership(supabase, school_id, row["teacher_id"],
                                   actor_id=actor_id)
        if not granted.get("ok"):
            # The decision is already written, so this is reported rather than hidden:
            # a caller told `ok` here would claim an access that does not exist.
            logger.warning("approved request %s granted nothing: %s",
                           row.get("id"), granted.get("reason"))
            return {"ok": False, "reason": granted.get("reason") or "grant_failed"}
    return {"ok": True, "request_id": row["id"], "decision": decision}
