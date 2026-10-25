"""The four identities and the one paper a ScanMeet staging run is measured on.

Why this tool exists
--------------------
`deploy/scanmeet_media_gate.py` takes twelve readings and takes *none* of them
without four accounts and a paper: a teacher who owns it, two pupils in one
school assigned to it, and a pupil of another school. It refuses with exit 2 and
names what it could not measure — which is honest, and which is why a staging
run used to end in "could not measure" no matter how well the code worked. The
runbook told a person to create those five rows by hand, in a dashboard, with no
statement of which fields matter; this module is that instruction, executable.

What it writes, and nothing else
--------------------------------
* two schools (`ZZ SCANMEET STAGING …`, marked so they are obvious and easy to
  remove), one class in each, one subject in the first;
* a teacher in school A who **owns the exam** — `can_manage_exam` answers from
  `exams.teacher_id`, so an exam owned by nobody is one no teacher can open;
* two pupils of school A in that class, and one pupil of school B. The pupils
  are in the *same* class on purpose: the paper has to be sittable by both, and
  the isolation row is about two accounts in one room, not two schools;
* one published exam with no window (open on every reading), assigned to that
  class, `scan_live_enabled` **on**.

Every account gets `must_change_password` cleared. `account_creation` sets that
flag (right for a pupil, wrong for an account whose whole purpose is to be
signed in by a script) and with it set every login is bounced to
`/auth/change-password`, the driver never reaches a room, and twelve rows turn
into twelve `SKIP`s. `provision_loadtest.py` documents the same trap.

Idempotent, and repairing rather than inserting
-----------------------------------------------
A second run finds what the first made and repairs it: the switch back on, the
assignment back to the class, the password reset to this run's value. "The rows
exist" and "the paper can be sat" are different claims and only the second keeps
a gate honest. The rows are found by their own markers (school NPSN, profile
full name, exam title), never by email — `profiles` carries no address.

Fail closed, twice
------------------
It refuses a project that is not an isolated staging one: the staging env's
`SUPABASE_URL` ref must differ from the production ref read out of
`deploy/supabase_region.py`, and the same placeholder rule the staging gate
applies to a copied template applies here. A fixture written into the
production project is the single worst thing this file could do — it creates
accounts and an exam in the database pupils are sitting.

The password is chosen by the run (`secrets.token_urlsafe`), printed once with
the four `email:password` arguments the gate wants, and stored nowhere: an
address and a password committed to this repository would be a credential in
git, and `tests/unit/test_no_committed_secrets.py` is right to refuse one.

Dry by default
--------------
`--write` is the flag that touches the database. Everything else prints the plan
and the identities, because a seeder that creates accounts by accident is a
seeder nobody runs a second time.

Usage (on the staging host, in the staging checkout)::

    python -m deploy.scanmeet_staging_fixture --env ~/.config/scanmeet/staging.env
    python -m deploy.scanmeet_staging_fixture --env ~/.config/scanmeet/staging.env --write

Exit codes follow the other gates: 0 provisioned (or planned) · 1 refused or a
write failed · 2 could not measure (no env file, no `supabase` package).
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from deploy.livekit_preflight import PLACEHOLDERS, parse_env

#: Appended to every school, class, account and exam this module creates.
MARK = "ZZ SCANMEET STAGING"

SCHOOL_A = {"name": f"{MARK} - sekolah uji", "npsn": "99990021"}
SCHOOL_B = {"name": f"{MARK} - sekolah lain", "npsn": "99990022"}
CLASS_NAME = "STG-1"
SUBJECT = "Matematika"
EXAM_TITLE = "[scanmeet-staging] Ujian Live"

#: The five rows the twelve readings need. `key` is the gate's argument name, so
#: the printed lines can be pasted straight into the command.
IDENTITIES = (
    ("teacher", "guru@scanmeet.invalid", "guru",
     f"{MARK} Guru", "a", ""),
    ("student", "murid1@scanmeet.invalid", "murid",
     f"{MARK} Murid 1", "a", "9999002101"),
    ("student2", "murid2@scanmeet.invalid", "murid",
     f"{MARK} Murid 2", "a", "9999002102"),
    ("outsider", "murid-lain@scanmeet.invalid", "murid",
     f"{MARK} Murid Sekolah Lain", "b", "9999002201"),
)


@dataclass(frozen=True)
class Identity:
    key: str
    email: str
    role: str
    full_name: str
    school: str          # "a" or "b"
    nisn: str = ""

    def as_gate_argument(self, password: str) -> str:
        return f"{self.email}:{password}"


IDENTITY_ROWS = tuple(Identity(*row) for row in IDENTITIES)


def ref_of(url: str) -> str:
    """The project ref in `https://<ref>.supabase.co` — empty when it is not one."""
    host = (url or "").strip().split("//")[-1].split("/")[0].split(":")[0].lower()
    if not host.endswith(".supabase.co"):
        return ""
    return host[: -len(".supabase.co")]


def problems(settings: dict[str, str], production_ref: str) -> list[str]:
    """What stops this fixture before it reads a single row."""
    found = []
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if not (settings.get(key) or "").strip():
            found.append(f"{key} is required to provision staging accounts")
    raw = (settings.get("SUPABASE_URL") or "").strip()
    if any(word in raw.lower() for word in PLACEHOLDERS):
        found.append("SUPABASE_URL is still a placeholder")
    ref = ref_of(raw)
    if raw and not ref:
        found.append("SUPABASE_URL must be an https project URL (<ref>.supabase.co)")
    elif ref and ref == production_ref:
        found.append("SUPABASE_URL is the production project — refusing to write "
                     "synthetic accounts and an exam into it")
    # The service key decides which project is actually written to; a URL that
    # names staging beside a key that belongs to production is the mistake this
    # check cannot make on its own, so it is named in the report instead of
    # assumed away.
    return found


def _one(query):
    rows = query.execute().data or []
    return rows[0] if rows else None


def _planned(kind: str, name: str) -> str:
    """A stand-in id for a dry run.

    Without one, every row that depends on the last would be skipped and the
    plan would describe a school and nothing inside it. Nothing is ever written
    under these names — they exist so the rest of the plan can be printed.
    """
    return f"planned:{kind}:{name}"


def _is_planned(value) -> bool:
    """Whether this is a stand-in a dry run made, not an id the database has.

    It matters because a stand-in is not a uuid, and a query filtered on one is
    refused outright: `school_id=eq.planned:school:99990021` comes back from
    Postgres as `22P02 invalid input syntax for type uuid`, which aborted the
    whole plan on its second line. The plan must not ask the database about a
    row the plan itself invented.
    """
    return isinstance(value, str) and value.startswith("planned:")


def _school(db, spec: dict, write: bool, say) -> str | None:
    row = _one(db.table("schools").select("id").eq("npsn", spec["npsn"]).limit(1))
    if row:
        return row["id"]
    if not write:
        say(f"   would create school {spec['name']}")
        return _planned("school", spec["npsn"])
    created = db.table("schools").insert({
        "name": spec["name"], "npsn": spec["npsn"], "status": "active",
        "tz_offset": 7,
    }).execute().data or []
    return created[0]["id"] if created else None


def _class(db, school_id: str, name: str, write: bool, say) -> str | None:
    if _is_planned(school_id):
        say(f"   would create class {name}")
        return _planned("class", name)
    row = _one(db.table("classes").select("id").eq("school_id", school_id)
               .eq("name", name).limit(1))
    if row:
        return row["id"]
    if not write:
        say(f"   would create class {name}")
        return _planned("class", name)
    created = db.table("classes").insert({
        "name": name, "school_id": school_id, "grade_level": "7",
    }).execute().data or []
    return created[0]["id"] if created else None


def _subject(db, school_id: str, write: bool, say) -> str | None:
    if _is_planned(school_id):
        say(f"   would create subject {SUBJECT}")
        return _planned("subject", SUBJECT)
    row = _one(db.table("subjects").select("id").eq("school_id", school_id)
               .eq("name", SUBJECT).limit(1))
    if row:
        return row["id"]
    if not write:
        say(f"   would create subject {SUBJECT}")
        return _planned("subject", SUBJECT)
    created = db.table("subjects").insert({
        "name": SUBJECT, "school_id": school_id,
    }).execute().data or []
    return created[0]["id"] if created else None


def _profile(db, school_id: str, identity: Identity):
    return _one(db.table("profiles").select("id,full_name,role")
                .eq("school_id", school_id).eq("role", identity.role)
                .eq("full_name", identity.full_name).limit(1))


def _account(db, identity: Identity, school_id: str, class_id: str | None,
             password: str, write: bool, say, create) -> str | None:
    """Create the account, or reset the password of the one already there.

    Both branches end with `must_change_password` cleared: an account that exists
    with the flag set is an account that cannot sign in, which reads downstream
    as a missing account rather than as a flag nobody cleared.
    """
    profile_fields = {"nisn": identity.nisn} if identity.nisn else {}
    if identity.role == "murid" and class_id and not _is_planned(class_id):
        # A planned class id is not a row; writing it into `class_id` would be a
        # promise the plan cannot keep even if the account were created.
        profile_fields["class_id"] = class_id
    existing = None if _is_planned(school_id) else _profile(db, school_id, identity)
    if existing:
        if not write:
            say(f"   would reset the password of {identity.email}")
            return existing["id"]
        db.auth.admin.update_user_by_id(existing["id"], {"password": password})
        uid = existing["id"]
        if profile_fields:
            db.table("profiles").update(profile_fields).eq("id", uid).execute()
    else:
        if not write:
            say(f"   would create {identity.role} {identity.email}")
            return None
        uid = create(
            db, school_id=school_id, role=identity.role,
            full_name=identity.full_name, email=identity.email,
            password=password, status="active",
            profile_fields=profile_fields,
            role_table="teachers" if identity.role == "guru" else None,
            role_fields={"employee_id": "STG-GURU-1"} if identity.role == "guru" else None,
            identifier=identity.email,
        )
    db.table("profiles").update({"must_change_password": False}).eq("id", uid).execute()
    return uid


def _exam_payload(teacher_id: str, school_id: str, class_id: str,
                  subject_id: str | None) -> dict:
    """One open, published, switch-on paper assigned to the staging class.

    No window (`exam_window.window_state` reads a row with neither timestamp as
    OPEN), and the paper carries no questions: these readings are about a room,
    not about answers, and an exam with a key is an exam whose key a staging run
    could accidentally submit.
    """
    return {
        "teacher_id": teacher_id, "school_id": school_id,
        "title": EXAM_TITLE, "subject": SUBJECT, "subject_id": subject_id,
        "duration_minutes": 60, "total_questions": 0, "passing_score": 70,
        "status": "active", "is_published": True, "publish_mode": "auto",
        "question_types": {}, "answer_key": {}, "question_weights": {},
        "class_ids": [class_id], "max_attempts": 5,
        "start_at": None, "end_at": None,
        # The whole point of the fixture: without this the API answers 409 and
        # the driver has no room to open.
        "scan_live_enabled": True,
    }


def _exam(db, school_id: str, teacher_id: str, class_id: str,
          subject_id: str | None, write: bool, say) -> str | None:
    payload = _exam_payload(teacher_id, school_id, class_id, subject_id)
    if _is_planned(school_id):
        say("   would create the staged exam")
        return None
    row = _one(db.table("exams").select("id,title").eq("school_id", school_id)
               .eq("title", EXAM_TITLE).limit(1))
    if row:
        if not write:
            say(f"   would repair exam {row['id']}")
            return row["id"]
        db.table("exams").update(payload).eq("id", row["id"]).execute()
        return row["id"]
    if not write:
        say("   would create the staged exam")
        return None
    created = db.table("exams").insert(payload).execute().data or []
    return created[0]["id"] if created else None


def ensure(db, *, write: bool, say=print, create=None,
           password: str | None = None) -> dict:
    """Make the staging database hold what the twelve readings need.

    Returns a report rather than printing one, so a test can assert the shape of
    what was written without parsing a terminal.
    """
    if create is None:                      # imported here: the app needs Flask
        from app.services.account_creation import create_account as create
    password = password or secrets.token_urlsafe(12)

    report = {"write": bool(write), "password": password, "identities": [],
              "exam_id": None, "school_a": None, "school_b": None}

    say(f"{'provisioning' if write else 'planning'} {MARK}")
    school_a = _school(db, SCHOOL_A, write, say)
    school_b = _school(db, SCHOOL_B, write, say)
    report["school_a"], report["school_b"] = school_a, school_b
    if not school_a:
        # Nothing below can exist without the school that owns the paper.
        return report

    class_a = _class(db, school_a, CLASS_NAME, write, say)
    class_b = _class(db, school_b, CLASS_NAME, write, say) if school_b else None
    subject_id = _subject(db, school_a, write, say)

    ids = {}
    for identity in IDENTITY_ROWS:
        school_id = school_b if identity.school == "b" else school_a
        if not school_id:
            say(f"   ! {identity.key}: school {identity.school} does not exist")
            continue
        class_id = class_b if identity.school == "b" else class_a
        uid = _account(db, identity, school_id, class_id, password, write, say, create)
        ids[identity.key] = uid
        report["identities"].append({"key": identity.key, "email": identity.email,
                                     "id": uid, "school": identity.school})

    if class_a and (ids.get("teacher") or not write):
        # In a plan the teacher has no id yet, so the paper is planned rather
        # than skipped: a dry run that silently omits half of what it would
        # write is a dry run somebody trusts for the wrong thing.
        report["exam_id"] = _exam(db, school_a, ids.get("teacher") or "new-teacher",
                                  class_a, subject_id, write, say)
    elif write:
        say("   ! no teacher, so no exam: the paper would be owned by nobody")
    return report


def verify(db, report: dict) -> list[str]:
    """The two claims a report cannot make on its own, read back from the rows."""
    found = []
    exam_id = report.get("exam_id")
    if not report.get("write") or not exam_id:
        return found
    row = _one(db.table("exams").select("id,scan_live_enabled,class_ids,teacher_id")
               .eq("id", exam_id).limit(1))
    if not row:
        return [f"the staged exam {exam_id} does not read back"]
    if not row.get("scan_live_enabled"):
        found.append("scan_live_enabled did not read back as true — migration 071 "
                     "(supabase/migrations/071_scan_live_exam_toggle.sql) is not "
                     "applied to this project")
    class_id = None
    for identity in report.get("identities", []):
        if identity["key"] == "student":
            profile = _one(db.table("profiles").select("class_id")
                           .eq("id", identity["id"]).limit(1)) if identity.get("id") else None
            class_id = (profile or {}).get("class_id")
    if class_id and class_id not in (row.get("class_ids") or []):
        found.append(f"the paper is not assigned to the pupils' class {class_id}, so "
                     "the door refuses both pupils")
    return found


def _print_report(report: dict, say=print) -> None:
    say("")
    say("ScanMeet staging fixture")
    say(f"  schools   A={report.get('school_a')}  B={report.get('school_b')}")
    say(f"  exam      {report.get('exam_id')}")
    if not report.get("write"):
        say("  (plan only — nothing was written; re-run with --write)")
        say("  passwords are chosen by the writing run and printed only by it")
        return
    say("")
    say("  These four arguments, exactly as the media gate wants them:")
    for identity in IDENTITY_ROWS:
        say(f"    --{identity.key:<9} {identity.as_gate_argument(report['password'])}")
    say("")
    say("  The same four, as the workflow's environment secrets:")
    for identity in IDENTITY_ROWS:
        name = {"teacher": "SCANMEET_TEACHER", "student": "SCANMEET_STUDENT",
                "student2": "SCANMEET_STUDENT2",
                "outsider": "SCANMEET_OUTSIDER"}[identity.key]
        say(f"    {name:<20} {identity.as_gate_argument(report['password'])}")
    say(f"    {'SCANMEET_EXAM_ID':<20} {report.get('exam_id')}")
    say("")
    say("  Printed once and stored nowhere: copy them now. The password is a")
    say("  fixture credential for synthetic accounts under .invalid addresses.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True,
                        help="private staging env file (never inside the checkout)")
    parser.add_argument("--write", action="store_true",
                        help="actually create the rows; without it this is a plan")
    parser.add_argument("--json", action="store_true",
                        help="also print the report as JSON (the password is included)")
    args = parser.parse_args(argv)

    checkout = Path(__file__).resolve().parents[1]
    path = args.env.resolve()
    if path == checkout or checkout in path.parents:
        print("FAIL: the staging env must live outside the checkout — a credential "
              "in the repository is the defect, not the setup")
        return 1
    try:
        settings = parse_env(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        print("FAIL: cannot read the private staging env file (--env)")
        return 2

    from deploy.supabase_region import REF     # never restated here
    found = problems(settings, REF)
    if found:
        for problem in found:
            print("FAIL: " + problem)
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("FAIL: supabase-py is not installed in this interpreter "
              "(pip install -r requirements.txt)")
        return 2
    db = create_client(settings["SUPABASE_URL"].strip(),
                       settings["SUPABASE_SERVICE_KEY"].strip())

    try:
        report = ensure(db, write=args.write)
        found = verify(db, report)
    except Exception as error:                       # a write that half-happened
        print(f"FAIL: {type(error).__name__}: {str(error)[:160]}")
        return 1
    _print_report(report)
    if args.json:
        print(json.dumps(report, indent=1))
    if found:
        for problem in found:
            print("FAIL: " + problem)
        return 1
    if args.write:
        print("PASS: four synthetic identities and one switch-on paper in the "
              "staging project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
