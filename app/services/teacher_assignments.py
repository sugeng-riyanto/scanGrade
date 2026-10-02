"""Who teaches what, as an admin edits it.

`teacher_assignments` has always been able to hold a *(teacher, class, subject)*
many-to-many — the table was built for it in migration 010 and migration 045
added the `status` and `school_year` a real editor needs. What was missing was
any way to *write* it in bulk: the only editor was the exam builder's dropdown,
and it wrote one pair at a time for the signed-in teacher. So a school admin had
no screen that said "this teacher takes Mathematics in 8A, 8B and 8C", and the
simpler `teachers.subject_id` column (one subject per teacher) was the only truth
the roster could show.

The rules here are the ones a matrix needs and a single-pair write never did:

* **the pair is one unit** — teaching Physics in 8A is not teaching Maths in 8A;
* **every id belongs to the admin's own school** — a manipulated `class_id` from
  another school is refused, not silently dropped (which would look like it
  worked);
* **removing is a soft close, never a delete** — a pair that already has papers
  is part of the historical record, and `status='inactive'` keeps the row while
  closing the door (`assignments.active_rows` reads a missing status as active,
  so an old row still grants its door until something sets it);
* **a pair with a live paper is not removed in silence** — the caller gets the
  conflicting exams back and must confirm before the write.

Scope stays *within* one school; the tenant boundary is the school filter and the
service key, exactly as it is for `assignments.py`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Statuses a stored row can carry; mirror of migration 045's CHECK.
ACTIVE = "active"
INACTIVE = "inactive"

#: The roles the matrix may assign and validate. A guru teaches; a head of school
#: or their deputy teaches *as well* — the request was explicit. The role alone
#: does not widen anything here: every write is still one *(class, subject)* pair,
#: and the exam side scopes the official to that pair exactly as it scopes a guru
#: (see `app/services/assignments.py`).
ASSIGNABLE_ROLES = ("guru", "teacher", "principal", "vice_principal")

#: The statuses of a paper that make removing its assignment worth confirming.
LIVE_EXAM_STATUSES = ("active",)


def active_school_year(supabase, school_id):
    """The year the admin is editing, or None when the school has no rows.

    `is_active` is the source of truth (007); the newest name is the fallback so
    a school that never flagged one still gets an editor rather than an empty
    picker.
    """
    rows = (supabase.table("school_years").select("id, name, is_active")
            .eq("school_id", school_id).execute().data or [])
    if not rows:
        return None
    for row in rows:
        if row.get("is_active"):
            return row
    return sorted(rows, key=lambda r: r.get("name") or "")[-1]


def _pair_key(class_id, subject_id):
    return (str(class_id), str(subject_id))


def _row_in_year(row, year_name):
    """Is this row part of the year we are editing?

    A row written before 045 carries no `school_year`. That is a real assignment
    from an earlier release, and hiding it would make the matrix read as "not
    assigned" and then *deactivate* it on the next save — a data loss dressed as a
    no-op. So a missing year is treated as the active year, the same discipline
    `backfill_enrollment` uses.
    """
    stored = (row.get("school_year") or "").strip()
    if not stored:
        return True
    if not year_name:
        return True
    return stored == year_name.strip()


def row_is_active(row, year_name=None) -> bool:
    """One rule for "is this assignment in force right now?"

    Every reader that shows a teacher what they teach — the exam builder, the
    dashboard, `/teacher/classes`, the class list on the roster — must agree, or
    one of them shows a pair another has already taken away. The two halves are

    * `status`: `inactive` is migration 045's soft close, the way an assignment is
      removed without losing the history a past paper was built under; a row with
      no `status` at all predates it and is still in force;
    * the **year**: a pair belongs to the year it was written for, and a row with
      no year (again, written before 045) belongs to the active year rather than
      vanishing from every screen.
    """
    if (row.get("status") or ACTIVE) != ACTIVE:
        return False
    return _row_in_year(row, year_name)


def current_pairs(supabase, school_id, teacher_id, year_name=None) -> set:
    """The active *(class_id, subject_id)* pairs for one teacher."""
    rows = (supabase.table("teacher_assignments")
            .select("class_id, subject_id, status, school_year")
            .eq("school_id", school_id).eq("teacher_id", teacher_id)
            .execute().data or [])
    pairs = set()
    for row in rows:
        if not row_is_active(row, year_name):
            continue
        if row.get("class_id") and row.get("subject_id"):
            pairs.add(_pair_key(row["class_id"], row["subject_id"]))
    return pairs


def assigned_class_ids(supabase, school_id, teacher_id, year_name=None) -> set:
    """The classes one teacher actively holds, for THIS year.

    The question `/teacher/students` has to ask before it can show the right
    pupils: a guru teaches in the classes they are assigned to, not in the whole
    school. Fails to the empty set on an unreadable table, which shows no pupils
    rather than all of them.
    """
    try:
        rows = (supabase.table("teacher_assignments")
                .select("class_id, subject_id, status, school_year")
                .eq("school_id", school_id).eq("teacher_id", teacher_id)
                .execute().data or [])
    except Exception:
        logger.warning("could not read assignments for %s", teacher_id, exc_info=True)
        return set()
    ids = set()
    for row in rows:
        if row_is_active(row, year_name) and row.get("class_id"):
            ids.add(str(row["class_id"]))
    return ids


#: The profile columns a pupil needs on a class roster — the same set the old
#: whole-school query named, so the page is unchanged except for the narrowing.
STUDENT_COLUMNS = "id, full_name, phone, role, class_id"


def students_in_classes(supabase, school_id, class_ids) -> list:
    """This school's pupils whose class is one of `class_ids`.

    An empty `class_ids` returns nobody, not the school: an unassigned teacher has
    no class roster, and the whole-school list this replaces was the leak — a guru
    with nothing assigned saw every pupil in the school.
    """
    wanted = [str(c) for c in (class_ids or []) if c]
    if not school_id or not wanted:
        return []
    try:
        return (supabase.table("profiles").select(STUDENT_COLUMNS)
                .eq("role", "murid").eq("school_id", school_id)
                .in_("class_id", wanted).execute().data or [])
    except Exception:
        logger.warning("could not read pupils for classes %s", wanted, exc_info=True)
        return []


#: The prefix a form field carries its assignment under: `assign_<subject_id>`
#: holds the class ids that subject is taught in. One key per subject, so a
#: subject can be checked in some classes and not others — the source of truth a
#: single `subject_id` select could never express.
ASSIGN_PREFIX = "assign_"


def pairs_from_form(form) -> list:
    """Read `(class_id, subject_id)` pairs out of a submitted form.

    The create/edit teacher forms carry one `assign_<subject_id>` field per
    subject, each holding the class ids checked for it. Reading it here (rather
    than in the route) keeps the two forms and their guards on one spelling.
    """
    pairs = []
    for key in form.keys():
        if not key.startswith(ASSIGN_PREFIX):
            continue
        subject_id = key[len(ASSIGN_PREFIX):]
        if not subject_id:
            continue
        for class_id in form.getlist(key):
            if class_id:
                pairs.append((class_id, subject_id))
    return pairs


def school_pairs_detail(supabase, school_id, year_name=None) -> dict:
    """Every teacher's active pairs, in one read — the roster preview.

    One query for the whole list rather than one per teacher: the roster draws a
    count beside each row, and each row's edit form needs its pre-checked matrix,
    so a page of 50 teachers must not become 50 queries. Returns counts *and* the
    ids, because the form has to tick exactly the pairs that are stored.
    """
    rows = (supabase.table("teacher_assignments")
            .select("teacher_id, class_id, subject_id, status, school_year")
            .eq("school_id", school_id).execute().data or [])
    by_teacher: dict = {}
    for row in rows:
        if not row_is_active(row, year_name):
            continue
        if not (row.get("teacher_id") and row.get("class_id") and row.get("subject_id")):
            continue
        bucket = by_teacher.setdefault(
            str(row["teacher_id"]),
            {"classes": set(), "subjects": set(), "pairs": set()},
        )
        class_id, subject_id = str(row["class_id"]), str(row["subject_id"])
        bucket["classes"].add(class_id)
        bucket["subjects"].add(subject_id)
        # The form checks `class_id|subject_id`, so the detail reports that same
        # key — the template's `in` then means exactly "this cell is stored".
        bucket["pairs"].add(f"{class_id}|{subject_id}")
    return {
        tid: {
            "classes": len(b["classes"]), "subjects": len(b["subjects"]),
            "class_ids": sorted(b["classes"]), "subject_ids": sorted(b["subjects"]),
            "pairs": sorted(b["pairs"]),
        }
        for tid, b in by_teacher.items()
    }


def school_pairs(supabase, school_id, year_name=None) -> dict:
    """Just the counts, the shape the roster first shipped with."""
    return {tid: {"classes": v["classes"], "subjects": v["subjects"]}
            for tid, v in school_pairs_detail(supabase, school_id, year_name).items()}


def owned_class_ids(supabase, school_id, class_ids) -> set:
    """Which of `class_ids` are this school's — the adversarial guard, positive."""
    wanted = [str(c) for c in (class_ids or []) if c]
    if not wanted:
        return set()
    rows = (supabase.table("classes").select("id")
            .eq("school_id", school_id).in_("id", wanted).execute().data or [])
    return {str(r["id"]) for r in rows}


def owned_subject_ids(supabase, school_id, subject_ids) -> set:
    wanted = [str(s) for s in (subject_ids or []) if s]
    if not wanted:
        return set()
    rows = (supabase.table("subjects").select("id")
            .eq("school_id", school_id).in_("id", wanted).execute().data or [])
    return {str(r["id"]) for r in rows}


def disabled_pairs(supabase, school_id, pairs) -> set:
    """The *(class, subject)* pairs this school has switched **off**.

    A subject is offered by every class unless a ``class_subjects`` row says it
    is not (see ``subject_levels.mapped_class_ids``), so the pair is blocked
    exactly when a row exists with ``is_active = false``. Reading only the
    subjects in `pairs` keeps it one query for a whole matrix save.
    """
    pairs = {_pair_key(c, s) for c, s in (pairs or []) if c and s}
    subject_ids = sorted({s for _c, s in pairs})
    if not subject_ids:
        return set()
    rows = (supabase.table("class_subjects")
            .select("class_id, subject_id, is_active")
            .eq("school_id", school_id).in_("subject_id", subject_ids)
            .execute().data or [])
    off = {
        _pair_key(r.get("class_id"), r.get("subject_id"))
        for r in rows if not r.get("is_active", True)
    }
    return pairs & off


def teacher_in_school(supabase, school_id, teacher_id) -> bool:
    rows = (supabase.table("profiles").select("id, role")
            .eq("id", teacher_id).eq("school_id", school_id)
            .in_("role", list(ASSIGNABLE_ROLES)).limit(1).execute().data or [])
    return bool(rows)


def running_exams_for_removal(supabase, school_id, teacher_id, remove_pairs):
    """Papers that make a removal worth confirming.

    A removal is checked against the exams it would orphan: the teacher's own,
    still live, for one of the subjects being dropped, attached to one of the
    classes being dropped. `exams.class_ids` is a JSON array (schema.sql), so the
    class test is done here rather than in PostgREST.
    """
    pairs = list(remove_pairs or [])
    if not pairs:
        return []
    subjects = {s for _c, s in pairs}
    classes = {c for c, _s in pairs}
    rows = (supabase.table("exams")
            .select("id, title, subject_id, class_ids, status, is_published")
            .eq("school_id", school_id).eq("teacher_id", teacher_id)
            .in_("subject_id", list(subjects)).execute().data or [])
    hits = []
    for exam in rows:
        if exam.get("status") not in LIVE_EXAM_STATUSES and not exam.get("is_published"):
            continue
        exam_classes = exam.get("class_ids") or []
        if isinstance(exam_classes, str):
            import json
            try:
                exam_classes = json.loads(exam_classes)
            except (ValueError, TypeError):
                exam_classes = []
        exam_classes = {str(c) for c in exam_classes}
        if exam_classes & classes:
            hits.append({"id": exam.get("id"), "title": exam.get("title") or "Ujian"})
    return hits


def validate_targets(supabase, school_id, teacher_id, pairs):
    """Refuse anything that is not this school's — 403, never a silent drop."""
    if not teacher_in_school(supabase, school_id, teacher_id):
        return "Guru tidak terdaftar di sekolah ini"
    class_ids = {c for c, _s in pairs}
    subject_ids = {s for _c, s in pairs}
    if not class_ids <= owned_class_ids(supabase, school_id, class_ids):
        return "Ada kelas yang bukan milik sekolah ini"
    if not subject_ids <= owned_subject_ids(supabase, school_id, subject_ids):
        return "Ada mata pelajaran yang bukan milik sekolah ini"
    # A subject can be switched off for one class and offered by the next (a
    # school unticks Geography for a language class). Assigning it anyway would
    # put a teacher in a room the subject is not taught in, so it is refused the
    # same way a foreign-school id is.
    if disabled_pairs(supabase, school_id, pairs):
        return "Ada mata pelajaran yang tidak diaktifkan untuk kelasnya"
    return None


def save(supabase, school_id, teacher_id, pairs, year_name=None, confirm_remove=False):
    """Diff `pairs` against the current set and apply it.

    Returns ``(ok, payload)`` where a refusal carries a `reason` and, for a
    removal with live papers, the `running` exams. Unlike the older
    single-pair write this never deletes a row: an assignment that is dropped is
    set `inactive`, so the pair a past paper was built under stays in the record.
    """
    wanted = {_pair_key(c, s) for c, s in pairs if c and s}
    error = validate_targets(supabase, school_id, teacher_id, wanted)
    if error:
        return False, {"reason": error, "status": 403}

    current = current_pairs(supabase, school_id, teacher_id, year_name)
    to_add = sorted(wanted - current)
    to_remove = sorted(current - wanted)

    if to_remove and not confirm_remove:
        running = running_exams_for_removal(supabase, school_id, teacher_id, to_remove)
        if running:
            return False, {
                "reason": "penugasan ini punya ujian berjalan",
                "status": 409,
                "needs_confirmation": True,
                "running": running,
                "removing": [{"class_id": c, "subject_id": s} for c, s in to_remove],
                "added": len(to_add),
                "removed": len(to_remove),
            }

    if to_add:
        rows = [{"teacher_id": teacher_id, "class_id": c, "subject_id": s,
                 "school_id": school_id, "status": ACTIVE,
                 "school_year": (year_name or None)}
                for c, s in to_add]
        supabase.table("teacher_assignments").upsert(
            rows, on_conflict="teacher_id,class_id,subject_id").execute()

    for class_id, subject_id in to_remove:
        supabase.table("teacher_assignments").update({"status": INACTIVE}) \
            .eq("school_id", school_id).eq("teacher_id", teacher_id) \
            .eq("class_id", class_id).eq("subject_id", subject_id).execute()

    return True, {
        "added": [{"class_id": c, "subject_id": s} for c, s in to_add],
        "removed": [{"class_id": c, "subject_id": s} for c, s in to_remove],
        "added_count": len(to_add), "removed_count": len(to_remove),
    }
