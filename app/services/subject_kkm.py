"""KKM — a subject's minimum mastery mark — as a fact one school can state.

Why a module rather than a column
---------------------------------
Fase 0 found where the number lives today: ``exams.passing_score``, a per-paper
integer defaulting to 70, read in ~27 places to decide lulus / tidak lulus. That
placement is why a school cannot say what it actually knows — "Geography passes at
70 in year 7 and 75 in year 9" — and why one subject can pass at two marks in the
same building.

The resolution order, and why the year is an argument
-----------------------------------------------------
:func:`effective` answers in three steps: an override for this grade, else the
subject's general mark, else :data:`DEFAULT_KKM`. The **year** is a required part
of the question rather than context read from "now", because a mark that decided a
printed report must not move afterwards. A caller that genuinely wants the running
year passes it; nothing here goes looking for it.

What this module deliberately does not do
-----------------------------------------
It does not rewrite ``exams.passing_score``. A paper keeps the mark it was created
with, so marks already awarded never change — the property Fase 0 asked to protect
above all. Moving the *reports* onto this table is a separate step; until then a
new paper can be created with this subject's mark, and an old paper keeps its own.

Every read and write takes the caller's ``school_id`` and never a school from the
request. Writes to a **closed** year are refused: a finished year's marks are
history.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The mark a subject with nothing on file reads as. The value the app has always
#: used, which is why nothing changes for a school that never opens this page.
DEFAULT_KKM = 70

#: What a mark may be. Mirrored by migration 051's CHECK, so the database refuses
#: a bad number even if a caller forgets to.
MIN_KKM, MAX_KKM = 0, 100

#: Every reason a write can refuse, as a key the pages translate. Kept together so
#: the bilingual catalogue can be checked against it: a refusal with no sentence
#: renders as an empty alert in both languages.
REFUSALS = (
    "bad_value",
    "bad_subject",
    "bad_grade",
    "year_closed",
    "write_failed",
)

COLUMNS = ("id, subject_id, school_year_id, grade_level, kkm, updated_by, updated_at")


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read.

    These rows decorate a page and answer a resolution; a Supabase hiccup should
    still let the page render (the default mark is a real answer), not 500.
    """
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("subject_kkm: read failed: %s", exc)
        return []


def _text(value) -> str | None:
    """A grade level as the text the column holds — ``7`` and ``"7"`` are one."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def list_kkm(supabase, school_id: str, *, year_id: str | None = None) -> list[dict]:
    """Every mark this school has on file for ``year_id``."""
    if not school_id:
        return []
    query = supabase.table("subject_kkm").select(COLUMNS).eq("school_id", school_id)
    if year_id:
        query = query.eq("school_year_id", year_id)
    return _rows(query)


def effective(supabase, school_id: str, subject_id: str, *,
              grade_level=None, year_id: str | None = None) -> int:
    """The mark that applies to this subject, grade and year.

    One read, filtered by school, subject and year, then chosen in code: the
    override for this grade if there is one, else the subject's general mark, else
    :data:`DEFAULT_KKM`. Read in one query rather than three so a page that shows
    forty subjects does not pay a hundred and twenty round-trips.
    """
    if not school_id or not subject_id:
        return DEFAULT_KKM
    query = supabase.table("subject_kkm").select("grade_level, kkm")\
        .eq("school_id", school_id).eq("subject_id", subject_id)
    if year_id:
        query = query.eq("school_year_id", year_id)
    rows = _rows(query)
    wanted = _text(grade_level)
    general = None
    for row in rows:
        level = _text(row.get("grade_level"))
        if wanted is not None and level == wanted:
            return _value(row.get("kkm"))
        if level is None:
            general = row.get("kkm")
    return _value(general) if general is not None else DEFAULT_KKM


def _value(raw) -> int:
    """Whatever the row holds, as a mark this module would itself accept."""
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_KKM
    return number if MIN_KKM <= number <= MAX_KKM else DEFAULT_KKM


def set_kkm(supabase, school_id: str, subject_id: str, value, *,
            grade_level=None, year_id: str | None = None,
            actor_id: str | None = None) -> dict:
    """Create or edit one mark, or refuse with a reason.

    Both refusals happen **before** any write, and the closed-year check is a read
    of ``school_years`` — so a refused write leaves ``subject_kkm`` untouched.

    Written as read-then-update-or-insert rather than an upsert: the uniqueness
    that makes "one general mark" true is a **partial** index (``WHERE grade_level
    IS NULL``), and PostgREST cannot name a partial index in ``ON CONFLICT`` — the
    same wall ``save_levels`` hit on ``student_subject_levels``. Two statements,
    still bounded.
    """
    if not school_id or not subject_id:
        return {"ok": False, "reason": "bad_subject"}
    try:
        number = int(value)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "bad_value"}
    if not MIN_KKM <= number <= MAX_KKM:
        return {"ok": False, "reason": "bad_value"}

    level = _text(grade_level)
    if not _year_is_editable(supabase, school_id, year_id):
        return {"ok": False, "reason": "year_closed"}

    payload = {"kkm": number, "updated_by": actor_id} if actor_id else {"kkm": number}

    find = supabase.table("subject_kkm").select("id")\
        .eq("school_id", school_id).eq("subject_id", subject_id)\
        .eq("school_year_id", year_id)
    # A general mark is the row whose level is null, and `.eq` cannot say that.
    find = find.is_("grade_level", "null") if level is None else find.eq("grade_level", level)
    existing = _rows(find)
    if existing:
        written = _rows(supabase.table("subject_kkm").update(payload)
                        .eq("id", existing[0]["id"]).eq("school_id", school_id))
        return {"ok": bool(written), "reason": "" if written else "write_failed",
                "kkm": written[0] if written else None, "created": False}

    payload.update({"school_id": school_id, "subject_id": subject_id,
                    "school_year_id": year_id, "grade_level": level})
    written = _rows(supabase.table("subject_kkm").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "kkm": written[0] if written else None, "created": True}


def clear_override(supabase, school_id: str, subject_id: str, grade_level,
                   *, year_id: str | None = None) -> dict:
    """Remove a level's override so it reads the subject's general mark again.

    Deletes only the override row: the general mark is a different row and is not
    what the admin asked to change.
    """
    level = _text(grade_level)
    if not school_id or not subject_id or level is None:
        return {"ok": False, "reason": "bad_grade"}
    if not _year_is_editable(supabase, school_id, year_id):
        return {"ok": False, "reason": "year_closed"}
    removed = _rows(supabase.table("subject_kkm").delete()
                    .eq("school_id", school_id).eq("subject_id", subject_id)
                    .eq("school_year_id", year_id).eq("grade_level", level))
    if removed is None:
        return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "reason": ""}


def _year_is_editable(supabase, school_id: str, year_id: str | None) -> bool:
    """Whether a mark for this year may be written.

    A year that cannot be read at all is treated as editable: a school with no
    ``school_years`` row yet (or a database that predates 047) must not be locked
    out of a page it has always been able to use. A year that *is* read and is
    ``closed`` is refused — that is the promise this helper exists to keep.
    """
    if not year_id:
        return True
    try:
        rows = supabase.table("school_years").select("status").eq(
            "id", year_id).eq("school_id", school_id).execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("subject_kkm: could not read the year: %s", exc)
        return True
    if not rows:
        return True
    return (rows[0].get("status") or "").lower() != "closed"
