"""Teacher import: the NIP check must be global, a failed row must leave nothing,
and nothing may write the non-existent ``teachers.status`` column.

Measured against the live database, the only unique constraints on ``teachers``
are ``teachers_pkey (id)`` and ``teachers_nuptk_key (nuptk)``:

* ``teachers.nuptk`` is UNIQUE but written by nobody, so it never fires.
* ``teachers.employee_id`` -- where the NIP actually goes -- is **not
  constrained at all**, so the database cannot reject a duplicate NIP. It has to
  be checked in code, and it matters: ``auth.login`` resolves a non-email login
  with ``.eq("employee_id", login_input).limit(1)``, so two teachers sharing a
  NIP silently make one of them unable to sign in with it.

Separately, ``teachers`` has no ``status`` column while ``profiles`` does, so the
single-teacher form wrote ``status`` into the teachers upsert and was rejected:

    42703    column teachers.status does not exist
    PGRST204 Could not find the 'status' column of 'teachers' in the schema cache

It created the auth user and the profile first, so every "Tambah Guru" click left
an account with no teachers row.

``FakeSupabase`` reproduces the global indexes and the delete cascade, so a
regression fails the way the database fails.
"""
import io
import re
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.errors import ValidationError
from app.services.teacher_import import (
    create_teacher_account,
    find_teacher_by_employee_id,
    find_teacher_by_nuptk,
)

SCHOOL_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SCHOOL_B = "bbbbbbbb-0000-0000-0000-00000000000b"


# ── fake ────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.payload = None
        self.op = "select"
        self._maybe_single = False
        self._limit = None

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

    def insert(self, payload):
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
    def __init__(self, teachers=(), profiles=(), subjects=(), fail_teachers_insert=None):
        self.tables = {
            "teachers": [dict(r) for r in teachers],
            "profiles": [dict(r) for r in profiles],
            "subjects": [dict(r) for r in subjects],
        }
        self.created_users = []
        self.deleted_users = []
        self.fail_teachers_insert = fail_teachers_insert
        self._next = 0
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

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
        for t in ("profiles", "teachers"):
            self.tables[t] = [r for r in self.tables[t] if r.get("id") != uid]

    def apply_upsert(self, table, payload):
        row = dict(payload)
        if table == "teachers":
            if self.fail_teachers_insert:
                raise RuntimeError(self.fail_teachers_insert)
            # The live schema: only id and nuptk are unique.
            if row.get("nuptk") is not None:
                for existing in self.tables["teachers"]:
                    if existing.get("nuptk") == row["nuptk"] and existing.get("id") != row.get("id"):
                        raise RuntimeError(
                            'duplicate key value violates unique constraint "teachers_nuptk_key"'
                        )
            if any(k == "status" for k in row):
                raise RuntimeError(
                    "PGRST204 Could not find the 'status' column of 'teachers' "
                    "in the schema cache"
                )
        for existing in self.tables[table]:
            if existing.get("id") == row.get("id"):
                existing.update(row)
                return dict(existing)
        self.tables[table].append(row)
        return dict(row)


def make_teacher(uid, employee_id, school, nuptk=None):
    return {"id": uid, "employee_id": employee_id, "school_id": school, "nuptk": nuptk}


def create(fake, **over):
    kwargs = dict(
        school_id=SCHOOL_A, full_name="Budi Guru", email="budi@example.test",
        password="pw", employee_id="1234567890",
    )
    kwargs.update(over)
    return create_teacher_account(fake, **kwargs)


# ── the global check ────────────────────────────────────────────────────────

def test_find_teacher_by_employee_id_ignores_school():
    fake = FakeSupabase(teachers=[make_teacher("u1", "1234567890", SCHOOL_B)])

    found = find_teacher_by_employee_id(fake, "1234567890")

    assert found is not None and found["id"] == "u1"


def test_a_blank_nip_is_not_looked_up():
    """A blank is not an identifier; it must not match every other blank."""
    fake = FakeSupabase(teachers=[make_teacher("u1", "", SCHOOL_B)])

    assert find_teacher_by_employee_id(fake, "") is None
    assert find_teacher_by_employee_id(fake, None) is None


def test_a_nip_used_at_another_school_is_refused():
    """The database cannot catch this: employee_id has no unique index."""
    fake = FakeSupabase(teachers=[make_teacher("u1", "1234567890", SCHOOL_B)])

    with pytest.raises(ValidationError) as exc:
        create(fake)

    assert "sekolah lain" in exc.value.user_message
    assert fake.created_users == [], "no account may be started for a taken NIP"
    assert fake.tables["profiles"] == []
    assert len(fake.tables["teachers"]) == 1


def test_a_nip_in_the_same_school_is_refused_and_says_so():
    fake = FakeSupabase(teachers=[make_teacher("u1", "1234567890", SCHOOL_A)])

    with pytest.raises(ValidationError) as exc:
        create(fake)

    assert "sekolah ini" in exc.value.user_message
    assert fake.created_users == []


def test_a_duplicate_nuptk_is_refused():
    """nuptk IS uniquely constrained, so a future sheet carrying it must not
    reach the index after the account already existed."""
    fake = FakeSupabase(teachers=[make_teacher("u1", "9999999999", SCHOOL_B, nuptk="NUPTK-1")])

    assert find_teacher_by_nuptk(fake, "NUPTK-1") is not None
    with pytest.raises(ValidationError) as exc:
        create(fake, employee_id="1111111111", nuptk="NUPTK-1")

    assert "NUPTK" in exc.value.user_message
    assert fake.created_users == []


# ── the teachers.status trap ────────────────────────────────────────────────

def test_the_teachers_payload_never_carries_status():
    """`teachers.status` does not exist; sending it is rejected with PGRST204,
    which is what made the add-teacher form create the account and then fail."""
    fake = FakeSupabase()

    create(fake, phone="0812")

    payload = fake.tables["teachers"][0]
    assert "status" not in payload, "teachers has no status column"
    assert payload["employee_id"] == "1234567890"
    assert payload["school_id"] == SCHOOL_A


def test_profile_still_carries_status():
    fake = FakeSupabase()

    create(fake)

    assert fake.tables["profiles"][0]["status"] == "active", (
        "profiles DOES have a status column; dropping it there would be a bug"
    )


# ── no orphans ──────────────────────────────────────────────────────────────

def test_a_failed_teachers_write_leaves_no_auth_user():
    """A race: the NIP was free at check time, taken by insert time."""
    fake = FakeSupabase(fail_teachers_insert="boom")

    with pytest.raises(RuntimeError):
        create(fake)

    assert len(fake.created_users) == 1, "the user was created before the failure"
    assert fake.deleted_users == [fake.created_users[0]["id"]], "it must be rolled back"
    assert fake.tables["profiles"] == [], "no profile may survive without a teachers row"
    assert fake.tables["teachers"] == []


def test_a_rejected_teachers_payload_rolls_back_too():
    """Exactly the production failure: the payload is refused by the API."""
    class Refusing(FakeSupabase):
        def apply_upsert(self, table, payload):
            if table == "teachers":
                raise RuntimeError(
                    "PGRST204 Could not find the 'status' column of 'teachers'"
                )
            return super().apply_upsert(table, payload)

    fake = Refusing()

    with pytest.raises(RuntimeError):
        create(fake)

    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert fake.tables["profiles"] == []


def test_a_failed_profile_write_also_rolls_back():
    class ExplodingProfile(FakeSupabase):
        def apply_upsert(self, table, payload):
            if table == "profiles":
                raise RuntimeError("permission denied for table profiles")
            return super().apply_upsert(table, payload)

    fake = ExplodingProfile()

    with pytest.raises(RuntimeError):
        create(fake)

    assert fake.deleted_users == [fake.created_users[0]["id"]]
    assert fake.tables["teachers"] == []


def test_a_rollback_failure_does_not_hide_the_original_error():
    class Undeletable(FakeSupabase):
        def delete_user(self, uid, should_soft_delete=False):
            raise RuntimeError("auth service unavailable")

    fake = Undeletable(fail_teachers_insert="boom")

    with pytest.raises(RuntimeError, match="boom"):
        create(fake)


# ── the happy path ──────────────────────────────────────────────────────────

def test_the_happy_path_writes_all_three_rows():
    fake = FakeSupabase()

    uid = create(fake, subject_id="sub-1", phone="0812")

    assert uid == fake.created_users[0]["id"]
    assert fake.tables["profiles"][0]["role"] == "guru"
    assert fake.tables["profiles"][0]["phone"] == "0812"
    assert fake.tables["teachers"][0]["id"] == uid
    assert fake.tables["teachers"][0]["subject_id"] == "sub-1"
    assert fake.deleted_users == []


# ── the bulk importer end to end ────────────────────────────────────────────

def _teacher_sheet(rows):
    """NEW 7-column template: NIP, Nama, Email, Mapel, HP, EmailPemulihan, Pw."""
    wb = Workbook()
    ws = wb.active
    ws.append(["NIP", "Nama", "Email", "Mapel", "No. HP", "Email Pemulihan", "Password"])
    for r in rows:
        ws.append(r)
    return ws


def bulk(fake, rows):
    from app.routes.admin_sekolah import _import_teachers

    results = {"students": 0, "teachers": 0, "subjects": 0, "errors": []}
    ws = _teacher_sheet(rows)
    _import_teachers(ws, SCHOOL_A, fake, results)
    return results


def test_bulk_import_checks_the_nip_globally():
    fake = FakeSupabase(teachers=[make_teacher("u1", "1234567890", SCHOOL_B)])

    results = bulk(fake, [["1234567890", "Budi Guru", "budi@example.test", "", "", "", ""]])

    assert results["teachers"] == 0
    assert len(results["errors"]) == 1
    assert "sudah terdaftar" in results["errors"][0]
    assert fake.created_users == [], "the rejected row must not create an account"
    assert fake.tables["profiles"] == []


def test_bulk_import_reports_the_validation_message_not_the_python_one():
    fake = FakeSupabase(teachers=[make_teacher("u1", "1234567890", SCHOOL_B)])

    results = bulk(fake, [["1234567890", "Budi Guru", "budi@example.test", "", "", "", ""]])

    message = results["errors"][0]
    assert not message.startswith("Validation failed for"), (
        "the admin should read the reason, not the exception repr"
    )
    assert "NIP 1234567890" in message


def test_bulk_import_imports_a_fresh_row_with_its_subject():
    fake = FakeSupabase(subjects=[{"id": "sub-1", "name": "Matematika", "school_id": SCHOOL_A}])

    results = bulk(fake, [["1234567890", "Budi Guru", "budi@example.test", "Matematika", "0812", "", ""]])

    assert results["teachers"] == 1 and results["errors"] == []
    assert fake.tables["teachers"][0]["employee_id"] == "1234567890"
    assert fake.tables["teachers"][0]["subject_id"] == "sub-1"
    assert fake.tables["profiles"][0]["phone"] == "0812"


def test_bulk_import_keeps_going_after_a_rejected_row():
    fake = FakeSupabase(teachers=[make_teacher("u1", "1111111111", SCHOOL_B)])

    results = bulk(fake, [
        ["1111111111", "Sudah Ada", "a@example.test", "", "", "", ""],
        ["2222222222", "Guru Baru", "b@example.test", "", "", "", ""],
    ])

    assert results["teachers"] == 1, "one bad row must not abort the sheet"
    assert len(results["errors"]) == 1
    assert len(fake.tables["teachers"]) == 2


# ── static guard ────────────────────────────────────────────────────────────

def test_every_teacher_path_goes_through_the_helper():
    """`teachers` may only be written by the helper, so no path can skip the
    global check, the rollback, or the status-column rule."""
    root = Path(__file__).resolve().parents[2]
    for name in ("app/routes/admin_sekolah.py", "app/routes/admin.py"):
        src = (root / name).read_text(encoding="utf-8-sig")
        assert not re.search(r'table\("teachers"\)\s*\.\s*insert', src), name
        assert not re.search(r'table\("teachers"\)\s*\.\s*upsert', src), (
            f"{name} writes teachers outside create_teacher_account"
        )


def test_routes_do_not_write_the_status_column_to_teachers():
    root = Path(__file__).resolve().parents[2]
    for name in ("app/routes/admin_sekolah.py", "app/routes/admin.py"):
        src = (root / name).read_text(encoding="utf-8-sig")
        for match in re.finditer(r'table\("teachers"\)', src):
            window = src[match.start(): match.start() + 400]
            assert '"status"' not in window.split(".execute()")[0], (
                f"{name}: teachers has no status column near offset {match.start()}"
            )
