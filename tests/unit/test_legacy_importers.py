"""The two importers under `/admin` are creators too, and now inherit the creator's rules.

`/admin-sekolah/import` is the importer the app actually uses, and it goes through
`create_student_account` / `create_teacher_account`, which check the identifier
globally, stamp the one-time-password rule and delete the auth user if a write fails.
The older pair in `app/routes/admin.py` wrote the same rows in the same order — auth
user, then `profiles` — and shared the hole: the failing write is the *last* one, so a
failure left an account that can sign in and belongs to nothing.

Two further gaps were pinned here as a *decision* rather than fixed, and this file now
closes both:

* **no role row on success.** A profile with no `students`/`teachers` row can sign in
  and appears in no class list — the same half-made account, reached by success instead
  of failure. The old note said closing it "needs a `school_id`, which `/admin` has no
  source for"; that was wrong. `g.user_school_id` is the server's own reading of the
  session, and the module already uses it (`admin.py` scopes its school reads with it).
  So the school comes from the session, never from the sheet.
* **no retry, and no one-time-password stamp.** Both arrived with the shared creator,
  which is the point of routing through it rather than copying its body.

What is deliberately *not* delegated: the sheet-facing concerns. The school check, the
per-row error text and the email fallback stay here, because the modern importer has a
different sheet, different columns and its own duplicate pre-checks (NIP/NUPTK/NISN),
and they must not be dragged into a shared primitive that a fourth caller will copy.
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

SCHOOL = "12ab34cd-0000-0000-0000-000000000001"


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

    def upsert(self, payload):
        # The shared creator upserts; the legacy code inserted. Both are the same
        # write for this fake, and supporting only one would have made the routing
        # look like a crash instead of a save.
        self.payload = payload
        return self

    def execute(self):
        if self.table in self.store.fail:
            raise RuntimeError(f"permission denied for table {self.table}")
        rows = self.store.tables.setdefault(self.table, [])
        # Upsert replaces the row with the same id; insert appends. The creator
        # upserts the profile and the role row, keyed by the auth user's id.
        key = self.payload.get("id") if isinstance(self.payload, dict) else None
        if key is not None:
            rows[:] = [r for r in rows if r.get("id") != key]
        rows.append(dict(self.payload))
        return _Resp([self.payload])


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        return self.store.create_user(attributes)

    def delete_user(self, uid, should_soft_delete=False):
        self.store.delete_user(uid)


class FakeSupabase:
    def __init__(self, fail=()):
        self.tables = {}
        self.created_users = []
        self.deleted_users = []
        self.fail = set(fail)
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


def run_import(view, monkeypatch, fake, ws, path, school=SCHOOL):
    monkeypatch.setattr(admin_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(admin_module, "_gen_password", lambda *a, **k: "pw")

    app = _app()
    with app.test_request_context(
        path, method="POST",
        data={"file": (io.BytesIO(_xlsx(ws)), "import.xlsx")},
        content_type="multipart/form-data",
    ):
        # The session's school, exactly as `auth.py` sets it. Never the sheet's.
        from flask import g
        g.user_school_id = school
        result = view.__wrapped__()
    return result.get_json()


# ── the rollback ─────────────────────────────────────────────────────────────

def test_the_legacy_student_import_rolls_back_a_half_created_account(monkeypatch):
    fake = FakeSupabase(fail=["profiles"])

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi Santoso", "12345678", "", ""]]),
                      "/admin/students/import")

    assert body["created"] == 0
    assert len(body["errors"]) == 1
    assert len(fake.created_users) == 1, "the auth user was created before the failure"
    assert fake.deleted_users == [fake.created_users[0]["id"]], "it must be rolled back"
    assert not fake.tables.get("profiles"), "no profile may survive it"


def test_the_legacy_teacher_import_rolls_back_a_half_created_account(monkeypatch):
    fake = FakeSupabase(fail=["profiles"])

    body = run_import(admin_module.import_teachers, monkeypatch, fake,
                      _teachers_sheet([["Sinta Dewi", "0812"]]),
                      "/admin/teachers/import")

    assert body["created"] == 0
    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert not fake.tables.get("profiles")


def test_the_role_row_is_rolled_back_too(monkeypatch):
    """The writer that fails may be the role row rather than the profile.

    `teachers` is the last write, so it is the likeliest to be rejected — and the
    undo has to reach the auth user from there, because deleting it is what cascades
    the profile and the role row away.
    """
    fake = FakeSupabase(fail=["teachers"])

    body = run_import(admin_module.import_teachers, monkeypatch, fake,
                      _teachers_sheet([["Sinta Dewi", "0812"]]),
                      "/admin/teachers/import")

    assert body["created"] == 0
    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert not fake.tables.get("teachers")


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


def test_a_transient_create_failure_is_shown_in_the_creators_words(monkeypatch):
    """GoTrue's generic database text must not be what the operator reads.

    The shared creator retries the transient create and, when every attempt is
    transient, raises `AccountNotCreated` carrying a `user_message`. The row error
    has to show *that*, because "Database error creating new user" names nothing a
    school admin can act on — the same defect the retry itself was written for.
    """
    class Transient(FakeSupabase):
        def create_user(self, attributes):
            raise RuntimeError("Database error creating new user")

    fake = Transient()

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi", "12345678", "", ""]]),
                      "/admin/students/import")

    assert body["created"] == 0
    assert len(body["errors"]) == 1
    assert "Database error creating new user" not in body["errors"][0], (
        "the operator is shown GoTrue's raw text for a transient create")
    assert "akun" in body["errors"][0].lower(), (
        "the row error dropped the creator's own sentence")
    assert fake.deleted_users == [], (
        "a transient create that never landed has nothing to roll back")


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


# ── the success path, with the gap closed ────────────────────────────────────

def test_the_legacy_student_import_writes_a_profile(monkeypatch):
    fake = FakeSupabase()

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi Santoso", "12345678", "99", "0812"]]),
                      "/admin/students/import")

    assert body["created"] == 1 and body["errors"] == []
    profile = fake.tables["profiles"][0]
    assert profile["role"] == "murid"
    assert profile["nisn"] == "12345678"
    assert profile["nis"] == "99"
    assert profile["phone"] == "0812"
    assert fake.deleted_users == []


def test_the_legacy_student_import_writes_the_students_row(monkeypatch):
    """The gap this file used to pin: a profile with no `students` row can sign in
    and appears in no class list. It is written now, with the session's school."""
    fake = FakeSupabase()

    run_import(admin_module.import_students, monkeypatch, fake,
               _students_sheet([["Budi", "12345678", "", ""]]),
               "/admin/students/import")

    assert "students" in fake.tables, (
        "the legacy importer still writes no students row, so a \"created\" pupil "
        "belongs to no school and no class list")
    row = fake.tables["students"][0]
    assert row["school_id"] == SCHOOL
    assert row["nisn"] == "12345678"
    assert row["id"] == fake.tables["profiles"][0]["id"], (
        "the role row must be keyed by the auth user's id — `students.id` references "
        "`profiles.id`")
    assert row["status"] == "active"


def test_the_legacy_teacher_import_writes_the_teachers_row(monkeypatch):
    fake = FakeSupabase()

    body = run_import(admin_module.import_teachers, monkeypatch, fake,
                      _teachers_sheet([["Sinta Dewi", "0812"]]),
                      "/admin/teachers/import")

    assert body["created"] == 1 and body["errors"] == []
    assert "teachers" in fake.tables, (
        "a \"created\" teacher is in no teacher list and cannot be assigned a class")
    row = fake.tables["teachers"][0]
    assert row["school_id"] == SCHOOL
    assert row["id"] == fake.tables["profiles"][0]["id"]


def test_the_imported_account_is_told_to_replace_its_password(monkeypatch):
    """The rule the shared creator owns: the password a school admin sets is a
    one-time one. Forgetting it fails silently — the account simply never asks."""
    fake = FakeSupabase()

    run_import(admin_module.import_students, monkeypatch, fake,
               _students_sheet([["Budi", "12345678", "", ""]]),
               "/admin/students/import")

    profile = fake.tables["profiles"][0]
    assert profile.get("must_change_password") is True, (
        "the imported account carries no must-change flag, so it can keep the "
        "generated password for ever")
    assert profile.get("email"), "the flag is mirrored onto the profile's email"


def test_the_school_comes_from_the_session_and_not_the_sheet(monkeypatch):
    """A sheet cannot name a school: it has no column for one, and if it did, a
    hand-made upload would be a way to place accounts in somebody else's NPSN."""
    fake = FakeSupabase()

    run_import(admin_module.import_students, monkeypatch, fake,
               _students_sheet([["Budi", "12345678", "", ""]]),
               "/admin/students/import")

    assert fake.tables["profiles"][0]["school_id"] == SCHOOL
    assert fake.tables["students"][0]["school_id"] == SCHOOL


def test_a_session_with_no_school_creates_nothing(monkeypatch):
    """`super_admin` has no school of its own. Writing the row anyway would make an
    unscoped account — the very shape this change removes — so the sheet is refused
    with a sentence instead."""
    fake = FakeSupabase()

    body = run_import(admin_module.import_students, monkeypatch, fake,
                      _students_sheet([["Budi", "12345678", "", ""]]),
                      "/admin/students/import", school=None)

    assert body["created"] == 0
    assert body["errors"], "a refused sheet must say why"
    assert fake.created_users == [], "no auth user may be made without a school"
    assert fake.tables == {}


# ── structural guards ────────────────────────────────────────────────────────

def _admin_source() -> str:
    return (ROOT / "app" / "routes" / "admin.py").read_text(encoding="utf-8-sig")


def test_the_legacy_importers_no_longer_create_users_themselves():
    """Walk the module: not one `create_user` may remain.

    A behavioural test only covers the importers that exist today; this is what
    catches a third one copied from them, writing the auth user by hand and
    forgetting the retry, the rollback or the password stamp.
    """
    tree = ast.parse(_admin_source())

    offenders = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_user"
    ]
    assert not offenders, (
        "admin.py creates auth users itself instead of going through "
        "app/services/account_creation.py, so it inherits none of its rules: "
        + ", ".join(offenders))


def test_both_importers_route_through_the_shared_creator():
    source = _admin_source()
    assert "account_creation.create_account(" in source, (
        "neither legacy importer goes through the shared account creator")
    assert source.count("account_creation.create_account(") >= 2, (
        "one of the two importers still creates its account by hand")


def test_the_legacy_importers_pass_the_session_school():
    """"The module has no source for a school" was the reason the gap stayed open.
    It has one, and the importer must use it."""
    source = _admin_source()
    assert "user_school_id" in source, (
        "the importer does not read the session's school, so the role row it now "
        "writes would have nowhere to belong")


def test_no_importer_in_admin_calls_the_private_undo():
    """`_discard_partial_account` was made public; calling the old private name
    would now be a NameError at the worst moment."""
    source = _admin_source()

    assert not re.search(r"(?<!\w\.)_discard_partial_account\s*\(", source)


def test_the_undo_alias_that_existed_only_for_these_importers_is_gone():
    """`discard_partial_account` was a public re-export whose only stated reason was
    this file. With the routing done, an alias nothing calls is a second name for one
    behaviour — exactly what the unification removed."""
    for path in ("app/services/student_import.py", "app/services/teacher_import.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "discard_partial_account" not in source, (
            f"{path} still re-exports an undo alias that nothing calls")
