"""Who teaches what, and therefore who may build a paper for it.

`teacher_assignments` (migration 010) is the (teacher, class, subject) pair an
admin hands a guru. Until now it was consulted in exactly one place — the
dropdown on the exam builder — and the *write* it feeds was unguarded, so a guru
whose list was empty (or who edited the request) could attach a paper to any
class in the school, and the empty case fell **open**: the form filled every
class and subject in the school, which reads as a permission rather than a
missing row.

The rule lives here, in one function, for the same reason `exam_access` holds
the student's: two callers answering "may this teacher write for this pair?"
separately will eventually disagree, and the one that drifts is the one nobody
is looking at.

Scope: this is *within* one school — the pair an admin assigned — not the
tenant boundary, which the school filter and the service key still own. An
`admin_sekolah` and a `super_admin` are deliberately not scoped; running a school
is their job, and both already pass `can_manage_exam`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Roles whose writes are limited to the pairs they hold. Named, not inferred, so
#: adding a role elsewhere cannot silently widen the school.
SCOPED_ROLES = ("guru", "teacher")


def is_scoped_role(role) -> bool:
    return role in SCOPED_ROLES


def active_rows(supabase, teacher_id, school_id) -> list:
    """The teacher's assignment rows that still grant the door.

    A row written before migration 045 — or by a deployment that has not run it —
    carries no `status` key at all; that is a real assignment and must not vanish,
    so the absence of the column is read as *active*.
    """
    res = (supabase.table("teacher_assignments")
           .select("class_id, subject_id, status, school_year")
           .eq("teacher_id", teacher_id)
           .eq("school_id", school_id)
           .execute())
    rows = getattr(res, "data", None) or []
    return [r for r in rows if (r.get("status") or "active") == "active"]


def unassigned_class_ids(supabase, teacher_id, school_id, subject_id, class_ids) -> list:
    """Which of `class_ids` this teacher may NOT attach a paper to.

    Empty list means every class is theirs to write for. When `subject_id` is
    absent the class alone is checked (there is no pair to test against); when it
    is present the *pair* is the unit, so teaching Physics in 8A does not carry
    Maths in 8A.

    Fails **closed**: if the lookup itself raises, every class is returned as
    unassigned. A teacher being unable to save for a moment is a smaller fault
    than a school's roster being handed out by a blip.
    """
    wanted = [str(c) for c in (class_ids or []) if c]
    if not wanted:
        return []
    try:
        rows = active_rows(supabase, teacher_id, school_id)
    except Exception:
        logger.exception("assignment lookup failed for teacher %s", teacher_id)
        return wanted
    subj = str(subject_id) if subject_id else None
    allowed = {
        str(r.get("class_id"))
        for r in rows
        if r.get("class_id")
        and (subj is None or str(r.get("subject_id") or "") == subj)
    }
    return [c for c in wanted if c not in allowed]
