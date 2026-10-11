"""Rows whose `school_id` disagrees with the school of the people they point at.

This is the check behind a week of "the number looks wrong" reports. Each was a
slightly different symptom of one fault — *"Kenapa 306 mapel. Harusnya hanya mapel
sesuai NPSN"*, a class list that offered another school's subjects, a page that
answered a school question about a pupil — and each was found by a human reading a
page, days later. The rows themselves were never wrong *in a way a page could see*:
they were well-formed, and the only thing broken about them was that two of their
columns disagrees.

What a cross-school row is, concretely. The tenant boundary in this app is
`school_id`, and it is copied onto every row that belongs to one school, so a row
can name a school in more than one place:

* a **class** names a school, and so does every **pupil** in it (`profiles.class_id`
  → `profiles.school_id`);
* a **subject** names a school, and so does every **offering** of it
  (`class_subjects.school_id`) and every **assignment** of a teacher to it
  (`teacher_assignments.school_id`);
* an **exam** names a school, and so does the **class** its candidates sit in
  (`exams.class_id` → `classes.school_id`);
* a **sitting** (`submissions`) holds no school of its own — it joins a paper to a
  pupil, and the two must agree (`exams.school_id` vs `profiles.school_id`);
* a **subject level** (`student_subject_levels`) names a school, and so do the
  **pupil** on that track and the **class** they were placed in.

Any pair that disagrees on the school is a row that will be read by one school's
pages while describing another school's people — which is exactly how a subject list
"mixed with other NPSN" and how an assignment count named a school's own subjects
while numbering another's. A paper scheduled in another school's class, a pupil
sitting a paper that is not their school's, and a level set against another
school's pupil are the same fault in three more tables.

Three decisions, each of which a caller could get wrong:

* **Install-wide, never per-school.** A sweep filtered by one school discards the
  row it is looking for, because the offending row is the one that names a
  *different* school. The dashboard runs it across the whole install; there is no
  school filter to pass.
* **A failed read is not a clean report.** Every other reader in this repository
  turns a failure into an empty list, and for a timeline or a code list that is
  right — a page that 500s because its evidence is missing is worse than one that
  says it has none. Here the opposite holds: "no cross-school rows" said because
  the query failed is the one answer that must never be a guess. So a read failure
  is collected in `errors` and `ok` is False; findings already proven are still
  returned beside it.
* **Unknown is not a contradiction.** A pupil with no `school_id` on file cannot be
  compared with their class and is *not* a finding — reporting every legacy NULL as
  a cross-school row is how a real alarm stops being read. Only two present,
  different values are a mismatch.

Read-only, and bounded: each finding is one row, the list is capped, and `truncated`
says when the cap was reached.

Called from the super-admin dashboard (`app/routes/super_admin.py`) through the
shared request cache, so the operator who has to act on a mixture sees it on the
page they already open rather than on one they would have to know to visit.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: How many findings are returned. A box with a systematic fault would otherwise
#: put thousands of rows on one page; the cap plus `truncated` says "and more".
MAX_FINDINGS = 200

#: `source` names the two places a row can disagree about a school. Kept as data so
#: a page can group by it without knowing the checks.
CLASS_PUPIL = "class_pupil_mismatch"
PAIR = "pair_school_mismatch"
ASSIGNMENT = "assignment_school_mismatch"
EXAM = "exam_school_mismatch"
SUBMISSION = "submission_school_mismatch"
LEVEL = "subject_level_school_mismatch"

def _read(supabase, table, columns, *, errors, not_null=None, in_=None, **filters):
    """One read of one table, with its failure recorded rather than swallowed.

    The failure is *returned* to the caller as an entry in `errors` instead of
    being turned into an empty list: for this sweep, "could not read" and "read
    and found nothing" are different answers and the whole point is not to
    confuse them.

    ``in_`` is ``(column, values)`` for a read bounded to a set of ids — the
    pupil schools behind the submissions and levels. An empty set is no read at
    all: ``.in_("id", [])`` is not a query for nothing, it is a query for
    nothing PostgREST will answer.
    """
    if in_ is not None and not list(in_[1]):
        return []
    try:
        query = supabase.table(table).select(columns)
        for column, value in filters.items():
            if value is not None:
                query = query.eq(column, value)
        if in_ is not None:
            query = query.in_(in_[0], list(in_[1]))
        if not_null:
            # Rows where the relation is absent cannot express a disagreement, and
            # every account holding no class would otherwise be pulled in.
            query = query.not_.is_(not_null, "null")
        return query.execute().data or []
    except Exception as exc:  # noqa: BLE001 — recorded, never raised
        detail = f"{type(exc).__name__}: {exc}"
        logger.warning("school_integrity: could not read %s: %s", table, detail)
        errors.append({"table": table, "error": detail})
        return []


def _same(a, b) -> bool:
    """Two school ids that are present and different — the only real mismatch."""
    if a in (None, "") or b in (None, ""):
        return True
    return str(a) == str(b)


def cross_school_findings(supabase, *, limit: int = MAX_FINDINGS) -> dict:
    """Sweep the install for rows that disagree about their school.

    Returns::

        {
          "ok": bool,          # every read succeeded — False means "did not check"
          "findings": [ ... ], # one row per mismatch, capped at `limit`
          "errors": [{"table", "error"}],
          "truncated": bool,   # the cap was reached
          "checked": {table: rows_read},
        }

    `ok: False` is never the same as `findings: []`; the page draws a different
    sentence for each, and this is the one place that decides which.
    """
    errors: list[dict] = []
    classes = _read(supabase, "classes", "id, name, school_id", errors=errors)
    subjects = _read(supabase, "subjects", "id, name, school_id", errors=errors)
    pairs = _read(supabase, "class_subjects",
                  "id, class_id, subject_id, school_id, is_active", errors=errors)
    assignments = _read(supabase, "teacher_assignments",
                        "id, class_id, subject_id, school_id, status", errors=errors)
    pupils = _read(supabase, "profiles", "id, class_id, school_id",
                   errors=errors, not_null="class_id")
    # The papers, the sittings and the tracks — three rows that each name a school
    # and point at a class or a pupil that names another.
    exams = _read(supabase, "exams", "id, title, class_id, school_id", errors=errors)
    submissions = _read(supabase, "submissions", "id, exam_id, student_id",
                        errors=errors)
    levels = _read(supabase, "student_subject_levels",
                   "id, student_id, subject_id, class_id, school_id", errors=errors)

    # A submission holds no `school_id` and a level's pupil is what it must agree
    # with, so the pupils these two name are read once by id — bounded to the ids
    # actually referenced, rather than pulling every account in the install.
    referenced = sorted({str(r.get("student_id")) for r in submissions + levels
                         if r.get("student_id")})
    pupil_rows = _read(supabase, "profiles", "id, school_id", errors=errors,
                       in_=("id", referenced))
    pupil_school = {str(r["id"]): r.get("school_id") for r in pupil_rows if r.get("id")}

    class_school = {str(c["id"]): c.get("school_id") for c in classes if c.get("id")}
    class_name = {str(c["id"]): c.get("name") for c in classes if c.get("id")}
    subject_school = {str(s["id"]): s.get("school_id") for s in subjects if s.get("id")}
    exam_school = {str(e["id"]): e.get("school_id") for e in exams if e.get("id")}

    findings: list[dict] = []

    # 1. A class, and the pupils sitting in it.
    for pupil in pupils:
        class_id = str(pupil.get("class_id") or "")
        # A class this read did not see is not a class-level finding: the pair
        # checks below own a class that another school holds.
        if not class_id or class_id not in class_school:
            continue
        if _same(pupil.get("school_id"), class_school[class_id]):
            continue
        findings.append({
            "kind": CLASS_PUPIL,
            "table": "profiles",
            "class_id": class_id,
            "class_name": class_name.get(class_id),
            "class_school_id": class_school[class_id],
            "pupil_id": pupil.get("id"),
            "pupil_school_id": pupil.get("school_id"),
        })

    # 2. An offering of a subject to a class: one row naming three schools.
    for row in pairs:
        pair_school = row.get("school_id")
        for field, other in (("subject", subject_school.get(str(row.get("subject_id") or ""))),
                             ("class", class_school.get(str(row.get("class_id") or "")))):
            if other is None or _same(other, pair_school):
                continue
            findings.append({
                "kind": PAIR,
                "table": "class_subjects",
                "pair_id": row.get("id"),
                "field": field,
                "class_id": row.get("class_id"),
                "subject_id": row.get("subject_id"),
                "pair_school_id": pair_school,
                f"{field}_school_id": other,
            })

    # 3. A teacher assigned to teach a class-subject pair in two schools.
    for row in assignments:
        row_school = row.get("school_id")
        for field, other in (("subject", subject_school.get(str(row.get("subject_id") or ""))),
                             ("class", class_school.get(str(row.get("class_id") or "")))):
            if other is None or _same(other, row_school):
                continue
            findings.append({
                "kind": ASSIGNMENT,
                "table": "teacher_assignments",
                "assignment_id": row.get("id"),
                "field": field,
                "class_id": row.get("class_id"),
                "subject_id": row.get("subject_id"),
                "assignment_school_id": row_school,
                f"{field}_school_id": other,
            })

    # 4. A paper, and the class its candidates sit in.
    for exam in exams:
        class_id = str(exam.get("class_id") or "")
        if not class_id or class_id not in class_school:
            continue
        if _same(exam.get("school_id"), class_school[class_id]):
            continue
        findings.append({
            "kind": EXAM,
            "table": "exams",
            "field": "class",
            "exam_id": exam.get("id"),
            "title": exam.get("title"),
            "class_id": class_id,
            "class_name": class_name.get(class_id),
            "exam_school_id": exam.get("school_id"),
            "class_school_id": class_school[class_id],
        })

    # 5. A sitting, which joins a paper to a pupil — no school of its own, so the
    #    pair is the paper's school against the pupil's.
    for row in submissions:
        exam_id = str(row.get("exam_id") or "")
        student_id = str(row.get("student_id") or "")
        exam_s = exam_school.get(exam_id)
        pupil_s = pupil_school.get(student_id)
        if exam_s is None or pupil_s is None or _same(exam_s, pupil_s):
            continue
        findings.append({
            "kind": SUBMISSION,
            "table": "submissions",
            "field": "pupil",
            "submission_id": row.get("id"),
            "exam_id": row.get("exam_id"),
            "student_id": row.get("student_id"),
            "exam_school_id": exam_s,
            "pupil_school_id": pupil_s,
        })

    # 6. A pupil's track in a subject: one row naming a pupil and a class.
    for row in levels:
        row_school = row.get("school_id")
        subject_id = row.get("subject_id")
        class_id = str(row.get("class_id") or "")
        student_id = str(row.get("student_id") or "")
        for field, other in (("pupil", pupil_school.get(student_id)),
                             ("class", class_school.get(class_id) if class_id else None)):
            if other is None or _same(other, row_school):
                continue
            findings.append({
                "kind": LEVEL,
                "table": "student_subject_levels",
                "field": field,
                "level_id": row.get("id"),
                "student_id": row.get("student_id"),
                "subject_id": subject_id,
                "class_id": row.get("class_id"),
                "level_school_id": row_school,
                f"{field}_school_id": other,
            })

    return {
        "ok": not errors,
        "findings": findings[: max(0, int(limit))],
        "errors": errors,
        "truncated": len(findings) > max(0, int(limit)),
        "checked": {
            "classes": len(classes),
            "subjects": len(subjects),
            "class_subjects": len(pairs),
            "teacher_assignments": len(assignments),
            "profiles": len(pupils),
            "exams": len(exams),
            "submissions": len(submissions),
            "student_subject_levels": len(levels),
        },
    }


__all__ = ["cross_school_findings", "CLASS_PUPIL", "PAIR", "ASSIGNMENT",
           "EXAM", "SUBMISSION", "LEVEL", "MAX_FINDINGS"]
