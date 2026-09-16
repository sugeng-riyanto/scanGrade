"""One row per student per exam, whatever its status.

`submissions` carries ``submissions_student_exam_unique`` on
``(student_id, exam_id)``, and the constraint counts **every** status. Two writes
in ``student.py`` used to ask a narrower question — "is there a *draft* row?" —
and INSERT when the answer was no. A ``retracted`` row is neither a draft nor a
live attempt (``max_attempts`` excludes it on purpose, which is exactly why the
exam comes back into the exam list), so the INSERT collided and the student could
not submit at all: a 500 carrying the raw Postgres text of ``duplicate key value
violates unique constraint "submissions_student_exam_unique"``.

These functions ask the constraint's question instead: *is there a row for this
(student, exam)?* If there is, that row **is** the submission, and a re-sit after
an approved retraction is written into it. What that costs is recorded honestly
below: the voided attempt's answers are replaced, because there is no second row
to hold them. The record of the retraction itself survives — ``retract_request``
and ``retract_approve`` are audit-log entries keyed by submission id
(``app/services/audit_service.py``), not fields of this row.
"""
from __future__ import annotations

from datetime import datetime, timezone

# An attempt that stands. Never overwritten: a duplicate submit answers
# "already submitted" instead.
LIVE_STATUSES = ("submitted", "graded", "published")
# A row that may be reused: the sitting continues, or a voided attempt is reopened.
ROW_COLUMNS = "id,status,started_at"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sitting_rows(supabase, exam_id, student_id):
    """Every row this student has for this exam — at most one, by the constraint."""
    return (
        supabase.table("submissions")
        .select(ROW_COLUMNS)
        .eq("exam_id", exam_id)
        .eq("student_id", student_id)
        .execute()
        .data
        or []
    )


def sitting_target(rows):
    """The row a finished attempt is written into, or ``None`` if none may be.

    A draft first — the ordinary path — then any row whose status is not live, so
    a retracted attempt is reused rather than collided with. A live row yields
    ``None``: that attempt already stands and must not be overwritten, which is
    the caller's cue to answer "already submitted".
    """
    if not rows:
        return None
    for row in rows:
        if row.get("status") == "draft":
            return row
    for row in rows:
        if row.get("status") not in LIVE_STATUSES:
            return row
    return None


def open_sitting(supabase, exam_id, student_id):
    """Return ``(row, opened)`` for the sitting this student has on this exam.

    ``opened`` is True when this call created the row or reopened a voided one.
    An open draft keeps the ``started_at`` it already carries — the timer has to
    survive a refresh, which is the whole reason the stamp is stored — and a row
    in a live status is returned untouched: whether the exam may be taken again is
    the route's judgement, not this function's.
    """
    rows = sitting_rows(supabase, exam_id, student_id)
    row = rows[0] if rows else None
    if row is None:
        created = (
            supabase.table("submissions")
            .insert({
                "exam_id": exam_id,
                "student_id": student_id,
                "answers": {},
                "score": 0,
                "max_score": 100,
                "status": "draft",
                "started_at": _stamp(),
            })
            .execute()
            .data
            or []
        )
        if created:
            return created[0], True
        # The API answered without a representation; the row exists either way.
        rows = sitting_rows(supabase, exam_id, student_id)
        return (rows[0] if rows else {}), True
    if row.get("status") in LIVE_STATUSES:
        return row, False
    if row.get("status") == "draft" and row.get("started_at"):
        return row, False
    patch = {"status": "draft", "started_at": _stamp()}
    if row.get("status") == "retracted":
        # A fresh sitting. The voided answers are replaced because the constraint
        # leaves no second row to hold them (see the module docstring).
        patch["answers"] = {}
    supabase.table("submissions").update(patch).eq("id", row["id"]).execute()
    return {**row, **patch}, True


def finish_sitting(supabase, exam_id, student_id, submission, rows=None):
    """Write a finished attempt into this (student, exam)'s row.

    Returns the id written to, or ``"already_submitted"`` when a live attempt owns
    the row and nothing was written. Pass ``rows`` when the caller has already read
    them: the ordinary path then costs no extra query.

    An insert is only ever attempted when no row exists, and a unique-violation
    there is not a failure — it means another tab (or the offline poll) created the
    row in between, so the write goes into that row instead. That is the whole
    point: the student pressed Send twice, and the answer is one recorded
    submission, not a 500.
    """
    rows = sitting_rows(supabase, exam_id, student_id) if rows is None else rows
    target = sitting_target(rows)
    if target is not None:
        supabase.table("submissions").update(submission).eq("id", target["id"]).execute()
        return target["id"]
    if rows:
        # The only row this student has for this exam is a live attempt.
        return "already_submitted"
    try:
        created = supabase.table("submissions").insert(submission).execute().data or []
        return created[0].get("id") if created else None
    except Exception as exc:  # noqa: BLE001 — re-raised unless it is the collision
        if "23505" not in str(exc) and "duplicate key" not in str(exc):
            raise
        target = sitting_target(sitting_rows(supabase, exam_id, student_id))
        if target is None:
            return "already_submitted"
        supabase.table("submissions").update(submission).eq("id", target["id"]).execute()
        return target["id"]
