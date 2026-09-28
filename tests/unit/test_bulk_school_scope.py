"""The student bulk reset and delete took a list of ids and touched every one.

Measured against the running code before this test existed
----------------------------------------------------------
Four bulk endpoints live side by side in ``app/routes/admin_sekolah.py``: a reset
and a delete for teachers, and a reset and a delete for pupils. The two *teacher*
ones ask whose row each id is before they act — ``profiles.school_id`` against
``g.user_school_id`` — and the two *student* ones did not. The asymmetry is the
whole defect: ``POST /admin-sekolah/students/bulk-delete`` with another school's
pupil id deleted that pupil's ``students`` row, their ``profiles`` row and their
``auth`` user, and ``bulk-reset-password`` re-set a stranger's password and handed
the new one back in the JSON response.

The page only ever *offers* this school's pupils, and a list drawn by a page is
not a guard: the endpoint takes ids off the request body, so anything the caller
writes there was acted on. The single-id twins are safe because they carry
``@require_school_access("students", "student_id")``; the bulk pair have no id in
the path for that decorator to read, so the check has to be per id inside the
loop — exactly what the teacher pair already does.

What is asserted here:

* a foreign pupil id in the list is neither reset nor deleted, while this
  school's own id in the same request is;
* the refusal is recorded the way the teacher reset records it, so a caller can
  tell "skipped" from "done";
* the teacher pair still behaves the same way, so the two families cannot drift
  apart again;
* the check is in the route source, before the write, for both student routes.

The views are driven for real with ``g`` populated, against a fake that answers
``profiles.school_id`` per row.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from flask import g

from tests.conftest import app_instance
from app.routes import admin_sekolah as mod
from app.utils import auth as auth_module
from app.routes.admin_sekolah import (
    bulk_delete_students,
    bulk_delete_teachers,
    bulk_reset_students_password,
    bulk_reset_teachers_password,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app" / "routes" / "admin_sekolah.py"

SCHOOL_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SCHOOL_B = "bbbbbbbb-0000-0000-0000-00000000000b"
ADMIN_A = "22222222-0000-0000-0000-00000000000a"
PUPIL_A = "11111111-0000-0000-0000-00000000000a"
PUPIL_B = "11111111-0000-0000-0000-00000000000b"
TEACHER_A = "33333333-0000-0000-0000-00000000000a"
TEACHER_B = "44444444-0000-0000-0000-00000000000b"

RESET = "/admin-sekolah/students"
TEACHERS = "/admin-sekolah/teachers"


# ── a fake whose `profiles` answers one school per row ──────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.op = "select"
        self._single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def delete(self):
        self.op = "delete"
        return self

    def single(self):
        # PostgREST answers a single row as an **object** and raises when there is
        # none, which is what the route reads as "no such profile".
        self._single = True
        return self

    def execute(self):
        rows = [r for r in self.store.data.get(self.table, [])
                if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.op == "delete":
            live = self.store.data.setdefault(self.table, [])
            for row in rows:
                live.remove(row)
            self.store.deletes.append((self.table, list(self.filters)))
            return _Resp([dict(r) for r in rows])
        if self._single:
            if not rows:
                raise RuntimeError("multiple (or no) rows in single() request")
            return _Resp(dict(rows[0]))
        return _Resp([dict(r) for r in rows])


class _AuthAdmin:
    def __init__(self, store):
        self.store = store

    def update_user_by_id(self, uid, payload):
        self.store.resets.append(uid)
        return SimpleNamespace(user=SimpleNamespace(id=uid))

    def delete_user(self, uid):
        self.store.auth_deletes.append(uid)
        return None


class FakeSupabase:
    """Two schools; one pupil and one teacher in each."""

    def __init__(self):
        self.data = {
            "students": [
                {"id": PUPIL_A, "school_id": SCHOOL_A},
                {"id": PUPIL_B, "school_id": SCHOOL_B},
            ],
            "teachers": [
                {"id": TEACHER_A, "school_id": SCHOOL_A},
                {"id": TEACHER_B, "school_id": SCHOOL_B},
            ],
            "profiles": [
                {"id": ADMIN_A, "role": "admin_sekolah", "school_id": SCHOOL_A},
                {"id": PUPIL_A, "role": "murid", "school_id": SCHOOL_A},
                {"id": PUPIL_B, "role": "murid", "school_id": SCHOOL_B},
                {"id": TEACHER_A, "role": "guru", "school_id": SCHOOL_A},
                {"id": TEACHER_B, "role": "guru", "school_id": SCHOOL_B},
            ],
        }
        self.resets = []
        self.auth_deletes = []
        self.deletes = []
        self.auth = SimpleNamespace(admin=_AuthAdmin(self))

    def table(self, name):
        return _Query(self, name)

    def rows(self, table):
        return self.data.setdefault(table, [])

    def has(self, table, row_id):
        return any(r.get("id") == row_id for r in self.rows(table))


# ── driving the real views ──────────────────────────────────────────────────

def peel(view):
    """The view behind its decorators; the role guard is tested on purpose in the
    suites that own it, not here."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def run(view, fake, monkeypatch, *, payload, school=SCHOOL_A, path=RESET):
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)
    app = app_instance()
    with app.test_request_context(path, method="POST",
                                  data={"user_ids": json.dumps(payload)}):
        g.user_id = ADMIN_A
        g.user_role = "admin_sekolah"
        g.user_school_id = school
        g.user_email = "admin@scan-grade.app"
        g.user_name = "Admin SMP"
        g.tz_offset = 7
        return peel(view)()


def results_of(response):
    return {r["id"]: r for r in response.get_json()["results"]}


# ── the pupil routes ────────────────────────────────────────────────────────

class TestStudentBulkReset:
    def test_a_pupil_of_another_school_is_not_reset(self, monkeypatch):
        fake = FakeSupabase()
        run(bulk_reset_students_password, fake, monkeypatch,
            payload=[PUPIL_A, PUPIL_B])
        assert PUPIL_A in fake.resets, "this school's own pupil was not reset"
        assert PUPIL_B not in fake.resets, (
            "another school's pupil had their password re-set by this admin, and "
            "the new password came back in the response")

    def test_the_refusal_is_recorded_like_the_teacher_route_records_it(self, monkeypatch):
        fake = FakeSupabase()
        response = run(bulk_reset_students_password, fake, monkeypatch,
                       payload=[PUPIL_A, PUPIL_B])
        by_id = results_of(response)
        assert by_id[PUPIL_A].get("success") is True, by_id[PUPIL_A]
        assert by_id[PUPIL_B].get("error") == "Not in school", (
            "a caller cannot tell a skipped id from a done one")


class TestStudentBulkDelete:
    def test_a_pupil_of_another_school_is_not_deleted(self, monkeypatch):
        fake = FakeSupabase()
        run(bulk_delete_students, fake, monkeypatch, payload=[PUPIL_A, PUPIL_B])
        assert not fake.has("students", PUPIL_A), "this school's pupil survived"
        assert not fake.has("profiles", PUPIL_A), "this school's profile survived"
        assert PUPIL_A in fake.auth_deletes, "this school's auth user survived"
        assert fake.has("students", PUPIL_B), (
            "another school's pupil row was deleted")
        assert fake.has("profiles", PUPIL_B), (
            "another school's profile row was deleted")
        assert PUPIL_B not in fake.auth_deletes, (
            "another school's auth user was deleted — the account is gone")


# ── the teacher routes already did this; keep them doing it ─────────────────

class TestTheTeacherPairStaysScoped:
    def test_a_foreign_teacher_is_not_reset(self, monkeypatch):
        fake = FakeSupabase()
        run(bulk_reset_teachers_password, fake, monkeypatch,
            payload=[TEACHER_A, TEACHER_B], path=TEACHERS)
        assert TEACHER_A in fake.resets and TEACHER_B not in fake.resets

    def test_a_foreign_teacher_is_not_deleted(self, monkeypatch):
        fake = FakeSupabase()
        run(bulk_delete_teachers, fake, monkeypatch,
            payload=[TEACHER_A, TEACHER_B], path=TEACHERS)
        assert not fake.has("teachers", TEACHER_A)
        assert fake.has("teachers", TEACHER_B), (
            "the teacher pair lost its school check")


# ── and the check is in the route, before the write ─────────────────────────

def _bulk_body(name: str) -> str:
    source = SRC.read_text(encoding="utf-8")
    start = source.index(f"def {name}(")
    # Up to the route decorator of the next view.
    end = source.index("\n@admin_sekolah_bp.route", source.index("return jsonify", start))
    return source[start:end]


class TestTheCheckIsInTheRoute:
    def test_the_student_reset_checks_the_school_before_it_resets(self):
        body = _bulk_body("bulk_reset_students_password")
        assert 'select("school_id")' in body, (
            "the pupil reset never reads whose profile it is")
        assert body.index('select("school_id")') < body.index(
            "update_user_by_id"), "the password is re-set before the id is checked"

    def test_the_student_delete_checks_the_school_before_it_deletes(self):
        body = _bulk_body("bulk_delete_students")
        assert 'select("school_id")' in body, (
            "the pupil delete never reads whose profile it is")
        assert body.index('select("school_id")') < body.index(
            '.table("students").delete()'), (
            "the pupil row is deleted before the id is checked")
