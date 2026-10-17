"""Per-subject grading prompts: the teacher's words, scoped to their subjects.

The settings page promises "a different prompt for each subject". Before this
module the promise was kept by a JSON array with a single `active_prompt_id`, so
only one of those prompts could ever apply — and the grading pipeline did not
read it at all. The row that makes the promise true is
`ai_grading_prompt(teacher_id, subject_key)`: one rubric per subject per teacher,
with a version that a suggestion records so the advice can be traced to the
words that produced it.

Who may write one
-----------------
The prompt is personal (a teacher's own rubric), but the *subject* is not: a
teacher may only write a rubric for a subject they actually teach. That is read
from `teacher_assignments` — the same record the rest of the app uses to decide
who teaches what — with the teacher's own exams as a second, conservative
source (an exam they created names a subject they are clearly working in). The
`default` rubric is always allowed: it is the fallback for every subject, and a
teacher with no assignment yet still needs somewhere to write one.

Everything raises `ValueError` for input the page can correct and
`PermissionError` for a subject the caller does not teach, so the route can
answer 400 and 403 without a second copy of the rules.
"""
from __future__ import annotations

import logging
import re

from app.services.ai_grading_prompt import (
    DEFAULT_TEACHER_PROMPT, MAX_TEACHER_PROMPT, validate_teacher_prompt,
)
from app.services.ai_grading_suggestion import DEFAULT_SUBJECT, subject_key

logger = logging.getLogger("app")

TABLE = "ai_grading_prompt"
LABEL_MAX = 160
DEFAULT_LABEL = "Semua Mapel"


def _rows(result):
    return getattr(result, "data", None) or []


def _clean_label(label, key):
    text = re.sub(r"\s+", " ", str(label or "")).strip()[:LABEL_MAX]
    return text or (DEFAULT_LABEL if key == DEFAULT_SUBJECT else key)


def subject_choices(supabase, teacher_id) -> list[dict]:
    """The subjects this teacher may write a rubric for, plus the default.

    Ordered: default first, then alphabetical by label, so the page's dropdown
    is stable between renders. `subject_id` is carried when a catalog row
    matched; the stored key is always the normalized text.
    """
    choices: dict[str, dict] = {
        DEFAULT_SUBJECT: {"key": DEFAULT_SUBJECT, "label": DEFAULT_LABEL,
                          "subject_id": None},
    }
    try:
        assignments = _rows(supabase.table("teacher_assignments")
                            .select("subject_id").eq("teacher_id", teacher_id)
                            .execute())
        subject_ids = [a["subject_id"] for a in assignments if a.get("subject_id")]
        if subject_ids:
            subjects = _rows(supabase.table("subjects").select("id,name,code")
                             .in_("id", subject_ids).execute())
            for row in subjects:
                name = row.get("name") or row.get("code") or ""
                key = subject_key(name)
                if key != DEFAULT_SUBJECT:
                    choices.setdefault(key, {"key": key, "label": name,
                                             "subject_id": row.get("id")})
    except Exception:
        logger.exception("could not read subject assignments for %s", teacher_id)

    try:
        exams = _rows(supabase.table("exams").select("subject")
                      .eq("teacher_id", teacher_id).execute())
        for row in exams:
            name = row.get("subject") or ""
            key = subject_key(name)
            if key != DEFAULT_SUBJECT:
                choices.setdefault(key, {"key": key, "label": name,
                                         "subject_id": None})
    except Exception:
        logger.exception("could not read exam subjects for %s", teacher_id)

    ordered = [choices[DEFAULT_SUBJECT]]
    ordered += sorted((c for k, c in choices.items() if k != DEFAULT_SUBJECT),
                      key=lambda c: c["label"].lower())
    return ordered


def can_write_subject(supabase, teacher_id, key, choices=None) -> bool:
    """Whether this teacher teaches `key` — `default` is always theirs."""
    key = subject_key(key)
    if key == DEFAULT_SUBJECT:
        return True
    available = {c["key"] for c in (choices if choices is not None
                                    else subject_choices(supabase, teacher_id))}
    return key in available


def list_prompts(supabase, teacher_id) -> dict:
    """`{subject_key: row}` for this teacher's saved rubrics."""
    try:
        rows = _rows(supabase.table(TABLE).select("*")
                     .eq("teacher_id", teacher_id).execute())
    except Exception:
        logger.exception("could not read grading prompts for %s", teacher_id)
        return {}
    return {str(r.get("subject_key")): r for r in rows if r.get("subject_key")}


def _existing(supabase, teacher_id, key):
    rows = _rows(supabase.table(TABLE).select("*").eq("teacher_id", teacher_id)
                 .eq("subject_key", key).execute())
    return rows[0] if rows else None


def save_prompt(supabase, *, teacher_id, subject, prompt_text, label=None,
                updated_by=None) -> dict:
    """Create or update one subject's rubric, bumping its version.

    `validate_teacher_prompt` refuses an oversized prompt (rather than trimming
    words the teacher will believe are in force) and neutralises the answer
    delimiter, which a teacher has no legitimate reason to type.
    """
    key = subject_key(subject)
    text = validate_teacher_prompt(prompt_text)
    if not text:
        raise ValueError("prompt tidak boleh kosong")
    if not can_write_subject(supabase, teacher_id, key):
        raise PermissionError("Anda tidak mengajar mapel ini")

    choices = {c["key"]: c for c in subject_choices(supabase, teacher_id)}
    choice = choices.get(key) or {}
    payload = {
        "teacher_id": teacher_id,
        "subject_key": key,
        "subject_label": _clean_label(label or choice.get("label"), key),
        "subject_id": choice.get("subject_id"),
        "prompt_text": text,
        "updated_by": updated_by or teacher_id,
    }

    existing = _existing(supabase, teacher_id, key)
    if existing:
        payload["version"] = int(existing.get("version") or 1) + 1
        written = _rows(supabase.table(TABLE).update(payload)
                        .eq("id", existing["id"]).execute())
        return written[0] if written else {**existing, **payload}

    payload["version"] = 1
    written = _rows(supabase.table(TABLE).insert(payload).execute())
    return written[0] if written else payload


def delete_prompt(supabase, *, teacher_id, subject) -> bool:
    """Remove one subject's rubric. The default falls back to the built-in one."""
    key = subject_key(subject)
    if not _existing(supabase, teacher_id, key):
        return False
    supabase.table(TABLE).delete().eq("teacher_id", teacher_id) \
        .eq("subject_key", key).execute()
    return True


def sample_prompt() -> str:
    """The text the page offers as a starting point, not stored until saved."""
    return DEFAULT_TEACHER_PROMPT


def limits() -> dict:
    return {"max_chars": MAX_TEACHER_PROMPT}
