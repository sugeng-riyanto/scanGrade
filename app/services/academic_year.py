"""Closing a school year, and opening the next one, without losing who was where.

Three things happen together at the turn of a year, and doing them by hand is how
history gets overwritten:

1. the old year is **closed** — its papers and marks become history, and a late
   edit can no longer change what a pupil was already shown;
2. a new year is created as a **draft**, so classes can be prepared against it
   without it becoming the running year by the mere act of existing;
3. every pupil gets an **outcome** — `naik`, `tinggal_kelas`, `lulus`, `pindah` —
   recorded in `student_enrollment` (migration 046), which is what makes "stayed
   back" distinguishable from "advanced" and from "left".

The plan is built first and shown, then applied. Nothing here writes on the read
path: `plan_close` reads and decides, `apply_close` writes, and the route shows
the first before running the second.
"""
from __future__ import annotations

import logging

from app.services import enrollment

logger = logging.getLogger(__name__)

#: A year's life. Kept in step with the CHECK in migration 047 by its test.
STATUSES = ("draft", "active", "closed")

#: The outcome words, re-exported from `enrollment` so a caller has one import.
OUTCOMES = enrollment.STATUSES


# ── lifecycle ────────────────────────────────────────────────────────────────

def is_closed(supabase, school_year_id) -> bool:
    """Whether this year is finished, and therefore read-only.

    Fails **open** on a read error in the sense that it does NOT claim closed —
    but the caller must treat a year it could not read as *not editable* rather
    than *editable*. `editable()` is the answer callers should use.
    """
    if not school_year_id:
        return False
    try:
        res = (supabase.table("school_years").select("status")
               .eq("id", school_year_id).limit(1).execute().data or [])
    except Exception:
        logger.debug("year status read failed for %s", school_year_id, exc_info=True)
        return False
    return bool(res) and res[0].get("status") == "closed"


def editable(supabase, school_year_id) -> bool:
    """Whether writes may land in this year. A year that cannot be read is not."""
    if not school_year_id:
        return False
    try:
        res = (supabase.table("school_years").select("status")
               .eq("id", school_year_id).limit(1).execute().data or [])
    except Exception:
        return False
    if not res:
        return False
    return res[0].get("status") != "closed"


# ── which year a write belongs to ────────────────────────────────────────────
#
# The rule "a closed year is read-only" is only enforceable if a write can name
# the year it lands in. An exam carries `school_year_id` (046); a submission
# reaches it through its exam; and an exam written before the column existed is
# dated by the classes it is attached to — the same derivation
# `deploy/backfill_enrollment.py` uses, and the same refusal to guess.

def year_of_exam(supabase, exam_id) -> str | None:
    """The school year a paper belongs to, or None when it cannot be shown.

    None means "not known", and it is deliberately NOT the same as "open": a
    caller that cannot read the exam must decide what to do about that, rather
    than being told a closed year is fine.
    """
    if not exam_id:
        return None
    try:
        rows = (supabase.table("exams")
                .select("id, school_year_id, class_ids")
                .eq("id", exam_id).limit(1).execute().data or [])
    except Exception:
        logger.debug("exam year read failed for %s", exam_id, exc_info=True)
        return None
    if not rows:
        return None
    exam = rows[0]
    if exam.get("school_year_id"):
        return exam["school_year_id"]
    return _year_from_classes(supabase, exam.get("class_ids"))


def _year_from_classes(supabase, class_ids) -> str | None:
    """The year of the class a paper is attached to, when they all agree."""
    import json as _json
    raw = class_ids or []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (_json.JSONDecodeError, TypeError):
            raw = []
    ids = [str(c) for c in raw if c]
    if not ids:
        return None
    try:
        rows = (supabase.table("classes").select("id, school_year_id")
                .in_("id", ids).execute().data or [])
    except Exception:
        return None
    years = {r.get("school_year_id") for r in rows if r.get("school_year_id")}
    return years.pop() if len(years) == 1 else None


def year_of_submission(supabase, submission_id) -> str | None:
    """The year of the paper a submission belongs to."""
    if not submission_id:
        return None
    try:
        rows = (supabase.table("submissions").select("exam_id")
                .eq("id", submission_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return year_of_exam(supabase, rows[0].get("exam_id"))


def year_of_class(supabase, class_id) -> str | None:
    """The year a class belongs to. A class is a year's roster, not a permanent one."""
    if not class_id:
        return None
    try:
        rows = (supabase.table("classes").select("school_year_id")
                .eq("id", class_id).limit(1).execute().data or [])
    except Exception:
        logger.debug("class year read failed for %s", class_id, exc_info=True)
        return None
    return (rows[0].get("school_year_id") if rows else None)


def year_of_whiteboard(supabase, whiteboard_id) -> str | None:
    """A whiteboard is a class's surface, so its year is the class's year."""
    if not whiteboard_id:
        return None
    try:
        rows = (supabase.table("whiteboards").select("class_id")
                .eq("id", whiteboard_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return year_of_class(supabase, rows[0].get("class_id"))


def year_of_schedule(supabase, schedule_id) -> str | None:
    """An invigilation sitting is a paper in a room, so its year is the paper's."""
    if not schedule_id:
        return None
    try:
        rows = (supabase.table("invigilation_schedules").select("exam_id")
                .eq("id", schedule_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return year_of_exam(supabase, rows[0].get("exam_id"))


def year_of_invigilator_assignment(supabase, assignment_id) -> str | None:
    """An invigilator's duty reaches the year through the sitting it belongs to."""
    if not assignment_id:
        return None
    try:
        rows = (supabase.table("invigilator_assignments").select("schedule_id")
                .eq("id", assignment_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return year_of_schedule(supabase, rows[0].get("schedule_id"))


def year_of_retake_request(supabase, request_id) -> str | None:
    """A retake request names the paper the pupil wants another sitting of."""
    if not request_id:
        return None
    try:
        rows = (supabase.table("exam_retake_requests").select("exam_id")
                .eq("id", request_id).limit(1).execute().data or [])
    except Exception:
        return None
    if not rows:
        return None
    return year_of_exam(supabase, rows[0].get("exam_id"))


def write_refusal(supabase, year_id):
    """Return a reason string when this year may not be written to, else None.

    A year the caller could not read is *not* refused here: refusing every write
    whenever a read blips would take the whole app down for a missing row. The
    refusal is specifically "this year is CLOSED" — the one rule the wizard
    promises — and `year_of_exam` returning None is handled by the caller.
    """
    if not year_id:
        return None
    try:
        rows = (supabase.table("school_years").select("status")
                .eq("id", year_id).limit(1).execute().data or [])
    except Exception:
        return None
    if rows and rows[0].get("status") == "closed":
        return ("Tahun ajaran ini sudah ditutup dan bersifat arsip. "
                "Nilai dan ujian di dalamnya tidak bisa diubah lagi.")
    return None


def close_year(supabase, school_id, year_id) -> None:
    """Finish a year: closed, and no longer the running one."""
    (supabase.table("school_years")
     .update({"status": "closed", "is_active": False})
     .eq("id", year_id).eq("school_id", school_id).execute())


def create_draft_year(supabase, school_id, name, start_date, end_date) -> dict:
    """The next year, as a draft — real, but not yet running."""
    res = (supabase.table("school_years").insert({
        "school_id": school_id, "name": name,
        "start_date": start_date, "end_date": end_date,
        "status": "draft", "is_active": False,
    }).execute())
    return (res.data or [{}])[0]


def activate_year(supabase, school_id, year_id) -> None:
    """Make a year the running one. Exactly one is active at a time."""
    (supabase.table("school_years")
     .update({"is_active": False, "status": "closed"})
     .eq("school_id", school_id).eq("is_active", True).execute())
    (supabase.table("school_years")
     .update({"is_active": True, "status": "active"})
     .eq("id", year_id).eq("school_id", school_id).execute())


# ── the close plan ───────────────────────────────────────────────────────────

def _class_grades(classes) -> dict:
    return {str(c["id"]): str(c.get("grade_level") or "") for c in classes}


def plan_close(supabase, school_id, from_year_id, to_year_id=None,
               classes=None, pupils=None) -> dict:
    """Who moves where, what each one's outcome is, and what cannot be decided.

    Returns `{"rows": [...], "errors": [...], "to_year": ..., "levels": [...]}`.
    Each row is a decision the admin can override in the form; each error is a
    pupil the wizard refuses to guess about, named with the reason.

    `rows` shape: `{student_id, name, from_class_id, from_class, grade_level,
    outcome, to_class_id, to_class}`. `outcome` defaults by rule — a school's
    last grade graduates, every other grade advances — and the wizard may change
    it per pupil.
    """
    classes = classes if classes is not None else (
        supabase.table("classes")
        .select("id, name, grade_level, school_year_id")
        .eq("school_id", school_id).execute().data or [])

    levels = enrollment.grade_order([c.get("grade_level") for c in classes])
    by_id = {str(c["id"]): c for c in classes}

    # The class a pupil is in *this year*, from the enrollment row when there is
    # one, else the two legacy pointers (see the backfill script: they disagree).
    if pupils is None:
        pupils = _pupils_for_year(supabase, school_id, from_year_id, classes)

    # Target classes in the next year, by grade, so "naik" can be matched to a
    # class that exists rather than invented.
    target_by_grade = {}
    if to_year_id:
        for c in classes:
            if str(c.get("school_year_id") or "") == str(to_year_id):
                target_by_grade.setdefault(str(c.get("grade_level") or ""), []).append(c)

    rows, errors = [], []
    for p in pupils:
        from_class = by_id.get(str(p.get("class_id")))
        if from_class is None:
            errors.append({"student_id": p.get("id"), "name": p.get("name"),
                           "reason": "pupil has no class this year"})
            continue
        grade = str(from_class.get("grade_level") or "").strip()
        outcome = enrollment.default_outcome(grade, levels)
        target = None
        if outcome == "naik":
            candidates = target_by_grade.get(_next_grade(grade, levels), [])
            target = candidates[0] if candidates else None
            if to_year_id and not candidates:
                # A year was chosen but has no class at the next grade yet. That
                # is a real gap, not a guess: the admin must create it.
                errors.append({"student_id": p.get("id"), "name": p.get("name"),
                               "reason": "no class at grade %s in the next year"
                                         % _next_grade(grade, levels)})
                continue
        rows.append({
            "student_id": p.get("id"),
            "name": p.get("name") or "",
            "from_class_id": from_class["id"],
            "from_class": from_class.get("name") or "",
            "grade_level": grade,
            "outcome": outcome,
            "to_class_id": (target or {}).get("id"),
            "to_class": (target or {}).get("name") or "",
        })
    return {"rows": rows, "errors": errors, "to_year": to_year_id,
            "levels": levels, "from_year": from_year_id}


def _next_grade(grade, levels) -> str:
    """The grade after `grade` in `levels`, or "" for a school's last grade."""
    ordered = enrollment.grade_order(levels)
    if grade in ordered:
        index = ordered.index(grade)
        if index + 1 < len(ordered):
            return ordered[index + 1]
    return ""


def _pupils_for_year(supabase, school_id, from_year_id, classes) -> list:
    """Every pupil in the closing year, with the class they sit in.

    Enrollment rows are the honest source; the two legacy pointers are the
    fallback for a school that has not been backfilled yet, and they are merged
    the same way `deploy/backfill_enrollment.py` merges them.
    """
    enrolled = []
    if from_year_id:
        enr = (supabase.table("student_enrollment")
               .select("student_id, class_id, school_years(name)")
               .eq("school_id", school_id)
               .eq("school_year_id", from_year_id).execute().data or [])
        for e in enr:
            enrolled.append({"id": e.get("student_id"), "class_id": e.get("class_id"),
                             "name": ""})
        if enrolled:
            return enrolled
        # No enrollment rows for this year at all — fall through to the pointers.
        logger.info("no enrollment rows for year %s; using the legacy class pointers",
                    from_year_id)

    students = (supabase.table("students").select("id, class_id, status")
                .eq("school_id", school_id).execute().data or [])
    profiles = (supabase.table("profiles").select("id, class_id, full_name")
                .eq("school_id", school_id).eq("role", "murid").execute().data or [])
    merged = {}
    for s in students:
        merged[str(s["id"])] = {"id": s["id"], "class_id": s.get("class_id"),
                                "name": ""}
    for p in profiles:
        key = str(p["id"])
        known = merged.get(key, {}).get("class_id")
        merged[key] = {"id": p["id"], "class_id": known or p.get("class_id"),
                       "name": p.get("full_name") or ""}
    return list(merged.values())


def apply_close(supabase, school_id, plan, overrides=None) -> dict:
    """Write a plan: outcomes to the old year, membership to the new one.

    `overrides` maps `student_id -> outcome` (or `-> {"outcome", "to_class_id"}`)
    from the form, so a per-pupil decision beats the default. Returns a per-row
    report: `{"moved", "recorded", "errors"}`, where an error names the pupil.
    """
    overrides = overrides or {}
    to_year = plan.get("to_year")
    from_year = plan.get("from_year")
    moved, recorded, errors = 0, 0, []

    for row in plan["rows"]:
        student_id = row["student_id"]
        choice = overrides.get(student_id)
        if isinstance(choice, dict):
            outcome = enrollment.normalise_status(choice.get("outcome"), row["outcome"])
            to_class_id = choice.get("to_class_id") or row.get("to_class_id")
        else:
            outcome = enrollment.normalise_status(choice, row["outcome"])
            to_class_id = row.get("to_class_id")
        try:
            # The closing year's outcome: this is what the pupil's history says
            # happened, and it is written whether or not they continue.
            enrollment.record(supabase, school_id, student_id, from_year,
                              row["from_class_id"], status=outcome)
            recorded += 1
            if outcome == "naik":
                if not to_class_id:
                    errors.append({"student_id": student_id,
                                   "reason": "advanced with no target class"})
                    continue
                # Both class pointers move together — the roster reads one and the
                # promote form counts the other; leaving them split is the defect
                # the backfill script exists to repair.
                (supabase.table("students").update({"class_id": to_class_id})
                 .eq("id", student_id).execute())
                (supabase.table("profiles").update({"class_id": to_class_id})
                 .eq("id", student_id).execute())
                if to_year:
                    enrollment.record(supabase, school_id, student_id, to_year,
                                      to_class_id, status="aktif")
                moved += 1
        except Exception as e:
            errors.append({"student_id": student_id, "reason": str(e)[:160]})
    return {"moved": moved, "recorded": recorded, "errors": errors}
