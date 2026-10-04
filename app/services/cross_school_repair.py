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
#: The quarantined column itself is read too, so the audit record can name the value a
#: quarantine *replaced* — which is what makes a restore a restore rather than a guess.
COLUMNS_FOR = {
    CLASS_PUPIL: "id, class_id, school_id",
    PAIR: "id, class_id, subject_id, school_id, is_active",
    ASSIGNMENT: "id, class_id, subject_id, teacher_id, school_id, status",
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

#: The inverse door's refusals. It only ever undoes a quarantine it can *read back*,
#: so a row with no record, a record missing the value to restore, and a row already
#: back on are three separate, honest answers rather than one "could not".
NOTHING_TO_RESTORE = "nothing_to_restore"
NO_PRIOR_VALUE = "no_prior_value"
ALREADY_RESTORED = "already_restored"

#: The shortest reason that is a reason. `x` is a keystroke, not a record.
MIN_REASON_LENGTH = 4

OK = "ok"
REPOINTED = "repointed"
QUARANTINED = "quarantined"
RESTORED = "restored"

#: How many quarantined rows the operator's list carries. A page that lists every
#: quarantine the box ever made is a log, not a door.
MAX_QUARANTINES = 25

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

#: The inverse of `QUARANTINE_FOR`: the column and the value that turns a link back
#: on. A pupil needs the class they were detached from, which is not a constant — the
#: audit record carries it, so `NEEDS_PRIOR` marks the value that comes from there.
NEEDS_PRIOR = "__prior__"
RESTORE_FOR = {
    CLASS_PUPIL: ("class_id", NEEDS_PRIOR),
    PAIR: ("is_active", True),
    ASSIGNMENT: ("status", "active"),
}

#: What a row looks like *while quarantined*. The restore write is guarded on this, so
#: a row somebody already turned back on reports `already_restored` instead of being
#: clobbered — the same discipline the repair door's school guard uses.
QUARANTINED_AS = {
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
    NOTHING_TO_RESTORE: "repair_nothing_to_restore",
    NO_PRIOR_VALUE: "repair_no_prior_value",
    ALREADY_RESTORED: "repair_already_restored",
    RESTORED: "repair_restored",
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

    _audit_quarantine(table, row_id, row, proposed, text, actor_id)
    _invalidate(kind, row, proposed)
    return {**proposed, "ok": True, "reason": OK, "action": QUARANTINED,
            "quarantine_column": column}


def _audit_quarantine(table: str, row_id, row: dict, proposed: dict, reason: str,
                      actor_id) -> None:
    """One record per quarantine: which row, who, why — and what it replaced.

    The reason is stored verbatim. It is the only thing that tells a later reader
    whether a link was closed because it was mis-wired or because somebody tidied
    the wrong table, which is exactly what an audit trail is for. The **prior value of
    the column the quarantine changed** is stored beside it, because that is the only
    thing that can turn the link back on later — a `class_id` a human made disappear
    cannot be recovered from anywhere else.
    """
    column = QUARANTINE_FOR.get(proposed.get("kind"), (None, None))[0]
    old_data = {"school_id": proposed.get("from_school_id")}
    if column:
        old_data[column] = (row or {}).get(column)
    try:
        log_activity(
            "update", table, row_id,
            old_data=old_data,
            new_data={"quarantine": "cross_school", "kind": proposed.get("kind"),
                      "reason": reason,
                      "class_id": (row or {}).get("class_id"),
                      "teacher_id": (row or {}).get("teacher_id")},
            user_id=actor_id)
    except Exception as exc:                              # noqa: BLE001 — the write stands
        logger.warning("cross_school_repair: quarantine audit failed for %s %s: %s",
                       table, row_id, exc)


# ── the inverse door: turn a quarantined link back on, from its own record ──

def quarantine_records(logs) -> list[dict]:
    """The cross-school quarantines in a batch of audit rows, ready to be listed or
    restored: which row, which kind, the reason, and the value the quarantine replaced.

    Pure on purpose. Both the page that lists them and the door that restores them read
    this one shape, so the list can never show a row the door cannot find.
    """
    out = []
    for row in logs or []:
        new = row.get("new_data") or {}
        if new.get("quarantine") != "cross_school":
            continue
        kind = new.get("kind")
        column = RESTORE_FOR.get(kind, (None, None))[0]
        old = row.get("old_data") or {}
        out.append({
            "kind": kind,
            "row_id": row.get("entity_id"),
            "table": row.get("entity_type"),
            "reason": new.get("reason"),
            "actor_id": row.get("user_id"),
            "at": row.get("created_at"),
            "column": column,
            "prior": old.get(column) if column else None,
            "class_id": new.get("class_id"),
            "teacher_id": new.get("teacher_id"),
            "school_id": old.get("school_id"),
        })
    return out


def restore_plan(record: dict | None) -> dict:
    """The write that turns a quarantined link back on, or the reason there is none.

    Pure: no reads, no writes, no clock. The caller supplies the record, so the rule
    can be read and tested without a database — the same shape `plan()` uses.
    """
    rec = record or {}
    kind = rec.get("kind")
    base = {"kind": kind, "row_id": rec.get("row_id"), "table": rec.get("table")}
    if kind not in RESTORE_FOR:
        return {**base, "ok": False, "reason": UNKNOWN_KIND, "action": None}

    column, value = RESTORE_FOR[kind]
    # A no-op quarantine is not undone: if the link was already detached, "restoring"
    # it would turn on a link the operator never turned off.
    _q_column, quarantined_value = QUARANTINED_AS[kind]
    if rec.get("prior") is not None and str(rec.get("prior")) == str(quarantined_value):
        return {**base, "ok": False, "reason": NOTHING_TO_RESTORE, "action": None}

    if value == NEEDS_PRIOR:
        value = rec.get("prior")
        if value in (None, ""):
            return {**base, "ok": False, "reason": NO_PRIOR_VALUE, "action": None}

    return {**base, "ok": True, "reason": OK, "action": RESTORED,
            "column": column, "value": value, "reason_text": rec.get("reason")}


def recent_quarantines(supabase, limit: int = MAX_QUARANTINES) -> list[dict]:
    """The quarantined rows an operator can bring back, newest first, one per row.

    The findings list cannot show them: a quarantined row is no longer cross-school
    (it is detached), so it drops out of the sweep. The audit trail is where they live,
    and this reads it — bounded to the three tables the door ever touches, so it does
    not pull the whole log.
    """
    try:
        logs = (supabase.table("audit_logs")
                .select("entity_type, entity_id, old_data, new_data, user_id, created_at")
                .in_("entity_type", list(TABLE_FOR.values()))
                .order("created_at", desc=True)
                .limit(200).execute().data) or []
    except Exception as exc:                              # noqa: BLE001 — a page, not a gate
        logger.warning("cross_school_repair: could not read quarantines: %s", exc)
        return []

    seen, out = set(), []
    for rec in quarantine_records(logs):
        key = (rec.get("table"), str(rec.get("row_id")))
        if not rec.get("row_id") or key in seen:
            continue
        seen.add(key)
        out.append(rec)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _latest_record(supabase, table: str, row_id):
    """The most recent quarantine of one row, read from the audit trail."""
    logs = (supabase.table("audit_logs")
            .select("entity_type, entity_id, old_data, new_data, user_id, created_at")
            .eq("entity_type", table).eq("entity_id", str(row_id))
            .order("created_at", desc=True).limit(20).execute().data) or []
    records = quarantine_records(logs)
    return records[0] if records else None


def restore(supabase, kind: str, row_id, actor_id) -> dict:
    """Turn a quarantined link back on — from its own record, never from the request.

    The caller names only *which* row. The column and the value come from the quarantine
    record in the audit trail, so a forged form cannot turn on a link, a class, or a
    column of its choosing: the door only ever replays a detach that really happened.
    The write is guarded on the quarantined state, so a row already back on reports
    `already_restored` rather than being clobbered.
    """
    table = TABLE_FOR.get(kind)
    base = {"kind": kind, "table": table, "row_id": row_id}
    if table is None:
        return {**base, "ok": False, "reason": UNKNOWN_KIND, "action": None}

    try:
        record = _latest_record(supabase, table, row_id)
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not read quarantine record for %s %s: %s",
                       table, row_id, exc)
        return {**base, "ok": False, "reason": READ_FAILED, "action": None}

    if not record:
        return {**base, "ok": False, "reason": NOTHING_TO_RESTORE, "action": None}

    proposed = restore_plan(record)
    if not proposed.get("ok"):
        return {**base, **proposed}

    column = proposed["column"]
    _q_column, quarantined_value = QUARANTINED_AS[kind]
    try:
        query = supabase.table(table).update({column: proposed["value"]}).eq("id", row_id)
        if quarantined_value is None:
            query = query.is_(column, "null")
        else:
            query = query.eq(column, quarantined_value)
        written = query.execute().data or []
    except Exception as exc:                              # noqa: BLE001
        logger.warning("cross_school_repair: could not restore %s %s: %s", table, row_id, exc)
        return {**base, "ok": False, "reason": WRITE_FAILED, "action": None}

    if not written:
        # Somebody already turned it back on (or changed it). Not an error, not a
        # restore: report it so the operator reloads rather than assumes it landed.
        return {**base, "ok": False, "reason": ALREADY_RESTORED, "action": None}

    _audit_restore(table, row_id, record, proposed, actor_id)
    _invalidate_restore(kind, record, proposed)
    return {**base, "ok": True, "reason": OK, "action": RESTORED, "column": column}


def _audit_restore(table: str, row_id, record: dict, proposed: dict, actor_id) -> None:
    """One record per restore: the quarantine undone, by whom, and why it had been made."""
    try:
        log_activity(
            "update", table, row_id,
            old_data={proposed["column"]: QUARANTINED_AS.get(record.get("kind"), (None, None))[1]},
            new_data={"restore": "cross_school", "kind": record.get("kind"),
                      "restored_column": proposed["column"],
                      "quarantine_reason": record.get("reason")},
            user_id=actor_id)
    except Exception as exc:                              # noqa: BLE001 — the write stands
        logger.warning("cross_school_repair: restore audit failed for %s %s: %s",
                       table, row_id, exc)


def _invalidate_restore(kind: str, record: dict, proposed: dict) -> None:
    """Drop the cached reads a restored row feeds — the same ones the quarantine dropped."""
    row = {"class_id": record.get("class_id"), "teacher_id": record.get("teacher_id")}
    school_id = record.get("school_id")
    _invalidate(kind, row, {"from_school_id": school_id, "to_school_id": school_id})


__all__ = [
    "ASSIGNMENT", "CLASS_PUPIL", "PAIR", "TABLE_FOR", "COLUMNS_FOR",
    "QUARANTINE_FOR", "RESTORE_FOR", "QUARANTINED_AS", "MAX_QUARANTINES",
    "MIN_REASON_LENGTH",
    "ROW_NOT_FOUND", "TARGET_UNKNOWN", "TWO_SCHOOLS", "ALREADY_CONSISTENT",
    "UNKNOWN_KIND", "READ_FAILED", "WRITE_FAILED", "ROW_CHANGED",
    "REASON_REQUIRED", "NOTHING_TO_QUARANTINE",
    "NOTHING_TO_RESTORE", "NO_PRIOR_VALUE", "ALREADY_RESTORED",
    "OK", "REPOINTED", "QUARANTINED", "RESTORED",
    "quarantine_records", "restore_plan", "recent_quarantines", "restore",
    "REASON_KEYS", "reason_key", "finding_row_id", "row_from_finding",
    "plan", "preview", "apply", "quarantine",
]
