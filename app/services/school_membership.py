"""One account, many schools: a teacher's membership, and which one is active.

Until migration 044 an account was one `profiles.school_id`. A teacher who taught
at two schools needed two accounts, two emails and two activations, and there was
no way to move between them without signing out and back in. `teacher_school_membership`
separates **identity** (one `profiles` row, one email) from **membership** (many
rows, one per school).

The security half is here too. The backend uses the service key and passes through
RLS, so the tenant boundary is decided in Python, not by the database — which means
the *active* school must be re-checked on every request rather than trusted because
it was chosen once. `resolve_for_request` is that check, and it is the only place
allowed to answer "which school is this request for?".

Roles: only `guru`, `principal` and `vice_principal` may hold more than one
membership. A pupil and a school admin stay single-school through
`profiles.school_id`, unchanged — their behaviour must not shift because a feature
for teachers was added.

The two directions are **not** the same question, so they are two functions. Reading
is `resolve_for_request`: when the memberships cannot be read at all it keeps the
home school, because an outage must not take a school away from anybody mid-page.
Writing is `filing_school`, and it refuses instead — a row filed against a school
nobody could confirm is how one teacher's paper and its marks end up counted by a
school that never set it. `filing_school` is the rule every exam-writing door asks.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Roles that may belong to more than one school.
MEMBERSHIP_ROLES = ("guru", "principal", "vice_principal")

#: The one membership status that grants access. The others (`invited`, `inactive`,
#: `closed`) all answer "no access to that school", which is why every access
#: question names this one and none of them names the rest.
STATUS_ACTIVE = "active"

#: The active school is kept server-side, never in the browser, so a client cannot
#: put itself in a school it is not a member of. The key is per session token.
ACTIVE_SCHOOL_KEY = "active_school:{token}"


def is_cross_school_role(role) -> bool:
    return role in MEMBERSHIP_ROLES


def memberships_for(supabase, user_id, status=STATUS_ACTIVE) -> list:
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
                .eq("status", STATUS_ACTIVE)
                .limit(1).execute().data or [])
    except Exception:
        return False
    return bool(rows)


def membership_rows(supabase, user_id) -> list[dict] | None:
    """Every membership row this user has — or ``None`` when it could not be read.

    The ``None`` is the point of the function, and it is why the *rows* are returned
    rather than a convenient set of active ids. Two situations a plain ``[]`` would
    merge are opposite, and the difference is a closure versus an outage:

    * **read succeeded, a row says `closed`** — the account held this school and no
      longer does;
    * **read failed** — nothing is known, and dropping a teacher out of the school
      their profile names because the database hiccuped is the worse failure.

    All rows, and not only the active ones, because "no row at all" and "a row that
    is not active" are different answers about the home school: the first is a school
    nobody ever recorded a membership for (every teacher created after migration 044
    looks like this), the second is a membership somebody closed. Only the first may
    keep the home school.

    There is a third state, and it is **not** an unreadable one: a database that has
    never run migration 044 has no such table at all. That state is `[]` — "no
    membership anywhere" — because it is not a failure to be told about, it is a
    database in which every teacher has exactly one school and `profiles.school_id`
    is the whole truth. Reading it as an outage would make the *write* side (which
    refuses an unconfirmed school) refuse every exam save on a box that never had the
    feature. This is the same reasoning `memberships_for` records for its own empty
    answer, and it is why the missing case is told apart from the failed one here
    rather than at each caller.
    """
    if not user_id:
        return []
    try:
        return (supabase.table("teacher_school_membership").select("school_id,status")
                .eq("user_id", user_id).execute().data or [])
    except Exception as exc:
        if _table_is_missing(exc):
            logger.debug("no membership table on this database; treating as no rows")
            return []
        logger.debug("membership read failed; narrowing nothing", exc_info=True)
        return None


#: What a database says when the *table itself* is not there. Postgres 42P01
#: (`undefined_table`), PostgREST's `PGRST205` schema-cache miss, and the two
#: wordings those arrive with. A set rather than one check because the three clients
#: in play (postgrest-py, supabase-py, psycopg) each carry the code in a different
#: attribute and the message is the only thing they agree on.
_MISSING_TABLE_CODES = ("42p01", "pgrst205")
_MISSING_TABLE_TEXT = ("does not exist", "could not find the table", "undefined table")


def _table_is_missing(exc) -> bool:
    """Whether this exception means "that table is not in this database".

    Deliberately narrow. Everything else — a timeout, a connection reset, a
    permission error, a malformed query — is an *unreadable* read, and the difference
    decides whether a teacher can save an exam (see `filing_school`): guessing
    "missing" where the truth is "could not ask" would hand a write the home school it
    may not have.
    """
    for attr in ("code", "pgcode"):
        value = getattr(exc, attr, None)
        if value and str(value).strip().lower() in _MISSING_TABLE_CODES:
            return True
    text = str(exc).lower()
    return any(marker in text for marker in _MISSING_TABLE_TEXT)


def resolve_from_memberships(home_school_id, chosen_school_id, rows, role) -> str | None:
    """The pure resolution, given the caller's membership rows already in hand.

    Order, and each step is a *narrowing*:

    1. a role that cannot be cross-school keeps its `profiles.school_id` — the
       membership table is never consulted, so pupils and admins behave exactly
       as they did before;
    2. ``rows is None`` (the read failed) also keeps the home school: an outage may
       not take a school away from anybody;
    3. the school chosen in this session, **re-verified** against an active row — a
       closed membership drops the choice on the very next request;
    4. the home school, when it is active **or has no row at all**. That second
       clause is load-bearing and was nearly a lockout: only a row that exists and
       is not active is a closure, so a teacher whose membership was never recorded
       (any account created after migration 044) keeps working;
    5. otherwise any other active membership, in a **sorted** order so two requests
       cannot disagree about which school that teacher is in.

    Returns ``None`` when there is nothing valid left, and a caller with no school is
    refused by `require_school_access` rather than defaulted somewhere.

    Kept separate from `resolve_active_school` so a caller that already read the rows
    (the per-request session apply, which reads them once) does not pay another
    round-trip to ask the same question again.
    """
    if not is_cross_school_role(role):
        return home_school_id
    if rows is None:
        return home_school_id
    active = {str(r.get("school_id")) for r in rows
              if str(r.get("status") or "") == STATUS_ACTIVE}
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

    For callers outside a request — the school switcher, the merge tool, the
    deploy's own integrity sweep — where a fresh read is the point.
    """
    if not is_cross_school_role(role):
        return home_school_id
    return resolve_from_memberships(home_school_id, chosen_school_id,
                                    membership_rows(supabase, user_id), role)


def _still_active(rows, school_id) -> bool:
    """Whether `rows` holds an ACTIVE membership in `school_id`."""
    wanted = str(school_id)
    return any(str(r.get("school_id")) == wanted
               and str(r.get("status") or "") == STATUS_ACTIVE for r in rows)


def _rows_for_request(user_id, supabase=None):
    """The rows this request already paid for, or a fresh read outside one.

    Memoised under one key so the session apply, `resolve_for_request` and
    `filing_school` all read the same rows: a request that resolves a school and
    then files a row under it must not be able to see two different answers, and the
    second read would be a round-trip this app cannot afford on every write.
    """
    from app.utils.req_cache import memo
    return memo("membership:rows:{}".format(user_id),
                lambda: membership_rows(supabase or _client(), user_id))


def _client():
    from app.utils.auth import get_supabase
    return get_supabase()


def resolve_for_request(token, user_id, home_school_id, role, supabase=None) -> str | None:
    """Which school is THIS request for? The single answer the request path uses.

    Every authenticated request resolves its school here (`app.utils.auth`), and the
    shape of the function is what makes a closure total rather than eventual:

    * a role that cannot be cross-school is returned untouched, so pupils and school
      admins behave exactly as before and pay nothing;
    * the rows are read **fresh** — once per request, memoised — so a closed row is
      honoured on the very next request instead of at the end of the session cache's
      TTL. The session cache holds the *profile*, whose `school_id` is the home
      school; it never holds the resolved answer, so there is no cached school to
      outlive its row;
    * the chosen school (kept server-side, per token) is only honoured while an
      active row still names it, and a choice that outlived its row is **cleared**
      here — the read path is the only place that sees both the token and the row,
      so it is the only place that can drop the one and keep the other honest.
    """
    if not is_cross_school_role(role):
        return home_school_id
    rows = _rows_for_request(user_id, supabase)
    chosen = get_active_school(token)
    if chosen and rows is not None and not _still_active(rows, chosen):
        clear_active_school(token)
        chosen = None
    return resolve_from_memberships(home_school_id, chosen, rows, role)


# ── the write side: the school a row is filed under ─────────────────────────
#
# Why this is not `resolve_for_request`. A read that cannot confirm the acting
# school keeps the home school, which is the safe direction for a page: the teacher
# keeps working and the worst case is a page drawn for the wrong school. A *write*
# that cannot confirm it is the opposite problem — `exams.school_id` is what scopes
# the paper's weighting, its marking, its pupils and its reports, so a paper filed in
# the home school while its author is teaching at another NPSN is a paper the wrong
# school grades. The two doors that can be steered by a client (`POST /exam/` and the
# builder's save) therefore ask this, and it refuses rather than guesses.

#: The refusals, in the words a teacher reads. Message constants rather than
#: literals at three call sites: the guard file pins each one, and a rule whose
#: refusal lives in two spellings is a rule nobody can grep for afterwards.
FILING_NO_SCHOOL = "Sekolah aktif Anda tidak dapat ditentukan, jadi ujian tidak disimpan."
FILING_UNVERIFIED = ("Keanggotaan sekolah Anda sedang tidak dapat dipastikan, jadi "
                     "ujian tidak disimpan. Coba lagi sebentar lagi.")
FILING_OTHER_SCHOOL = "Ujian hanya dapat disimpan pada sekolah yang sedang Anda gunakan."


def filing_school(*, role, user_id, active_school_id, home_school_id=None,
                  requested_school_id=None, supabase=None):
    """``(school_id, error)`` — the school a row this request writes is filed under.

    Returns the school and ``None``, or ``None`` and the sentence to answer with.
    The rules, each of which is a way a paper could otherwise land in the wrong
    school's weighting and results:

    * **the school is the resolved active one, never the request's.** A caller that
      names a school is naming an intention, not an authority: a name that disagrees
      with the school this session is actually acting in is refused, so a client can
      never file a paper into another NPSN (its home school included);
    * **nothing is filed without a school.** No active school resolves → refuse. A
      paper with no school is read by `exam_sitting_allowed` as "every school's
      pupils may sit it", which is strictly worse than a refusal;
    * **a cross-school role's school must be granted by the rows read in this same
      request.** The session resolution already did that at the start of the request;
      asking again here is what turns "the school the page was drawn for" into "the
      school the database is granting right now", and the memo means the two
      questions share one read. When the rows cannot be read the answer is a refusal
      rather than the home school — see the note above.
    """
    wanted = str(requested_school_id).strip() if requested_school_id else ""
    school = str(active_school_id).strip() if active_school_id else ""
    if not school:
        return None, FILING_NO_SCHOOL
    if not is_cross_school_role(role):
        # A pupil or a school admin has exactly one school and it is the profile's;
        # the membership table is not consulted, exactly as on the read side.
        if wanted and wanted != school:
            return None, FILING_OTHER_SCHOOL
        return school, None
    rows = _rows_for_request(user_id, supabase)
    if rows is None:
        return None, FILING_UNVERIFIED
    granted = resolve_from_memberships(home_school_id, school, rows, role)
    if str(granted or "") != school:
        # The rows no longer give this school — a closure that landed before this
        # write, or a session whose school was never granted at all.
        return None, FILING_UNVERIFIED
    if wanted and wanted != school:
        return None, FILING_OTHER_SCHOOL
    return school, None


def set_active_school(token, school_id) -> None:
    """Remember the chosen school server-side for this session.

    Kept in the same cache the session itself uses, keyed by the token, so the
    choice dies with the session and never travels in a cookie or a form the
    browser can edit.

    Writing a choice is not the same as being allowed to use it: the choice is only
    honoured while an active membership still grants it (`resolve_for_request`), so
    an unverified write here cannot put anybody in a school they are not a member of.
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


def clear_active_school(token) -> None:
    """Forget the chosen school for this session (a closure drops it, so does logout)."""
    if not token:
        return
    from app.utils.kv_cache import cache_delete
    cache_delete(ACTIVE_SCHOOL_KEY.format(token=token))


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
