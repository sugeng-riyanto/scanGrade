"""Who a paper is *for*, pupil by pupil.

An exam's audience used to be its classes: tick a class and everyone in it sits
the paper. This service adds the one sentence that was missing — "everyone in
these classes, except these pupils" — so a teacher can leave a child out for
izin, sakit or any other reason without splitting the paper.

The model is deliberately small:

* **Inclusion is a row.** ``exam_target_student`` holds one row per (exam, pupil)
  with ``included`` or ``excluded``. A pupil with **no row** is not part of the
  paper at all — they were never on the roster, or they joined the class after it
  was written. "Excluded on purpose" and "never enrolled" are therefore different
  facts and are never collapsed into one another (`excluded` carries a reason;
  the absence of a row does not).
* **The exam decides which rule applies.** ``exams.target_mode`` is ``class`` for
  every paper written before this table (membership decides, exactly as before)
  and ``students`` once a target list exists. The access check reads that flag, so
  it is one indexed lookup rather than a count that cannot tell "no targets at
  all" from "this pupil is not among them".
* **Nothing is ever deleted on a diff.** A pupil unchecked after submitting keeps
  their attempt; the row flips to ``excluded`` and the history stays readable.
  Dropping and re-inserting the table would break the very link that keeps it.

The roster prefers `student_enrollment` (046) — the per-year membership — and
falls back to ``profiles.class_id`` while a school's enrollment has not been
backfilled yet, so the feature works on the live box today and starts reading the
year table as soon as the backfill runs.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

INCLUDED = "included"
EXCLUDED = "excluded"

#: The columns a roster needs. `profiles` is where a pupil's name and class live.
STUDENT_COLUMNS = "id, full_name, nisn, nis, class_id"

#: The statuses a *sitting* has once it counts. A pupil who reached one of these
#: has a history the target diff must not touch.
SUBMITTED_STATUSES = ("submitted", "graded", "published")


# ── the roster a paper draws from ────────────────────────────────────────────

def roster_for_classes(supabase, school_id, class_ids, year_id=None) -> list[dict]:
    """The pupils in ``class_ids``, each as ``{student_id, class_id, full_name}``.

    Prefers `student_enrollment` (the year's membership) and falls back to
    `profiles.class_id`. The fallback is not a lesser guess: it is the exact
    pointer the rest of the app uses today, and reading it rather than an empty
    enrollment table is what lets a school use targets before its backfill has
    run.

    Duplicated by design: a pupil appears once per class they are a member of, and
    the caller groups them by class for display.
    """
    wanted = [str(c) for c in (class_ids or []) if c]
    if not school_id or not wanted:
        return []

    enrolled = _enrolled_roster(supabase, school_id, wanted, year_id)
    if enrolled:
        return enrolled
    return _profile_roster(supabase, school_id, wanted)


def _enrolled_roster(supabase, school_id, class_ids, year_id) -> list[dict]:
    try:
        query = (supabase.table("student_enrollment")
                 .select("student_id, class_id")
                 .eq("school_id", school_id).eq("status", "aktif")
                 .in_("class_id", class_ids))
        if year_id:
            query = query.eq("school_year_id", year_id)
        rows = query.execute().data or []
    except Exception:
        logger.debug("enrollment roster read failed", exc_info=True)
        return []
    if not rows:
        return []
    names = _names_for(supabase, [r.get("student_id") for r in rows])
    return [{"student_id": r.get("student_id"), "class_id": r.get("class_id"),
             "full_name": names.get(str(r.get("student_id")), "")}
            for r in rows if r.get("student_id")]


def _profile_roster(supabase, school_id, class_ids) -> list[dict]:
    try:
        rows = (supabase.table("profiles").select(STUDENT_COLUMNS)
                .eq("role", "murid").eq("school_id", school_id)
                .in_("class_id", class_ids).execute().data or [])
    except Exception:
        logger.debug("profile roster read failed", exc_info=True)
        return []
    return [{"student_id": r.get("id"), "class_id": r.get("class_id"),
             "full_name": r.get("full_name") or ""}
            for r in rows if r.get("id")]


def _names_for(supabase, student_ids) -> dict:
    clean = [str(s) for s in student_ids if s]
    if not clean:
        return {}
    try:
        rows = (supabase.table("profiles").select("id, full_name")
                .in_("id", clean).execute().data or [])
    except Exception:
        logger.debug("roster name read failed", exc_info=True)
        return {}
    return {str(r["id"]): (r.get("full_name") or "") for r in rows if r.get("id")}


# ── the target list ──────────────────────────────────────────────────────────

def targets_for_exam(supabase, exam_id) -> dict:
    """``{student_id: row}`` for one paper's target list."""
    if not exam_id:
        return {}
    try:
        rows = (supabase.table("exam_target_student")
                .select("student_id, class_id, status, reason, set_at")
                .eq("exam_id", exam_id).execute().data or [])
    except Exception:
        logger.debug("target read failed for %s", exam_id, exc_info=True)
        return {}
    return {str(r["student_id"]): r for r in rows if r.get("student_id")}


def included_student_ids(supabase, exam_id) -> set:
    """The pupils a paper is currently for. Empty for a 'class' paper."""
    return {sid for sid, row in targets_for_exam(supabase, exam_id).items()
            if row.get("status") == INCLUDED}


def included_exam_ids(supabase, student_id, exam_ids) -> set:
    """Which of ``exam_ids`` this pupil is *included* in, in one query.

    The list pages need this for every target paper on the page at once; asking
    per paper would be the N+1 Fase 6 forbids, on the page a whole class opens at
    the same minute.
    """
    wanted = [str(e) for e in (exam_ids or []) if e]
    if not student_id or not wanted:
        return set()
    try:
        rows = (supabase.table("exam_target_student")
                .select("exam_id").eq("student_id", student_id)
                .eq("status", INCLUDED).in_("exam_id", wanted).execute().data or [])
    except Exception:
        logger.warning("batch target check failed for %s", student_id, exc_info=True)
        return set()
    return {str(r["exam_id"]) for r in rows if r.get("exam_id")}


def student_is_included(supabase, exam_id, student_id) -> bool:
    """One indexed lookup: is this pupil on the paper's included list?

    Fails **closed** on a read error — a target paper is only ever opened by a
    pupil who can be shown to be on it, so a failure that cannot answer the
    question must not answer yes.
    """
    if not exam_id or not student_id:
        return False
    try:
        row = (supabase.table("exam_target_student")
               .select("status").eq("exam_id", exam_id)
               .eq("student_id", student_id).limit(1).execute().data)
    except Exception:
        logger.warning("target check failed for %s/%s", exam_id, student_id,
                       exc_info=True)
        return False
    if not row:
        return False
    first = row[0] if isinstance(row, list) else row
    return (first or {}).get("status") == INCLUDED


# ── the diff that create/edit writes ─────────────────────────────────────────

def plan_diff(roster, existing, included_ids, reasons) -> list[dict]:
    """What to write for one paper. Pure — no I/O, so the rule is testable.

    ``roster`` is ``[{student_id, class_id}]``; ``existing`` is the current target
    map; ``included_ids`` is the set the teacher left ticked; ``reasons`` maps a
    pupil id to the free text typed beside an unchecked name.

    A pupil removed **from the roster** (not in ``roster``) keeps whatever row
    they had: they belong to another class or an earlier year, and the diff is not
    the place that decides their fate. Only the pupils the teacher is looking at
    are rewritten, which is what keeps this from touching a history it cannot see.
    """
    included = {str(s) for s in (included_ids or [])}
    rows = []
    for entry in roster:
        sid = str(entry.get("student_id") or "")
        if not sid:
            continue
        status = INCLUDED if sid in included else EXCLUDED
        before = existing.get(sid) or {}
        reason = (reasons or {}).get(sid) or None
        if status == INCLUDED:
            reason = None
        if (before.get("status") == status and before.get("class_id") == entry.get("class_id")
                and (before.get("reason") or None) == (reason or None)):
            continue                      # already says exactly this — idempotent
        rows.append({"student_id": sid, "class_id": entry.get("class_id"),
                     "status": status, "reason": reason})
    return rows


def apply_diff(supabase, exam_id, school_id, rows, set_by=None) -> int:
    """Write a planned diff, and switch the paper to per-student mode.

    Bulk upsert on ``(exam_id, student_id)``, so a re-run after a partial failure
    and a first run are the same operation. The ``target_mode`` flip is part of
    the same write: once a paper has a target list, access reads the list.
    """
    if not rows:
        return 0
    payload = [{"exam_id": exam_id, **row, "set_by": set_by} for row in rows]
    try:
        supabase.table("exam_target_student").upsert(
            payload, on_conflict="exam_id,student_id").execute()
    except Exception:
        logger.exception("could not write exam targets for %s", exam_id)
        return 0
    try:
        supabase.table("exams").update({"target_mode": "students"}) \
            .eq("id", exam_id).eq("school_id", school_id).execute()
    except Exception:
        # The rows are the truth; the flag is the fast path. If the flag write
        # fails the next access check still falls back to class mode — safe, just
        # wider — and a later edit flips it. Never raise: the targets are saved.
        logger.warning("could not set target_mode for exam %s", exam_id, exc_info=True)
    return len(payload)


def submission_statuses(supabase, exam_id) -> dict:
    """``{student_id: status}`` for the papers a pupil has already handed in.

    Used by the roster to mark a pupil who has submitted, and by the dashboard so
    an excluded-after-submit pupil is shown as history rather than as missing.
    """
    if not exam_id:
        return {}
    try:
        rows = (supabase.table("submissions").select("student_id, status")
                .eq("exam_id", exam_id).execute().data or [])
    except Exception:
        logger.debug("submission status read failed for %s", exam_id, exc_info=True)
        return {}
    out: dict = {}
    for r in rows:
        sid = str(r.get("student_id") or "")
        if sid:
            out[sid] = r.get("status")
    return out


def targets_from_form(form, roster) -> tuple[set, dict]:
    """Read the roster form: the ticked ids and the reason per unticked pupil.

    The marker ``target_present`` separates "the teacher unchecked everyone" from
    "another caller posted only a title" — without it an unrelated edit would wipe
    the selection, the same trap `assignment_present` guards on the teacher form.
    The caller checks the marker; this reads what was posted.
    """
    included = {str(s) for s in form.getlist("target_students")}
    reasons = {}
    for entry in roster:
        sid = str(entry.get("student_id") or "")
        if not sid:
            continue
        reason = (form.get(f"target_reason_{sid}") or "").strip()
        if reason:
            reasons[sid] = reason
    return included, reasons


def sync_from_form(supabase, exam_id, school_id, class_ids, form, *, set_by=None,
                   year_id=None) -> dict:
    """Write a teacher's roster choices for one paper, and report what changed.

    Returns ``{roster, included, excluded, written, skipped}``. ``skipped`` is set
    when the form carried no target section at all (an unrelated edit), in which
    case nothing is touched.

    **A paper with everyone included stays in class mode.** Writing a row for every
    pupil of every paper would be a table full of "yes" that says nothing, and one
    more lookup on the hot access path for no question asked. Rows are written only
    once there is at least one exception; that is what flips the paper to
    ``students`` mode, and it is exactly when the list is needed.
    """
    # `target_loaded` is the roster rendered the pupils at all. Without it a
    # selection that arrived empty would be indistinguishable from "the teacher
    # unchecked every name" and would exclude the whole class; with it, an empty
    # list means the teacher genuinely cleared it.
    if not form.get("target_present") or not form.get("target_loaded"):
        return {"roster": 0, "included": 0, "excluded": 0, "written": 0,
                "skipped": True}

    roster = roster_for_classes(supabase, school_id, class_ids, year_id)
    if not roster:
        # No roster to write — leave any previous list alone rather than wipe it
        # because a read failed.
        return {"roster": 0, "included": 0, "excluded": 0, "written": 0,
                "skipped": True}

    included, reasons = targets_from_form(form, roster)
    roster_ids = [str(e["student_id"]) for e in roster]
    included &= set(roster_ids)
    excluded = [sid for sid in roster_ids if sid not in included]
    existing = targets_for_exam(supabase, exam_id)

    if not excluded:
        # Everyone is on the paper. If it already had a list, keep it in step
        # (a pupil re-ticked after being unchecked returns to `included`); if it
        # never had one, leave it in class mode and write nothing.
        if not existing:
            return {"roster": len(roster), "included": len(included),
                    "excluded": 0, "written": 0, "skipped": False}

    rows = plan_diff(roster, existing, included, reasons)
    written = apply_diff(supabase, exam_id, school_id, rows, set_by=set_by)
    return {"roster": len(roster), "included": len(included),
            "excluded": len(excluded), "written": written, "skipped": False}
