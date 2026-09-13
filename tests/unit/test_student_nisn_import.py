"""Student import: the NISN check must be global, and a failed row must leave nothing.

Two defects, both from the live schema.

``students.nisn`` is ``TEXT UNIQUE`` across the **whole** database (migration
007), but every importer checked it with ``.eq("school_id", school_id)`` — a
different question. A NISN already used at another school passed the check and
was then rejected by the index. The Excel importer had no check at all.

The account is written in three steps, in an order the foreign keys force:
``auth user -> profiles -> students``. The failing step is the *last* one, so the
failure left an auth user and a profile with no ``students`` row: an account that
can sign in but appears in no class list. The helper now checks the NISN globally
first and deletes the auth user if anything still fails, which cascades.

``FakeSupabase`` reproduces the global unique index and the delete cascade, so a
regression fails the way the database fails.
"""
import io
import re
from pathlib import Path

import pytest

from app.errors import ValidationError
from app.services import student_import as si
from app.services.student_import import create_student_account, find_student_by_nisn

SCHOOL_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SCHOOL_B = "bbbbbbbb-0000-0000-0000-00000000000b"


# ── fake ────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.payload = None
        self.op = "select"
        self._maybe_single = False
        self._limit = None

    # -- builder -------------------------------------------------
    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, n, *_a, **_k):
        self._limit = n
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def upsert(self, payload):
        self.op = "upsert"
        self.payload = payload
        return self

    def _rows(self):
        return [
            dict(r) for r in self.store.tables.get(self.table, [])
            if all(str(r.get(c)) == str(v) for c, v in self.filters)
        ]

    def execute(self):
        if self.op == "upsert":
            return _Resp([self.store.apply_upsert(self.table, self.payload)])
        rows = self._rows()
        if self._limit:
            rows = rows[: self._limit]
        if self._maybe_single:
            # postgrest returns None when nothing matched
            return None if not rows else _Resp(rows[0])
        return _Resp(rows)


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        return self.store.create_user(attributes)

    def delete_user(self, uid, should_soft_delete=False):
        self.store.delete_user(uid)


class FakeSupabase:
    def __init__(self, students=(), profiles=(), classes=(), fail_students_insert=None):
        self.tables = {
            "students": [dict(r) for r in students],
            "profiles": [dict(r) for r in profiles],
            "classes": [dict(r) for r in classes],
        }
        self.created_users = []
        self.deleted_users = []
        self.fail_students_insert = fail_students_insert
        self._next = 0
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

    # -- helpers -------------------------------------------------
    def table(self, name):
        self.tables.setdefault(name, [])
        return _Query(self, name)

    def create_user(self, attributes):
        self._next += 1
        uid = f"user-{self._next}"
        self.created_users.append({"id": uid, **attributes})
        return type("Created", (), {"user": type("U", (), {"id": uid})()})()

    def delete_user(self, uid, should_soft_delete=False):
        """Hard delete, cascading the way the foreign keys do."""
        self.deleted_users.append(uid)
        for t in ("profiles", "students"):
            self.tables[t] = [r for r in self.tables[t] if r.get("id") != uid]

    def apply_upsert(self, table, payload):
        row = dict(payload)
        if table == "students":
            if self.fail_students_insert:
                raise RuntimeError(self.fail_students_insert)
            nisn = row.get("nisn")
            for existing in self.tables["students"]:
                if str(existing.get("nisn")) == str(nisn) and existing.get("id") != row.get("id"):
                    raise RuntimeError(
                        'duplicate key value violates unique constraint "students_nisn_key"'
                    )
        for existing in self.tables[table]:
            if existing.get("id") == row.get("id"):
                existing.update(row)
                return dict(existing)
        self.tables[table].append(row)
        return dict(row)


def make_student(uid, nisn, school):
    return {"id": uid, "nisn": nisn, "school_id": school, "status": "active"}


# ── the global check ────────────────────────────────────────────────────────

def test_find_student_by_nisn_ignores_school():
    fake = FakeSupabase(students=[make_student("u1", "12345678", SCHOOL_B)])

    found = find_student_by_nisn(fake, "12345678")

    assert found is not None and found["id"] == "u1"


def test_find_student_by_nisn_returns_none_on_a_miss():
    assert find_student_by_nisn(FakeSupabase(), "99999999") is None


def test_a_nisn_used_at_another_school_is_refused():
    """The scoped check let this through; the global index then rejected it."""
    fake = FakeSupabase(students=[make_student("u1", "12345678", SCHOOL_B)])

    with pytest.raises(ValidationError) as exc:
        create_student_account(
            fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
            email="budi@example.test", password="pw",
        )

    assert "sekolah lain" in exc.value.user_message
    assert fake.created_users == [], "no account may be started for a taken NISN"
    assert fake.tables["profiles"] == []
    assert len(fake.tables["students"]) == 1


def test_a_nisn_in_the_same_school_is_refused_and_says_so():
    fake = FakeSupabase(students=[make_student("u1", "12345678", SCHOOL_A)])

    with pytest.raises(ValidationError) as exc:
        create_student_account(
            fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
            email="budi@example.test", password="pw",
        )

    assert "sekolah ini" in exc.value.user_message
    assert fake.created_users == []


# ── no orphans ──────────────────────────────────────────────────────────────

def test_a_failed_students_write_leaves_no_auth_user():
    """A race: the NISN was free at check time, taken by insert time."""
    fake = FakeSupabase(fail_students_insert='duplicate key value violates unique '
                                             'constraint "students_nisn_key"')

    with pytest.raises(RuntimeError):
        create_student_account(
            fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
            email="budi@example.test", password="pw",
        )

    assert len(fake.created_users) == 1, "the user was created before the failure"
    assert fake.deleted_users == [fake.created_users[0]["id"]], "it must be rolled back"
    assert fake.tables["profiles"] == [], "no profile may survive without a student row"
    assert fake.tables["students"] == []


def test_a_failed_profile_write_also_rolls_back():
    class ExplodingProfile(FakeSupabase):
        def apply_upsert(self, table, payload):
            if table == "profiles":
                raise RuntimeError("permission denied for table profiles")
            return super().apply_upsert(table, payload)

    fake = ExplodingProfile()

    with pytest.raises(RuntimeError):
        create_student_account(
            fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
            email="budi@example.test", password="pw",
        )

    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert fake.tables["students"] == []


def test_a_rollback_failure_does_not_hide_the_original_error():
    class Undeletable(FakeSupabase):
        def delete_user(self, uid, should_soft_delete=False):
            raise RuntimeError("auth service unavailable")

    fake = Undeletable(fail_students_insert="boom")

    with pytest.raises(RuntimeError, match="boom"):
        create_student_account(
            fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
            email="budi@example.test", password="pw",
        )


# ── the happy path ──────────────────────────────────────────────────────────

def test_the_happy_path_writes_all_three_rows():
    fake = FakeSupabase(classes=[{"id": "c1", "name": "VII-A", "school_id": SCHOOL_A}])

    uid = create_student_account(
        fake, school_id=SCHOOL_A, nisn="12345678", full_name="Budi",
        email="budi@example.test", password="pw", class_id="c1", phone="0812",
    )

    assert uid == fake.created_users[0]["id"]
    assert fake.tables["profiles"][0]["nisn"] == "12345678"
    assert fake.tables["profiles"][0]["class_id"] == "c1"
    assert fake.tables["profiles"][0]["phone"] == "0812"
    assert fake.tables["students"][0]["id"] == uid
    assert fake.deleted_users == []


# ── the CSV importer end to end ─────────────────────────────────────────────

def _csv(rows):
    header = "nama,nisn,email,kelas,password\n"
    return io.BytesIO((header + "".join(rows)).encode("utf-8"))


def import_csv(fake, body, monkeypatch):
    monkeypatch.setattr(si, "get_supabase", lambda: fake)
    return si.import_students_from_csv(body, SCHOOL_A)


def test_csv_import_checks_globally_not_per_school(monkeypatch):
    fake = FakeSupabase(
        students=[make_student("u1", "12345678", SCHOOL_B)],
        classes=[{"id": "c1", "name": "VII-A", "school_id": SCHOOL_A}],
    )

    results = import_csv(fake, _csv(["Budi,12345678,,VII-A,pw\n"]), monkeypatch)

    assert results["success"] == 0
    assert results["failed"] == 1
    assert "sudah terdaftar" in results["errors"][0]["message"]
    assert fake.created_users == []
    assert fake.tables["profiles"] == []


def test_csv_import_reports_the_validation_message_not_the_python_one(monkeypatch):
    fake = FakeSupabase(students=[make_student("u1", "12345678", SCHOOL_B)])

    results = import_csv(fake, _csv(["Budi,12345678,,,pw\n"]), monkeypatch)

    message = results["errors"][0]["message"]
    assert not message.startswith("Validation failed for"), (
        "the admin should read the reason, not the exception repr"
    )
    assert "NISN 12345678" in message


def test_csv_import_imports_a_fresh_row(monkeypatch):
    fake = FakeSupabase(classes=[{"id": "c1", "name": "VII-A", "school_id": SCHOOL_A}])

    results = import_csv(fake, _csv(["Budi,12345678,,VII-A,pw\n"]), monkeypatch)

    assert results["success"] == 1 and results["failed"] == 0
    assert len(fake.tables["students"]) == 1
    assert fake.tables["students"][0]["class_id"] == "c1"


# ── static guard ────────────────────────────────────────────────────────────

def test_every_student_path_goes_through_the_helper():
    """`students` may only be written by the helper, so no path can skip the
    global check or the rollback."""
    root = Path(__file__).resolve().parents[2]
    for name in ("app/routes/admin_sekolah.py", "app/routes/api.py"):
        src = (root / name).read_text(encoding="utf-8-sig")
        assert not re.search(r'table\("students"\)\s*\.\s*insert', src), name
        assert not re.search(r'table\("students"\)\s*\.\s*upsert', src), (
            f"{name} writes students outside create_student_account"
        )
        assert "create_student_account" in src, name
