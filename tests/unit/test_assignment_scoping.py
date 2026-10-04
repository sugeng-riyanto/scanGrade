"""A teacher may only build a paper for the class and subject they teach.

`teacher_assignments` has existed since migration 010, but it was consulted in
exactly one place: the *dropdown* on `/teacher/exams/new`. The list the form
offered was scoped, and the write was not — so a guru who edited the request, or
whose assignment list came back empty, could attach a paper to any class in the
school. Worse, the empty case fell *open*: when a teacher had no assignments the
page filled the dropdowns with every class and subject the school had, which
looks like a permission rather than a missing row.

Two properties are pinned here:

* the rule — a teacher is limited to the (class, subject) pairs they actively
  hold, a lookup that fails refuses the write instead of widening it, and an
  admin is not limited at all;
* the wiring — both exam write paths ask the rule, and the dropdown fallback is
  gated on the admin role rather than run for everyone.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services import assignments

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Sb:
    """Only enough PostgREST to answer the assignment lookup."""

    def __init__(self, rows, boom=False):
        self.rows = rows
        self.boom = boom

    def table(self, name):
        assert name == "teacher_assignments", name
        if self.boom:
            raise RuntimeError("supabase is down")
        return _Query(self.rows)


# ── the rule ─────────────────────────────────────────────────────────────────

def _row(class_id, subject_id, status="active", teacher_id="t-1", school_id="sc-1"):
    row = {"class_id": class_id, "subject_id": subject_id,
           "teacher_id": teacher_id, "school_id": school_id}
    if status is not None:
        row["status"] = status
    return row


def test_a_teacher_may_build_for_their_own_pair():
    sb = _Sb([_row("c-1", "s-1")])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", ["c-1"]) == []


def test_a_colleagues_pair_is_refused():
    sb = _Sb([_row("c-1", "s-1")])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", ["c-2"]) == ["c-2"]


def test_the_same_class_under_a_different_subject_is_refused():
    """The pair is the unit: teaching Physics in 8A is not teaching Maths in 8A."""
    sb = _Sb([_row("c-1", "s-physics")])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-maths", ["c-1"]) == ["c-1"]


def test_an_inactive_assignment_no_longer_allows_the_write():
    """A teacher who stopped teaching the pair keeps the history, not the door."""
    sb = _Sb([_row("c-1", "s-1", status="inactive")])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", ["c-1"]) == ["c-1"]


def test_a_row_without_a_status_column_is_treated_as_active():
    """Migration 045 adds `status`; a row written before it must not vanish."""
    sb = _Sb([_row("c-1", "s-1", status=None)])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", ["c-1"]) == []


def test_lookup_failure_refuses_rather_than_widens():
    """A blip must not hand a teacher the whole school."""
    sb = _Sb([], boom=True)

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", ["c-1"]) == ["c-1"]


def test_no_classes_means_nothing_to_check():
    """A draft with no class ticked is not an over-reach."""
    sb = _Sb([])

    assert assignments.unassigned_class_ids(sb, "t-1", "sc-1", "s-1", []) == []


def test_admin_roles_are_not_scoped():
    for role in ("admin_sekolah", "super_admin", "admin"):
        assert not assignments.is_scoped_role(role), role
    # `principal` and `vice_principal` joined the scoped set when the school asked
    # for its officials to be assignable as subject teachers: a head of school who
    # teaches is held to the same pair rule as a guru, not exempted from it.
    for role in ("guru", "teacher", "principal", "vice_principal"):
        assert assignments.is_scoped_role(role), role
    for role in ("murid", "admin_sekolah", "super_admin"):
        assert not assignments.is_scoped_role(role), role


# ── the wiring ───────────────────────────────────────────────────────────────

def _source():
    return TEACHER.read_text(encoding="utf-8")


def test_the_new_exam_write_asks_the_rule():
    src = _source()
    call = src.split("def exam_form(")[1].split("\ndef ")[0]
    assert "unassigned_class_ids(" in call, (
        "POST /teacher/exams/new attaches class_ids without asking the assignment rule")


def test_a_programmatic_refusal_is_a_403_not_a_redirect():
    """A JSON caller must be told 'no', not sent to a page that looks fine."""
    src = _source()
    call = src.split("def exam_form(")[1].split("\ndef ")[0]
    guard = call.split("unassigned_class_ids(")[1][:400]
    assert "403" in guard, "the write refuses by redirecting even when asked as JSON"


def test_the_edit_write_asks_the_rule():
    src = _source()
    call = src.split("def exam_detail(")[1].split("\ndef ")[0]
    assert "unassigned_class_ids(" in call, (
        "POST /teacher/exams/<id> re-attaches class_ids without asking the assignment rule")


# ── the migration ────────────────────────────────────────────────────────────

MIGRATION = ROOT / "supabase" / "migrations" / "045_assignment_school_year_status.sql"


def test_the_migration_is_additive():
    """Published marks live beside these rows; the migration must only add."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration is destructive"
    assert not re.search(r"DELETE\s+FROM", sql), "the migration deletes rows"
    assert "ADD COLUMN IF NOT EXISTS school_year" in sql
    assert "ADD COLUMN IF NOT EXISTS status" in sql
    # The default is what keeps every existing row meaningful without a backfill.
    assert "DEFAULT 'active'" in sql


def test_the_rule_reads_the_year_and_status_the_migration_adds():
    """The column the app filters on and the column the migration adds must match."""
    sql = MIGRATION.read_text(encoding="utf-8")
    source = (ROOT / "app" / "services" / "assignments.py").read_text(encoding="utf-8")
    for column in ("school_year", "status"):
        assert column in sql
        assert column in source


def test_the_dropdown_fallback_is_gated_on_the_admin_role():
    """A whole-school read is the *admin's* privilege, not a guru's default.

    The fallback lives in `_builder_scope` (both builder doors share it). The
    gate has to come **before** the school-wide reads: a rewrite that read first
    and asked later would offer every class and subject to an unassigned teacher.
    """
    src = _source()
    body = src.split("def _builder_scope(")[1].split("\ndef ")[0]
    assert "is_scoped_role" in body, (
        "the empty-assignment fallback is unconditional — an unassigned teacher "
        "is offered every class and subject in the school")
    gate = body.index("is_scoped_role")
    assert body.index('supabase.table("subjects")') > gate, (
        "the whole-school subject read is not gated on the admin role")
    assert body.index('supabase.table("classes")') > gate, (
        "the whole-school class read is not gated on the admin role")
