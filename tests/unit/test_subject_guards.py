"""A subject is a school-wide row, so writing one is the admin's job.

Measured against the running code before this test existed
----------------------------------------------------------
``/teacher/subjects``, ``/teacher/subjects/new`` and
``/teacher/subjects/<id>/delete`` carried ``@login_required`` and nothing else.
That decorator answers one question — "is somebody signed in?" — and every
signed-in member of the school answers yes, *murid included*. So:

* a student could list, create and delete the school's subjects;
* the delete filtered on ``id`` alone, with no ``school_id``, so the path
  reached **any** school's subject row (and its ``teacher_assignments``).

The rule the app already applied to classes — ``assignments()`` refuses a
non-admin with "Hanya admin sekolah yang bisa menambah kelas" — was simply
never extended to subjects. These tests pin the guards so it cannot happen
again, and pin the service that answers "what would this delete break?".

The write rule, decided deliberately:

* teachers **read** the subject list;
* only ``admin_sekolah`` (and ``super_admin``) create or delete one;
* a delete that would release a teacher assignment or strip an exam's subject
  is refused until the caller has been shown the counts.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g, redirect

from app.decorators.security import require_school_access
from app.services import subject_service

ROOT = Path(__file__).resolve().parents[2]
TEACHER_SRC = ROOT / "app" / "routes" / "teacher.py"
ADMIN_SRC = ROOT / "app" / "routes" / "admin_sekolah.py"
ADMIN_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "subjects.html"
TEACHER_TEMPLATE = ROOT / "app" / "templates" / "teacher" / "subjects.html"


def _decorators(source_path, func):
    """The whole decorator block above ``func``, as one string."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == func)
    if not node.decorator_list:
        return ""
    lines = source.splitlines()
    first = min(d.lineno for d in node.decorator_list)
    return "\n".join(lines[first - 1:node.lineno - 1])


def _body(source_path, func):
    """The function body text, from its ``def`` to the next top-level ``def``."""
    source = source_path.read_text(encoding="utf-8")
    start = source.index(f"def {func}(")
    tail = source[start + 1:]
    end = tail.find("\n@teacher_bp.route")
    if end == -1:
        end = tail.find("\ndef _")
    return tail if end == -1 else tail[:end]


# ── the role guard is on the routes ──────────────────────────────

class TestTheTeacherSubjectRoutesCarryARoleGuard:
    @pytest.mark.parametrize("func", [
        "teacher_subjects",
        "teacher_subject_create",
        "teacher_subject_delete",
    ])
    def test_not_login_required_alone(self, func):
        decorators = _decorators(TEACHER_SRC, func)
        assert "teacher_or_admin_required" in decorators, (
            f"{func} has no role guard: @login_required alone lets any member "
            f"of the school — a murid included — reach it.\n{decorators}"
        )
        assert "@login_required" not in decorators, (
            f"{func} fell back to a session-only guard"
        )

    def test_deleting_also_checks_the_school(self):
        decorators = _decorators(TEACHER_SRC, "teacher_subject_delete")
        assert 'require_school_access("subjects"' in decorators, (
            "the id in the URL decides which row is deleted; without the school "
            "check it reaches another school's subject"
        )


class TestOnlyAnAdminMayWrite:
    """The write rule, and the same one ``assignments()`` already applies."""

    @pytest.mark.parametrize("func", ["teacher_subject_create", "teacher_subject_delete"])
    def test_the_body_refuses_a_non_admin(self, func):
        body = _body(TEACHER_SRC, func)
        assert 'g.get("user_role") not in ("admin_sekolah", "super_admin")' in body, (
            f"{func} admits a teacher; a subject is school-wide, so its writes "
            f"belong to the admin"
        )


# ── the delete is shown before it happens ────────────────────────

class TestADeleteIsConfirmedAgainstItsBlastRadius:
    def test_the_service_answers_what_breaks(self):
        body = _body(TEACHER_SRC, "teacher_subject_delete")
        assert "subject_usage(" in body
        assert "usage_confirmation_needed(" in body

    def test_an_unconfirmed_delete_is_refused(self):
        body = _body(TEACHER_SRC, "teacher_subject_delete")
        assert 'request.form.get("confirm") != "1"' in body, (
            "without this the dialog is the only gate, and a bare POST skips it"
        )

    def test_the_admin_delete_takes_the_same_gate(self):
        body = _body(ADMIN_SRC, "admin_subject_delete")
        assert "subject_usage(" in body
        assert "usage_confirmation_needed(" in body
        assert 'request.form.get("confirm") != "1"' in body

    def test_the_dialogs_send_the_confirmation(self):
        for template in (ADMIN_TEMPLATE, TEACHER_TEMPLATE):
            html = template.read_text(encoding="utf-8")
            assert 'name="confirm" value="1"' in html, (
                f"{template.name} never sends the confirmation the route now wants"
            )

    def test_the_cards_name_what_is_attached(self):
        for template in (ADMIN_TEMPLATE, TEACHER_TEMPLATE):
            html = template.read_text(encoding="utf-8")
            assert "assignment_count" in html and "exam_count" in html, (
                f"{template.name} asks for a confirmation without saying what it costs"
            )


# ── the teacher page offers no write control to a teacher ────────

class TestTheTeacherPageIsReadOnlyForTeachers:
    def test_write_controls_are_gated_on_the_admin_role(self):
        html = TEACHER_TEMPLATE.read_text(encoding="utf-8")
        assert "can_manage" in html, (
            "the page has no role gate, so it offers a teacher a button the "
            "route refuses"
        )
        assert "g.user_role in ('admin_sekolah', 'super_admin')" in html

    def test_the_form_and_the_delete_button_sit_inside_it(self):
        html = TEACHER_TEMPLATE.read_text(encoding="utf-8")
        gate = html.split("{% set can_manage", 1)[1]
        create = gate.split("/teacher/subjects/new", 1)[0]
        assert "{% if can_manage %}" in create, (
            "the create form renders outside the admin gate"
        )


# ── the school check itself, exercised ───────────────────────────

class _Query:
    def __init__(self, row):
        self._row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._row)


class _Supabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _Query(self._rows.get(name))


def _school_app(school_id):
    app = Flask(__name__)

    @app.route("/subject/<subject_id>")
    @require_school_access("subjects", "subject_id")
    def subject(subject_id):
        return "ok"

    @app.before_request
    def _set():
        g.user_school_id = school_id

    return app


@pytest.fixture
def stub_supabase(monkeypatch):
    def _install(rows):
        monkeypatch.setattr("app.utils.auth.get_supabase",
                            lambda: _Supabase(rows))
    return _install


def test_the_schools_own_subject_is_allowed(stub_supabase):
    stub_supabase({"subjects": {"school_id": "school-A"}})
    assert _school_app("school-A").test_client().get("/subject/s-1").status_code == 200


def test_another_schools_subject_is_refused(stub_supabase):
    stub_supabase({"subjects": {"school_id": "school-B"}})
    r = _school_app("school-A").test_client().get("/subject/s-1")
    assert r.status_code == 403, (
        "a subject belonging to another school was reachable"
    )


def test_a_missing_subject_is_not_found(stub_supabase):
    stub_supabase({"subjects": None})
    assert _school_app("school-A").test_client().get("/subject/gone").status_code == 404


# ── what a delete would take, counted ────────────────────────────

class _CountingSupabase:
    def __init__(self, counts):
        self._counts = counts

    def table(self, name):
        return _CountQuery(self._counts.get(name, 0))


class _CountQuery:
    def __init__(self, count):
        self._count = count

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=[], count=self._count)


def test_usage_counts_both_tables():
    usage = subject_service.subject_usage(
        _CountingSupabase({"teacher_assignments": 4, "exams": 2}), "s-1")
    assert usage == {"assignments": 4, "exams": 2}


def test_a_subject_in_use_needs_confirmation():
    assert subject_service.usage_confirmation_needed({"assignments": 1, "exams": 0})
    assert subject_service.usage_confirmation_needed({"assignments": 0, "exams": 3})


def test_an_unused_subject_needs_none():
    assert not subject_service.usage_confirmation_needed({"assignments": 0, "exams": 0})


def test_the_message_names_both_numbers_in_both_languages():
    usage = {"assignments": 3, "exams": 1}
    id_text = subject_service.usage_message(usage, "id")
    en_text = subject_service.usage_message(usage, "en")
    assert "3" in id_text and "1" in id_text
    assert "3" in en_text and "1" in en_text
    assert id_text != en_text


def test_a_failing_count_does_not_crash_the_reader():
    """A count that cannot be read reports zero, so the caller decides."""
    class _Broken:
        def table(self, name):
            raise RuntimeError("database down")

    assert subject_service.subject_usage(_Broken(), "s-1") == {
        "assignments": 0, "exams": 0}
