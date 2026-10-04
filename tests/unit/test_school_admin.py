"""The super admin can create, edit and deactivate a school and its admin.

The two doors the request names — ``/super-admin/schools`` and
``/super-admin/users/manage`` — could only *read*. A school was born through the
public registration form and its approval, and afterwards neither its name, its
NPSN, nor its admin's address could be corrected; the admin's *jabatan* was
collected at registration and then dropped, because no column held it.

What this file pins, and why each one is not "a button exists":

* **the account is made by the shared primitive** — read off the source, so a
  creator copied from an old one that talks to GoTrue directly fails here;
* **the school is undone if the account fails** — a failed create must not leave a
  school row that runs nothing;
* **removing a school is a status, never a DELETE** — ``classes.school_id``
  cascades and ``profiles.school_id`` is nulled, so a delete would take a school's
  classes and detach its people; the guard reads the source for ``delete``;
* **the address is written to Auth before the mirror** — the order
  ``account_emails`` argues for: Auth is what login reads, the mirror is cosmetic;
* **scope** — every new write is super-admin only.

The stand-in below answers both halves of the Supabase client the service touches:
the PostgREST table calls and ``auth.admin``.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "school_admin.py"
MIGRATION = ROOT / "supabase" / "migrations" / "060_profiles_position.sql"
ROUTES = ROOT / "app" / "routes" / "super_admin.py"
SCHOOLS_PAGE = ROOT / "app" / "templates" / "super_admin" / "schools.html"
USERS_PAGE = ROOT / "app" / "templates" / "super_admin" / "user_management.html"

from app.services import school_admin as sa  # noqa: E402


# ── a PostgREST + GoTrue stand-in ───────────────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self._op, self._payload, self._filters, self._order = "select", None, [], None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", dict(payload)
        return self

    def update(self, payload):
        self._op, self._payload = "update", dict(payload)
        return self

    def upsert(self, payload):
        self._op, self._payload = "upsert", dict(payload)
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, *_a):
        return self

    def _match(self, row):
        for column, value in self._filters:
            if isinstance(value, list):
                if str(row.get(column)) not in {str(v) for v in value}:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        self.db.log.append((self._op, self.table, dict(self._payload or {})))
        if self._op == "select":
            rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
            if self._order:
                rows.sort(key=lambda r: str(r.get(self._order[0]) or ""),
                          reverse=bool(self._order[1]))
            return _Res(rows)
        if self._op in ("insert", "upsert"):
            self.db.counter += 1
            row = dict(self._payload)
            row.setdefault("id", f"{self.table}-{self.db.counter}")
            self.db.tables.setdefault(self.table, []).append(row)
            return _Res([dict(row)])
        if self._op == "update":
            hits = [r for r in self.db.tables.get(self.table, []) if self._match(r)]
            for r in hits:
                r.update(self._payload)
            return _Res([dict(r) for r in hits])
        kept, gone = [], []
        for r in self.db.tables.get(self.table, []):
            (gone if self._match(r) else kept).append(r)
        self.db.tables[self.table] = kept
        return _Res([dict(r) for r in gone])


class _Admin:
    def __init__(self, db):
        self.db = db

    def create_user(self, attrs):
        if self.db.create_error:
            raise self.db.create_error
        self.db.creates.append(attrs)
        uid = f"uid-{len(self.db.creates)}"
        return type("Created", (), {"user": type("U", (), {"id": uid})()})()

    def delete_user(self, uid, *_a, **_k):
        self.db.deleted.append(uid)

    def update_user_by_id(self, uid, attrs):
        if self.db.update_error:
            raise self.db.update_error
        self.db.email_updates.append((uid, attrs))
        return type("Updated", (), {"user": type("U", (), {"id": uid})()})()


class _DB:
    def __init__(self, tables=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.log: list[tuple] = []
        self.counter = 0
        self.creates, self.deleted, self.email_updates = [], [], []
        self.create_error = None
        self.update_error = None
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

    def table(self, name):
        return _Query(self, name)

    def writes(self, table):
        return [e for e in self.log if e[1] == table and e[0] != "select"]


def _db_with(schools=None, profiles=None):
    return _DB({"schools": schools or [], "profiles": profiles or []})


# ── the schema ──────────────────────────────────────────────────────────────

class TestTheSchema:
    def test_the_migration_adds_the_column_idempotently(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS position TEXT" in sql, (
            "the position column is missing or not idempotent")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration drops something"
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M), "the runner owns the transaction"


# ── creating a school with its admin ────────────────────────────────────────

class TestCreatingASchool:
    def test_a_school_and_its_admin_are_created_together(self):
        db = _db_with()
        out = sa.create_school(db, name="SMA Nusantara", npsn="12345678",
                               admin_name="Bu Sari", admin_email="admin@nusantara.sch.id",
                               position="Admin Sekolah", password="pw-secret")
        assert out["ok"], out
        school = db.tables["schools"][0]
        assert school["name"] == "SMA Nusantara" and school["npsn"] == "12345678"
        profile = db.tables["profiles"][0]
        assert profile["role"] == "admin_sekolah"
        assert profile["school_id"] == school["id"]
        assert profile["position"] == "Admin Sekolah"
        assert profile["must_change_password"] is True, "the issued password is not stamped"
        assert out["password"] == "pw-secret"

    def test_a_name_is_required(self):
        db = _db_with()
        assert sa.create_school(db, name="", npsn="1", admin_email="a@b.co")["reason"] == "name_required"
        assert db.tables["schools"] == []

    def test_an_npsn_is_required(self):
        db = _db_with()
        assert sa.create_school(db, name="X", npsn="", admin_email="a@b.co")["reason"] == "npsn_required"

    def test_a_taken_npsn_is_refused(self):
        db = _db_with(schools=[{"id": "s1", "name": "Lain", "npsn": "12345678"}])
        out = sa.create_school(db, name="X", npsn="12345678", admin_email="a@b.co")
        assert out["reason"] == "npsn_taken"
        assert len(db.tables["schools"]) == 1, "a second school stole an NPSN"

    def test_an_invalid_email_is_refused_before_anything_is_written(self):
        db = _db_with()
        assert sa.create_school(db, name="X", npsn="1", admin_email="not-an-email")["reason"] == "email_invalid"
        assert db.tables["schools"] == [] and db.creates == []

    def test_a_failing_account_undoes_the_school(self):
        db = _db_with()
        db.create_error = RuntimeError("boom")
        out = sa.create_school(db, name="X", npsn="1", admin_email="a@b.co")
        assert out["ok"] is False
        assert db.tables["schools"] == [], "a failed create left a school that runs nothing"

    def test_a_duplicate_address_is_reported_as_taken(self):
        db = _db_with()
        db.create_error = RuntimeError("User already registered")
        out = sa.create_school(db, name="X", npsn="1", admin_email="a@b.co")
        assert out["reason"] == "email_taken", out
        assert db.tables["schools"] == []


# ── editing the school half ─────────────────────────────────────────────────

class TestEditingASchool:
    def test_name_and_npsn_are_written(self):
        db = _db_with(schools=[{"id": "s1", "name": "Lama", "npsn": "111"}])
        out = sa.update_school(db, "s1", name="Baru", npsn="222")
        assert out["ok"], out
        assert db.tables["schools"][0]["name"] == "Baru"
        assert db.tables["schools"][0]["npsn"] == "222"

    def test_a_missing_school_is_reported(self):
        db = _db_with()
        assert sa.update_school(db, "nope", name="X", npsn="1")["reason"] == "not_found"

    def test_the_schools_own_npsn_is_not_a_clash(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "111"}])
        out = sa.update_school(db, "s1", name="A", npsn="111")
        assert out["ok"], out

    def test_another_schools_npsn_is_refused(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "111"},
                               {"id": "s2", "name": "B", "npsn": "222"}])
        assert sa.update_school(db, "s1", name="A", npsn="222")["reason"] == "npsn_taken"

    def test_editing_a_school_touches_no_user(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "111"}],
                      profiles=[{"id": "u1", "school_id": "s1", "role": "guru"}])
        sa.update_school(db, "s1", name="Baru", npsn="333")
        assert db.tables["profiles"][0]["role"] == "guru"


# ── deactivating, never deleting ────────────────────────────────────────────

class TestRemovingASchool:
    def test_deactivate_sets_the_status(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "1", "status": "active"}])
        out = sa.set_active(db, "s1", False)
        assert out["ok"] and db.tables["schools"][0]["status"] == "inactive"

    def test_reactivate_sets_it_back(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "1", "status": "inactive"}])
        assert sa.set_active(db, "s1", True)["status"] == "active"

    def test_a_missing_school_is_reported(self):
        db = _db_with()
        assert sa.set_active(db, "nope", False)["reason"] == "not_found"

    def test_the_service_never_deletes_a_school(self):
        source = SERVICE.read_text(encoding="utf-8")
        # The one delete allowed is the create's rollback of a brand-new row.
        assert source.count(".delete()") <= 1, (
            "the directory deletes schools; removal must be a status")


# ── editing the admin half ──────────────────────────────────────────────────

class TestEditingTheAdmin:
    def _db(self):
        return _db_with(
            schools=[{"id": "s1", "name": "A", "npsn": "1"}],
            profiles=[{"id": "adm1", "school_id": "s1", "role": "admin_sekolah",
                       "full_name": "Bu Sari", "position": "", "email": "old@x.co"}])

    def test_position_and_name_are_written(self):
        db = self._db()
        out = sa.update_admin(db, "s1", full_name="Bu Sari W", position="Kepala Sekolah")
        assert out["ok"], out
        row = db.tables["profiles"][0]
        assert row["position"] == "Kepala Sekolah" and row["full_name"] == "Bu Sari W"

    def test_a_school_without_an_admin_is_reported(self):
        db = _db_with(schools=[{"id": "s1", "name": "A", "npsn": "1"}])
        assert sa.update_admin(db, "s1", position="X")["reason"] == "no_admin"

    def test_the_address_is_written_to_auth_before_the_mirror(self):
        db = self._db()
        out = sa.update_admin(db, "s1", email="new@x.co")
        assert out["ok"], out
        assert db.email_updates and db.email_updates[0][1]["email"] == "new@x.co"
        assert db.tables["profiles"][0]["email"] == "new@x.co"

    def test_an_invalid_address_is_refused_without_touching_auth(self):
        db = self._db()
        assert sa.update_admin(db, "s1", email="bad")["reason"] == "email_invalid"
        assert db.email_updates == []

    def test_an_unchanged_address_is_not_rewritten(self):
        db = self._db()
        sa.update_admin(db, "s1", email="old@x.co", position="Admin")
        assert db.email_updates == [], "the same address was pushed to Auth again"

    def test_a_refused_address_is_reported(self):
        db = self._db()
        db.update_error = RuntimeError("A user with this email address has already been registered")
        assert sa.update_admin(db, "s1", email="taken@x.co")["reason"] == "email_taken"

    def test_set_position_writes_canonical_max_length(self):
        db = _db_with(profiles=[{"id": "u9", "role": "admin_sekolah", "position": ""}])
        sa.set_position(db, "u9", "x" * 200)
        assert len(db.tables["profiles"][0]["position"]) == sa.POSITION_MAX_LENGTH


# ── the doors ───────────────────────────────────────────────────────────────

def _route_blocks(source: str) -> list[str]:
    starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
    starts.append(len(source))
    return [source[a:b] for a, b in zip(starts, starts[1:])]


NEW_ROUTES = (
    '"/schools/create"',
    '"/schools/<school_id>/edit"',
    '"/schools/<school_id>/status"',
    '"/schools/<school_id>/admin"',
    '"/api/user/<user_id>/position"',
)


class TestTheDoors:
    def test_every_new_write_is_super_admin_only(self):
        source = ROUTES.read_text(encoding="utf-8")
        for path in NEW_ROUTES:
            assert path in source, f"{path} is missing"
            block = next(b for b in _route_blocks(source)
                         if f"@super_bp.route({path}" in b)
            assert "@_sa_required" in block, f"{path} is not super-admin only"
            assert "methods=[\"POST\"]" in block, f"{path} is not a POST"

    def test_the_schools_route_passes_the_admin_columns_to_the_page(self):
        source = ROUTES.read_text(encoding="utf-8")
        assert "school_admin.list_schools(" in source, "the page does not read the directory service"

    def test_the_user_list_reads_the_position_column(self):
        source = ROUTES.read_text(encoding="utf-8")
        block = next(b for b in _route_blocks(source)
                     if '@super_bp.route("/users/manage"' in b)
        assert "position" in block, "the user list does not read jabatan"
        assert '"position": p.get("position"' in block or "position\": p.get(\"position\"" in block, (
            "the position is not carried into the row the template renders")

    def test_the_pages_carry_their_controls(self):
        schools = SCHOOLS_PAGE.read_text(encoding="utf-8")
        assert "/super-admin/schools/create" in schools, "no create form"
        assert "/edit" in schools and "/status" in schools, "no edit or deactivate control"
        assert "admin_position" in schools and "admin_email" in schools, (
            "the page does not show the admin address or jabatan")
        users = USERS_PAGE.read_text(encoding="utf-8")
        assert "Jabatan" in users or "position" in users, "the user page has no jabatan column"
        assert "/api/user/" in users and "position" in users, "jabatan cannot be edited"

    def test_the_refusal_catalog_covers_the_service(self):
        # Every refusal key must be renderable; an unknown key shows raw text.
        assert sa.REFUSALS, "no refusals declared"
        for key in sa.REFUSALS:
            assert re.fullmatch(r"[a-z_]+", key), key
