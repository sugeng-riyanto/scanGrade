"""Who watches which sitting, and who may let a pupil sit one again.

Why this is a module rather than queries inside the routes
----------------------------------------------------------
Three roles touch these rows and each one is allowed a different slice of them: a
vice principal *builds* the schedule, a principal only reads it, a teacher reads the
one duty that has their name on it, and a pupil reads only their own request. Give
each route its own query and the fourth one written, months later, is the one that
forgets a filter — and forgetting a filter here means a schedule, a teacher's duty or
a child's retake decision crossing to another school.

So every read and every write lives here, every one of them takes the caller's
``school_id`` as a **required argument**, and none of them takes a school from the
request. The routes cannot forget the scope because they are never the ones holding
it.

The scope is also carried on the row, not walked to
---------------------------------------------------
The three tables each store ``school_id``. That is deliberate redundancy: a write can
be filtered by a column that always exists on the row it is writing, so the chain
``.eq("school_id", …)`` can be written at all. Scoping through ``exam_id`` instead
would mean every future write has to remember to read ``exams`` first, and the one
that does not would write into another school — the easiest mistake to make in a POST
handler, which is exactly what this file exists to make impossible.

One exception, and it is named
------------------------------
``submissions`` has no ``school_id`` in this schema (see ``supabase/schema.sql``).
The retake decision therefore retracts a submission by ``(exam_id, student_id)`` —
both of which this module has *already* proved belong to the caller's school within
the same call, the exam through :func:`_exam_in_school` and the pupil through
:func:`_student_in_school`. That is a weaker guarantee than a column filter and it is
written down here rather than left for a reader to notice.

The decision is a race, and the write is the referee
----------------------------------------------------
Two people may decide the same request at the same moment — the invigilator of the
class and the vice principal both have the button. Reading the row and then writing it
is one interleaving away from two decisions and two audit rows, so the write itself
carries the condition (``status = 'pending'``) and **an empty result is the refusal**:
whoever loses the race is told the request was already decided rather than being
allowed to overwrite the winner. The read that precedes it only chooses the sentence;
it is never what decides.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: The two decisions a reader can take. `withdrawn` exists in the CHECK constraint for
#: a future pupil-initiated retraction and is deliberately not offered here.
DECISIONS = ("approved", "rejected")

#: Every reason a write can refuse, as a key the pages translate. Kept together so the
#: bilingual catalogue (`app/templates/shared/_invigilation_reasons.html`) can be
#: checked against it — a refusal with no sentence renders as an empty alert in both
#: languages, which is a button that does nothing with no explanation.
REFUSALS = (
    "exam_not_in_school",
    "class_not_in_school",
    "teacher_not_in_school",
    "schedule_not_in_school",
    "student_not_in_school",
    "not_found",
    "not_invigilated",
    "already_decided",
    "already_requested",
    "nothing_to_retake",
    "bad_decision",
    "write_failed",
)

#: The sentences a *successful* write flashes. Same reason as :data:`REFUSALS`: the
#: page looks the key up in one place, so a key it does not hold renders as an empty
#: green alert — a button that appears to have done nothing.
NOTICES = ("retake_requested", "retake_decided")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read.

    These are lookups behind a button, not the point of a page: a Supabase hiccup
    should say "not found on this sheet" rather than answer the operator a 500.
    """
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("invigilation: read failed: %s", exc)
        return []


def _row_in_school(supabase, table: str, school_id: str, row_id: str,
                   columns: str = "id") -> dict | None:
    """The row, if it exists **in this school**.

    One query, two conditions, no branch that could read the row first and filter it
    afterwards — the filter is the read.
    """
    rows = _rows(supabase.table(table).select(columns)
                 .eq("id", row_id).eq("school_id", school_id))
    return rows[0] if rows else None


def _exam_in_school(supabase, school_id: str, exam_id: str) -> bool:
    """Whether this exam may be scheduled by this school.

    Two ways to be true, and the second is not a loophole:

    * the exam's own ``school_id`` is this school — the normal case;
    * the exam's ``school_id`` is **null**, which older rows can be (the column is
      ``ON DELETE SET NULL`` and predates the school-scoped flows), *and* its author
      teaches here. A teacher's own paper is this school's paper even when the column
      was never backfilled, and refusing it would make a legacy exam unschedulable
      with no error the school could act on.

    A second school's exam is neither, so it is refused.
    """
    rows = _rows(supabase.table("exams").select("id, school_id, teacher_id")
                 .eq("id", exam_id))
    if not rows:
        return False
    exam = rows[0]
    if exam.get("school_id"):
        return str(exam["school_id"]) == str(school_id)
    return bool(_row_in_school(supabase, "teachers", school_id, exam.get("teacher_id")))


# ── the schedule: what a vice principal builds ───────────────────────────────

def save_schedule(supabase, school_id: str, *, exam_id: str, class_id: str,
                  scheduled_at: str, room: str = "", notes: str = "",
                  actor_id: str | None = None) -> dict:
    """Create or move the sitting of one exam in one class.

    Unique per ``(exam, class)`` at the database level and matched here, so the POST
    from a page that was open twice edits the row rather than raising a constraint
    error the operator cannot act on.
    """
    if not _exam_in_school(supabase, school_id, exam_id):
        return {"ok": False, "reason": "exam_not_in_school"}
    if not _row_in_school(supabase, "classes", school_id, class_id):
        return {"ok": False, "reason": "class_not_in_school"}
    if not scheduled_at:
        return {"ok": False, "reason": "write_failed"}

    payload = {
        "school_id": school_id,
        "exam_id": exam_id,
        "class_id": class_id,
        "scheduled_at": scheduled_at,
        "room": (room or "").strip() or None,
        "notes": (notes or "").strip() or None,
        "updated_at": _now(),
    }
    if actor_id:
        payload["created_by"] = actor_id

    existing = _rows(supabase.table("invigilation_schedules").select("id")
                     .eq("school_id", school_id)
                     .eq("exam_id", exam_id).eq("class_id", class_id))
    if existing:
        written = _rows(supabase.table("invigilation_schedules").update(payload)
                        .eq("id", existing[0]["id"]).eq("school_id", school_id))
        return {"ok": bool(written), "reason": "" if written else "write_failed",
                "schedule": written[0] if written else None, "created": False}

    written = _rows(supabase.table("invigilation_schedules").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "schedule": written[0] if written else None, "created": True}


def list_schedules(supabase, school_id: str, *, exam_id: str | None = None) -> list[dict]:
    """Every sitting of this school, newest first, with its exam and class named."""
    query = supabase.table("invigilation_schedules").select(
        "id, exam_id, class_id, scheduled_at, room, notes, created_at").eq(
            "school_id", school_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    schedules = _rows(query)
    if not schedules:
        return []
    return _decorate(supabase, school_id, schedules)


def form_options(supabase, school_id: str) -> dict:
    """The three lists the schedule form is built from, each read in this school.

    The form offers only rows the write would accept: an exam from another school
    cannot be picked, so the refusal messages stay for the cases a form cannot
    prevent — a page left open while another school's row appears through a stale
    cache, or a hand-made POST.
    """
    exams = _rows(supabase.table("exams").select("id, title")
                  .eq("school_id", school_id).order("title"))
    classes = _rows(supabase.table("classes").select("id, name")
                    .eq("school_id", school_id).order("name"))
    teachers = []
    for row in _rows(supabase.table("teachers").select("id, profiles!inner(full_name)")
                     .eq("school_id", school_id)):
        teachers.append({"id": str(row["id"]),
                         "name": (row.get("profiles") or {}).get("full_name") or ""})
    teachers.sort(key=lambda t: t["name"].lower())
    return {"exams": exams, "classes": classes, "teachers": teachers}


def _decorate(supabase, school_id: str, schedules: list[dict]) -> list[dict]:
    """Attach the exam title, the class name and the invigilators to each sitting.

    Three reads for the whole page rather than three per row: a school with forty
    classes would otherwise pay a hundred and twenty round-trips at ~150 ms each to
    draw one table.
    """
    exam_ids = sorted({str(s["exam_id"]) for s in schedules if s.get("exam_id")})
    class_ids = sorted({str(s["class_id"]) for s in schedules if s.get("class_id")})
    schedule_ids = [str(s["id"]) for s in schedules]

    exams = {str(r["id"]): r.get("title") or "" for r in _rows(
        supabase.table("exams").select("id, title").in_("id", exam_ids))} if exam_ids else {}
    classes = {str(r["id"]): r.get("name") or "" for r in _rows(
        supabase.table("classes").select("id, name")
        .eq("school_id", school_id).in_("id", class_ids))} if class_ids else {}

    assignments = _rows(supabase.table("invigilator_assignments")
                        .select("id, schedule_id, teacher_id, is_lead")
                        .eq("school_id", school_id)
                        .in_("schedule_id", schedule_ids)) if schedule_ids else []
    teacher_ids = sorted({str(a["teacher_id"]) for a in assignments})
    names = {}
    if teacher_ids:
        names = {str(r["id"]): (r.get("profiles") or {}).get("full_name") or ""
                 for r in _rows(supabase.table("teachers")
                                .select("id, profiles!inner(full_name)")
                                .eq("school_id", school_id)
                                .in_("id", teacher_ids))}

    by_schedule: dict[str, list[dict]] = {}
    for a in assignments:
        by_schedule.setdefault(str(a["schedule_id"]), []).append({
            "id": str(a["id"]),
            "teacher_id": str(a["teacher_id"]),
            "name": names.get(str(a["teacher_id"]), ""),
            "is_lead": bool(a.get("is_lead")),
        })

    out = []
    for s in schedules:
        out.append(dict(s,
                        exam_title=exams.get(str(s.get("exam_id")), ""),
                        class_name=classes.get(str(s.get("class_id")), ""),
                        invigilators=by_schedule.get(str(s["id"]), [])))
    return out


def assign_invigilator(supabase, school_id: str, *, schedule_id: str, teacher_id: str,
                       is_lead: bool = False,
                       actor_id: str | None = None) -> dict:
    """Put one teacher on one sitting, or update the duty they already have."""
    if not _row_in_school(supabase, "invigilation_schedules", school_id, schedule_id):
        return {"ok": False, "reason": "schedule_not_in_school"}
    if not _row_in_school(supabase, "teachers", school_id, teacher_id):
        return {"ok": False, "reason": "teacher_not_in_school"}

    existing = _rows(supabase.table("invigilator_assignments").select("id")
                     .eq("school_id", school_id).eq("schedule_id", schedule_id)
                     .eq("teacher_id", teacher_id))
    if existing:
        written = _rows(supabase.table("invigilator_assignments")
                        .update({"is_lead": bool(is_lead)})
                        .eq("id", existing[0]["id"]).eq("school_id", school_id))
        return {"ok": bool(written), "reason": "" if written else "write_failed",
                "assignment": written[0] if written else None, "created": False}

    payload = {"school_id": school_id, "schedule_id": schedule_id,
               "teacher_id": teacher_id, "is_lead": bool(is_lead)}
    if actor_id:
        payload["created_by"] = actor_id
    written = _rows(supabase.table("invigilator_assignments").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "assignment": written[0] if written else None, "created": True}


def remove_assignment(supabase, school_id: str, assignment_id: str) -> dict:
    """Take one teacher off one sitting. Scoped by school, like every other write."""
    removed = _rows(supabase.table("invigilator_assignments").delete()
                    .eq("id", assignment_id).eq("school_id", school_id))
    return {"ok": bool(removed), "reason": "" if removed else "not_found"}


# ── the teacher's own duty ──────────────────────────────────────────────────

def tasks_for_teacher(supabase, school_id: str, teacher_id: str) -> list[dict]:
    """The sittings this teacher is assigned to, with the rest of their room.

    Read from the assignment, never from the schedule: a teacher's page lists the
    duties that carry their name, and a schedule they are not on is not their duty.
    """
    mine = _rows(supabase.table("invigilator_assignments").select("schedule_id")
                 .eq("school_id", school_id).eq("teacher_id", teacher_id))
    schedule_ids = sorted({str(r["schedule_id"]) for r in mine})
    if not schedule_ids:
        return []
    schedules = _rows(supabase.table("invigilation_schedules")
                      .select("id, exam_id, class_id, scheduled_at, room, notes")
                      .eq("school_id", school_id)
                      .in_("id", schedule_ids))
    return _decorate(supabase, school_id, schedules)


def teacher_board(supabase, school_id: str, teacher_id: str) -> dict:
    """The teacher's duties and the requests they may decide, from one set of reads.

    One function for two callers — the dashboard's card and the page it links to —
    because a card that computed the duty differently from the page is a card that
    sends a teacher to a list that does not contain what the card promised.

    The cost is real and worth naming: with the co-invigilators' names and the pupils'
    names attached, this is about nine reads. The dashboard pays it behind its own
    20-second per-teacher cache, so it is once per teacher per 20 seconds rather than
    once per load, and a teacher with no duty at all pays two (the assignment read,
    which comes back empty, and nothing else).
    """
    tasks = tasks_for_teacher(supabase, school_id, teacher_id)
    exam_ids = {str(t["exam_id"]) for t in tasks if t.get("exam_id")}
    return {
        "tasks": tasks,
        "exam_ids": exam_ids,
        "pending_requests": (retake_requests(supabase, school_id, status="pending",
                                            exam_ids=exam_ids) if exam_ids else []),
    }


def invigilated_exam_ids(supabase, school_id: str, teacher_id: str) -> set[str]:
    """The exams this teacher is an invigilator for, as the ids themselves.

    This is the whole of the teacher's authority over a retake request, so it is one
    function that both the page and the decision read — a page that showed a request
    the decision would refuse, or the other way round, is two implementations of one
    rule.
    """
    mine = _rows(supabase.table("invigilator_assignments").select("schedule_id")
                 .eq("school_id", school_id).eq("teacher_id", teacher_id))
    schedule_ids = sorted({str(r["schedule_id"]) for r in mine})
    if not schedule_ids:
        return set()
    schedules = _rows(supabase.table("invigilation_schedules").select("exam_id")
                      .eq("school_id", school_id).in_("id", schedule_ids))
    return {str(r["exam_id"]) for r in schedules if r.get("exam_id")}


# ── the retake request ──────────────────────────────────────────────────────

def retake_requests(supabase, school_id: str, *, status: str | None = None,
                    exam_ids=None, student_id: str | None = None) -> list[dict]:
    """Requests in this school, optionally only those for a set of exams.

    ``exam_ids`` is how the teacher's page is scoped: passing an **empty set** means
    "this reader invigilates nothing", which must select nothing rather than
    everything, so the caller's ``[]`` is honoured instead of being skipped as falsy.
    """
    if exam_ids is not None and not exam_ids:
        return []
    query = supabase.table("exam_retake_requests").select(
        "id, exam_id, student_id, class_id, reason, status, decided_by, decided_at,"
        " decision_note, created_at").eq("school_id", school_id)
    if status:
        query = query.eq("status", status)
    if exam_ids is not None:
        query = query.in_("exam_id", list(exam_ids))
    if student_id:
        query = query.eq("student_id", student_id)
    rows = _rows(query.order("created_at", desc=True))
    return _decorate_requests(supabase, school_id, rows)


def _decorate_requests(supabase, school_id: str, requests: list[dict]) -> list[dict]:
    """Attach exam title and pupil name — two reads for the page, not two per row."""
    if not requests:
        return []
    exam_ids = sorted({str(r["exam_id"]) for r in requests if r.get("exam_id")})
    student_ids = sorted({str(r["student_id"]) for r in requests if r.get("student_id")})
    exams = {str(r["id"]): r.get("title") or "" for r in _rows(
        supabase.table("exams").select("id, title").in_("id", exam_ids))} if exam_ids else {}
    pupils = {}
    if student_ids:
        pupils = {str(r["id"]): (r.get("profiles") or {}).get("full_name") or ""
                  for r in _rows(supabase.table("students")
                                 .select("id, profiles!inner(full_name)")
                                 .eq("school_id", school_id)
                                 .in_("id", student_ids))}
    return [dict(r,
                 exam_title=exams.get(str(r.get("exam_id")), ""),
                 student_name=pupils.get(str(r.get("student_id")), ""))
            for r in requests]


def request_retake(supabase, school_id: str, *, exam_id: str, student_id: str,
                   reason: str = "") -> dict:
    """A pupil asks to sit a paper again.

    Refused rather than created when there is nothing to sit again: a request whose
    subject never took the paper is a decision about nothing, and it would still
    appear in a teacher's queue as work.
    """
    if not _exam_in_school(supabase, school_id, exam_id):
        return {"ok": False, "reason": "exam_not_in_school"}
    if not _row_in_school(supabase, "students", school_id, student_id):
        return {"ok": False, "reason": "student_not_in_school"}

    submissions = _rows(supabase.table("submissions").select("id, status")
                        .eq("exam_id", exam_id).eq("student_id", student_id))
    if not submissions:
        return {"ok": False, "reason": "nothing_to_retake"}

    open_rows = _rows(supabase.table("exam_retake_requests").select("id")
                      .eq("school_id", school_id).eq("exam_id", exam_id)
                      .eq("student_id", student_id)
                      .in_("status", ["pending", "approved"]))
    if open_rows:
        return {"ok": False, "reason": "already_requested"}

    class_id = subs_class(supabase, school_id, student_id)
    payload = {"school_id": school_id, "exam_id": exam_id, "student_id": student_id,
               "class_id": class_id, "reason": (reason or "").strip() or None,
               "status": "pending"}
    written = _rows(supabase.table("exam_retake_requests").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "request": written[0] if written else None}


def subs_class(supabase, school_id: str, student_id: str) -> str | None:
    """The pupil's class, or None — used to record which room the sitting was in."""
    rows = _rows(supabase.table("students").select("class_id")
                 .eq("id", student_id).eq("school_id", school_id))
    return rows[0].get("class_id") if rows else None


def decide_retake(supabase, school_id: str, request_id: str, *, decision: str,
                  actor_id: str | None = None, note: str = "",
                  within_exam_ids=None) -> dict:
    """Approve or reject one request, and let an approved one actually be sat again.

    ``within_exam_ids`` is the teacher's authority expressed as data: when it is not
    ``None`` the decision is refused unless the request's exam is in that set. A
    teacher's caller passes :func:`invigilated_exam_ids`; a vice principal's passes
    ``None``, because their authority is the whole school and there is nothing to
    narrow.

    The write carries ``status = 'pending'`` and an empty result is the refusal, so
    the losing side of a race is told rather than allowed to overwrite the winner.
    """
    if decision not in DECISIONS:
        return {"ok": False, "reason": "bad_decision"}

    rows = _rows(supabase.table("exam_retake_requests")
                 .select("id, exam_id, student_id, status")
                 .eq("id", request_id).eq("school_id", school_id))
    if not rows:
        return {"ok": False, "reason": "not_found"}
    request = rows[0]

    if within_exam_ids is not None and str(request["exam_id"]) not in {
            str(i) for i in within_exam_ids}:
        return {"ok": False, "reason": "not_invigilated"}
    if request.get("status") != "pending":
        return {"ok": False, "reason": "already_decided"}

    written = _rows(supabase.table("exam_retake_requests").update({
        "status": decision,
        "decided_by": actor_id,
        "decided_at": _now(),
        "decision_note": (note or "").strip() or None,
        "updated_at": _now(),
    }).eq("id", request_id).eq("school_id", school_id).eq("status", "pending"))
    if not written:
        # Someone decided it between the read above and this write. The read is not
        # what decides — this is.
        return {"ok": False, "reason": "already_decided"}

    if decision == "approved":
        retract_submission(supabase, exam_id=request["exam_id"],
                           student_id=request["student_id"])
    return {"ok": True, "reason": "", "request": written[0]}


def retract_submission(supabase, *, exam_id: str, student_id: str) -> dict:
    """Return a paper to the pupil's exam list so an approved retake can be sat.

    ``retracted`` is the status the student routes already treat as "not an attempt":
    the paper comes back into the list and the attempt ceiling stops counting it, so
    an approval needs no second mechanism to take effect.

    Scoped by ``(exam_id, student_id)`` because ``submissions`` has no ``school_id``.
    Both ids were proved to be this school's by the caller — see the module docstring.
    """
    written = _rows(supabase.table("submissions")
                    .update({"status": "retracted", "updated_at": _now()})
                    .eq("exam_id", exam_id).eq("student_id", student_id))
    return {"ok": bool(written)}
