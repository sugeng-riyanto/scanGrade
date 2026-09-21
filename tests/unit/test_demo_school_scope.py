"""A destructive reset clears the rows you name — never the whole platform.

Measured against the running code before this test existed
----------------------------------------------------------
Two routes on /super-admin looped a list of tables and deleted every row with a
``.delete().neq("id", "00000000-0000-0000-0000-000000000000")`` chain:

* ``/reset-demo-data`` — ten tables, and its button, dialog and docstring all
  said "demo";
* ``/file-management`` action ``reset_exam_data`` — four tables, including
  ``audit_logs``.

No row carries the nil UUID, so ``neq`` matched **all of them, in every school**,
and the server holds the service-role key, so RLS did not narrow it either. One
click on a button labelled "reset demo data" therefore emptied a real school's
students, teachers, classes, subjects, exams and submissions.

The rule these tests pin: a reset's scope is a **parameter** — school ids
resolved from an NPSN — and an empty list clears nothing rather than everything.

Two deliberate choices about the shape of these tests:

* The defect is detected **structurally**, by walking the AST for a ``neq`` call
  whose arguments are the column ``id`` and a nil-uuid string. A plain text scan
  would fire on the docstrings that *describe* the defect — including this one —
  and a test that cannot tell code from the comment explaining it is a test that
  gets deleted rather than fixed.
* The fake postgrest applies ``eq``/``in_`` for real and removes the rows it
  matched, because "the other school survived" is the whole claim; a fake that
  ignored the filter would pass whatever the code did.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import demo_schools, school_reset

ROOT = Path(__file__).resolve().parents[2]
SUPER_SRC = ROOT / "app" / "routes" / "super_admin.py"
DASHBOARD_TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"
FILE_TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "file_management.html"

DEMO_A = "school-demo-a"
REAL_B = "school-real-b"
NIL_UUID = "00000000-0000-0000-0000-000000000000"


# ── reading the code, not the prose about it ─────────────────────

def _nil_uuid_neqs(node):
    """``neq(<col>, "00000000…")`` calls inside this AST node, by line."""
    hits = []
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "neq"
                and len(call.args) >= 2):
            continue
        column, value = call.args[0], call.args[1]
        if (isinstance(column, ast.Constant) and column.value == "id"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.startswith("00000000")):
            hits.append(call.lineno)
    return hits


def _func_node(source, name):
    tree = ast.parse(source)
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _func_source(source, name):
    """Exactly this function's text.

    Slicing to the next route marker is too loose: the tail runs to the end of
    the file, so a line the mutation removed from *this* function was still
    found a hundred lines below in another one, and the assertion passed.
    """
    node = _func_node(source, name)
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def _clear_schools_table_args(node):
    """Every table name passed to a ``clear_schools`` call in this subtree."""
    names = set()
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "clear_schools"):
            continue
        for kw in call.keywords:
            for elt in getattr(kw.value, "elts", []):
                if isinstance(elt, ast.Constant):
                    names.add(elt.value)
    return names


def _deleted_tables(node):
    """Table names this node deletes from, read off the ``.table()`` in the chain."""
    names = set()
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "delete"):
            continue
        inner = call.func.value
        if (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "table"
                and inner.args
                and isinstance(inner.args[0], ast.Constant)):
            names.add(inner.args[0].value)
    return names


# ── an in-memory postgrest that really filters ───────────────────

class _Ne:
    """``neq`` needs its own filter, or the tautology could not be reproduced."""

    def __init__(self, value):
        self.value = value


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._mode = None
        self._filters = []

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, [value]))
        return self

    def neq(self, column, value):
        self._filters.append((column, _Ne(value)))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row):
        for column, wanted in self._filters:
            if isinstance(wanted, _Ne):
                if row.get(column) == wanted.value:
                    return False
            elif row.get(column) not in wanted:
                return False
        return True

    def execute(self):
        rows = self.db.tables.get(self.name, [])
        hit = [r for r in rows if self._matches(r)]
        if self._mode == "delete":
            self.db.deletes.append((self.name, tuple(self._filters)))
            self.db.tables[self.name] = [r for r in rows if not self._matches(r)]
        return SimpleNamespace(data=hit, count=len(hit))


class _DB:
    def __init__(self, tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.deletes = []

    def table(self, name):
        return _Query(self, name)

    def rows(self, name):
        return self.tables.get(name, [])


def _two_schools():
    """Every table the reset touches, populated for a demo school AND a real one.

    ``exams`` is the one table whose ids the exam-linked tables point at, so it
    carries the two exam ids rather than the generic ``<table>-demo`` naming.
    """
    tables = {t: [{"id": f"{t}-demo", "school_id": DEMO_A},
                  {"id": f"{t}-real", "school_id": REAL_B}]
              for t in school_reset.SCHOOL_TABLES}
    tables["exams"] = [{"id": "exam-demo", "school_id": DEMO_A},
                       {"id": "exam-real", "school_id": REAL_B}]
    for t in school_reset.EXAM_TABLES:
        tables[t] = [{"id": f"{t}-demo", "exam_id": "exam-demo"},
                     {"id": f"{t}-real", "exam_id": "exam-real"}]
    tables["schools"] = [{"id": DEMO_A, "npsn": "99887711"},
                         {"id": REAL_B, "npsn": "12345678"}]
    return _DB(tables)


# ── the scope is a parameter, and it is honoured ─────────────────

class TestClearingOneSchoolLeavesTheOther:
    @pytest.mark.parametrize("table", school_reset.SCHOOL_TABLES)
    def test_the_named_school_is_emptied(self, table):
        db = _two_schools()
        school_reset.clear_schools(db, [DEMO_A])
        survivor_id = "exam-real" if table == "exams" else f"{table}-real"
        assert db.rows(table) == [{"id": survivor_id, "school_id": REAL_B}]

    @pytest.mark.parametrize("table", school_reset.EXAM_TABLES)
    def test_the_exam_linked_tables_are_emptied(self, table):
        db = _two_schools()
        school_reset.clear_schools(db, [DEMO_A])
        assert db.rows(table) == [{"id": f"{table}-real", "exam_id": "exam-real"}]

    def test_the_unamed_school_survives_untouched(self):
        """The claim the old code could not make: a real school keeps its year."""
        db = _two_schools()
        school_reset.clear_schools(db, [DEMO_A])
        assert db.rows("students") == [{"id": "students-real", "school_id": REAL_B}]
        assert db.rows("submissions") == [{"id": "submissions-real", "exam_id": "exam-real"}]

    def test_no_delete_is_ever_unfiltered(self):
        """A filterless delete is the defect; every one of them must name a column."""
        db = _two_schools()
        school_reset.clear_schools(db, [DEMO_A])
        assert db.deletes, "nothing was deleted at all"
        for table, filters in db.deletes:
            assert filters, f"a delete on {table} carried no filter"

    def test_dependents_are_cleared_before_their_exam(self):
        """`exams` must not go first: the migrations declare no ON DELETE."""
        db = _two_schools()
        school_reset.clear_schools(db, [DEMO_A])
        order = [t for t, _ in db.deletes]
        assert order.index("exams") > max(
            order.index(t) for t in school_reset.EXAM_TABLES if t in order)


class TestAnEmptyScopeClearsNothing:
    """The fail-safe: a lookup that finds no school must not mean "all of them"."""

    @pytest.mark.parametrize("scope", [[], None, ["", None]])
    def test_nothing_is_deleted(self, scope):
        db = _two_schools()
        report = school_reset.clear_schools(db, scope)
        assert report["cleared"] == 0
        assert db.deletes == [], "an empty scope reached the database"

    def test_every_row_still_stands(self):
        db = _two_schools()
        school_reset.clear_schools(db, [])
        assert len(db.rows("students")) == 2
        assert len(db.rows("exams")) == 2


class TestResolvingTheScope:
    def test_school_ids_come_from_the_npsns(self):
        db = _two_schools()
        assert school_reset.school_ids_for_npsns(db, ["99887711"]) == [DEMO_A]

    def test_an_unknown_npsn_resolves_to_nothing(self):
        db = _two_schools()
        assert school_reset.school_ids_for_npsns(db, ["00000000"]) == []

    def test_exam_ids_are_the_schools_own(self):
        db = _two_schools()
        assert school_reset.exam_ids_for_schools(db, [DEMO_A]) == ["exam-demo"]

    def test_exam_ids_for_no_school_are_empty(self):
        db = _two_schools()
        assert school_reset.exam_ids_for_schools(db, []) == []


# ── which schools count as "demo" ────────────────────────────────

class TestTheDemoSchoolsAreTheSeeds:
    def test_the_list_comes_from_the_seed(self):
        from manage import DEMO_SCHOOLS
        assert demo_schools.demo_school_npsns() == [
            str(s["npsn"]) for s in DEMO_SCHOOLS if s.get("npsn")]

    def test_the_fallback_is_in_step_with_the_seed(self):
        """The fallback is a second copy of the list; drift would mean a reset
        that silently skips a demo school."""
        from manage import DEMO_SCHOOLS
        assert sorted(demo_schools.FALLBACK_NPSNS) == sorted(
            str(s["npsn"]) for s in DEMO_SCHOOLS if s.get("npsn"))


# ── the routes use the scope ─────────────────────────────────────

class TestTheDemoResetIsScoped:
    @pytest.fixture
    def body(self):
        return SUPER_SRC.read_text(encoding="utf-8")

    def test_it_resolves_the_demo_schools_and_clears_them(self, body):
        text = _func_source(body, "reset_demo_data")
        assert "demo_school_npsns()" in text
        assert "school_ids_for_npsns(" in text, (
            "the scope must come from the demo NPSNs, not from every school"
        )
        assert "clear_schools(" in text

    def test_it_does_not_resolve_every_school(self, body):
        """A lookup for `schools` with no NPSN filter is the unscoped reset again."""
        text = _func_source(body, "reset_demo_data")
        assert 'table("schools").select' not in text, (
            "the route is reading the schools table itself instead of resolving "
            "the demo NPSNs"
        )

    def test_it_no_longer_wipes_by_tautology(self, body):
        assert not _nil_uuid_neqs(_func_node(body, "reset_demo_data")), (
            "the nil-uuid predicate matches every row; it is the defect itself"
        )

    def test_it_refuses_when_the_demo_schools_are_absent(self, body):
        """A lookup that found nothing must not fall through to a full wipe."""
        text = _func_source(body, "reset_demo_data")
        assert "if not school_ids:" in text
        assert "404" in text


class TestTheExamResetIsPerSchool:
    @pytest.fixture
    def node(self):
        return _func_node(SUPER_SRC.read_text(encoding="utf-8"), "file_management")

    def test_it_takes_an_npsn(self):
        text = SUPER_SRC.read_text(encoding="utf-8")
        branch = text.split('action == "reset_exam_data"', 1)[1][:1200]
        assert 'request.form.get("npsn"' in branch, (
            "the exam reset still has no school to scope itself to"
        )
        assert "school_ids_for_npsns(" in branch
        assert "clear_schools(" in branch

    def test_it_no_longer_wipes_by_tautology(self, node):
        assert not _nil_uuid_neqs(node)

    def test_it_never_erases_the_platform_audit_log(self, node):
        assert "audit_logs" not in _deleted_tables(node), (
            "audit_logs has no school_id, so it cannot be scoped — and it is the "
            "record of who did what, including this deletion"
        )

    def test_the_reset_primitive_is_never_handed_the_audit_log(self, node):
        """The delete happens *inside* `clear_schools`, so the table list the
        route passes it is the thing that has to be checked.

        A literal `.table("audit_logs").delete()` chain in the route was the
        obvious shape; naming it in `school_tables=` would do the same damage
        while looking like a scoped call.
        """
        assert "audit_logs" not in _clear_schools_table_args(node), (
            "clear_schools was handed audit_logs, so it would be deleted "
            "unscoped — the table has no school_id to filter on"
        )

    def test_the_per_school_button_uses_the_same_primitive(self):
        text = SUPER_SRC.read_text(encoding="utf-8")
        branch = text.split('action == "delete_school_exam_data"', 1)[1][:1200]
        assert "clear_schools(" in branch, (
            "the per-school button deleted two of the exam's four dependents"
        )


def test_the_tautology_is_gone_from_the_whole_app():
    """It only ever had two homes, and both were destructive."""
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        try:
            node = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:                       # a stray non-module file
            continue
        if _nil_uuid_neqs(node):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "a delete filtered on a nil uuid matches every row in the table:\n  "
        + "\n  ".join(offenders)
    )


# ── the copy matches what the button now does ────────────────────

class TestTheCopyNamesTheScope:
    def test_the_dashboard_dialog_says_demo_schools_only(self):
        html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
        assert "SEKOLAH DEMO saja" in html, (
            "the dialog must say the real schools are not touched"
        )
        assert "Sekolah asli TIDAK tersentuh" in html

    def test_the_file_panel_asks_for_a_school(self):
        html = FILE_TEMPLATE.read_text(encoding="utf-8")
        # A *picker*, not merely the string: a hidden `name="npsn"` would satisfy
        # a substring check while offering the operator no school to choose.
        assert '<select name="npsn" required' in html, (
            "the exam reset has no school picker, so it cannot be scoped"
        )
        assert "Reset Semua" not in html, (
            "the platform-wide button must be gone, not merely reworded"
        )
