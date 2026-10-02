"""A school's assessment calendar: Mid Semester, Final Semester, Try Out, Asesmen.

Why this is a module and not queries in the routes
--------------------------------------------------
`exams.exam_type` has carried this vocabulary since migration 007 and no code
ever read it, so the gap was never the words — it was that nothing could
*schedule* a period. A period is now one row, and every read and write of it
lives here, takes the caller's ``school_id`` as a **required argument**, and never
takes a school from the request. The routes cannot forget the scope because they
are never the ones holding it, which is the same arrangement
:mod:`app.services.invigilation` uses and for the same reason.

Exactly one period runs at a time
---------------------------------
"Which period is this paper in" has to have one answer, because that answer is
what makes a teacher's page and a pupil's list name the same period. The database
enforces it with a partial unique index (migration 050), and :func:`save_period`
keeps it true by clearing the previous running period in the same school before
it turns another on. The clear is scoped to the caller's school on purpose: an
unscoped ``UPDATE ... SET is_active = FALSE`` would stop every other school's
period as a side effect of this one's edit.

A read that fails is an empty read
----------------------------------
These rows decorate pages rather than being the point of them, so a Supabase
hiccup says "no period is running" rather than answering the reader a 500 — the
same choice :mod:`app.services.invigilation` makes for its lookups.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The four periods a school runs a year on, in the order a year meets them. The
#: keys are the ones migration 050's CHECK constraint accepts; the words are the
#: school's, and they live in the templates.
KINDS: tuple[str, ...] = ("mid_semester", "final_semester", "tryout", "asesmen")

#: Every reason a write can refuse, as a key the pages translate. Kept together so
#: the bilingual catalogue (`app/templates/shared/_assessment_period_reasons.html`)
#: can be checked against it: a refusal with no sentence renders as an empty alert
#: in both languages, which is a button that does nothing with no explanation.
REFUSALS = (
    "bad_kind",
    "name_required",
    "bad_dates",
    "not_found",
    "write_failed",
)


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: read failed: %s", exc)
        return []


def list_periods(supabase, school_id: str) -> list[dict]:
    """Every period of this school, newest first.

    Scoped by ``school_id`` in the query rather than filtered afterwards: a
    school's calendar is the whole of what this page shows, and a filter applied
    after the read is one refactor away from being dropped.
    """
    if not school_id:
        return []
    return _rows(supabase.table("assessment_periods").select("*")
                 .eq("school_id", school_id).order("start_date", desc=True))


def active_period(supabase, school_id: str) -> dict | None:
    """The one period running now, or ``None``.

    Read by the school and the flag together, so this cannot return another
    school's running period even if two rows somehow carry the flag.
    """
    if not school_id:
        return None
    rows = _rows(supabase.table("assessment_periods").select("*")
                 .eq("school_id", school_id).eq("is_active", True).limit(1))
    return rows[0] if rows else None


def save_period(supabase, school_id: str, *, period_id: str | None,
                kind: str, name: str, start_date: str, end_date: str,
                is_active: bool = False, actor_id: str | None = None) -> dict:
    """Create or edit one period, or refuse with a reason.

    Every refusal happens **before** any write: a partial write would leave the
    deputy with a calendar they cannot see the shape of, which is harder to
    explain than a refusal.
    """
    kind = (kind or "").strip()
    name = (name or "").strip()
    start_date = (start_date or "").strip()
    end_date = (end_date or "").strip()

    if kind not in KINDS:
        return {"ok": False, "reason": "bad_kind"}
    if not name:
        return {"ok": False, "reason": "name_required"}
    if not start_date or not end_date or end_date < start_date:
        # ISO dates compare as strings in the right order, and that is exactly the
        # comparison the database's CHECK constraint makes.
        return {"ok": False, "reason": "bad_dates"}

    payload = {
        "kind": kind,
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "is_active": bool(is_active),
        "updated_at": _now(),
    }

    if period_id:
        # The row is proved to be this school's by the *update's own filter*: a
        # read-then-write would be one interleaving away from editing another
        # school's row, and an unscoped update would edit it outright.
        written = _rows(supabase.table("assessment_periods").update(payload)
                        .eq("id", period_id).eq("school_id", school_id))
        if not written:
            return {"ok": False, "reason": "not_found"}
        if is_active:
            _clear_other_active(supabase, school_id, period_id)
        return {"ok": True, "reason": "", "period": written[0], "created": False}

    payload["school_id"] = school_id
    if actor_id:
        payload["created_by"] = actor_id
    if is_active:
        # Cleared before the insert, not after: the partial unique index refuses a
        # second running period, so inserting first would fail rather than move it.
        _clear_other_active(supabase, school_id, keep_id=None)
    written = _rows(supabase.table("assessment_periods").insert(payload))
    if not written:
        return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "reason": "", "period": written[0], "created": True}


def delete_period(supabase, school_id: str, period_id: str) -> dict:
    """Remove one period. Scoped by school, like every other write here."""
    if not period_id:
        return {"ok": False, "reason": "not_found"}
    removed = _rows(supabase.table("assessment_periods").delete()
                    .eq("id", period_id).eq("school_id", school_id))
    return {"ok": True, "reason": ""} if removed is not None else {
        "ok": False, "reason": "write_failed"}


def _clear_other_active(supabase, school_id: str, keep_id: str | None) -> None:
    """Stop whichever period of *this school* was running.

    Scoped to ``school_id`` and nothing else: a clear without that filter is one
    deputy editing their calendar and stopping every other school's period.
    """
    query = (supabase.table("assessment_periods")
             .update({"is_active": False, "updated_at": _now()})
             .eq("school_id", school_id).eq("is_active", True))
    if keep_id:
        query = query.neq("id", keep_id)
    _rows(query)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
