"""Who may build a school's invigilation schedule, and who may let a pupil retake.

Requested: "vice principals create per-exam/class schedules and assign invigilators,
the teacher's own dashboard shows their invigilation tasks, and every write is
school-scoped with tests that fail first. Provide approval to retake test if student
request based on class invigilate."

The failure this file exists to prevent is not "a button does nothing". It is the
quieter one: a POST that writes into another school because it trusted an id from the
form. So the assertions are about *scope* and *authority* first and about features
second:

* **the write** — a vice principal of school A cannot schedule school B's exam, attach
  school B's teacher, or decide school B's request. Each of those is one missing
  ``.eq("school_id", …)`` away, and each would look like a working page;
* **the authority** — a teacher decides a retake only for an exam they actually
  invigilate. That is expressed as data (`invigilated_exam_ids`) read by both the page
  and the decision, so a page cannot offer a button the decision refuses;
* **the race** — the invigilator and the vice principal both hold the button. Two
  decisions must not both land, and "already decided" has to be reported rather than
  overwritten;
* **the retake itself** — an approved request has to *do* something. It returns the
  paper to the pupil's list (`retracted`, the status the student routes already treat
  as "not an attempt"), so approval needs no second mechanism to take effect;
* **the schema** — three tables that carry their own ``school_id``, RLS enabled, and
  every policy naming the caller in the database's own roles rather than being open to
  ``PUBLIC``.

The stand-in below is a small PostgREST that **applies** its writes. That matters for
one assertion in particular: the conditional update is the referee of the race, so a
fake that merely recorded patches could not tell a working guard from a described one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "invigilation.py"
MIGRATION = ROOT / "supabase" / "migrations" / "041_invigilation.sql"
PRINCIPAL_ROUTES = ROOT / "app" / "routes" / "principal.py"
TEACHER_ROUTES = ROOT / "app" / "routes" / "teacher.py"
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"
REASON_PARTIAL = ROOT / "app" / "templates" / "shared" / "_invigilation_reasons.html"
OFFICIAL_PAGE = ROOT / "app" / "templates" / "principal" / "invigilation.html"
TEACHER_PAGE = ROOT / "app" / "templates" / "teacher" / "invigilation.html"

from app.services import invigilation as iv  # noqa: E402


# ── a PostgREST stand-in that applies its writes ────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters: list[tuple[str, str, object]] = []
        self._op: str | None = None
        self._payload = None
        self._order = None
        self._primed = None
        self._limit = None
        self._range = None

    # -- query building -----------------------------------------------------
    def select(self, *cols, **kw):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def ilike(self, column, pattern):
        # A case-insensitive `contains`, which is what `%term%` means to PostgREST.
        # The `%` is the only wildcard any caller in this repo uses, so stripping
        # it to a plain substring is the honest reading of the request.
        self._filters.append(("ilike", column, pattern))
        return self

    def limit(self, count):
        self._limit = count
        return self

    def range(self, start, end):
        # PostgREST's own window, which is how a caller reads past the 1000 rows a
        # single request is allowed to hand back: `range(0, 999)`, then `range(1000,
        # 1999)`, and so on until a short page says the data ended.
        self._range = (start, end)
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    # -- execution ----------------------------------------------------------
    def _match(self, row) -> bool:
        for kind, column, value in self._filters:
            if kind == "eq":
                if str(row.get(column)) != str(value):
                    return False
            elif kind == "ilike":
                haystack = str(row.get(column) or "").lower()
                if str(value).strip("%").lower() not in haystack:
                    return False
            else:
                if str(row.get(column)) not in {str(v) for v in value}:
                    return False
        return True

    def execute(self):
        self.db.log.append((self._op or "select", self.table, dict(self._payload or {}),
                            list(self._filters)))
        if self._op == "select":
            if self._primed is not None:
                return _Res([dict(r) for r in self._primed])
            rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
            if self._order:
                column, desc = self._order
                rows.sort(key=lambda r: str(r.get(column) or ""), reverse=bool(desc))
            if self._limit is not None:
                rows = rows[:int(self._limit)]
            if self._range is not None:
                start, end = self._range
                rows = rows[int(start):int(end) + 1]
            return _Res(rows)
        if self._op == "insert":
            self.db._counter += 1
            row = dict(self._payload)
            row.setdefault("id", f"{self.table}-{self.db._counter}")
            self.db.tables.setdefault(self.table, []).append(row)
            return _Res([dict(row)])
        if self._op == "update":
            hits = [r for r in self.db.tables.get(self.table, []) if self._match(r)]
            for row in hits:
                row.update(self._payload)
            return _Res([dict(r) for r in hits])
        if self._op == "delete":
            kept, gone = [], []
            for row in self.db.tables.get(self.table, []):
                (gone if self._match(row) else kept).append(row)
            self.db.tables[self.table] = kept
            return _Res([dict(r) for r in gone])
        raise AssertionError(self._op)


class _DB:
    """Rows in, rows out — and a log of every statement, in order."""

    def __init__(self, tables=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.log: list[tuple] = []
        self._counter = 0
        self.prime_next_select: tuple[str, list[dict]] | None = None

    def table(self, name):
        query = _Query(self, name)
        if self.prime_next_select and self.prime_next_select[0] == name:
            query._primed = self.prime_next_select[1]
            self.prime_next_select = None
        return query

    def writes(self, table: str) -> list[tuple]:
        return [entry for entry in self.log if entry[1] == table and entry[0] != "select"]


SCHOOL = "s1"
OTHER = "s2"


def _tables() -> dict:
    return {
        "schools": [{"id": SCHOOL}, {"id": OTHER}],
        "profiles": [{"id": "vp1"}, {"id": "pr1"}, {"id": "t1"}, {"id": "t9"},
                     {"id": "p1"}, {"id": "p9"}],
        "exams": [
            {"id": "ex1", "school_id": SCHOOL, "title": "Matematika", "teacher_id": "t1"},
            {"id": "ex-legacy", "school_id": None, "title": "Lama", "teacher_id": "t1"},
            {"id": "ex9", "school_id": OTHER, "title": "Bukan kita", "teacher_id": "t9"},
        ],
        "classes": [{"id": "c1", "school_id": SCHOOL, "name": "7A"},
                    {"id": "c9", "school_id": OTHER, "name": "9Z"}],
        "teachers": [{"id": "t1", "school_id": SCHOOL, "profiles": {"full_name": "Bu Sari"}},
                     {"id": "t2", "school_id": SCHOOL, "profiles": {"full_name": "Pak Budi"}},
                     {"id": "t9", "school_id": OTHER, "profiles": {"full_name": "Asing"}}],
        "students": [{"id": "p1", "school_id": SCHOOL, "class_id": "c1",
                      "profiles": {"full_name": "Ana"}},
                     {"id": "p9", "school_id": OTHER, "class_id": "c9",
                      "profiles": {"full_name": "Budi"}}],
        "invigilation_schedules": [],
        "invigilator_assignments": [],
        "exam_retake_requests": [],
        "submissions": [{"id": "sub1", "exam_id": "ex1", "student_id": "p1",
                         "status": "graded"},
                        {"id": "sub9", "exam_id": "ex9", "student_id": "p9",
                         "status": "graded"}],
    }


def _sitting(db, schedule_id="sc1", *, exam="ex1", klass="c1"):
    db.tables["invigilation_schedules"].append({
        "id": schedule_id, "school_id": SCHOOL, "exam_id": exam, "class_id": klass,
        "scheduled_at": "2026-10-01T08:00:00+07:00", "room": "R1", "notes": None})


def _duty(db, teacher="t1", schedule_id="sc1", assignment_id="as1"):
    db.tables["invigilator_assignments"].append({
        "id": assignment_id, "school_id": SCHOOL, "schedule_id": schedule_id,
        "teacher_id": teacher, "is_lead": False})


def _request(db, request_id="rq1", *, exam="ex1", student="p1", status="pending",
             school=SCHOOL):
    db.tables["exam_retake_requests"].append({
        "id": request_id, "school_id": school, "exam_id": exam, "student_id": student,
        "class_id": "c1", "reason": "sakit", "status": status, "decided_by": None,
        "decided_at": None, "decision_note": None,
        "created_at": "2026-09-30T09:00:00+00:00"})


# ── 1. building the schedule ────────────────────────────────────────────────

class TestBuildingTheSchedule:
    def test_a_sitting_is_written_with_the_callers_school(self):
        db = _DB(_tables())
        out = iv.save_schedule(db, SCHOOL, exam_id="ex1", class_id="c1",
                               scheduled_at="2026-10-01T08:00:00+07:00",
                               room="Lab", actor_id="vp1")
        assert out["ok"] and out["created"], out
        stored = db.tables["invigilation_schedules"][0]
        assert stored["school_id"] == SCHOOL, "the row was written for another school"
        assert stored["created_by"] == "vp1"

    def test_another_schools_exam_is_refused(self):
        db = _DB(_tables())
        out = iv.save_schedule(db, SCHOOL, exam_id="ex9", class_id="c1",
                               scheduled_at="2026-10-01T08:00:00+07:00")
        assert out == {"ok": False, "reason": "exam_not_in_school"}, out
        assert db.tables["invigilation_schedules"] == [], "a foreign exam was scheduled"

    def test_another_schools_class_is_refused(self):
        db = _DB(_tables())
        out = iv.save_schedule(db, SCHOOL, exam_id="ex1", class_id="c9",
                               scheduled_at="2026-10-01T08:00:00+07:00")
        assert out["reason"] == "class_not_in_school", out
        assert db.tables["invigilation_schedules"] == []

    def test_an_exam_with_no_school_column_is_this_schools_when_its_author_teaches_here(self):
        """`exams.school_id` is nullable and older rows are null. Refusing them would
        make a legacy paper unschedulable with no error the school could act on."""
        db = _DB(_tables())
        out = iv.save_schedule(db, SCHOOL, exam_id="ex-legacy", class_id="c1",
                               scheduled_at="2026-10-01T08:00:00+07:00")
        assert out["ok"], out

    def test_the_same_exam_and_class_edits_the_sitting_rather_than_duplicating(self):
        db = _DB(_tables())
        _sitting(db, "sc1")
        out = iv.save_schedule(db, SCHOOL, exam_id="ex1", class_id="c1",
                               scheduled_at="2026-10-02T08:00:00+07:00", room="R2")
        assert out["ok"] and out["created"] is False, out
        assert len(db.tables["invigilation_schedules"]) == 1
        assert db.tables["invigilation_schedules"][0]["room"] == "R2"

    def test_every_write_on_the_new_tables_carries_the_school_filter(self):
        """The row-level guard, read from the statements themselves: a write to these
        three tables that is not filtered by `school_id` is the bug this feature is."""
        db = _DB(_tables())
        _sitting(db, "sc1")
        _duty(db, "t1", "sc1", "as1")
        _request(db, "rq1")
        iv.save_schedule(db, SCHOOL, exam_id="ex1", class_id="c1",
                         scheduled_at="2026-10-03T08:00:00+07:00")
        iv.assign_invigilator(db, SCHOOL, schedule_id="sc1", teacher_id="t2")
        iv.remove_assignment(db, SCHOOL, "as1")
        iv.decide_retake(db, SCHOOL, "rq1", decision="rejected", actor_id="vp1")

        offenders = []
        for kind, table, payload, filters in (db.writes("invigilation_schedules")
                                              + db.writes("invigilator_assignments")
                                              + db.writes("exam_retake_requests")):
            if kind == "insert":
                # A new row carries its school; an update or a delete is filtered by it.
                scoped = str(payload.get("school_id")) == SCHOOL
            else:
                scoped = any(column == "school_id" for _k, column, _v in filters)
            if not scoped:
                offenders.append((kind, table, payload.get("school_id"), filters))
        assert not offenders, f"a write went out unscoped: {offenders}"


# ── 2. assigning invigilators ───────────────────────────────────────────────

class TestAssigningInvigilators:
    def test_a_teacher_from_another_school_is_refused(self):
        db = _DB(_tables())
        _sitting(db)
        out = iv.assign_invigilator(db, SCHOOL, schedule_id="sc1", teacher_id="t9")
        assert out["reason"] == "teacher_not_in_school", out
        assert db.tables["invigilator_assignments"] == []

    def test_a_sitting_from_another_school_is_refused(self):
        db = _DB(_tables())
        db.tables["invigilation_schedules"].append({
            "id": "sc9", "school_id": OTHER, "exam_id": "ex9", "class_id": "c9",
            "scheduled_at": "2026-10-01T08:00:00+07:00"})
        out = iv.assign_invigilator(db, SCHOOL, schedule_id="sc9", teacher_id="t1")
        assert out["reason"] == "schedule_not_in_school", out
        assert db.tables["invigilator_assignments"] == []

    def test_a_teacher_can_be_made_lead_without_a_second_row(self):
        db = _DB(_tables())
        _sitting(db)
        first = iv.assign_invigilator(db, SCHOOL, schedule_id="sc1", teacher_id="t1")
        second = iv.assign_invigilator(db, SCHOOL, schedule_id="sc1", teacher_id="t1",
                                       is_lead=True)
        assert first["created"] is True and second["created"] is False, (first, second)
        assert len(db.tables["invigilator_assignments"]) == 1
        assert db.tables["invigilator_assignments"][0]["is_lead"] is True

    def test_a_foreign_assignment_is_not_removed(self):
        db = _DB(_tables())
        _sitting(db)
        db.tables["invigilator_assignments"].append({
            "id": "as9", "school_id": OTHER, "schedule_id": "sc9", "teacher_id": "t9",
            "is_lead": False})
        out = iv.remove_assignment(db, SCHOOL, "as9")
        assert out == {"ok": False, "reason": "not_found"}, out
        assert len(db.tables["invigilator_assignments"]) == 1, "another school's row was deleted"

    def test_the_schedule_lists_its_invigilators_by_name(self):
        db = _DB(_tables())
        _sitting(db)
        _duty(db, "t1", "sc1", "as1")
        rows = iv.list_schedules(db, SCHOOL)
        assert len(rows) == 1, rows
        assert rows[0]["exam_title"] == "Matematika", rows
        assert rows[0]["class_name"] == "7A", rows
        assert [i["name"] for i in rows[0]["invigilators"]] == ["Bu Sari"], rows


# ── 3. the teacher's own duty ───────────────────────────────────────────────

class TestTheTeachersOwnDuty:
    def test_only_the_teachers_own_sittings_are_returned(self):
        db = _DB(_tables())
        _sitting(db, "sc1")
        _sitting(db, "sc2")
        _duty(db, "t1", "sc1", "as1")
        _duty(db, "t2", "sc2", "as2")
        tasks = iv.tasks_for_teacher(db, SCHOOL, "t1")
        assert [t["id"] for t in tasks] == ["sc1"], tasks

    def test_a_duty_in_another_school_is_not_returned(self):
        db = _DB(_tables())
        db.tables["invigilation_schedules"].append({
            "id": "sc9", "school_id": OTHER, "exam_id": "ex9", "class_id": "c9",
            "scheduled_at": "2026-10-01T08:00:00+07:00"})
        db.tables["invigilator_assignments"].append({
            "id": "as9", "school_id": OTHER, "schedule_id": "sc9", "teacher_id": "t1",
            "is_lead": False})
        assert iv.tasks_for_teacher(db, SCHOOL, "t1") == []

    def test_the_invigilated_exam_set_is_read_from_the_assignments(self):
        db = _DB(_tables())
        _sitting(db, "sc1")
        _duty(db, "t1", "sc1", "as1")
        assert iv.invigilated_exam_ids(db, SCHOOL, "t1") == {"ex1"}
        assert iv.invigilated_exam_ids(db, SCHOOL, "t2") == set()


# ── 4. the decision, and the race for it ────────────────────────────────────

class TestDecidingARetake:
    def test_a_teacher_who_does_not_invigilate_that_exam_is_refused(self):
        db = _DB(_tables())
        _request(db, "rq1")
        out = iv.decide_retake(db, SCHOOL, "rq1", decision="approved", actor_id="t2",
                               within_exam_ids=set())
        assert out == {"ok": False, "reason": "not_invigilated"}, out
        assert db.tables["exam_retake_requests"][0]["status"] == "pending"

    def test_the_invigilator_of_that_exam_may_decide(self):
        db = _DB(_tables())
        _request(db, "rq1")
        out = iv.decide_retake(db, SCHOOL, "rq1", decision="approved", actor_id="t1",
                               within_exam_ids={"ex1"})
        assert out["ok"], out

    def test_another_schools_request_is_not_found(self):
        db = _DB(_tables())
        _request(db, "rq9", exam="ex9", student="p9", school=OTHER)
        out = iv.decide_retake(db, SCHOOL, "rq9", decision="approved", actor_id="vp1")
        assert out == {"ok": False, "reason": "not_found"}, out
        assert db.tables["exam_retake_requests"][0]["status"] == "pending"

    def test_approving_returns_the_paper_to_the_pupil(self):
        """An approval that changes nothing is a promise the app does not keep:
        `retracted` is the status the student routes already read as 'not an attempt',
        so the paper comes back into the list and the attempt ceiling stops counting it."""
        db = _DB(_tables())
        _request(db, "rq1")
        iv.decide_retake(db, SCHOOL, "rq1", decision="approved", actor_id="vp1")
        submission = [s for s in db.tables["submissions"] if s["id"] == "sub1"][0]
        assert submission["status"] == "retracted", submission

    def test_rejecting_leaves_the_paper_alone(self):
        db = _DB(_tables())
        _request(db, "rq1")
        iv.decide_retake(db, SCHOOL, "rq1", decision="rejected", actor_id="vp1",
                         note="tidak ada bukti")
        submission = [s for s in db.tables["submissions"] if s["id"] == "sub1"][0]
        assert submission["status"] == "graded", submission
        row = db.tables["exam_retake_requests"][0]
        assert row["status"] == "rejected" and row["decision_note"] == "tidak ada bukti"

    def test_the_read_is_not_what_decides_a_race(self):
        """Two readers hold the button. Priming the read to say 'pending' while the
        stored row says 'approved' is exactly the interleaving — the conditional write
        has to lose, and the loser has to be told, not to overwrite the winner."""
        db = _DB(_tables())
        _request(db, "rq1", status="approved")
        db.prime_next_select = ("exam_retake_requests",
                                [{"id": "rq1", "exam_id": "ex1", "student_id": "p1",
                                  "status": "pending"}])
        out = iv.decide_retake(db, SCHOOL, "rq1", decision="rejected", actor_id="vp1")
        assert out == {"ok": False, "reason": "already_decided"}, out
        assert db.tables["exam_retake_requests"][0]["status"] == "approved"

    def test_a_second_decision_cannot_overwrite_the_first(self):
        db = _DB(_tables())
        _request(db, "rq1")
        first = iv.decide_retake(db, SCHOOL, "rq1", decision="approved", actor_id="vp1")
        second = iv.decide_retake(db, SCHOOL, "rq1", decision="rejected", actor_id="t1")
        assert first["ok"] and second["reason"] == "already_decided", (first, second)
        assert db.tables["exam_retake_requests"][0]["status"] == "approved"

    def test_only_the_two_decisions_are_accepted(self):
        db = _DB(_tables())
        _request(db, "rq1")
        out = iv.decide_retake(db, SCHOOL, "rq1", decision="withdrawn", actor_id="vp1")
        assert out == {"ok": False, "reason": "bad_decision"}, out
        assert iv.DECISIONS == ("approved", "rejected")


# ── 5. a pupil asking ───────────────────────────────────────────────────────

class TestAskingForARetake:
    def test_a_foreign_exam_cannot_be_asked_for(self):
        db = _DB(_tables())
        out = iv.request_retake(db, SCHOOL, exam_id="ex9", student_id="p1")
        assert out["reason"] == "exam_not_in_school", out
        assert db.tables["exam_retake_requests"] == []

    def test_a_foreign_pupil_is_refused(self):
        db = _DB(_tables())
        out = iv.request_retake(db, SCHOOL, exam_id="ex1", student_id="p9")
        assert out["reason"] == "student_not_in_school", out

    def test_a_pupil_who_never_sat_the_paper_cannot_ask(self):
        db = _DB(_tables())
        db.tables["submissions"] = []
        out = iv.request_retake(db, SCHOOL, exam_id="ex1", student_id="p1")
        assert out["reason"] == "nothing_to_retake", out

    def test_one_open_request_at_a_time(self):
        db = _DB(_tables())
        _request(db, "rq1")
        out = iv.request_retake(db, SCHOOL, exam_id="ex1", student_id="p1")
        assert out["reason"] == "already_requested", out
        assert len(db.tables["exam_retake_requests"]) == 1

    def test_the_request_records_the_pupils_class(self):
        db = _DB(_tables())
        out = iv.request_retake(db, SCHOOL, exam_id="ex1", student_id="p1",
                                reason="demam")
        assert out["ok"], out
        row = db.tables["exam_retake_requests"][0]
        assert row["class_id"] == "c1" and row["status"] == "pending", row
        assert row["school_id"] == SCHOOL, row

    def test_the_teachers_queue_is_empty_when_they_invigilate_nothing(self):
        """`[]` must select nothing. A page asking for "the exams I invigilate" on a
        teacher who invigilates none must not be handed the whole school's queue."""
        db = _DB(_tables())
        _request(db, "rq1")
        assert iv.retake_requests(db, SCHOOL, status="pending", exam_ids=[]) == []


# ── 6. the pages, the routes and the schema ─────────────────────────────────

class TestTheRoutesSayWhoMayDoWhat:
    def test_each_page_is_behind_the_role_it_belongs_to(self):
        principal = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        teacher = TEACHER_ROUTES.read_text(encoding="utf-8")
        student = STUDENT_ROUTES.read_text(encoding="utf-8")
        assert '/principal/invigilation' in principal
        assert '/vice-principal/invigilation' in principal
        assert '@vice_principal_required' in principal, "the schedule's writes lost their role"
        assert '/teacher/invigilation' in teacher
        assert '@teacher_required' in teacher
        assert 'retake-requests' in student

    def test_no_route_takes_a_school_from_the_request(self):
        """The school is the session's, never the form's. A route that reads it from
        the request is a route that writes into another school when the form says so."""
        for path in (PRINCIPAL_ROUTES, TEACHER_ROUTES, STUDENT_ROUTES):
            source = path.read_text(encoding="utf-8")
            # Each route from its own decorator up to the next one, so the body really
            # is the body — the first cut stopped at the very next `@`, which is the
            # role decorator two lines down, and measured an empty string.
            starts = [m.start() for m in re.finditer(
                r"@\w*bp\.route\(\"[^\"]*(?:invigilation|retake)[^\"]*\"", source)]
            starts = starts + [len(source)]
            for start, end in zip(starts, starts[1:]):
                block = source[start:end]
                assert "invigilation" in block or "retake" in block, block[:80]
                for stolen in ('request.form.get("school_id")',
                               'request.args.get("school_id")',
                               'request.values.get("school_id")',
                               'request.get_json().get("school_id")'):
                    assert stolen not in block, (
                        f"{path.name}: a route reads the school from the request")
            # Every one of them reaches the school through the session — directly in
            # its own body, or through the one helper that builds the page.
            assert "user_school_id" in source or "_school_id()" in source, (
                f"{path.name}: no invigilation route takes the session's school")

    def test_every_writing_route_carries_its_own_guard(self):
        """A presence check is not enough: the mutator's own survivor was a route that
        kept `@vice_principal_required` somewhere in the file and lost it on the one
        route that writes. Every POST is checked against its own decorator."""
        homes = {PRINCIPAL_ROUTES: "vice_principal_required",
                 TEACHER_ROUTES: "teacher_required",
                 STUDENT_ROUTES: "login_required"}
        for path, guard in homes.items():
            source = path.read_text(encoding="utf-8")
            starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
            starts.append(len(source))
            for start, end in zip(starts, starts[1:]):
                block = source[start:end]
                if not re.search(r"(invigilation|retake-requests)", block):
                    continue
                if "methods=[\"POST\"]" not in block:
                    continue
                assert guard in block, (
                    f"{path.name}: a writing route has no {guard}: {block[:90]}")

    def test_the_official_page_is_read_only_for_the_principal(self):
        source = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        assert '@principal_required' in source
        # every write route is registered on the vice-principal prefix
        writes = re.findall(r'@principal_bp\.route\(\"(/principal[^\"]*)\",\s*methods=\[\"POST\"\]\)',
                            source)
        assert not [w for w in writes if "invigilation" in w or "retake" in w], writes


class TestTheSchemaSaysTheSameThing:
    def test_the_three_tables_carry_their_own_school(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        for table in ("invigilation_schedules", "invigilator_assignments",
                      "exam_retake_requests"):
            assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql, table
            assert re.search(rf"school_id UUID NOT NULL REFERENCES public\.schools\(id\)"
                             rf"[\s\S]{{0,900}}?CREATE TABLE", sql), table
        enabled = re.findall(r"ALTER TABLE public\.(\w+) ENABLE ROW LEVEL SECURITY", sql)
        assert set(enabled) == {"invigilation_schedules", "invigilator_assignments",
                               "exam_retake_requests"}, enabled

    def test_every_policy_names_the_caller(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        policies = re.findall(r"CREATE POLICY \"([^\"]+)\" ON public\.(\w+)\s+"
                              r"FOR (SELECT|INSERT|UPDATE|ALL)\s+TO (\w+)", sql)
        assert policies, "no policy was created"
        bad_scope = [p for p in policies if p[3] != "authenticated"]
        assert not bad_scope, f"a policy is open to everyone: {bad_scope}"
        assert "TO public" not in sql.lower().replace("to public.", "")
        for _name, _table, _verb, _role in policies:
            body = sql.split(f'CREATE POLICY "{_name}"')[1].split(";")[0]
            assert "public._user_school_id()" in body, (
                f"{_name} does not compare the row's school with the caller's")

    def test_the_migration_is_additive_and_idempotent(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration drops something"
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M), "the runner owns the transaction"
        assert not re.search(r"^\s*COMMIT\s*;", sql, re.M)
        for statement in re.findall(r"CREATE TABLE (?!IF NOT EXISTS)", sql):
            raise AssertionError(f"a table is created twice-run-unsafe: {statement}")
        for policy in re.findall(r"CREATE POLICY \"([^\"]+)\"", sql):
            assert f'DROP POLICY IF EXISTS "{policy}"' in sql, (
                f"{policy} is created without being dropped first, so a second run fails")


class TestThePagesCanSayWhy:
    def test_every_refusal_has_a_sentence_in_both_languages(self):
        partial = REASON_PARTIAL.read_text(encoding="utf-8")
        missing = [key for key in iv.REFUSALS + iv.NOTICES
                   if not re.search(rf"'{key}':\s*\[\s*'[^']+',\s*'[^']+',?\s*\]", partial)]
        assert not missing, f"a refusal renders as an empty alert: {missing}"

    def test_both_pages_show_the_reason_and_the_duties(self):
        official = OFFICIAL_PAGE.read_text(encoding="utf-8")
        teacher = TEACHER_PAGE.read_text(encoding="utf-8")
        assert "reason_note" in official and "reason_note" in teacher, (
            "a page refuses a write without saying why")
        # The official page can create a sitting and assign an invigilator; the teacher
        # page lists their duties and can decide. The route paths themselves are the
        # assertion, so a form pointing at a path that does not exist fails here.
        # The prefix is the caller's since the admin school gained the same page, so
        # the page names `invigilation_base` and the routes name their own prefixes.
        assert '{{ invigilation_base }}/invigilation/save' in official
        assert '{{ invigilation_base }}/invigilation/' in official, "no sitting can be assigned"
        principal = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        assert '/vice-principal/invigilation/save' in principal
        assert '/vice-principal/invigilation/' in principal
        assert 'options.teachers' in official, "the assign form has no teachers to pick"
        assert 'invigilators' in official
        assert 'tasks' in teacher, "the teacher's page does not list their duties"
        assert '/teacher/retake-requests/' in teacher, (
            "the teacher's page cannot decide a request")
