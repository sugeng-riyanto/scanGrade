"""Re-point a row that names two schools at the school its people belong to.

`school_integrity.cross_school_findings` finds the rows whose `school_id` disagrees
with the school of the people they point at — the fault behind a week of "the number
looks wrong" reports. Finding them was half the job. The other half is that the
operator then had a table of ids and a sentence telling them to go and fix it in SQL,
from a page that cannot do it; so the finding was found and left, which is the same
shape of failure as a check nobody opens.

This is the door. Two rules make it safe:

* **The target comes from the row, never from the request.** The request carries only
  *which* row to repair (its kind and id). The school it should hold is re-derived
  from that row's own `class_id`/`subject_id` on every apply, so a forged form cannot
  re-home a row into a school of the attacker's choosing — there is no field to forge.
* **An ambiguous row is refused, not guessed.** A `class_subjects` or
  `teacher_assignments` row points at *two* things. When the class and the subject
  belong to different schools there is no single right answer, and `two_schools` is
  the honest answer: re-pointing to one would satisfy one pointer and break the other.

What "its people actually belong to" means, concretely: for a pupil, the school of the
class they are a member of; for a class-subject map or a teacher assignment, the school
its class and its subject *agree* on. A pointer this read cannot see is `target_unknown`
— a guess is exactly what the sweep exists to avoid.

The write is guarded on the school the plan was made against (`eq("school_id", from)`),
so a repair racing a concurrent fix reports `row_changed` rather than clobbering it, and
every applied repair writes an audit record naming the actor, the row and both schools.
The reads it changes are invalidated, because a cached class-offering would otherwise
keep serving the old school for its TTL.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from app.services.audit_service import log_activity
from app.services.school_integrity import (ASSIGNMENT, CLASS_PUPIL, PAIR)

#: The table each finding kind repairs. The *kind* picks the table and the columns, so
#: a request can only ever name a row the sweep already knows how to read — never an
#: arbitrary table or column.
TABLE_FOR = {
    CLASS_PUPIL: "profiles",
    PAIR: "class_subjects",
    ASSIGNMENT: "teacher_assignments",
}

#: The columns the plan reads from each table. `profiles` has no `subject_id`, and
#: naming a column a table does not have makes PostgREST answer 400, not "absent".
COLUMNS_FOR = {
    CLASS_PUPIL: "id, class_id, school_id",
    PAIR: "id, class_id, subject_id, school_id",
    ASSIGNMENT: "id, class_id, subject_id, teacher_id, school_id",
}

#: Refusal keys, translated by the page. Fixed strings, never sentences: the language
#: lives in the browser, which the server never sees.
ROW_NOT_FOUND = "row_not_found"
TARGET_UNKNOWN = "target_unknown"
TWO_SCHOOLS = "two_schools"
ALREADY_CONSISTENT = "already_consistent"
UNKNOWN_KIND = "unknown_kind"
READ_FAILED = "read_failed"
WRITE_FAILED = "write_failed"
ROW_CHANGED = "row_changed"

#: The second door's refusals. `REASON_REQUIRED` is the whole point of it: a link a
#: human made disappear has to say why, or the audit record is "somebody clicked".
REASON_REQUIRED = "reason_required"
NOTHING_TO_QUARANTINE = "nothing_to_quarantine"

#: The shortest reason that is a reason. `x` is a keystroke, not a record.
MIN_REASON_LENGTH = 4

OK = "ok"
REPOINTED = "repointed"
QUARANTINED = "quarantined"

#: How each kind is *detached* when it can never be re-pointed, expressed the way the
#: app itself removes a link: a subject is taken off a class by closing the offering
#: (`is_active=false`, migration 049), an assignment is retired by `status='inactive'`
#: (migration 045), and the pupil is simply taken out of the wrong class (their
#: `class_id` is nullable by design, migration 002). Nothing here deletes a row, so a
#: mistaken quarantine is reversible and no paper loses its home. The *kind* picks the
#: column and value, so a request can never name one.
QUARANTINE_FOR = {
    CLASS_PUPIL: ("class_id", None),
    PAIR: ("is_active", False),
    ASSIGNMENT: ("status", "inactive"),
}

#: What the page is told, per reason. A key again, for the same reason the lock
#: gate's refusals are keys.
REASON_KEYS = {
    OK: "repair_ok",
    ROW_NOT_FOUND: "repair_row_not_found",
    TARGET_UNKNOWN: "repair_target_unknown",
    TWO_SCHOOLS: "repair_two_schools",
    ALREADY_CONSISTENT: "repair_already_consistent",
    UNKNOWN_KIND: "repair_unknown_kind",
    READ_FAILED: "repair_read_failed",
    WRITE_FAILED: "repair_write_failed",
    ROW_CHANGED: "repair_row_changed",
    REASON_REQUIRED: "repair_reason_required",
    NOTHING_TO_QUARANTINE: "repair_nothing_to_quarantine",
    QUARANTINED: "repair_quarantined",
}


def reason_key(reason: str) -> str:
    """The page's key for a refusal reason, never the reason itself."""
    return REASON_KEYS.get(reason or "", "repair_failed")


# ── the finding, read into the row it names ─────────────────────────────────

def finding_row_id(finding: dict) -> str | None:
    """The row id a finding names, whichever kind it is.

    One definition, because the page and the batch apply both need it and an OR-chain
    of id fields in two places is how one of them starts reading a field the other
    stopped writing.
    """
    finding = finding or {}
    return (finding.get("pair_id") or finding.get("assignment_id")
            or finding.get("pupil_id"))


def row_from_finding(finding: dict) -> dict:
    """The shape `plan()` reads, built from the finding's own pointer fields.

    `school_id` is the row's *current* school — `pair_school_id` etc — so a preview
    compares against the same value the write will be guarded on.
    """
    finding = finding or {}
    kind = finding.get("kind")
    row = {"id": finding_row_id(finding), "class_id": finding.get("class_id")}
    if kind == CLASS_PUPIL:
        row["school_id"] = finding.get("pupil_school_id")
    elif kind == PAIR:
        row["subject_id"] = finding.get("subject_id")
        row["school_id"] = finding.get("pair_school_id")
    elif kind == ASSIGNMENT:
        row["subject_id"] = finding.get("subject_id")
        row["school_id"] = finding.get("assignment_school_id")
    return row


# ── reads ───────────────────────────────────────────────────────────────────

def _rows_by_id(supabase, table, ids, columns) -> dict:
    """One read of one table by ids, as a map. A failed read is an empty map.

    Empty here means "this pointer could not be seen", which `plan()` turns into
    `target_unknown` — a refusal, never a guess. That is the difference from the
    sweep, which reports a failed read as `ok: False`; here there is nothing to
    report, only something not to do.
    """
    wanted = [str(i) for i in ids if i]
    if not wanted:
        return {}
    try:
        data = (supabase.table(table).select(columns)
                .in_("id", wanted).execute().data) or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not read %s: %s", table, exc)
        return {}
    return {str(r.get("id")): r for r in data if r.get("id")}


def _pointer_ids(kind, row) -> tuple[list, list]:
    """The class and subject ids a row points at, for the reads its plan needs."""
    row = row or {}
    classes = [row.get("class_id")]
    subjects = [] if kind == CLASS_PUPIL else [row.get("subject_id")]
    return classes, subjects


def _maps(supabase, pairs) -> tuple[dict, dict, dict]:
    """The class/subject/school lookups a set of rows needs, read once.

    `pairs` is `(kind, row)` per row, because a page can list findings of *different*
    kinds at once and each row's pointers depend on its own kind.
    """
    class_ids, subject_ids = [], []
    for kind, row in pairs:
        c, s = _pointer_ids(kind, row)
        class_ids += c
        subject_ids += s
    classes = _rows_by_id(supabase, "classes", class_ids, "id, name, school_id")
    subjects = _rows_by_id(supabase, "subjects", subject_ids, "id, name, school_id")
    # The rows' *own* current school is named too, so the preview can say where a row
    # is moving *from* as well as to — a name only for the destination reads as a
    # silent change of the other one.
    school_ids = [v.get("school_id") for v in list(classes.values()) + list(subjects.values())]
    school_ids += [(row or {}).get("school_id") for _kind, row in pairs]
    schools = _rows_by_id(supabase, "schools", school_ids, "id, name")
    return classes, subjects, schools


# ── the plan, a pure function of the row ────────────────────────────────────

def plan(kind: str, row: dict | None, classes: dict, subjects: dict,
         schools: dict | None = None) -> dict:
    """`_plan_raw`, with the reason also named as a page key.

    The page must translate a refusal, and the browser owns the language — so the
    plan carries a *key* (`reason_key`) beside the raw reason, exactly as the lock
    gate's refusals do.
    """
    out = _plan_raw(kind, row, classes, subjects, schools)
    out["reason_key"] = reason_key(out.get("reason") or "")
    return out


def _plan_raw(kind: str, row: dict | None, classes: dict, subjects: dict,
              schools: dict | None = None) -> dict:
    """The proposed re-point for one row, or the reason there is none.

    Pure: no reads, no writes, no clock. The caller supplies the class/subject maps
    (and, for a page, the school names), so the rule can be read and tested without a
    database — the same shape the lock gate's `decide()` uses.
    """
    table = TABLE_FOR.get(kind)
    base = {"kind": kind, "table": table, "row_id": (row or {}).get("id")}
    if table is None:
        return {**base, "ok": False, "reason": UNKNOWN_KIND, "action": None}
    if not row or not row.get("id"):
        return {**base, "ok": False, "reason": ROW_NOT_FOUND, "action": None}

    frm = row.get("school_id")
    klass = (classes or {}).get(str(row.get("class_id") or ""))
    to = (klass or {}).get("school_id")

    if kind != CLASS_PUPIL:
        # Two pointers, so both must be seen and must agree — otherwise there is no
        # single school "its people belong to".
        subject = (subjects or {}).get(str(row.get("subject_id") or ""))
        subject_school = (subject or {}).get("school_id")
        if to is None or subject_school is None:
            return {**base, "ok": False, "reason": TARGET_UNKNOWN,
                    "from_school_id": frm, "action": None}
        if str(to) != str(subject_school):
            return {**base, "ok": False, "reason": TWO_SCHOOLS,
                    "from_school_id": frm, "action": None}
    elif to is None:
        return {**base, "ok": False, "reason": TARGET_UNKNOWN,
                "from_school_id": frm, "action": None}

    if str(frm) == str(to):
        return {**base, "ok": False, "reason": ALREADY_CONSISTENT,
                "from_school_id": frm, "to_school_id": to, "action": None}

    names = schools or {}
    return {
        **base, "ok": True, "reason": OK, "action": REPOINTED,
        "from_school_id": frm,
        "to_school_id": to,
        "from_school_name": (names.get(str(frm)) or {}).get("name"),
        "to_school_name": (names.get(str(to)) or {}).get("name"),
    }


# ── the preview: read-only, and it can name where the row would move ────────

def preview(supabase, findings) -> list[dict]:
    """A plan per finding, with both school names, and **no writes at all**.

    This is what the operator reads before pressing anything: the page lists rows, so
    the page must also say what repairing them would do — including which ones it will
    refuse and why, so a refusal is a sentence on the page rather than a surprise.
    """
    findings = list(findings or [])
    if not findings:
        return []
    # One read per table for the whole page: a preview that read per finding would be
    # N+1 on a page whose entire job is to be fast to glance at.
    classes, subjects, schools = _maps(
        supabase, [(f.get("kind"), row_from_finding(f)) for f in findings])
    return [plan(f.get("kind"), row_from_finding(f), classes, subjects, schools)
            for f in findings]


# ── the apply ───────────────────────────────────────────────────────────────

def apply(supabase, kind: str, row_id: str, actor_id) -> dict:
    """Re-point one row at the school its people belong to — or refuse.

    The row is re-read here rather than trusted from the request: only its own
    pointers decide the school, and the write is guarded on the value the plan was
    made against, so a repair that races a concurrent fix reports `row_changed`
    instead of overwriting it.
    """
    table = TABLE_FOR.get(kind)
    if table is None:
        return {"ok": False, "reason": UNKNOWN_KIND, "kind": kind,
                "row_id": row_id, "action": None}

    columns = COLUMNS_FOR[kind]
    try:
        rows = (supabase.table(table).select(columns)
                .eq("id", row_id).execute().data) or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not read %s %s: %s", table, row_id, exc)
        return {"ok": False, "reason": READ_FAILED, "kind": kind,
                "table": table, "row_id": row_id, "action": None}

    row = rows[0] if rows else None
    classes, subjects, schools = _maps(supabase, [(kind, row)])
    proposed = plan(kind, row, classes, subjects, schools)
    if not proposed.get("ok"):
        return proposed

    try:
        query = (supabase.table(table).update({"school_id": proposed["to_school_id"]})
                 .eq("id", row_id))
        # Optimistic: only write if the row still holds the school the plan was made
        # against. A row that is already NULL has nothing to compare, so the id is the
        # guard — matching the sweep, which never reports a NULL as a mismatch.
        if proposed.get("from_school_id") is not None:
            query = query.eq("school_id", proposed["from_school_id"])
        written = query.execute().data or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not fix %s %s: %s", table, row_id, exc)
        return {**proposed, "ok": False, "reason": WRITE_FAILED, "action": None}

    if not written:
        # Somebody fixed it between the plan and the write. Not an error, and not a
        # repair: report it so the operator can reload rather than assume it landed.
        return {**proposed, "ok": False, "reason": ROW_CHANGED, "action": None}

    _audit(table, row_id, proposed, actor_id)
    _invalidate(kind, row, proposed)
    return {**proposed, "ok": True, "reason": OK, "action": REPOINTED}


def _audit(table: str, row_id, proposed: dict, actor_id) -> None:
    """One record per applied repair: a human re-homed a row, and must be answerable."""
    try:
        log_activity(
            "update", table, row_id,
            old_data={"school_id": proposed.get("from_school_id")},
            new_data={"school_id": proposed.get("to_school_id"),
                      "repair": "cross_school_repoint"},
            user_id=actor_id)
    except Exception as exc:                              # noqa: BLE001 — the write stands
        logger.warning("cross_school_repair: audit failed for %s %s: %s",
                       table, row_id, exc)


def _invalidate(kind: str, row: dict, proposed: dict) -> None:
    """Drop the cached reads this row fed, best-effort.

    A class-offering is cached per class for its TTL, so without this the page that
    was showing the wrong school would keep showing it for a minute after the fix.
    """
    from app.utils import req_cache

    class_id = (row or {}).get("class_id")
    try:
        if kind in (PAIR, ASSIGNMENT) and class_id:
            req_cache.invalidate_class_subjects(class_id)
        if kind == ASSIGNMENT and (row or {}).get("teacher_id"):
            for school_id in (proposed.get("from_school_id"),
                              proposed.get("to_school_id")):
                if school_id:
                    req_cache.invalidate_teacher_assignments(
                        row.get("teacher_id"), school_id)
    except Exception as exc:                              # noqa: BLE001 — a cache miss is not a failure
        logger.warning("cross_school_repair: could not invalidate caches: %s", exc)


# ── the second door: quarantine a row that can never be re-pointed ──────────

def quarantine(supabase, kind: str, row_id, reason, actor_id) -> dict:
    """Detach a cross-school row the re-point door refused — with a mandatory reason.

    `apply` needs a single school "its people belong to". A `two_schools` row has
    none, and a `target_unknown` row cannot even be read, so both were left to SQL.
    This is the door for those: it does not guess a school, it removes the link the
    way the app removes one (`QUARANTINE_FOR`), so the cross-school read stops and
    nothing is destroyed.

    The rules that make it safe are the repair door's, plus one:

    * **the column and value are the kind's**, never the request's — the form names
      only *which* row;
    * **the reason is mandatory** (>= `MIN_REASON_LENGTH` after trimming): the record
      of why a link disappeared is the only thing that makes this answerable later;
    * a row that is merely *already consistent* is refused (`nothing_to_quarantine`),
      so this never becomes a delete button for healthy rows;
    * the write is guarded on the school the read saw, so a row already fixed by
      somebody else reports `row_changed` rather than being clobbered.
    """
    table = TABLE_FOR.get(kind)
    base = {"kind": kind, "table": table, "row_id": row_id}
    if table is None:
        return {**base, "ok": False, "reason": UNKNOWN_KIND, "action": None}

    text = (reason or "").strip()
    if len(text) < MIN_REASON_LENGTH:
        return {**base, "ok": False, "reason": REASON_REQUIRED, "action": None}

    try:
        rows = (supabase.table(table).select(COLUMNS_FOR[kind])
                .eq("id", row_id).execute().data) or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not read %s %s: %s", table, row_id, exc)
        return {**base, "ok": False, "reason": READ_FAILED, "action": None}

    row = rows[0] if rows else None
    if not row or not row.get("id"):
        return {**base, "ok": False, "reason": ROW_NOT_FOUND, "action": None}

    classes, subjects, schools = _maps(supabase, [(kind, row)])
    proposed = plan(kind, row, classes, subjects, schools)
    # A healthy row has nothing to detach. Closing it would be data loss dressed up
    # as a repair, so the door says no rather than turning into a delete button.
    if proposed.get("reason") == ALREADY_CONSISTENT:
        return {**proposed, "ok": False, "reason": NOTHING_TO_QUARANTINE, "action": None}

    column, value = QUARANTINE_FOR[kind]
    try:
        query = supabase.table(table).update({column: value}).eq("id", row_id)
        if row.get("school_id") is not None:
            query = query.eq("school_id", row.get("school_id"))
        written = query.execute().data or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not quarantine %s %s: %s",
                       table, row_id, exc)
        return {**base, "ok": False, "reason": WRITE_FAILED, "action": None}

    if not written:
        return {**base, "ok": False, "reason": ROW_CHANGED, "action": None}

    _audit_quarantine(table, row_id, proposed, text, actor_id)
    _invalidate(kind, row, proposed)
    return {**proposed, "ok": True, "reason": OK, "action": QUARANTINED,
            "quarantine_column": column}


def _audit_quarantine(table: str, row_id, proposed: dict, reason: str, actor_id) -> None:
    """One record per quarantine: which row, who, and — the whole point — why.

    The reason is stored verbatim. It is the only thing that tells a later reader
    whether a link was closed because it was mis-wired or because somebody tidied
    the wrong table, which is exactly what an audit trail is for.
    """
    try:
        log_activity(
            "update", table, row_id,
            old_data={"school_id": proposed.get("from_school_id")},
            new_data={"quarantine": "cross_school", "kind": proposed.get("kind"),
                      "reason": reason},
            user_id=actor_id)
    except Exception as exc:                              # noqa: BLE001 — the write stands
        logger.warning("cross_school_repair: quarantine audit failed for %s %s: %s",
                       table, row_id, exc)


__all__ = [
    "ASSIGNMENT", "CLASS_PUPIL", "PAIR", "TABLE_FOR", "COLUMNS_FOR",
    "QUARANTINE_FOR", "MIN_REASON_LENGTH",
    "ROW_NOT_FOUND", "TARGET_UNKNOWN", "TWO_SCHOOLS", "ALREADY_CONSISTENT",
    "UNKNOWN_KIND", "READ_FAILED", "WRITE_FAILED", "ROW_CHANGED",
    "REASON_REQUIRED", "NOTHING_TO_QUARANTINE",
    "OK", "REPOINTED", "QUARANTINED",
    "REASON_KEYS", "reason_key", "finding_row_id", "row_from_finding",
    "plan", "preview", "apply", "quarantine",
]
