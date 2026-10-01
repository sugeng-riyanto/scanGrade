"""Where a pupil was, year by year — the fact promotion used to overwrite.

`students.class_id` and `profiles.class_id` are single mutable pointers:
promotion rewrites both, and nothing anywhere records the class a child sat in
last year. `student_enrollment` (migration 046) is that record, one row per
pupil per school year, and this module is the only place that decides what a
row's `status` may be and what a pupil's history looks like once the rows exist.

Two rules live here rather than in the routes, because the promote wizard, the
year-close flow and the longitudinal progress chart all need the same answers:

* **an outcome is one of five words** — `aktif`, `naik`, `tinggal_kelas`,
  `pindah`, `lulus` — and the database CHECK holds the same list, so a typo is
  refused on both sides;
* **the last grade of a school defaults to `lulus`, not `naik`** — a pupil
  cannot be promoted out of a school that has no higher year, and the wizard's
  default must say so before the admin has to.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The five words a `student_enrollment.status` may hold. Kept in step with the
#: CHECK in migration 046 by `tests/unit/test_enrollment.py`.
STATUSES = ("aktif", "naik", "tinggal_kelas", "pindah", "lulus")

#: Statuses that mean "this pupil left this school" — they are why a pupil with
#: no further year is not treated as a data error.
LEFT_STATUSES = ("pindah", "lulus")


def is_status(value) -> bool:
    return value in STATUSES


def normalise_status(value, default="aktif") -> str:
    value = str(value or "").strip().lower()
    return value if value in STATUSES else default


def grade_order(levels) -> list[str]:
    """Grade labels ordered low → high, best-effort.

    Indonesian schools write grades as `7..9` or `VII..IX` or `10..12`. The
    order decides which grade is *final*, so it has to be recovered from the
    labels the school actually uses rather than assumed.
    """
    roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
             "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}

    def rank(label):
        word = str(label or "").strip().lower()
        if word in roman:
            return roman[word]
        digits = "".join(ch for ch in word if ch.isdigit())
        return int(digits) if digits else 0

    ordered, seen = [], set()
    for label in sorted(levels or [], key=rank):
        if not str(label or "").strip():
            continue
        if label in seen:
            continue
        seen.add(label)
        ordered.append(label)
    return ordered


def default_outcome(grade_level, known_levels) -> str:
    """`lulus` for a school's last grade, `naik` for every other one.

    A wizard that defaulted every pupil to `naik` would silently graduate nobody
    and leave the final-year cohort in a year that no longer exists.
    """
    levels = grade_order(known_levels)
    last = levels[-1] if levels else None
    if last is not None and str(grade_level or "").strip() == str(last).strip():
        return "lulus"
    return "naik"


def history(supabase, student_id) -> list:
    """Every enrollment row for one pupil, oldest year first.

    Joined to the class and the year so a caller does not have to fetch names
    separately — this is the shape the longitudinal chart and the pupil's own
    year filter both render.
    """
    res = (supabase.table("student_enrollment")
           .select("id, status, note, class_id, school_year_id, "
                   "classes(name, grade_level), school_years(name, start_date, end_date)")
           .eq("student_id", student_id)
           .execute())
    rows = getattr(res, "data", None) or []
    return sorted(rows, key=lambda r: ((r.get("school_years") or {}).get("start_date") or "",
                                       (r.get("school_years") or {}).get("name") or ""))


def current_class_id(supabase, student_id, school_year_id) -> str | None:
    """The class this pupil is enrolled in for `school_year_id`, if any."""
    if not student_id or not school_year_id:
        return None
    res = (supabase.table("student_enrollment")
           .select("class_id")
           .eq("student_id", student_id)
           .eq("school_year_id", school_year_id)
           .execute())
    rows = getattr(res, "data", None) or []
    return rows[0].get("class_id") if rows else None


def record(supabase, school_id, student_id, school_year_id, class_id, status="aktif",
           note=None) -> dict:
    """Write (or update) one pupil's membership for one year.

    Conflicts on `(student_id, school_year_id)` — the partial unique index in
    046 — rather than the primary key, because the payload carries no `id` and
    the default target in supabase-py would have nothing to collide with. Without
    this, re-running a promotion for the same year would raise a duplicate-key
    error instead of updating the row it meant to update.
    """
    payload = {
        "school_id": school_id,
        "student_id": student_id,
        "school_year_id": school_year_id,
        "class_id": class_id,
        "status": normalise_status(status),
        "note": note,
    }
    res = (supabase.table("student_enrollment")
           .upsert(payload, on_conflict="student_id,school_year_id")
           .execute())
    return (res.data or [{}])[0]


def progress_by_year(history_rows, subject_key="subject") -> list:
    """A pupil's rows, oldest first, as (year label, value) pairs a chart can use.

    Empty years are **kept as a gap**, not filled with a zero: a pupil who
    arrived mid-way has no data for the years before, and drawing a zero there
    would read as a fail rather than as an absence.
    """
    series = []
    for row in history_rows:
        series.append({
            "year": ((row.get("school_years") or {}).get("name") or ""),
            "value": row.get(subject_key),
            "has_data": row.get(subject_key) is not None,
        })
    return series
