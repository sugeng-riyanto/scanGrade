"""The subject a paper is for, as a display name — resolved once and never ``None``.

Why this exists
---------------
`exams.subject` is a legacy `TEXT` column. The current exam builder stores
`subject_id` (an FK to `subjects`) and never writes the old text column, so a paper
created today carries a ``NULL`` `subject` while its real mapel lives in another
table. The printed sheets read ``{{ exam.get('subject', '-') }}``, and that default
only applies when the *key is absent* — the key is present with a ``None`` value, so
Python's ``None`` leaked straight onto the page as the literal word ``None`` beside
"Mata Pelajaran".

One resolver, because three documents print the subject (the exam sheet, the report
card and the pupil's result PDF) and they must not disagree:

* :func:`display_name` — the name already carried on the row, or ``""``;
* :func:`names_for` — ``{exam_id: name}`` for the subjects a batch of rows points at,
  in one read rather than one query per row;
* :func:`annotate` — fills ``exam["subject_name"]`` on a row (or a list of them) so a
  template has a value that is *already* the answer, not a second guess;
* :func:`label` — the pure, template-facing form: the name, or the honest fallback.

The fallback is a sentence rather than a dash on purpose: a dash says "there is a
subject and it was withheld", and an empty cell says the same thing more quietly.
"Mapel belum diatur" is the only one of the three that is true.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Shown when neither half of the answer is available. The pinned Indonesian print
#: documents use it directly; a caller that wants English passes its own string.
FALLBACK_ID = "Mapel belum diatur"
FALLBACK_EN = "Subject not set"

#: The order the row's own fields are trusted in. `subject_name` is the resolved one
#: `annotate` writes; `subject` is the legacy text the builder no longer fills.
_SOURCES = ("subject_name", "subject")


def display_name(exam) -> str:
    """The subject name already carried on an exam row, or ``""``.

    Never returns ``None``: a caller that prints the result cannot print the word
    ``None``, which is the whole bug this module answers.
    """
    if not isinstance(exam, dict):
        return ""
    for key in _SOURCES:
        value = exam.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def label(exam, fallback: str = FALLBACK_ID) -> str:
    """The display string for one exam row — the name, or ``fallback``."""
    return display_name(exam) or fallback


def names_for(supabase, exam_rows) -> dict:
    """``{exam_id: name}`` for a batch of exam rows, resolving `subject_id`.

    One query for every distinct subject id that a row does not already name, so a
    class report of thirty cards is one read rather than thirty. A read that fails
    returns the names it could not resolve as ``""`` rather than raising: a report
    that crashes because a subject could not be looked up is worse than a report that
    says the subject is not set.
    """
    exams = [e for e in (exam_rows or []) if isinstance(e, dict)]
    wanted = sorted({
        str(e.get("subject_id"))
        for e in exams
        if e.get("subject_id") and not display_name(e)
    })
    resolved: dict = {}
    if wanted:
        try:
            rows = (supabase.table("subjects").select("id, name")
                    .in_("id", wanted).execute().data or [])
            resolved = {str(r["id"]): r.get("name") for r in rows if r.get("id")}
        except Exception:
            logger.exception("Could not read names for %d subject(s)", len(wanted))
            resolved = {}
    out: dict = {}
    for exam in exams:
        if exam.get("id") is None:
            continue
        out[str(exam["id"])] = (
            display_name(exam) or (resolved.get(str(exam.get("subject_id"))) or "")
        )
    return out


def annotate(supabase, exams):
    """Fill ``subject_name`` on a row or a list of rows; returns the same shape.

    A row whose id is ``None`` (a single exam read without an id column selected)
    still gets its own name when it carries one, because :func:`names_for` is keyed
    by id and cannot answer for it — the caller that hands in one row must still be
    able to print it.
    """
    if isinstance(exams, dict):
        _annotate_one(supabase, exams)
        return exams
    if not exams:
        return exams
    names = names_for(supabase, exams)
    for exam in exams:
        if not isinstance(exam, dict):
            continue
        exam["subject_name"] = names.get(str(exam.get("id"))) or display_name(exam)
    return exams


def _annotate_one(supabase, exam: dict) -> None:
    if display_name(exam):
        exam["subject_name"] = display_name(exam)
        return
    if not exam.get("subject_id") or exam.get("id") is None:
        exam["subject_name"] = ""
        return
    names = names_for(supabase, [exam])
    exam["subject_name"] = names.get(str(exam.get("id"))) or ""
