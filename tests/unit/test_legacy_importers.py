"""The two importers under `/admin` must not leave an account behind either.

`/admin-sekolah/import` is the importer the app actually uses, and it goes through
`create_student_account` / `create_teacher_account`, which check the identifier
globally and delete the auth user if a write fails. The older pair in
`app/routes/admin.py` writes the same rows in the same order — auth user, then
`profiles` — and had the same hole: the failing write is the *last* one, so a
failure left an account that can sign in and belongs to nothing.

The blanket case is easy to miss because it only shows up on a failure, which is
why the last test here is structural rather than behavioural: it walks the module
and requires every `create_user` call to sit in a `try` whose handlers undo it. A
future importer copied from the ones above it fails that walk.

Known gap, pinned below rather than hidden: neither legacy importer writes the
role-specific row (`students` / `teachers`) even when it *succeeds*, so a "created"
student has no class membership. That needs a `school_id`, which this legacy panel
has no source for, so it is a decision rather than a bug fix.
"""
import ast
import io
import re
from pathlib import Path

import pytest
from openpyxl import Workbook

from tests.conftest import build_app
from app.routes import admin as admin_module

ROOT = Path(__file__).resolve().parents[2]


# ── a fake that can fail the way the database can ────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.table == "profiles" and self.store.fail_profiles_insert:
            raise RuntimeError("permission denied for table profiles")
        self.store.tables.setdefault(self.table, []).append(dict(self.payload))
        return _Resp([self.payload])


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        return self.store.create_user(attributes)

    def delete_user(self, uid, should_soft_delete=False):
        self.store.delete_user(uid)


class FakeSupabase:
    def __init__(self, fail_profiles_insert=False):
        self.tables = {}
        self.created_users = []
        self.deleted_users = []
        self.fail_profiles_insert = fail_profiles_insert
        self._next = 0
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

    def table(self, name):
        return _Query(self, name)

    def create_user(self, attributes):
        self._next += 1
        uid = f"user-{self._next}"
        self.created_users.append({"id": uid, **attributes})
        return type("Created", (), {"user": type("U", (), {"id": uid})()})()

    def delete_user(self, uid, should_soft_delete=False):
        """Hard delete, cascading the way the foreign keys do."""
        self.deleted_users.append(uid)
        for table in self.tables.values():
            table[:] = [r for r in table if r.get("id") != uid]


# ── driving the real views ───────────────────────────────────────────────────

def _students_sheet(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["Nama", "NISN", "NIS", "No. HP"])
    for row in rows:
        ws.append(row)
    return ws


def _teachers_sheet(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["Nama", "No. HP"])
    for row in rows:
        ws.append(row)
    return ws


def _xlsx(ws) -> bytes:
    buf = io.BytesIO()
    ws.parent.save(buf)
    return buf.getvalue()


_APP = None


def _app():
    """One app for the file: every call here would pay for the build again."""
    global _APP
    if _APP is None:
        _APP = build_app("testing")
    return _APP


def run_import(view, monkeypatch, fake, ws, path):
    monkeypatch.setattr(admin_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(admin_module, "_gen_password", lambda *a, **k: "pw")

    app = _app()
    with app.test_request_context(
        path, method="POST",
        data={"file": (io.BytesIO(_xlsx(ws)), "import.xlsx")},
        content_type="multipart/form-data",
    ):
        result = view.__wrapped__()
    return result.get_json()


# ── the rollback ─────────────────────────────────────────────────────────────

def test_the_legacy_student_import_rolls_back_a_half_created_account(monkeypatch):
    fake = FakeSupabase(fail_profiles_insert=True)

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi Santoso", "12345678", "", ""]]),
                      "/admin/students/import")

    assert body["created"] == 0
    assert len(body["errors"]) == 1
    assert len(fake.created_users) == 1, "the auth user was created before the failure"
    assert fake.deleted_users == [fake.created_users[0]["id"]], "it must be rolled back"
    assert not fake.tables.get("profiles"), "no profile may survive it"


def test_the_legacy_teacher_import_rolls_back_a_half_created_account(monkeypatch):
    fake = FakeSupabase(fail_profiles_insert=True)

    body = run_import(admin_module.import_teachers, monkeypatch, fake,
                      _teachers_sheet([["Sinta Dewi", "0812"]]),
                      "/admin/teachers/import")

    assert body["created"] == 0
    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert not fake.tables.get("profiles")


def test_a_bad_row_does_not_stop_the_good_ones(monkeypatch):
    class SecondRowFails(FakeSupabase):
        def __init__(self):
            super().__init__()
            self.seen = 0

        def create_user(self, attributes):
            self.seen += 1
            if self.seen == 2:
                raise RuntimeError("email already registered")
            return super().create_user(attributes)

    fake = SecondRowFails()

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi", "11111111", "", ""],
                                       ["Ani", "22222222", "", ""],
                                       ["Cita", "33333333", "", ""]]),
                      "/admin/students/import")

    assert body["created"] == 2, "one refused row must not abandon the sheet"
    assert len(body["errors"]) == 1
    # The row that never got an auth user has nothing to roll back, and rolling
    # back nothing must not delete somebody else's account.
    assert len(fake.created_users) == 2
    assert fake.deleted_users == []


def test_a_missing_file_writes_nothing(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(admin_module, "get_supabase", lambda: fake)

    app = _app()
    with app.test_request_context("/admin/students/import", method="POST",
                                  data={}, content_type="multipart/form-data"):
        body, status = admin_module.import_students.__wrapped__()

    assert status == 400
    assert fake.created_users == []
    assert fake.deleted_users == []


# ── the success path, including the gap it does not close ────────────────────

def test_the_legacy_student_import_writes_a_profile(monkeypatch):
    fake = FakeSupabase()

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi Santoso", "12345678", "99", "0812"]]),
                      "/admin/students/import")

    assert body["created"] == 1 and body["errors"] == []
    profile = fake.tables["profiles"][0]
    assert profile["role"] == "murid"
    assert profile["nisn"] == "12345678"
    assert profile["phone"] == "0812"
    assert fake.deleted_users == []


def test_the_legacy_student_import_does_not_write_the_students_row(monkeypatch):
    """Pins a known gap so it is not mistaken for covered behaviour.

    A profile with no `students` row can sign in and appears in no class list —
    the same "half-created account" this file is about, reached by success
    instead of failure. Closing it needs a `school_id`, which `/admin` has no
    source for; the `/admin-sekolah` importer does it properly.
    """
    fake = FakeSupabase()

    run_import(admin_module.import_students, monkeypatch, fake,
               _students_sheet([["Budi", "12345678", "", ""]]),
               "/admin/students/import")

    assert "profiles" in fake.tables
    assert "students" not in fake.tables, (
        "if this starts failing, the legacy importer grew a students row — "
        "update this test and the note above")


# ── structural guard ─────────────────────────────────────────────────────────

def _undo_calls(handler: ast.ExceptHandler) -> list[str]:
    return [
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
    ]


def test_every_account_creation_in_admin_rolls_back_on_failure():
    """Walk the module: each `create_user` must be undone by its `except`.

    A behavioural test only covers the importers that exist today. This is what
    catches the next one copied from them without the rollback.
    """
    source = (ROOT / "app" / "routes" / "admin.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    checked = 0
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(func):
            if not isinstance(node, ast.Try):
                continue
            creates = [
                call for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "create_user"
                # only its own body, not a nested try's
                and not any(isinstance(inner, ast.Try) for inner in ast.walk(call))
            ]
            if not creates:
                continue
            checked += 1
            undoes = [name for handler in node.handlers for name in _undo_calls(handler)]
            assert any(name.startswith("discard") for name in undoes), (
                f"{func.name}: create_user is not undone on failure — an account "
                f"could be left behind (handlers called {undoes})")

    assert checked >= 2, "expected both legacy importers to be checked"


def test_no_importer_in_admin_calls_the_private_undo():
    """`_discard_partial_account` was made public; calling the old private name
    would now be a NameError at the worst moment."""
    source = (ROOT / "app" / "routes" / "admin.py").read_text(encoding="utf-8-sig")

    assert not re.search(r"(?<![\w.])_discard_partial_account\s*\(", source)
