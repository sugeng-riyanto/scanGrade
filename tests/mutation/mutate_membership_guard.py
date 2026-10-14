"""Mutation check for the membership guard — who teaches where, and to whom.

The membership table (migration 044) is the one place in this app where a person
belongs to more than one school, and the guard around it is a set of *narrowings*
rather than a permission bit. That makes it exactly the wrong kind of code to trust
on green tests: every rule here is a clause whose removal keeps the happy path
working and quietly widens a school's reach, and none of the widenings raises — a
paper filed under the wrong NPSN, a closed membership that still opens a school, a
pupil whose school came out of a table nobody meant to consult.

So each entry below breaks one clause and demands a red suite. Two families:

* **the powerful one is completeness** — `resolve_from_memberships` is five ordered
  clauses, and three of them are "keep the answer you already had" (a role that
  cannot be cross-school, a read that failed, a home school with no row). Dropping
  any of them turns a teacher out of their own school or lets a client pick one;
* **the write side is the reason `filing_school` is separate** — `exams.school_id`
  scopes a paper's weighting, its marking and its pupils, so a write that cannot
  confirm its school refuses instead of guessing. A mutation that turns the refusal
  into a fallback is a paper the wrong school grades.

Guards run together on purpose: `test_school_membership.py` owns the pure resolvers,
`test_member_teaches_here.py` and `teacher_assignments` own "does this person teach
here" through every door, and `test_exam_school_authority.py` owns the write side
and the route that calls it. A mutation that satisfies one by breaking another is
not a passing mutation.

Run:  python deploy/mutation_check.py --run mutate_membership_guard     (~7 minutes)
      python deploy/mutation_check.py --check                          (anchors only)
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = [
    "tests/unit/test_school_membership.py",
    "tests/unit/test_member_teaches_here.py",
    "tests/unit/test_membership_closure.py",
    "tests/unit/test_membership_single_writer.py",
    "tests/unit/test_membership_request_path.py",
    "tests/unit/test_exam_school_authority.py",
]
SERVICE = "app/services/school_membership.py"
ASSIGNMENTS = "app/services/teacher_assignments.py"

MUTATIONS = [
    # ── who the membership table is allowed to speak for at all ─────────────
    ("a pupil and a school admin start consulting the membership table",
     SERVICE,
     "    return role in MEMBERSHIP_ROLES",
     "    return True"),

    # ── the five narrowings of the active school ────────────────────────────
    ("an unreadable membership read takes a school away",
     SERVICE,
     "    if rows is None:\n        return home_school_id",
     "    if rows is None:\n        return None"),
    ("the school chosen in this session stops being re-verified",
     SERVICE,
     "    if chosen_school_id and str(chosen_school_id) in active:",
     "    if chosen_school_id:"),
    ("a home school with no row at all stops counting as the home school",
     SERVICE,
     "    if home and (home in active or home not in known):",
     "    if home and home in active:"),
    ("a school with no active row is offered as the fallback",
     SERVICE,
     "    return sorted(active)[0] if active else None",
     "    return sorted(known)[0] if known else None"),
    ("a closed row still answers \"is this person an active member\"",
     SERVICE,
     '                .eq("status", STATUS_ACTIVE)\n'
     "                .limit(1).execute().data or [])",
     '                .limit(1).execute().data or [])'),
    ("the active-membership read stops naming \"active\"",
     SERVICE,
     '        if status:\n            query = query.eq("status", status)',
     '        if False:\n            query = query.eq("status", status)'),
    ("a database with no membership table is read as an outage",
     SERVICE,
     "        if _table_is_missing(exc):",
     "        if False:"),

    # ── the session's own school, kept honest on every request ──────────────
    ("an outage clears the school the session had chosen",
     SERVICE,
     "    if chosen and rows is not None and not _still_active(rows, chosen):",
     "    if chosen and not _still_active(rows, chosen):"),
    ("a chosen school survives a row that is no longer active",
     SERVICE,
     '    return any(str(r.get("school_id")) == wanted\n'
     '               and str(r.get("status") or "") == STATUS_ACTIVE for r in rows)',
     '    return any(str(r.get("school_id")) == wanted for r in rows)'),

    # ── the write side: a refusal is not a fallback ─────────────────────────
    ("a write that cannot read the rows falls back to the home school",
     SERVICE,
     "    if rows is None:\n        return None, FILING_UNVERIFIED",
     "    if rows is None:\n        return home_school_id, None"),
    ("a single-school role may name another school after all",
     SERVICE,
     "        if wanted and wanted != school:\n"
     "            return None, FILING_OTHER_SCHOOL\n"
     "        return school, None",
     "        return school, None"),
    ("a cross-school write is filed under a school the rows do not grant",
     SERVICE,
     '    if str(granted or "") != school:',
     "    if False:"),

    # ── the predicate the school's own doors ask ────────────────────────────
    ("a school's own account stops teaching at that school",
     ASSIGNMENTS,
     "    if rows:\n        return True",
     "    if rows:\n        return False"),
    ("inviting a member already on the books conflicts with the row instead",
     SERVICE,
     '    }, on_conflict="user_id,school_id").execute())',
     "    }).execute())"),
]

if __name__ == "__main__":
    sys.exit(main("mutate_membership_guard", GUARDS, MUTATIONS))
