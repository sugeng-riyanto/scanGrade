"""Weighted grade components — what a school's final mark actually is.

The mark a pupil carries out of a subject is not one exam; it is a policy. A
school gives *Tugas Kelas* 30%, *UTS* 30% and *UAS* 40%, and the number a parent
sees has to be that sum — not the simple mean of whatever papers happened to be
graded, which silently weights a short quiz the same as the final exam.

Two ideas, kept apart on purpose:

* :func:`list_components` — the **components a school uses** (its own names:
  "Tugas Kelas", "Proyek", "UTS", …). Administered by ``admin_sekolah``.
* :func:`config_for` — the **weight** of each of those components for one subject
  in one school year. Per subject per year, because a school that wants one policy
  everywhere simply gives the same weights to every subject, and because a mark
  already reported was decided by the weights in force then.

The fallback is the contract this module exists to keep
--------------------------------------------------------
A school that has configured nothing must not break. :func:`compute` therefore
answers in two modes and names which one it used:

* ``"weighted"`` when the subject has an active configuration summing to 100;
* ``"simple"`` when it does not — the plain mean over every score, which is
  exactly what the page did before this feature existed.

A page that shows a final mark must show the mode beside it, so a simple mean is
never mistaken for a weighted policy.

Missing components count as zero
--------------------------------
When a component is configured with a weight but the pupil has no score in it,
:func:`compute` treats that component as **0** for that pupil — it does *not*
renormalise the remaining weights up. Renormalising would quietly raise a pupil's
mark for work they never did, and hide the real gaps from the teacher. Zero is
the conservative reading, and the page tells the teacher which component is empty
so they can fix the cause instead.

Exams with no component are ignored under weighting
---------------------------------------------------
A scored paper whose component is unset (or set to a component the subject does
not weight) contributes nothing to ``"weighted"``; :func:`compute` counts them in
``untagged`` so the page can say so rather than silently dropping marks.
"""

from __future__ import annotations

import logging

from app.utils.exam_access import result_released

logger = logging.getLogger(__name__)

#: The component a school must reach before its weights are used. Mirrored by the
#: application check in :func:`save_config`; a CHECK constraint cannot sum rows.
REQUIRED_TOTAL = 100

#: What :func:`compute` does with a configured component the pupil has no score in.
#: Stated as data so the page can render the same sentence the module enforces.
MISSING_COMPONENT_POLICY = "zero"

COMPONENT_COLUMNS = ("id, school_id, name, sort_order, is_active, default_weight, "
                     "created_at, updated_at")
CONFIG_COLUMNS = ("id, school_id, subject_id, school_year_id, component_id, "
                  "weight_percent, is_active")


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read.

    These rows decorate a page; a Supabase hiccup must let the page render (the
    simple mean is a real answer), not 500.
    """
    try:
        return query.execute().data or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: read failed: %s", exc)
        return []


# ── the components a school uses ─────────────────────────────────────────────

def list_components(supabase, school_id: str, *, active_only: bool = True) -> list[dict]:
    """The school's grade components, in the order the school put them."""
    if not school_id:
        return []
    query = supabase.table("grade_component_type").select(COMPONENT_COLUMNS)\
        .eq("school_id", school_id)
    if active_only:
        query = query.eq("is_active", True)
    rows = _rows(query.order("sort_order").order("name"))
    return rows


def default_config(supabase, school_id: str) -> dict[str, int]:
    """The school's **default** distribution, or ``{}``.

    Read from the active components' own `default_weight` (058). It is a real
    policy only when it adds up to :data:`REQUIRED_TOTAL`; a half-filled default
    is not a policy and answers ``{}``, so a subject that follows it falls back to
    the simple mean rather than to a total that never reached 100.
    """
    if not school_id:
        return {}
    weights = {}
    for row in list_components(supabase, school_id):
        value = row.get("default_weight")
        if value is None:
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            weights[str(row["id"])] = value
    if not weights or sum(weights.values()) != REQUIRED_TOTAL:
        return {}
    return weights


def save_defaults(supabase, school_id: str, weights: dict, *,
                  actor_id=None) -> tuple[bool, dict]:
    """Set the school's default distribution across its active components.

    ``weights`` is ``{component_id: percent}``. Every id must be an active
    component this school owns (else 403); a non-empty set must sum to exactly
    :data:`REQUIRED_TOTAL` (else 400). An empty set clears every default, so a
    school that removes its default goes back to per-subject configs and the
    simple mean.
    """
    if not school_id:
        return False, {"error": "Sekolah tidak diketahui", "status": 400}
    components = list_components(supabase, school_id)
    owned = {str(c["id"]) for c in components}
    cleaned: dict[str, int] = {}
    for cid, raw in (weights or {}).items():
        if str(cid) not in owned:
            return False, {"error": "Ada komponen bukan milik sekolah ini", "status": 403}
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return False, {"error": "Bobot harus berupa angka", "status": 400}
        if value < 0 or value > 100:
            return False, {"error": "Bobot harus 0-100", "status": 400}
        if value:
            cleaned[str(cid)] = value
    total = sum(cleaned.values())
    if cleaned and total != REQUIRED_TOTAL:
        return False, {"error": f"Total bobot default harus 100% (sekarang {total}%)",
                       "status": 400, "total": total}
    try:
        for comp in components:
            cid = str(comp["id"])
            # `updated_by` lives on the weight-config rows, not on this table
            # (057 gave the component list `created_by` only), so only the value
            # that actually exists is written here.
            supabase.table("grade_component_type").update(
                {"default_weight": cleaned.get(cid, 0)})\
                .eq("id", comp["id"]).eq("school_id", school_id).execute()
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: save defaults failed: %s", exc)
        return False, {"error": "Gagal menyimpan bobot default", "status": 400}
    return True, {"weights": cleaned, "total": total, "cleared": not cleaned}


def effective_config(supabase, school_id: str, subject_id: str,
                     year_id: str | None) -> dict[str, int]:
    """The weights a subject's mark actually uses.

    A custom distribution for the subject/year wins; otherwise the school's
    **default** distribution applies; otherwise ``{}`` (the simple mean). This is
    the one read every surface should use, so a subject the admin never touched
    still carries the school's declared weights.
    """
    custom = config_for(supabase, school_id, subject_id, year_id)
    if custom:
        return custom
    return default_config(supabase, school_id)


def component_ids(supabase, school_id: str) -> set[str]:
    """Every component id this school owns, active or not — the write guard."""
    return {str(r["id"]) for r in list_components(supabase, school_id, active_only=False)
            if r.get("id")}


def _clean_name(name) -> str:
    return " ".join((name or "").split())


def create_component(supabase, school_id: str, name, *, sort_order=None,
                     actor_id=None) -> tuple[bool, dict]:
    """Add a component to the school's list. Refuses a blank or duplicate name."""
    clean = _clean_name(name)
    if not school_id:
        return False, {"error": "Sekolah tidak diketahui", "status": 400}
    if not clean:
        return False, {"error": "Nama komponen wajib diisi", "status": 400}
    existing = list_components(supabase, school_id, active_only=False)
    if any((r.get("name") or "").strip().lower() == clean.lower() for r in existing):
        return False, {"error": "Komponen dengan nama itu sudah ada", "status": 400}
    order = sort_order
    if order is None:
        order = max([int(r.get("sort_order") or 0) for r in existing] or [0]) + 1
    payload = {"school_id": school_id, "name": clean, "sort_order": order,
               "is_active": True}
    if actor_id:
        payload["created_by"] = actor_id
    try:
        written = supabase.table("grade_component_type").insert(payload).execute().data or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: create component failed: %s", exc)
        return False, {"error": "Gagal menyimpan komponen", "status": 400}
    return True, {"component": written[0] if written else None}


def update_component(supabase, school_id: str, component_id: str, *,
                     name=None, sort_order=None, is_active=None) -> tuple[bool, dict]:
    """Rename, reorder or (de)activate one of the school's components."""
    if not school_id or not component_id:
        return False, {"error": "Komponen tidak ditemukan", "status": 404}
    owned = component_ids(supabase, school_id)
    if str(component_id) not in owned:
        return False, {"error": "Komponen bukan milik sekolah ini", "status": 403}
    payload: dict = {}
    if name is not None:
        clean = _clean_name(name)
        if not clean:
            return False, {"error": "Nama komponen wajib diisi", "status": 400}
        others = [r for r in list_components(supabase, school_id, active_only=False)
                  if str(r.get("id")) != str(component_id)]
        if any((r.get("name") or "").strip().lower() == clean.lower() for r in others):
            return False, {"error": "Komponen dengan nama itu sudah ada", "status": 400}
        payload["name"] = clean
    if sort_order is not None:
        payload["sort_order"] = int(sort_order)
    if is_active is not None:
        payload["is_active"] = bool(is_active)
    if not payload:
        return True, {"component": None, "saved": 0}
    try:
        written = supabase.table("grade_component_type").update(payload)\
            .eq("id", component_id).eq("school_id", school_id).execute().data or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: update component failed: %s", exc)
        return False, {"error": "Gagal menyimpan komponen", "status": 400}
    return True, {"component": written[0] if written else None, "saved": len(written)}


# ── the weights for one subject in one year ──────────────────────────────────

def config_for(supabase, school_id: str, subject_id: str,
               year_id: str | None) -> dict[str, int]:
    """``{component_id: weight}`` for an **active** configuration, or ``{}``.

    An empty dict is not an error: it is the school that has not configured this
    subject, and the caller falls back to the simple mean. A year is required to
    read a configuration — a weight belongs to a year — so a missing year reads as
    "not configured" rather than matching some other year's rows.
    """
    if not school_id or not subject_id or not year_id:
        return {}
    rows = _rows(supabase.table("grade_weight_config").select(CONFIG_COLUMNS)
                 .eq("school_id", school_id).eq("subject_id", subject_id)
                 .eq("school_year_id", year_id).eq("is_active", True))
    return {str(r["component_id"]): int(r.get("weight_percent") or 0)
            for r in rows if r.get("component_id")}


def configs_for_school(supabase, school_id: str, year_id: str | None) -> dict[str, dict]:
    """``{subject_id: {component_id: weight}}`` for the whole school in one read."""
    if not school_id or not year_id:
        return {}
    rows = _rows(supabase.table("grade_weight_config").select(CONFIG_COLUMNS)
                 .eq("school_id", school_id).eq("school_year_id", year_id)
                 .eq("is_active", True))
    out: dict[str, dict] = {}
    for r in rows:
        sid = r.get("subject_id")
        cid = r.get("component_id")
        if sid and cid:
            out.setdefault(str(sid), {})[str(cid)] = int(r.get("weight_percent") or 0)
    return out


def save_config(supabase, school_id: str, subject_id: str, year_id: str,
                weights: dict, *, actor_id=None) -> tuple[bool, dict]:
    """Set a subject's weights for a year, or refuse.

    ``weights`` is ``{component_id: percent}``. The rules, all checked **before**
    anything is written:

    * every component is one this school owns (else 403);
    * a non-empty set must sum to exactly :data:`REQUIRED_TOTAL` (else 400) — a
      policy that does not add up is not a policy;
    * every value is an integer in 0-100.

    An all-zero (or empty) set is a **clear**: every row for the pair is
    deactivated, which puts the subject back on the simple mean rather than
    leaving a half-configuration behind.
    """
    if not school_id or not subject_id or not year_id:
        return False, {"error": "Mapel dan tahun ajaran wajib", "status": 404}
    owned_components = component_ids(supabase, school_id)
    cleaned: dict[str, int] = {}
    for cid, raw in (weights or {}).items():
        if str(cid) not in owned_components:
            return False, {"error": "Ada komponen bukan milik sekolah ini", "status": 403}
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return False, {"error": "Bobot harus berupa angka", "status": 400}
        if value < 0 or value > 100:
            return False, {"error": "Bobot harus 0-100", "status": 400}
        if value:
            cleaned[str(cid)] = value

    total = sum(cleaned.values())
    if cleaned and total != REQUIRED_TOTAL:
        return False, {"error": f"Total bobot harus 100% (sekarang {total}%)",
                       "status": 400, "total": total}

    subjects = _rows(supabase.table("subjects").select("id")
                     .eq("id", subject_id).eq("school_id", school_id))
    if not subjects:
        return False, {"error": "Mapel tidak ditemukan di sekolah ini", "status": 404}

    try:
        existing = _rows(supabase.table("grade_weight_config").select("id, component_id")
                         .eq("school_id", school_id).eq("subject_id", subject_id)
                         .eq("school_year_id", year_id))
        known = {str(r["component_id"]): r["id"] for r in existing if r.get("component_id")}
        # Rows for the pair that are not in the new set are deactivated, never
        # deleted: what a mark was computed under is history.
        stale_ids = [rid for cid, rid in known.items() if cid not in cleaned]
        if stale_ids:
            supabase.table("grade_weight_config").update({"is_active": False})\
                .in_("id", stale_ids).execute()
        to_insert, to_update = [], []
        for cid, value in cleaned.items():
            row = {"school_id": school_id, "subject_id": subject_id,
                   "school_year_id": year_id, "component_id": cid,
                   "weight_percent": value, "is_active": True}
            if actor_id:
                row["updated_by"] = actor_id
            if cid in known:
                to_update.append((known[cid], value))
            else:
                to_insert.append(row)
        if to_insert:
            supabase.table("grade_weight_config").insert(to_insert).execute()
        for rid, value in to_update:
            patch = {"weight_percent": value, "is_active": True}
            if actor_id:
                patch["updated_by"] = actor_id
            supabase.table("grade_weight_config").update(patch)\
                .eq("id", rid).eq("school_id", school_id).execute()
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: save config failed: %s", exc)
        return False, {"error": "Gagal menyimpan bobot", "status": 400}

    return True, {"weights": cleaned, "total": total, "cleared": not cleaned}


# ── the preview's seed: a real pupil's own marks ─────────────────────────────

def find_pupils(supabase, school_id: str, query, limit: int = 8) -> list[dict]:
    """Active pupils of **this school** whose name contains ``query``.

    A preview over invented scores answers "what does 80/90 carry?". The read
    behind its picker is what lets an admin ask the better question — what does a
    *named* learner's record carry out under the distribution being typed. Two
    rules, both deliberate:

    * the school is a required argument **and** a filter, so a name typed at one
      school can never surface a pupil of another however the query is spelled;
    * a query shorter than two characters finds **nothing**. A search box that
      answers "" with the whole roster is a roster dump with a text field in
      front of it, and this page never needed one.

    Names come from ``profiles`` (the school's own pupils); the class is read from
    the pupil's row so two learners with one name can be told apart. A pupil whose
    record is ``alumni``/``dropped`` is not offered: weights are previewed for the
    running year.
    """
    school = str(school_id or "")
    term = " ".join((query or "").split())
    if not school or len(term) < 2:
        return []
    try:
        size = max(1, int(limit))
    except (TypeError, ValueError):
        size = 8
    people = _rows(supabase.table("profiles").select("id, full_name")
                   .eq("school_id", school).eq("role", "murid")
                   .ilike("full_name", f"%{term}%").limit(size))
    ids = [str(p["id"]) for p in people if p.get("id")]
    if not ids:
        return []
    rows = _rows(supabase.table("students").select("id, classes(name)")
                 .in_("id", ids).eq("status", "active"))
    names = {str(p["id"]): (p.get("full_name") or "?") for p in people if p.get("id")}
    out = []
    for row in rows:
        pid = str(row.get("id"))
        if pid not in names:
            continue
        out.append({"id": pid, "name": names[pid],
                    "class_name": (row.get("classes") or {}).get("name") or ""})
    out.sort(key=lambda p: p["name"].casefold())
    return out


def pupil_component_marks(supabase, school_id: str, subject_id, year_id: str | None,
                          student_id) -> dict | None:
    """One pupil's own mean in each component of one subject — the preview's seed.

    Returns ``{"pupil": {id, name, class_name}, "marks": {component_id: mean},
    "scored": n}`` when the pupil is **this school's**, else ``None``. ``None`` is
    the ownership answer, not an empty read: the caller refuses the request rather
    than answering "no marks", so a forged id from another school cannot be told
    apart from a real pupil with nothing graded.

    ``marks`` holds only the components the pupil has a mark in, on purpose. A
    component left out makes the preview apply this module's own
    :data:`MISSING_COMPONENT_POLICY` ("zero") instead of a zero this read invented,
    so the admin sees the same gap the roster will have.

    The arithmetic is :func:`compute`'s: each component's mean over the pupil's
    scored papers (``final_score`` when present, else ``score``), read through this
    school's exams for this subject and year — so a paper of another subject, of
    another year, or of another school's pupil is never counted. A paper the
    teacher has not released **does** count here: this is the teacher's roster
    arithmetic, which includes every graded paper, and the preview exists to agree
    with it rather than to invent a second rule.
    """
    school = str(school_id or "")
    student = str(student_id or "")
    if not school or not subject_id or not student:
        return None
    pupil = _rows(supabase.table("students")
                  .select("id, profiles!inner(full_name), classes(name)")
                  .eq("id", student).eq("school_id", school)
                  .eq("status", "active"))
    if not pupil:
        return None
    row = pupil[0]
    exams_q = (supabase.table("exams").select("id, grade_component_type_id")
               .eq("school_id", school).eq("subject_id", subject_id))
    if year_id:
        exams_q = exams_q.eq("school_year_id", year_id)
    exams = _rows(exams_q)
    components = {str(e["id"]): e.get("grade_component_type_id")
                  for e in exams if e.get("id")}
    out = {
        "pupil": {"id": student,
                  "name": (row.get("profiles") or {}).get("full_name") or "?",
                  "class_name": (row.get("classes") or {}).get("name") or ""},
        "marks": {},
        "scored": 0,
    }
    if not components:
        return out
    subs = _rows(supabase.table("submissions").select("exam_id, score, final_score")
                 .in_("exam_id", list(components)).in_("student_id", [student]))
    buckets: dict[str, list[float]] = {}
    for sub in subs:
        cid = components.get(str(sub.get("exam_id")))
        if not cid:
            continue                    # an uncategorised paper cannot be placed
        raw = sub.get("final_score")
        if raw is None:
            raw = sub.get("score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            continue
        buckets.setdefault(str(cid), []).append(score)
    out["marks"] = {cid: round(sum(v) / len(v), 1) for cid, v in buckets.items()}
    out["scored"] = sum(len(v) for v in buckets.values())
    return out


# ── the mark itself ──────────────────────────────────────────────────────────

def compute(rows, weights: dict) -> dict:
    """The pupil's final mark from their scored rows and the subject's weights.

    ``rows`` is an iterable of ``(component_id, score)`` — ``component_id`` may be
    ``None`` for a paper the teacher has not categorised, and ``score`` may be
    ``None`` for a sitting with no mark yet.

    Returns::

        {
          "mode":       "weighted" | "simple",
          "final":      float | None,     # None when there is nothing to average
          "detail":     [ {component_id, weight, average, count}, … ],
          "untagged":   int,              # scored rows ignored under weighting
          "total":      int,              # weights total, 100 when weighted
        }

    The arithmetic, spelled out: each configured component's mean is multiplied by
    its weight and divided by 100; a component with no score counts as 0 (see the
    module docstring), and the results are summed. Under ``"simple"`` the plain
    mean over every score is taken and ``detail`` is empty.
    """
    scored = [(cid, float(score)) for cid, score in rows if score is not None]
    weight_map = {str(k): int(v) for k, v in (weights or {}).items() if int(v or 0) > 0}

    if not weight_map:
        values = [s for _cid, s in scored]
        final = round(sum(values) / len(values), 1) if values else None
        return {"mode": "simple", "final": final, "detail": [],
                "untagged": 0, "total": 0}

    buckets: dict[str, list[float]] = {cid: [] for cid in weight_map}
    untagged = 0
    for cid, score in scored:
        key = str(cid) if cid is not None else ""
        if key in buckets:
            buckets[key].append(score)
        else:
            # A scored paper with no component (or one this subject does not
            # weight) cannot be placed; it is counted and reported, not guessed.
            untagged += 1

    detail, total = [], 0.0
    for cid in weight_map:                       # dict order = insertion order
        values = buckets.get(cid, [])
        average = sum(values) / len(values) if values else 0.0
        weight = weight_map[cid]
        detail.append({
            "component_id": cid,
            "weight": weight,
            "average": round(average, 1) if values else None,
            "count": len(values),
        })
        total += average * weight / 100.0

    return {"mode": "weighted", "final": round(total, 1), "detail": detail,
            "untagged": untagged, "total": sum(weight_map.values())}


def subject_finals(supabase, school_id: str, subject_id: str, year_id: str | None,
                   student_ids: list[str]) -> dict:
    """Every pupil's final mark in one subject, in one place.

    This is the read the roster table, the XLSX and the PDF all share, so the
    number on screen and the number in the export cannot be computed twice and
    disagree. It returns ``{student_id: compute(...)}`` where the value is exactly
    what :func:`compute` returns — ``mode``, ``final``, ``detail``, ``untagged``.

    Scoping, stated so a caller cannot widen it by accident: the exams read is
    scoped to the **school and the subject** (and the year when one is given), and
    the submissions read is scoped to those exam ids **and** the student ids
    handed in. A pupil outside ``student_ids`` is never read, and a paper of
    another subject is never counted.
    """
    students = [str(s) for s in (student_ids or []) if s]
    if not school_id or not subject_id or not students:
        return {}
    try:
        exams_q = (supabase.table("exams")
                   .select("id, grade_component_type_id")
                   .eq("school_id", school_id).eq("subject_id", subject_id))
        if year_id:
            exams_q = exams_q.eq("school_year_id", year_id)
        exams = exams_q.execute().data or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: exam read failed: %s", exc)
        exams = []
    exam_components = {str(e["id"]): e.get("grade_component_type_id")
                       for e in exams if e.get("id")}
    weights = effective_config(supabase, school_id, subject_id, year_id)
    if not exam_components:
        return {sid: compute([], weights) for sid in students}
    try:
        subs = (supabase.table("submissions")
                .select("student_id, exam_id, score, final_score")
                .in_("exam_id", list(exam_components))
                .in_("student_id", students).execute().data or [])
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: submission read failed: %s", exc)
        subs = []
    by_student: dict[str, list] = {sid: [] for sid in students}
    for sub in subs:
        sid = str(sub.get("student_id"))
        if sid not in by_student:
            continue
        raw = sub.get("final_score")
        if raw is None:
            raw = sub.get("score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        by_student[sid].append((exam_components.get(str(sub.get("exam_id"))), score))
    return {sid: compute(rows, weights) for sid, rows in by_student.items()}


def finals_for_student(supabase, school_id: str, subject_ids, year_id: str | None,
                       student_id: str, *, released_only: bool = True) -> dict:
    """One pupil's final mark in each of **several** subjects, in two reads.

    The single-subject read (:func:`subject_finals`) answers the teacher's roster:
    one subject, every pupil. The pupil's own dashboard asks the transpose — one
    pupil, every subject — so calling :func:`subject_finals` per subject would cost
    two reads per subject on a 1-CPU box. This is that transpose, but the
    arithmetic is the same :func:`compute` and the weights are the same
    effective-policy read (a subject's own config, else the school default), so the
    number beside a subject on the pupil's page is the number the teacher's table
    computes for it.

    Scoping, stated so no caller can widen it: the exams read is bounded by the
    **school**, the **subject ids asked for** and the year when one is given; the
    submissions read is bounded by those exam ids **and the one pupil id**. A
    classmate is never read, and a paper of another subject or another year is
    never counted. Returns ``{subject_id: compute(...)}``, one entry per requested
    subject even when the pupil has no rows in it.

    ``released_only`` is the pupil's own rule, not the teacher's: a mark is not
    official to the pupil until the teacher releases it, so an unreleased paper is
    left out of the pupil's final. The teacher's roster reads every graded paper —
    which is why this is a parameter and not a silent difference.
    """
    subjects = [str(s) for s in (subject_ids or []) if s]
    student = str(student_id or "")
    if not school_id or not subjects or not student:
        return {}

    # 1. the papers of these subjects, one read
    try:
        exams_q = (supabase.table("exams")
                   .select("id, subject_id, grade_component_type_id")
                   .eq("school_id", school_id).in_("subject_id", subjects))
        if year_id:
            exams_q = exams_q.eq("school_year_id", year_id)
        exams = exams_q.execute().data or []
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: exam read failed: %s", exc)
        exams = []
    exam_subject = {str(e["id"]): str(e.get("subject_id"))
                    for e in exams if e.get("id")}
    exam_components = {str(e["id"]): e.get("grade_component_type_id")
                       for e in exams if e.get("id")}

    # 2. the weights, one read for the whole school plus its default
    configs = configs_for_school(supabase, school_id, year_id)
    default = default_config(supabase, school_id)

    def _entry(subj: str, rows: list) -> dict:
        mark = compute(rows, configs.get(subj) or default)
        # Under weighting, `compute` answers 0.0 for a subject the pupil has no
        # scored papers in — the same "missing component is zero" policy the
        # teacher's table applies, which is right for a roster but would print a
        # hard `0` on the pupil's own card. `scored` lets the page tell "no mark
        # yet" from a real zero without re-deriving the policy here.
        mark["scored"] = sum(1 for _cid, score in rows if score is not None)
        return mark

    if not exam_subject:
        return {subj: _entry(subj, []) for subj in subjects}

    # 3. this pupil's rows, one read
    columns = "exam_id, score, final_score, is_published, status"
    try:
        subs = (supabase.table("submissions").select(columns)
                .in_("exam_id", list(exam_subject))
                .in_("student_id", [student]).execute().data or [])
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("grade_weighting: submission read failed: %s", exc)
        subs = []

    by_subject: dict[str, list] = {subj: [] for subj in subjects}
    for sub in subs:
        if released_only and not result_released(sub):
            continue
        subj = exam_subject.get(str(sub.get("exam_id")))
        if subj not in by_subject:
            continue
        raw = sub.get("final_score")
        if raw is None:
            raw = sub.get("score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        by_subject[subj].append((exam_components.get(str(sub.get("exam_id"))), score))

    out: dict[str, dict] = {}
    for subj in subjects:
        out[subj] = _entry(subj, by_subject[subj])
    return out


def rows_from_submissions(submissions: list[dict], exam_components: dict) -> list[tuple]:
    """``(component_id, score)`` rows from submissions and an exam→component map.

    The score is ``final_score`` when present, else ``score``; a row with neither
    is a sitting with no mark and is skipped by :func:`compute`.
    """
    out = []
    for sub in submissions or []:
        raw = sub.get("final_score")
        if raw is None:
            raw = sub.get("score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        out.append((exam_components.get(str(sub.get("exam_id"))), score))
    return out
