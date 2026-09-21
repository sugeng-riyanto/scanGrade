"""Delete assessment data for a *named* set of schools — and nothing else.

Both destructive buttons on /super-admin used to loop over a list of tables and
delete every row with ``.neq("id", "00000000-…")``. That predicate is a
tautology: no row carries the nil UUID, so it matches all of them, in every
school. One is labelled "reset demo data" and the other "reset exam data", and
between them they emptied ten tables for the whole platform — the very tables a
real school's year lives in. The server holds the service key, so RLS did not
narrow it either.

A super admin *should* be able to clear data; that is what the role is for. What
was missing is the parameter, so "which rows" travelled as a tautology instead
of as a list of schools. This module is that parameter.

The two table families are separate because they are reached differently:

* :data:`SCHOOL_TABLES` carry ``school_id`` outright.
* :data:`EXAM_TABLES` reach their school through ``exam_id`` — they have no
  ``school_id`` column, so they are cleared by the ids of the school's exams.

Dependents are always cleared before their exam, because the migrations do not
declare the foreign keys, so nothing guarantees a cascade.
"""
import logging

logger = logging.getLogger(__name__)

#: Tables that name their school directly.
SCHOOL_TABLES = (
    "teacher_assignments", "exams", "students", "teachers", "classes", "subjects",
)

#: Tables that reach their school through the exam that owns them.
EXAM_TABLES = (
    "submissions", "violation_logs", "exam_access_codes", "analytics_cache",
)


def school_ids_for_npsns(supabase, npsns) -> list[str]:
    """The ids of the schools with these NPSNs, in whatever order they come back."""
    npsns = [str(n).strip() for n in npsns if str(n).strip()]
    if not npsns:
        return []
    rows = (
        supabase.table("schools").select("id").in_("npsn", npsns).execute().data or []
    )
    return [r["id"] for r in rows if r.get("id")]


def exam_ids_for_schools(supabase, school_ids) -> list[str]:
    """The ids of every exam owned by these schools."""
    if not school_ids:
        return []
    rows = (
        supabase.table("exams").select("id").in_("school_id", school_ids).execute().data or []
    )
    return [r["id"] for r in rows if r.get("id")]


def clear_schools(supabase, school_ids, *, school_tables=SCHOOL_TABLES,
                  exam_tables=EXAM_TABLES) -> dict:
    """Delete these schools' rows, table by table.

    Returns ``{"cleared": n, "errors": [...]}`` where ``cleared`` counts tables
    that ran without error. An empty ``school_ids`` clears NOTHING — the guard
    that matters most, because the caller resolves those ids from an NPSN and a
    lookup that finds nothing must never mean "all of them".
    """
    report = {"cleared": 0, "errors": []}
    school_ids = [s for s in (school_ids or []) if s]
    if not school_ids:
        return report

    exam_ids = exam_ids_for_schools(supabase, school_ids) if exam_tables else []

    # Dependents first: an exam is deleted only after the rows that point at it.
    for table in exam_tables:
        if not exam_ids:
            break
        try:
            supabase.table(table).delete().in_("exam_id", exam_ids).execute()
            report["cleared"] += 1
        except Exception as e:                          # noqa: BLE001 - reported
            report["errors"].append(f"{table}: {str(e)[:60]}")

    for table in school_tables:
        try:
            supabase.table(table).delete().in_("school_id", school_ids).execute()
            report["cleared"] += 1
        except Exception as e:                          # noqa: BLE001 - reported
            report["errors"].append(f"{table}: {str(e)[:60]}")

    return report
