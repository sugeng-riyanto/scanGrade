"""Reading a pupil's recovery code, for the staff who already hold their sitting.

The pupil's results page says *"Your class invigilator decides"* and *"an approval
returns that paper to your exam list"*, and the lock feature
(:mod:`app.services.resume_code`) borrows the recovery code from
:mod:`app.utils.exam_recovery` — the six-digit code issued per ``(student, exam)`` and
shown in the pupil's own topbar. What was missing is the **read** side: no staff
surface showed a pupil's code, so an invigilator asked to let a locked pupil back in
had nothing to compare the number they were read against.

This module is that read, and nothing else. It mints nothing, writes nothing, and
takes the caller's ``school_id`` as a **required argument** — the same rule every
other module here follows, because a route that reads a school from the request is
one missing filter away from another school's codes.

Two authorities, expressed as data
----------------------------------
The caller passes the exams it is allowed to see; this module never decides that.

* a **teacher** passes the exams they invigilate *or own* (the subject teacher who
  assigned the test) — see :func:`app.routes.teacher._teacher_code_exam_ids`;
* an **official** (vice principal or school admin) passes the school's exams, because
  their authority is the whole school.

Passing an **empty set** means "this reader holds nothing", which selects nothing
rather than everything — the caller's ``[]`` is honoured instead of being skipped as
falsy, exactly as :func:`app.services.invigilation.retake_requests` does it.
"""
from __future__ import annotations

import logging

from app.services.submission_service import LOCKED_STATUS

logger = logging.getLogger(__name__)


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("exam_codes: read failed: %s", exc)
        return []


def codes_for_exams(supabase, school_id: str, exam_ids) -> list[dict]:
    """The recovery codes for ``exam_ids``, with the pupil and exam named.

    ``exam_ids`` is the reader's authority already resolved by the caller. An empty
    set selects nothing on purpose: a teacher who holds no exam must see no codes,
    not every code.

    ``school_id`` is required and used to name the pupils, so a code row cannot be
    paired with a name from another school even if an id were ever passed wrongly.
    """
    wanted = [str(i) for i in (exam_ids or []) if i]
    if not wanted:
        return []

    codes = _rows(supabase.table("exam_access_codes")
                  .select("id, exam_id, student_id, code, is_used, used_at")
                  .in_("exam_id", wanted))
    if not codes:
        return []

    exam_ids_present = sorted({str(c["exam_id"]) for c in codes if c.get("exam_id")})
    student_ids = sorted({str(c["student_id"]) for c in codes if c.get("student_id")})

    exams = {str(r["id"]): r.get("title") or "" for r in _rows(
        supabase.table("exams").select("id, title").in_("id", exam_ids_present))} \
        if exam_ids_present else {}

    pupils = {}
    if student_ids:
        pupils = {str(r["id"]): (r.get("profiles") or {}).get("full_name") or ""
                  for r in _rows(supabase.table("students")
                                 .select("id, profiles!inner(full_name)")
                                 .eq("school_id", school_id)
                                 .in_("id", student_ids))}

    # Which of these sittings is *locked right now*. A staff page that offers to
    # reopen a paper must know which papers are locked, or it draws the button on
    # every pupil and the invigilator guesses. One read over the same pairs the
    # codes already name, keyed by (exam, pupil) — the unique constraint makes that
    # pair the sitting's identity, so the answer cannot be about another paper.
    sittings = {}
    if exam_ids_present and student_ids:
        for r in _rows(supabase.table("submissions")
                       .select("exam_id, student_id, status")
                       .in_("exam_id", exam_ids_present)
                       .in_("student_id", student_ids)):
            sittings[(str(r.get("exam_id")), str(r.get("student_id")))] = r.get("status")

    out = []
    for c in codes:
        status = sittings.get((str(c.get("exam_id")), str(c.get("student_id"))))
        out.append(dict(c,
                        exam_title=exams.get(str(c.get("exam_id")), ""),
                        student_name=pupils.get(str(c.get("student_id")), ""),
                        sitting_status=status,
                        is_locked=(status == LOCKED_STATUS)))
    return out


__all__ = ["codes_for_exams"]
