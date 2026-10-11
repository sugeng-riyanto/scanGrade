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

from app.services import subject_kkm as kkm_service
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


def paper_weight_gap(supabase, school_id: str, subject_id: str, component_id,
                     year_id: str | None) -> dict | None:
    """Why a paper's component does not reach the final mark, or ``None``.

    :func:`compute` counts a scored paper in ``untagged`` when its component is not
    one the subject weights, so the paper contributes **nothing** to the final
    mark. The realistic path into that state is not the picker — it only ever
    offers weighted components — it is the *drift* after the fact: the admin drops
    a component from a subject's weights while papers are already filed under it.

    Returns ``{"component_id", "name", "weighted": [names]}`` — the component the
    paper is filed under and the components that *would* count — or ``None`` when
    there is no gap. It is deliberately quiet in the three states that are not a
    gap:

    * an uncategorised paper (``component_id`` is ``None``) — a separate,
      deliberate state the roster already reports in ``untagged``;
    * a subject with no weight policy at all — :func:`effective_config` answers
      ``{}`` and the simple mean counts every paper, so nothing is lost;
    * a component the school does not own — not this school's policy to warn about.

    A component that was **deactivated** is still named: switching a component off
    is the commonest drift of all, and a deactivated component is exactly one the
    subject's active weights no longer carry.
    """
    cid = str(component_id) if component_id else ""
    if not cid or not school_id or not subject_id:
        return None
    weights = effective_config(supabase, school_id, subject_id, year_id)
    if not weights or cid in weights:
        return None
    components = {str(c["id"]): c
                  for c in list_components(supabase, school_id, active_only=False)
                  if c.get("id")}
    if cid not in components:
        return None
    return {
        "component_id": cid,
        "name": components[cid].get("name") or "?",
        "weighted": [(components.get(w) or {}).get("name") or "?" for w in weights],
    }


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
        # The pupil's own mean in each component they have a mark in. `compute`
        # already buckets these rows this way for its weighted branch; this is
        # the same bucketing kept **as data**, so a caller can re-run the
        # arithmetic under other weights — which is what the weight page needs to
        # show one learner across several subjects, under the distribution being
        # typed rather than the saved one. A component with no mark is *absent*
        # rather than 0, exactly as in :func:`pupil_component_marks`: the caller
        # applies this module's own MISSING_COMPONENT_POLICY instead of a zero
        # this read invented. Only scored rows with a component count — an
        # uncategorised sitting has no component to be placed in.
        buckets: dict[str, list[float]] = {}
        for cid, score in rows:
            if score is None or not cid:
                continue
            buckets.setdefault(str(cid), []).append(score)
        mark["marks"] = {cid: round(sum(v) / len(v), 1)
                         for cid, v in buckets.items()}
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


# ── what a change does to the class, not to one sample ────────────────────────

#: The movement that counts as a move. Both marks are rounded to one decimal, so a
#: real change is a multiple of 0.1; this only keeps float noise out.
IMPACT_MIN_DELTA = 0.05

#: How many rows one impact read may page through before it says it may have
#: stopped short. PostgREST hands back 1000 rows per request whatever the caller
#: asked for, so a school-wide read that does not page is a *silently partial*
#: answer — the counts would look plausible and be wrong.
IMPACT_PAGE = 1000
IMPACT_ROW_CAP = 20000


def _paged_rows(build, *, page: int = IMPACT_PAGE,
                cap: int = IMPACT_ROW_CAP) -> tuple[list[dict], bool]:
    """``(rows, capped)`` — every row, past PostgREST's own 1000-row window.

    ``build`` returns a **fresh** query each time it is called (a builder is
    cheap; reusing one while moving its range is how a page gets skipped), and it
    is called until a short page comes back — so the loop ends on the real end of
    the data, not on a guess: a *full* page is followed by one more request, which
    comes back empty at the true end (cheaper, and far safer, than a row count that
    is one write out of date). ``capped`` is ``True`` only when the row cap was
    reached, and it means "these counts may be short", never "these counts are
    wrong": the caller reports it rather than rounding it away.
    """
    out: list[dict] = []
    offset = 0
    while True:
        take = min(page, cap - len(out))
        if take <= 0:
            return out, True
        try:
            batch = build().range(offset, offset + take - 1).execute().data or []
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("grade_weighting: paged read failed: %s", exc)
            return out, bool(out)
        out.extend(batch)
        if len(batch) < take:
            return out, False
        offset += take


def affected_subjects(supabase, school_id: str, year_id: str | None, scope: str,
                      subject_id=None) -> list[dict]:
    """The subjects a change to ``scope`` would actually move.

    ``"subject"`` is the one subject named. ``"default"`` is every **active**
    subject of the school that has no configuration of its own for this year — the
    ones the school default binds. A subject with its own row is untouched by a
    default change, so listing it would invent movement that will not happen; that
    exclusion is the whole reason this is computed here and not by the caller.

    Returns ``[{"subject_id", "name"}, …]`` in subject-name order, and **empty for
    anything else** — an unknown scope, or a subject that is not this school's, is
    nothing to weigh rather than the whole school.
    """
    if not school_id:
        return []
    rows = _rows(supabase.table("subjects").select("id, name").order("name")
                 .eq("school_id", school_id).eq("is_active", True))
    names = {str(r["id"]): (r.get("name") or "?") for r in rows if r.get("id")}
    if scope == "subject":
        sid = str(subject_id or "")
        return [{"subject_id": sid, "name": names[sid]}] if sid in names else []
    if scope != "default":
        return []
    custom = configs_for_school(supabase, school_id, year_id)
    return [{"subject_id": sid, "name": name}
            for sid, name in names.items() if not custom.get(sid)]


def class_impact(supabase, school_id: str, subjects: list[dict], year_id: str | None,
                 weights: dict, *, limit: int = 25,
                 min_delta: float = IMPACT_MIN_DELTA) -> dict:
    """Which pupils' final marks a distribution change would move, and by how much.

    The weight preview answers what a change does to **one** learner. A mark is
    changed for a class, though, so the question that decides a save is what it
    does to everyone: this takes the weights *being typed* and returns the
    movement they would cause across ``subjects``, against the marks those pupils
    carry today.

    Both sides are :func:`compute`: ``saved`` uses this year's saved policy for the
    subject (its own row, else the school default), ``live`` uses ``weights`` — so
    the same rule is compared with itself and no second arithmetic is invented.
    With ``weights`` empty the live side is the simple mean, which is exactly what
    the roster would fall back to, so the fallback is reported rather than refused.

    The payload::

        {
          "pupils":  [ {student_id, name, class_name, subject_id,
                        saved, live, delta, released}, … ],   # biggest |delta| first
          "counts":  {pairs, moved, up, down, reported, pupils_marked,
                      pupils_moved, max_up, max_down},
          "subjects":[ {subject_id, name, evaluated, moved, up, down, reported}, … ],
          "kkm":     {pupils: [ {student_id, subject_id, name, class_name,
                                 subject_name, kkm, source, saved, live, delta}, … ],
                      count, shown, checked},   # worst fall below the line first
          "shown":   int,        # rows in "pupils" (the cap is per read, not per pupil)
          "capped":  bool,       # the read hit its row cap: counts may be short
          "typed_total": int,    # the weights as typed, so a caller can say "not 100%"
        }

    Rules worth stating, because each one is a way to mislead an admin:

    * **only pupils who have a mark** in an affected subject are counted — a policy
      cannot move a mark that does not exist, and a weighted ``0`` for a pupil with
      no papers is the *policy*, not a pupil;
    * **only subjects that are affected** are read (see :func:`affected_subjects`),
      so a default change never reports movement in a subject that keeps its own
      row;
    * a movement smaller than ``min_delta`` is **not** a movement;
    * a mark the pupil has already been shown is flagged (``released``): a change
      to a number the child can open is the one that needs a decision, while a
      change to an unreleased draft is ordinary work in progress;
    * a name that cannot be read is ``"?"`` — the counts and the deltas do not
      depend on it, and a pupil is never dropped for being unprintable.

    ``kkm`` is the same question asked against the school's **pass line**: a
    **released** mark that reads as passing today and would read as failing after
    the save. It is listed separately from ``pupils`` and tested on the unrounded
    finals, so it is exact at the line rather than one display rounding away from
    it; ``checked`` is how many released pairs were tested, so an empty list is
    "nothing crosses" rather than "nothing was looked at". ``source`` says which
    KKM applied (``"grade"``, ``"subject"`` or ``None`` for the app default of
    :data:`subject_kkm.DEFAULT_KKM`), because a default is not a standard the
    school chose. The KKM is resolved from ``subject_kkm``, once per read, and
    never from ``exams.passing_score``: the paper's own mark belongs to the paper.

    Reads: the affected subjects' papers, their graded rows (paged), the saved
    policy, the KKM rows, and the pupils (name, class and level) who have a mark.
    It writes nothing.
    """
    empty = {"pupils": [], "shown": 0, "capped": False, "typed_total": 0,
             "subjects": [], "kkm": {"pupils": [], "count": 0, "shown": 0,
                                     "checked": 0},
             "counts": {"pairs": 0, "moved": 0, "up": 0, "down": 0,
                        "reported": 0, "pupils_marked": 0, "pupils_moved": 0,
                        "max_up": None, "max_down": None}}
    ids = [str(s["subject_id"]) for s in (subjects or []) if s.get("subject_id")]
    if not school_id or not ids:
        return empty

    typed: dict[str, int] = {}
    for cid, raw in (weights or {}).items():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            typed[str(cid)] = value

    def _exams_query():
        query = (supabase.table("exams")
                 .select("id, subject_id, grade_component_type_id")
                 .eq("school_id", school_id).in_("subject_id", ids))
        return query.eq("school_year_id", year_id) if year_id else query

    exams, capped = _paged_rows(_exams_query)
    exam_subject = {str(e["id"]): str(e.get("subject_id"))
                    for e in exams if e.get("id")}
    exam_component = {str(e["id"]): e.get("grade_component_type_id")
                      for e in exams if e.get("id")}
    if not exam_subject:
        return empty

    subs, page_capped = _paged_rows(
        lambda: supabase.table("submissions")
        .select("student_id, exam_id, score, final_score, is_published, status")
        .in_("exam_id", list(exam_subject)))
    capped = capped or page_capped

    rows_by: dict[tuple[str, str], list] = {}
    released: dict[tuple[str, str], bool] = {}
    for sub in subs:
        student = str(sub.get("student_id") or "")
        subject = exam_subject.get(str(sub.get("exam_id")))
        if not student or subject is None:
            continue
        raw = sub.get("final_score")
        if raw is None:
            raw = sub.get("score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            continue                      # a sitting with no mark moves no mark
        key = (student, subject)
        rows_by.setdefault(key, []).append((exam_component.get(str(sub.get("exam_id"))), score))
        if result_released(sub):
            released[key] = True

    # The pupils (name, class and — for the KKM — their grade level) are read
    # **before** the marks are weighed, once and paged: a crossing of the pass line
    # can only be judged against the level the pupil is in, and every released pair
    # needs it, so looking it up per pair would be a query per pupil. The KKM rows
    # are read once for the same reason. Both are bounded by the school, and the
    # KKM by the year, so neither can be answered from another school's file.
    names: dict[str, str] = {}
    classes: dict[str, str] = {}
    levels: dict[str, object] = {}
    if rows_by:
        people, people_capped = _paged_rows(
            lambda: supabase.table("students")
            .select("id, profiles!inner(full_name), classes(name, grade_level)")
            .in_("id", sorted({student for (student, _subject) in rows_by}))
            .eq("school_id", school_id))
        capped = capped or people_capped
        for person in people:
            if not person.get("id"):
                continue
            key = str(person["id"])
            names[key] = ((person.get("profiles") or {}).get("full_name") or "?")
            klass = person.get("classes") or {}
            classes[key] = klass.get("name") or ""
            levels[key] = klass.get("grade_level")
    kkm_rows = (kkm_service.list_kkm(supabase, school_id, year_id=year_id)
                if rows_by else [])

    custom = configs_for_school(supabase, school_id, year_id)
    default = default_config(supabase, school_id)
    name_of_subject = {str(s["subject_id"]): (s.get("name") or "?") for s in subjects}
    per_subject = {sid: {"subject_id": sid, "name": name_of_subject.get(sid, "?"),
                         "evaluated": 0, "moved": 0, "up": 0, "down": 0,
                         "reported": 0} for sid in ids}

    marked: set[str] = set()
    moved: list[dict] = []
    crossings: list[dict] = []
    released_pairs = 0
    for (student, subject), rows in rows_by.items():
        saved = compute(rows, custom.get(subject) or default)
        live = compute(rows, typed)
        if saved.get("final") is None or live.get("final") is None:
            continue
        entry = per_subject[subject]
        entry["evaluated"] += 1
        marked.add(student)
        was_released = bool(released.get((student, subject)))
        # The pass line, tested on the **released** marks only: a mark the pupil
        # cannot open yet is work in progress, and warning about it would train the
        # admin to click past the warning. A crossing is a mark that reads as
        # passing to the child and would read as failing after the save — the one
        # change a distribution must not make silently — and it is tested before
        # ``min_delta``, so a caller's movement threshold can never hide it.
        if was_released:
            released_pairs += 1
            kkm, source = kkm_service.resolve_with_source(
                kkm_rows, subject, levels.get(student))
            if saved["final"] >= kkm > live["final"]:
                crossings.append({
                    "student_id": student, "subject_id": subject,
                    "name": names.get(student, "?"),
                    "class_name": classes.get(student, ""),
                    "subject_name": entry["name"], "kkm": kkm, "source": source,
                    "saved": saved["final"], "live": live["final"],
                    "delta": round(live["final"] - saved["final"], 1)})
        delta = round(live["final"] - saved["final"], 1)
        if abs(delta) < min_delta:
            continue                      # unchanged is the common answer, and not a finding
        entry["moved"] += 1
        entry["up" if delta > 0 else "down"] += 1
        if was_released:
            entry["reported"] += 1
        moved.append({"student_id": student, "subject_id": subject,
                      "name": names.get(student, "?"),
                      "class_name": classes.get(student, ""),
                      "subject_name": entry["name"],
                      "saved": saved["final"], "live": live["final"],
                      "delta": delta, "released": was_released})

    moved.sort(key=lambda r: (-abs(r["delta"]), r["subject_id"], r["student_id"]))
    # Worst fall below the line first, so a capped list still shows the pupils an
    # admin most needs to see; the pupil is the tie-break, so the order is stable.
    crossings.sort(key=lambda r: (r["live"] - r["kkm"], r["subject_id"],
                                  r["student_id"]))
    try:
        size = max(1, int(limit))
    except (TypeError, ValueError):
        size = 25
    top = moved[:size]

    ups = [r["delta"] for r in moved if r["delta"] > 0]
    downs = [r["delta"] for r in moved if r["delta"] < 0]
    return {
        "pupils": top,
        "shown": len(top),
        "capped": capped,
        "typed_total": sum(typed.values()),
        "subjects": [per_subject[sid] for sid in ids],
        "kkm": {"pupils": crossings[:size], "count": len(crossings),
                "shown": len(crossings[:size]), "checked": released_pairs},
        "counts": {
            "pairs": sum(e["evaluated"] for e in per_subject.values()),
            "moved": len(moved),
            "up": sum(e["up"] for e in per_subject.values()),
            "down": sum(e["down"] for e in per_subject.values()),
            "reported": sum(e["reported"] for e in per_subject.values()),
            "pupils_marked": len(marked),
            "pupils_moved": len({r["student_id"] for r in moved}),
            "max_up": max(ups) if ups else None,
            "max_down": min(downs) if downs else None,
        },
    }


def pupil_subject_finals(supabase, school_id: str, student_id,
                         year_id: str | None = None) -> dict | None:
    """One pupil's final mark in **every** subject they have a mark in.

    :func:`finals_for_student` answers the pupil's own page (one mark per subject
    they are enrolled in). This answers the admin's question when a distribution
    is about to change: *is this policy fair between the subjects this learner
    actually sits?* Only subjects the pupil has a **scored** paper in are listed —
    a subject with no mark has nothing to compare, and a row of empty cells would
    read as a zero.

    Returns ``{"pupil": {id, name, class_name}, "subjects": [ … ]}`` for a pupil
    of **this school**, else ``None``. ``None`` is the ownership answer, not an
    empty read — the same contract :func:`pupil_component_marks` keeps, so a
    forged id from another school is a refusal rather than "no marks".

    Each row carries both the arithmetic's result and its **input**:

    * ``final``, ``mode``, ``total`` — the mark under the *saved* policy, exactly
      as the teacher's roster and the pupil's card compute it;
    * ``marks`` — the pupil's own mean per component, so the page can re-run the
      same rule under the weights currently being typed and show the change
      before it is saved. The arithmetic is still :func:`compute`'s (the page's
      ``sgPreviewFinal`` is its mirror); this read only hands over the numbers it
      works on;
    * ``scored`` (graded papers) and ``untagged`` (graded papers filed under no
      component, which therefore cannot reach a weighted mark) so a comparison
      cannot silently imply that every paper counted.

    The weights are read the roster's way, not the pupil's: ``released_only`` is
    ``False`` here on purpose. The pupil's own page hides an unreleased paper
    until the teacher releases it, but an admin comparing subjects is comparing
    what the school's tables report — so this must equal
    :func:`finals_for_student` at the roster's default, and a guard asserts that.
    Rows come back in subject-name order, so the list is a comparison and never
    a ranking.
    """
    school = str(school_id or "")
    student = str(student_id or "")
    if not school or not student:
        return None
    pupil = _rows(supabase.table("students")
                  .select("id, profiles!inner(full_name), classes(name)")
                  .eq("id", student).eq("school_id", school)
                  .eq("status", "active"))
    if not pupil:
        return None
    row = pupil[0]
    # Only the subjects this page can weight: the matrix lists the school's
    # **active** subjects, so a row for a retired subject would be a cell the
    # admin has no weights for and could not act on.
    people = _rows(supabase.table("subjects").select("id, name")
                   .eq("school_id", school).eq("is_active", True))
    ids = [str(s["id"]) for s in people if s.get("id")]
    names = {str(s["id"]): (s.get("name") or "?") for s in people if s.get("id")}
    out = {
        "pupil": {"id": student,
                  "name": (row.get("profiles") or {}).get("full_name") or "?",
                  "class_name": (row.get("classes") or {}).get("name") or ""},
        "subjects": [],
    }
    if not ids:
        return out
    finals = finals_for_student(supabase, school, ids, year_id, student,
                                released_only=False)
    listed = []
    for sid in ids:
        mark = finals.get(sid) or {}
        if not mark.get("scored"):
            continue
        listed.append({"subject_id": sid, "name": names.get(sid, "?"),
                       "final": mark.get("final"), "mode": mark.get("mode"),
                       "scored": mark.get("scored", 0),
                       "untagged": mark.get("untagged", 0),
                       "total": mark.get("total", 0),
                       "marks": mark.get("marks") or {}})
    listed.sort(key=lambda r: r["name"].casefold())
    out["subjects"] = listed
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
