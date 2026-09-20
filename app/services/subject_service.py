"""What deleting a subject would take with it.

Why this is not just a count in the view
----------------------------------------
A subject is a school-wide row. Two other tables name it:

* ``teacher_assignments.subject_id`` — the delete path removes these first, so
  every teacher who assigned the subject silently loses the assignment.
* ``exams.subject_id`` — declared ``ON DELETE SET NULL``, so the exam survives
  but forgets which subject it was for. That is data loss with no error and no
  trace, and it is the reason a delete must be *shown* before it happens.

Both the admin page and the teacher's subject list delete subjects, so the
question lives here rather than in either route.
"""


def subject_usage(supabase, subject_id) -> dict:
    """``{"assignments": n, "exams": m}`` for one subject.

    Counts are read with ``count="exact"`` so the whole row set never travels;
    a subject used by 40 classes costs one count each. A read that fails reports
    zero rather than raising — a delete that cannot measure its blast radius is
    still better refused by the caller's confirm gate than crashed here.
    """
    usage = {"assignments": 0, "exams": 0}
    try:
        res = (
            supabase.table("teacher_assignments").select("id", count="exact")
            .eq("subject_id", subject_id).execute()
        )
        usage["assignments"] = res.count or 0
    except Exception:
        pass
    try:
        res = (
            supabase.table("exams").select("id", count="exact")
            .eq("subject_id", subject_id).execute()
        )
        usage["exams"] = res.count or 0
    except Exception:
        pass
    return usage


def usage_confirmation_needed(usage: dict) -> bool:
    """True when the delete would break a reference and must be confirmed."""
    return bool(usage.get("assignments") or usage.get("exams"))


def usage_message(usage: dict, lang: str = "id") -> str:
    """The sentence shown when a delete needs confirmation."""
    assignments = usage.get("assignments", 0)
    exams = usage.get("exams", 0)
    if lang == "en":
        return (
            f"This subject is still used by {assignments} teacher "
            f"assignment(s) and {exams} exam(s). Deleting it releases all of "
            f"them — repeat to confirm."
        )
    return (
        f"Mapel ini masih dipakai {assignments} penugasan guru dan {exams} "
        f"ujian. Hapus akan melepas semuanya — ulangi untuk konfirmasi."
    )
