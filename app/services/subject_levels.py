"""Which subjects a class offers, and how far each pupil has got in one.

Two facts the school could not state before, from migration 049:

* **a class offers only some subjects.** A (class, subject) pair used to exist
  only because a teacher held it (`teacher_assignments`), so a subject waiting
  for a teacher — or a class between teachers — had no row anywhere, and the
  exam builder offered every subject in the school against every class.
  `class_subjects` is that missing sentence: one row per offered pair, closed
  with `is_active` rather than deleted so the history under it survives.

* **a pupil is on a track inside a subject** — *basic*, *intermediate* or
  *advanced* — because a school teaches one subject at more than one depth, and
  a basic group's marks are not comparable with an advanced group's.
  `student_subject_levels` records which track a pupil was placed in.

Both tables are scoped to one school. Every id a caller sends is checked
against that school **before** anything is written: a manipulated `class_id` or
`student_id` from another school is refused with 403, never quietly dropped —
a silent drop is indistinguishable from success to the admin who tried it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The tracks, in order. Kept in step with migration 049's CHECK by its test.
LEVELS = ("basic", "intermediate", "advanced")

#: What a pupil with no row on file reads as. Not a missing value: it is the
#: track a school starts everyone on, and the roster must show it so a class just
#: mapped is usable without the admin setting every pupil by hand first.
DEFAULT_LEVEL = "basic"

#: The columns a roster needs to name a pupil.
STUDENT_COLUMNS = "id, full_name, nisn, nis, class_id, role, school_id"


def _rows(supabase, table, columns, filters=()):
    """One read, ``filters`` as ``(method, column, value)``, returning a list.

    ``method`` is the postgrest builder's own name, so a caller writes ``"in_"``
    for membership — the client's method really is spelled ``in_``.
    """
    try:
        query = supabase.table(table).select(columns)
        for op, col, val in filters:
            query = getattr(query, op)(col, val)
        return query.execute().data or []
    except Exception:
        logger.debug("%s read failed", table, exc_info=True)
        return []


# ── the offering ─────────────────────────────────────────────────────────────

def mapped_class_ids(supabase, school_id, subject_id, all_class_ids=None) -> set:
    """The classes that offer this subject, as ``{class_id}``.

    Two questions, and the `all_class_ids` argument is which one is being asked.

    **Without it**, the caller wants the stored rows: the classes an admin has
    ticked. This is the narrow question, and it is what a page that only has an
    id to show asks.

    **With it**, the caller wants the *offering* — and a pair is then offered
    **unless a row says it is not**. That is what "every subject ticked for every
    class by default" means honestly: absence means yes. Backfilling every pair
    for a school would write `M x N` rows on its first day, and the next class a
    school creates would be missing from the backfill and so silently excluded
    from every subject. Turning one pair off writes exactly one row, which is the
    only row anybody needs to write.

    A subject with no rows is therefore offered everywhere, which is also how the
    page read before it had a mapping at all — so a school that never opens the
    panel sees nothing change.
    """
    eff = [] if all_class_ids is None else [("in_", "class_id", list(all_class_ids))]
    rows = _rows(supabase, "class_subjects", "class_id, is_active", [
        ("eq", "school_id", school_id),
        ("eq", "subject_id", subject_id),
        *eff,
    ])
    if all_class_ids is None:
        return {str(r["class_id"]) for r in rows
                if r.get("class_id") and r.get("is_active", True)}
    closed = {str(r["class_id"]) for r in rows if not r.get("is_active", True)}
    return {str(c) for c in all_class_ids if str(c) not in closed}


def levels_by_subject(supabase, school_id) -> dict:
    """``{subject_id: [grade_level, …]}`` — the grade levels that teach a subject.

    A subject is offered by a class **unless** a ``class_subjects`` row closes the
    pair (``is_active=False``) — the same "absence means yes" rule
    :func:`mapped_class_ids` reads — so a subject is taught in a grade level when
    that level has at least one class still open for it. A school that has never
    closed a pair therefore teaches every active subject in every level it has,
    which is the honest answer and the one a per-level reset needs.

    The level names come from ``classes.grade_level``, the school's own label, and
    are returned sorted so a page renders the same order every time.
    """
    if not school_id:
        return {}
    classes = _rows(supabase, "classes", "id, grade_level",
                    [("eq", "school_id", school_id)])
    class_level = {str(r["id"]): str(r.get("grade_level") or "")
                   for r in classes if r.get("id")}
    subjects = _rows(supabase, "subjects", "id, is_active",
                     [("eq", "school_id", school_id), ("eq", "is_active", True)])
    closed = _rows(supabase, "class_subjects", "subject_id, class_id, is_active",
                   [("eq", "school_id", school_id)])
    closed_pairs = {(str(r.get("subject_id")), str(r.get("class_id")))
                    for r in closed if not r.get("is_active", True)}
    out = {}
    for subject in subjects:
        sid = str(subject.get("id") or "")
        if not sid:
            continue
        out[sid] = sorted({lvl for cid, lvl in class_level.items()
                           if lvl and (sid, cid) not in closed_pairs})
    return out


def subjects_for_grade_level(supabase, school_id, grade_level) -> list:
    """The ids of the active subjects a grade level teaches.

    The counterpart of :func:`levels_by_subject`, so a caller can ask either
    direction without re-deriving the offering rule. An empty or missing level is
    never a question — it answers ``[]`` rather than every subject.
    """
    if grade_level in (None, ""):
        return []
    level = str(grade_level)
    levels = levels_by_subject(supabase, school_id)
    return [sid for sid, lvls in levels.items() if level in lvls]


def save_mapping(supabase, school_id, subject_id, class_ids, created_by=None):
    """Set which classes offer ``subject_id`` for this school.

    Returns ``(ok, result)``. The diff is computed against the stored rows: a
    class newly ticked is **reactivated** (or inserted once), a class unticked is
    **closed** with ``is_active=False``, and no row is ever deleted — a paper or
    a level written under the pair must still point somewhere.

    Refuses with 403 if any class is not this school's, before writing anything:
    a partial write would leave the admin unsure which half landed.
    """
    if not subject_id:
        return False, {"error": "Mapel tidak ditemukan", "status": 404}

    subjects = _rows(supabase, "subjects", "id", [
        ("eq", "id", subject_id), ("eq", "school_id", school_id)])
    if not subjects:
        return False, {"error": "Mapel tidak ditemukan di sekolah ini", "status": 404}

    wanted = {str(c) for c in (class_ids or []) if c}
    owned = _rows(supabase, "classes", "id", [("eq", "school_id", school_id)])
    known = {str(r["id"]) for r in owned if r.get("id")}
    foreign = wanted - known
    if foreign:
        return False, {"error": "Ada kelas yang bukan milik sekolah ini", "status": 403}

    existing = _rows(supabase, "class_subjects", "id, class_id, is_active", [
        ("eq", "school_id", school_id), ("eq", "subject_id", subject_id)])
    by_class = {str(r["class_id"]): r for r in existing if r.get("class_id")}

    # Two statements, never one per class. The first version inserted class by
    # class, and a live probe caught what that costs: the connection dropped
    # between inserts, one row had already landed, and the caller was told the
    # save failed — a half-applied mapping the admin had no way to see. Bulk
    # writes make that window as small as it can be, and the operation is
    # idempotent, so re-saving after a network error simply finishes the job.
    # Only what changed is written. The page posts its *whole* selection on every
    # tick (that is what keeps two open tabs from disagreeing), so writing all of
    # it back would mean a tick on a school with 22 classes rewrites 22 rows — and
    # with the tick saving itself, that happens on every single tick. A class
    # already on is left alone.
    to_on = [c for c in sorted(wanted)
             if c not in by_class or not by_class[c].get("is_active", True)]
    # An un-tick has to be *written*, not merely omitted. Absence means offered
    # (see `mapped_class_ids`), so the class the admin just un-ticked — which in
    # the usual case has no row at all — would stay offered and the un-tick would
    # be silently ignored. Every class of the school that is not wanted and is
    # not already closed is therefore written closed, by upsert so a class with a
    # row is closed in place and one without a row is inserted closed; the unique
    # (class_id, subject_id) keeps either from duplicating.
    to_off = [c for c in sorted(known - wanted)
              if c not in by_class or by_class[c].get("is_active", True)]
    added = len(to_on)
    removed = len(to_off)

    try:
        rows = [{"school_id": school_id, "class_id": class_id,
                 "subject_id": subject_id, "is_active": True,
                 "created_by": created_by} for class_id in to_on]
        rows += [{"school_id": school_id, "class_id": class_id,
                  "subject_id": subject_id, "is_active": False,
                  "created_by": created_by} for class_id in to_off]
        if rows:
            # One upsert over the unique (class_id, subject_id): a class that was
            # closed is reactivated in place and a class just un-ticked is closed
            # in place, so a row is never duplicated either way.
            (supabase.table("class_subjects")
             .upsert(rows, on_conflict="class_id,subject_id")
             .execute())
    except Exception as e:
        logger.warning("could not save subject mapping for %s", subject_id, exc_info=True)
        return False, {"error": str(e), "status": 400}

    # The pupil dashboard counts the subjects a class offers, which is cached per
    # class. Every class this save turned on or off has a stale answer now.
    try:
        from app.utils.req_cache import invalidate_class_subjects
        for _cid in set(to_on) | set(to_off) | set(by_class):
            invalidate_class_subjects(_cid)
    except Exception:  # noqa: BLE001 — invalidation must never fail the save
        logger.debug("could not invalidate class subjects for %s", subject_id)

    return True, {"added": added, "removed": removed,
                  "mapped": sorted(wanted)}


# ── the roster and its levels ────────────────────────────────────────────────

def _class_students(supabase, school_id, class_id) -> list:
    """This school's pupils whose class is ``class_id``.

    Reads `profiles.class_id`, the pointer the rest of the app uses today for a
    class roster (`teacher_assignments.students_in_classes`), so the level screen
    and the assignment screen never disagree about who is in a class.
    """
    if not school_id or not class_id:
        return []
    rows = _rows(supabase, "profiles", STUDENT_COLUMNS, [
        ("eq", "role", "murid"),
        ("eq", "school_id", school_id),
        ("eq", "class_id", class_id),
    ])
    return sorted(rows, key=lambda r: (r.get("full_name") or "").lower())


def _stored_levels(supabase, school_id, subject_id, student_ids, year_id=None) -> dict:
    wanted = [str(s) for s in (student_ids or []) if s]
    if not wanted:
        return {}
    filters = [
        ("eq", "school_id", school_id),
        ("eq", "subject_id", subject_id),
        ("in_", "student_id", wanted),
    ]
    if year_id:
        filters.append(("eq", "school_year_id", year_id))
    rows = _rows(supabase, "student_subject_levels", "student_id, level", filters)
    return {str(r["student_id"]): r.get("level") for r in rows if r.get("student_id")}


def roster_with_levels(supabase, school_id, subject_id, class_id, year_id=None) -> list:
    """Every pupil of ``class_id`` with the level they are on in ``subject_id``.

    A pupil with no row reads as :data:`DEFAULT_LEVEL`, so a class that was just
    mapped is usable at once instead of showing blanks the admin might mistake
    for "not enrolled".
    """
    students = _class_students(supabase, school_id, class_id)
    levels = _stored_levels(supabase, school_id, subject_id,
                            [s.get("id") for s in students], year_id)
    return [{
        "student_id": s.get("id"),
        "full_name": s.get("full_name") or "",
        "nisn": s.get("nisn"),
        "nis": s.get("nis"),
        "level": levels.get(str(s.get("id")), DEFAULT_LEVEL),
    } for s in students if s.get("id")]


def save_levels(supabase, school_id, subject_id, class_id, levels,
                year_id=None, set_by=None):
    """Write the level of each pupil in ``levels`` — ``{student_id: level}``.

    Returns ``(ok, result)``. Both halves are checked **before** any write:

    * every ``level`` is one of :data:`LEVELS` (else 400);
    * every pupil is really in ``class_id`` of this school (else 403), so a
      manipulated ``student_id`` cannot put another school's pupil on a track.

    A refusal writes nothing, not even the pupils whose value was fine: a partial
    write is harder to explain than a refusal.
    """
    if not subject_id or not class_id:
        return False, {"error": "Mapel atau kelas tidak lengkap", "status": 404}

    subjects = _rows(supabase, "subjects", "id", [
        ("eq", "id", subject_id), ("eq", "school_id", school_id)])
    if not subjects:
        return False, {"error": "Mapel tidak ditemukan di sekolah ini", "status": 404}
    owned_classes = _rows(supabase, "classes", "id", [
        ("eq", "id", class_id), ("eq", "school_id", school_id)])
    if not owned_classes:
        return False, {"error": "Kelas bukan milik sekolah ini", "status": 403}

    incoming = {str(k): (v or DEFAULT_LEVEL) for k, v in (levels or {}).items() if k}
    for level in incoming.values():
        if level not in LEVELS:
            return False, {"error": f"Level '{level}' tidak dikenal", "status": 400}

    roster = {str(s["id"]) for s in _class_students(supabase, school_id, class_id)
              if s.get("id")}
    strangers = {sid for sid in incoming if sid not in roster}
    if strangers:
        return False, {"error": "Ada murid yang bukan anggota kelas ini", "status": 403}

    if not incoming:
        return True, {"saved": 0}

    # An upsert would be one statement, but the uniqueness here is a *partial*
    # index (`WHERE school_year_id IS NOT NULL`, migration 049) and PostgREST
    # sends `ON CONFLICT (cols)` without the predicate — Postgres then cannot
    # infer the index and answers 42P10, measured live. So the write is split
    # instead: one bulk insert for the pupils with no row yet, and one update per
    # level for the rest. Bounded at four statements, never one per pupil — a
    # loop is how a dropped connection left a half-applied mapping once.
    existing = _rows(supabase, "student_subject_levels", "id, student_id", [
        ("eq", "school_id", school_id),
        ("eq", "subject_id", subject_id),
        ("in_", "student_id", list(incoming.keys())),
    ] + ([] if not year_id else [("eq", "school_year_id", year_id)]))
    known = {str(r["student_id"]): r["id"] for r in existing
             if r.get("student_id") and r.get("id")}

    to_insert = [{
        "school_id": school_id, "student_id": sid, "subject_id": subject_id,
        "class_id": class_id, "school_year_id": year_id,
        "level": level, "set_by": set_by,
    } for sid, level in sorted(incoming.items()) if sid not in known]

    by_level: dict = {}
    for sid, level in incoming.items():
        if sid in known:
            by_level.setdefault(level, []).append(known[sid])

    try:
        if to_insert:
            supabase.table("student_subject_levels").insert(to_insert).execute()
        for level, ids in by_level.items():
            (supabase.table("student_subject_levels")
             .update({"level": level, "class_id": class_id, "set_by": set_by})
             .in_("id", ids).execute())
    except Exception as e:
        logger.warning("could not save subject levels for %s", subject_id, exc_info=True)
        return False, {"error": str(e), "status": 400}

    return True, {"saved": len(incoming), "inserted": len(to_insert),
                  "updated": len(incoming) - len(to_insert)}
