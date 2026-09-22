"""A report a teacher can send, and can take back.

The link is the whole feature: a curriculum lead, a colleague at another school
or a parent opens it and reads what the paper measured, with no account on this
box. Three properties make that safe to offer, and none of them is decoration:

* **revocable** — one row is one link. `revoke()` sets a date and the very next
  request with that token is a 404, which is the difference between sharing and
  publishing;
* **dated** — a link expires on its own, so the report a school asked for in
  March does not stay readable forever because nobody remembered it;
* **counted** — every open bumps `views`, so "I sent it" and "somebody read it"
  are two different states a teacher can tell apart.

What the visitor may *see* is decided elsewhere on purpose
(`app/routes/public.py` strips the answer key and every student name before the
template is rendered) — this module only decides who is allowed to look.

A link has a **scope**, and it is one nullable column rather than a second table
(`033_analysis_student_share.sql`): `student_id IS NULL` is the exam's report, a
profile id is that one learner's. The same token, expiry, revoke and counter, and
every read is filtered by the scope — so a learner's link can never be handed
back as "the exam's link", which is the one mix-up this column exists to prevent.

The token is stored as it is rather than hashed. A hash would make the one
moment a teacher can read their own link the moment it is created; reload the
page and it is gone, and a link nobody can find again is a link they will paste
somewhere they cannot take it back. So the protecting controls are the ones that
actually bind: a 256-bit `secrets` token that cannot be guessed, a table only the
service key can read (`031_analysis_share_links.sql`, RLS with no policy), and a
revoke button that works immediately.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.utils.helpers import row_or_none

#: How long a link lives unless the caller says otherwise. A month is long enough
#: for a meeting to be rescheduled twice and short enough that last term's report
#: is not still readable next year.
DEFAULT_DAYS = 30

#: 32 bytes is 43 urlsafe characters — 256 bits, which is not guessable at any
#: rate a scanner can afford.
TOKEN_BYTES = 32

ACTIVE = "active"
EXPIRED = "expired"
REVOKED = "revoked"

#: The columns the card and the resolver both read. Named rather than `*` so a
#: column added later cannot silently change what travels to a page.
COLUMNS = ("id", "exam_id", "student_id", "token", "created_by", "created_at",
           "expires_at", "revoked_at", "views", "last_viewed_at")

#: How many of an exam's rows are read before the scope is applied. A row is one
#: link, and an exam accumulates one per scope per revocation, so this is far past
#: anything a school reaches — and it has to be *past* it, because the scope is
#: filtered after the read (see `_rows`).
ROW_CEILING = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def moment(value: Any) -> datetime | None:
    """A timestamp from PostgREST, as a datetime, or ``None``.

    PostgREST sends `timestamptz` as an ISO string, and the tests hand this
    module real datetimes; both have to work, because the comparison below is
    what decides whether a link is still alive.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def state(row: Mapping[str, Any] | None, now: datetime | None = None) -> str:
    """Which of the three a row is *now*: active, expired or revoked.

    The *presence* of a date is what decides, not whether it parses. Each column
    is only ever written to stop a link, so a value this function cannot read is
    still a value somebody wrote to end the share — and the permissive reading of
    a corrupt date is a link that outlives the report it points at. An expiry the
    database sends as garbage therefore reads as expired, and a revocation it
    sends as garbage reads as revoked.
    """
    if not row:
        return REVOKED
    if row.get("revoked_at"):
        return REVOKED
    raw = row.get("expires_at")
    if raw in (None, ""):
        return ACTIVE
    expires = moment(raw)
    if expires is None or expires <= (now or _now()):
        return EXPIRED
    return ACTIVE


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _rows(supabase, exam_id: str, student_id: str | None = None, *,
          newest_first: bool = False) -> list[dict]:
    """This scope's rows, read on a schema with or without the 033 column.

    `select("*")` deliberately, and this is the only place in the module that does
    it. Asking for `student_id` **by name** is a `42703` on a box where migration
    033 has not been pasted in yet — and every caller's `except` turns that into
    "there is no link", so the class share stopped minting, stopped revoking and
    told the teacher *"Tidak ada tautan aktif"* while a live link went on serving
    the report. Reading each row's own columns and comparing what it actually
    carries makes the scope a property of the row rather than of the query: a row
    on the older schema has no `student_id` key at all, so it is the exam's — which
    is true, because a learner's link cannot exist on a schema that cannot record
    one.

    `COLUMNS` is still what leaves this function, so what a page or a resolver sees
    is unchanged by the wider read.
    """
    query = supabase.table("analysis_share_links").select("*").eq("exam_id", exam_id)
    if newest_first:
        query = query.order("created_at", desc=True)
    rows = (query.limit(ROW_CEILING).execute().data) or []
    wanted = str(student_id) if student_id else None
    out: list[dict] = []
    for row in rows:
        scope = row.get("student_id")
        if (str(scope) if scope else None) == wanted:
            out.append({name: row.get(name) for name in COLUMNS})
    return out


def path(token: str) -> str:
    """The path an anonymous visitor uses. Kept here so the card, the route and
    the tests cannot disagree about the shape of a share link."""
    return f"/r/{token}"


def live(supabase, exam_id: str, student_id: str | None = None) -> dict | None:
    """The current usable link for this *scope*, if there is one.

    Newest first: an exam may hold several rows over its life (a revoked link, an
    expired one, then a new one), and only the newest that is still alive is the
    link a teacher means by "the link".

    The scope is part of the question, not a filter applied afterwards: a live link
    to one learner's page must never be returned as the exam's link, or the report
    page would show a URL that opens somebody else's copy of the report.
    """
    try:
        rows = _rows(supabase, exam_id, student_id, newest_first=True)
    except Exception:
        # A table that has not been migrated yet must not break the report it
        # sits on: the page simply offers to create a link, as if there were none.
        return None
    for row in rows:
        if state(row) == ACTIVE:
            return row
    return None


def create(supabase, exam_id: str, created_by: str | None,
           days: int | None = DEFAULT_DAYS,
           student_id: str | None = None) -> dict | None:
    """Mint (or return) the shareable link for this scope.

    Idempotent on purpose: a teacher who presses the button twice wants *the*
    link, not a second one — two links to the same report is two things to
    remember to revoke.
    """
    existing = live(supabase, exam_id, student_id)
    if existing:
        return existing
    row = {
        "exam_id": exam_id,
        "token": new_token(),
        "created_by": created_by or None,
        "expires_at": ((_now() + timedelta(days=days)).isoformat()
                       if days else None),
    }
    # Written only when there is a scope to write, so the exam's link is still
    # mintable on a box whose table has no `student_id` column yet.
    if student_id:
        row["student_id"] = student_id
    try:
        created = supabase.table("analysis_share_links").insert(row).execute().data
    except Exception:
        # The write may have landed even though the answer did not: this is the
        # one call in this module that creates state, and a dropped response
        # after a commit is a real failure mode on this connection (seen in
        # production: `RemoteProtocolError: Server disconnected` with the row
        # already written). Retrying would mint a second link, so ask instead of
        # guessing — and if it did land, return *it* rather than telling the
        # teacher "try again" while a live link sits in the table.
        return live(supabase, exam_id, student_id)
    return (created or [None])[0]


def _revoked(supabase, link_id: str) -> bool:
    """Whether this row now carries a revocation, as the database tells it."""
    try:
        row = row_or_none(
            supabase.table("analysis_share_links").select("revoked_at")
            .eq("id", link_id).maybe_single().execute())
    except Exception:
        return False
    return bool((row or {}).get("revoked_at"))


def _stop_one(supabase, link_id: str) -> bool:
    """Revoke one row, and answer whether it is *actually* revoked.

    Measured on the live connection: an UPDATE whose answer was dropped made
    `revoke()` report "0 stopped", the page tell the teacher "Tidak ada tautan
    aktif untuk dihentikan" — and the link went on serving the report. That is
    the one outcome a revocable link cannot have, so the stop is written until it
    is *confirmed*: setting a revocation is idempotent (the same timestamp twice
    is the same row), and between attempts the row is read, which is the only way
    to tell "it landed" from "it did not".
    """
    stamp = _now().isoformat()
    for _attempt in (1, 2):
        try:
            (supabase.table("analysis_share_links")
             .update({"revoked_at": stamp}).eq("id", link_id).execute())
            return True
        except Exception:
            pass
    return _revoked(supabase, link_id)


def revoke(supabase, exam_id: str, student_id: str | None = None) -> int:
    """Stop every live link in this scope. Returns how many were stopped.

    Every link in the scope, not the newest one: a teacher pressing "stop sharing"
    means that report is not readable by anybody, and an older row someone still
    holds must not survive the click. A link that could not be stopped is *not*
    counted — the caller has to be able to tell "nothing to stop" from "it did not
    take".

    With no `student_id` this stops the exam's links **and only those**: a teacher
    stopping the class report should not silently kill the individual links they
    handed to four families, whose text says one learner's page and whose audience
    is one household.
    """
    try:
        rows = _rows(supabase, exam_id, student_id)
    except Exception:
        return 0
    stopped = 0
    for row in rows:
        if state(row) != ACTIVE:
            continue
        if _stop_one(supabase, row["id"]):
            stopped += 1
    return stopped


def resolve(supabase, token: str) -> dict | None:
    """The link a visitor's token names, or ``None``.

    ``None`` covers all three refusals — unknown, expired, revoked — because the
    caller must answer the same 404 for each: telling a stranger *which* of the
    three it was tells them a token existed.
    """
    if not token or not token.strip():
        return None
    try:
        # `*` for the same reason `_rows` reads wide: naming `student_id` in a
        # projection is a 42703 on a schema that has not run migration 033, and
        # this is the read every *existing* link goes through — a token that used
        # to open the class report would have stopped opening anything at all.
        data = row_or_none(
            supabase.table("analysis_share_links").select("*")
            .eq("token", token.strip()).maybe_single().execute())
    except Exception:
        return None
    if not data:
        return None
    data = {name: data.get(name) for name in COLUMNS}
    return data if state(data) == ACTIVE else None


def register_view(supabase, row: Mapping[str, Any]) -> None:
    """Count an open. Best effort by design.

    Read-then-write rather than `views = views + 1`: PostgREST has no atomic
    increment without a database function, and two visitors in the same
    millisecond undercounting by one is worth less than another migration. A
    failure here must never cost the reader their report, so it is swallowed.
    """
    try:
        supabase.table("analysis_share_links").update({
            "views": int(row.get("views") or 0) + 1,
            "last_viewed_at": _now().isoformat(),
        }).eq("id", row["id"]).execute()
    except Exception:
        pass


def card(row: Mapping[str, Any] | None, base_url: str = "") -> dict | None:
    """The link as the teacher's page shows it, or ``None`` when there is none.

    The URL is built from `path()`, so the address a teacher copies is the
    address this module resolves — one shape, written once.
    """
    if not row:
        return None
    expires = moment(row.get("expires_at"))
    return {
        "url": (base_url.rstrip("/") if base_url else "") + path(row["token"]),
        "state": state(row),
        "views": int(row.get("views") or 0),
        "created_at": moment(row.get("created_at")),
        "expires_at": expires,
        # Travels so a page can say *what* the link opens. Two live links on one
        # screen — the exam's and one learner's — are otherwise distinguishable
        # only by their token.
        "student_id": row.get("student_id"),
    }
