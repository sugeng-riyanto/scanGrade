"""Promote moves a whole class at once, so whose class it is decides everything.

Measured against the running code before this test existed
----------------------------------------------------------
``POST /admin-sekolah/promote`` read ``source_class_id`` and ``target_class_id``
straight off the form and touched the rows they named — with no ``school_id`` in
either query and no check against ``g.user_school_id``. The dropdown only ever
*offers* this school's classes, and a dropdown is not a guard: a POST that names
another school's class listed its pupils and then **moved them into ours**. A
class id nobody had answered 500 (``.single()`` on an empty set), which is how
the missing check was visible without ever trying it with a real foreign id.

The page also had no CRUD at all — the two dropdowns were filled from classes
that could only be managed on another page — so "activate CRUD, based on RBAC"
here means both halves: the controls exist *on* the promote page, and every write
they make is scoped to the school that is signed in.

What is asserted:

* the ownership check lives in the route, before the preview reads anything and
  long before a pupil's ``class_id`` is written;
* the four routes carry the role guard, and the two that take an id also carry
  ``require_school_access``;
* a wali kelas or a school year from outside the school is refused rather than
  stored — the row would otherwise hang off somebody else's roster;
* the legacy twin under ``/admin/classes/*`` no longer takes ``school_id`` from
  the request body or deletes a class with no school in the query;
* a delete of an occupied class is refused until the caller repeats it;
* the forms send what the routes read (``next``, ``confirm=1``,
  ``school_year_id``) and the page stays *untranslated* — it pins
  ``content_lang = 'id'``, and a ``t()`` pair on a pinned page can never render
  its English half, which ``deploy/i18n_coverage.py`` rejects as newly frozen.

The views are driven for real with ``g`` populated, against a fake that scopes
rows the way the schema does.
"""
from pathlib import Path

import pytest
from flask import g, get_flashed_messages

from tests.conftest import app_instance
from app.routes import admin as legacy_module
from app.routes import admin_sekolah as mod
from app.routes.admin_sekolah import create_class, delete_class, edit_class, promote
from app.utils import auth as auth_module

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app" / "routes" / "admin_sekolah.py"
LEGACY_SRC = ROOT / "app" / "routes" / "admin.py"
PROMOTE_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "promote.html"
CLASSES_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "classes.html"

SCHOOL_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SCHOOL_B = "bbbbbbbb-0000-0000-0000-00000000000b"
ADMIN_A = "22222222-0000-0000-0000-00000000000a"
TEACHER_A = "33333333-0000-0000-0000-00000000000a"
TEACHER_B = "44444444-0000-0000-0000-00000000000b"
YEAR_A = "55555555-0000-0000-0000-00000000000a"
YEAR_B = "66666666-0000-0000-0000-00000000000b"

CLASS_A1 = "7e700d56-63cb-4b6f-8269-6307c4025553"   # school A, holds a pupil
CLASS_A2 = "d1f1e335-2575-4c9e-bcb3-7fb011504364"   # school A, empty
CLASS_B1 = "c6c1cc53-88c0-4da5-a82d-8beeafc37014"   # school B — the foreign one
PUPIL_A = "77777777-0000-0000-0000-00000000000a"


# ── a fake that scopes rows the way the schema does ─────────────────────────

class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    """One chained query. ``school_id`` is honoured exactly as PostgREST would."""

    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.negated = []
        self.payload = None
        self.op = "select"
        self.want_count = False
        self._limit = None

    def select(self, *_a, count=None, **_k):
        self.want_count = bool(count)
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def neq(self, col, val):
        self.negated.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        # PostgREST answers a single row as an **object**, and raises when there
        # is none — `require_school_access` reads `.data["school_id"]`, so a list
        # here would raise and be read as "not found" instead of "not yours".
        self._single = True
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _rows(self, live=False):
        rows = [r for r in self.store.rows_for(self.table)
                if all(str(r.get(c)) == str(v) for c, v in self.filters)
                and all(str(r.get(c)) != str(v) for c, v in self.negated)]
        if self._limit is not None:
            rows = rows[:self._limit]
        return rows if live else [dict(r) for r in rows]

    def execute(self):
        if self.op != "insert" and self.store.fail_on == self.table:
            raise RuntimeError("Server disconnected")
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"new-{len(self.store.rows_for(self.table)) + 1}")
            self.store.rows_for(self.table).append(row)
            self.store.inserts.append((self.table, dict(self.payload)))
            return _Resp([row])
        if self.op == "update":
            # Live rows, or the write lands on a copy and every assertion about
            # "what changed in the table" would pass against a throwaway.
            rows = self._rows(live=True)
            for row in rows:
                row.update(self.payload)
                self.store.updates.append((self.table, dict(self.payload)))
                self.store.update_filters.append((self.table, list(self.filters)))
            return _Resp([dict(r) for r in rows])
        if self.op == "delete":
            rows = self._rows(live=True)
            self.store.rows_for(self.table)[:] = [
                r for r in self.store.rows_for(self.table) if r not in rows]
            self.store.deletes.append(self.table)
            return _Resp([dict(r) for r in rows])
        rows = self._rows()
        if getattr(self, "_single", False):
            if not rows:
                raise RuntimeError("multiple (or no) rows in single() request")
            return _Resp(rows[0])
        return _Resp(rows, count=len(rows) if self.want_count else None)


class FakeSupabase:
    """Two schools, three classes, one pupil — every row where it belongs."""

    def __init__(self):
        self.data = {
            "classes": [
                {"id": CLASS_A1, "name": "VII-A", "grade_level": "7",
                 "school_id": SCHOOL_A, "teacher_id": TEACHER_A,
                 "school_year_id": YEAR_A,
                 "school_years": {"name": "2025/2026"},
                 "profiles": {"full_name": "Budi Matematika"}},
                {"id": CLASS_A2, "name": "VII-B", "grade_level": "7",
                 "school_id": SCHOOL_A, "teacher_id": None,
                 "school_year_id": None, "school_years": None,
                 "profiles": None},
                {"id": CLASS_B1, "name": "RUYIN-SMP-B", "grade_level": "7",
                 "school_id": SCHOOL_B, "teacher_id": None,
                 "school_year_id": None, "school_years": None,
                 "profiles": None},
            ],
            "students": [
                {"id": PUPIL_A, "class_id": CLASS_A1, "school_id": SCHOOL_A,
                 "status": "active", "profiles": {"full_name": "Ayu"}},
            ],
            "profiles": [
                {"id": ADMIN_A, "role": "admin_sekolah", "school_id": SCHOOL_A,
                 "class_id": None, "full_name": "Admin SMP"},
                {"id": TEACHER_A, "role": "guru", "school_id": SCHOOL_A,
                 "class_id": None, "full_name": "Budi Matematika"},
                {"id": TEACHER_B, "role": "guru", "school_id": SCHOOL_B,
                 "class_id": None, "full_name": "Dewi Matematika"},
                {"id": PUPIL_A, "role": "murid", "school_id": SCHOOL_A,
                 "class_id": CLASS_A1, "full_name": "Ayu"},
            ],
            "school_years": [
                {"id": YEAR_A, "school_id": SCHOOL_A, "name": "2025/2026",
                 "is_active": True},
                {"id": YEAR_B, "school_id": SCHOOL_B, "name": "2025/2026",
                 "is_active": True},
            ],
        }
        self.inserts = []
        self.updates = []
        self.deletes = []
        self.update_filters = []
        #: Set to a table name to make every read of it fail the way a dropped
        #: connection does.
        self.fail_on = None

    def table(self, name):
        return _Query(self, name)

    def rows_for(self, name):
        return self.data.setdefault(name, [])

    def writes_to(self, table):
        return [u for t, u in self.updates if t == table]


# ── driving the real views ──────────────────────────────────────────────────

def peel(view):
    """The view behind its decorators. The role guard's absence is the subject
    of a test below, not an oversight: ``require_school_access`` is peeled only
    where a test runs it on purpose."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def run(view, monkeypatch, fake, *, method="POST", data=None, accept="",
        school=SCHOOL_A, role="admin_sekolah", path="/admin-sekolah/promote",
        peel_all=True, kwargs=None):
    """Call the real view with ``g`` populated. Returns the raw result, and
    records the flash messages it produced (see ``flashes``) — the context is
    gone by the time the test can read them."""
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(mod, "invalidate_school", lambda *a, **k: None)
    monkeypatch.setattr(mod, "invalidate_class", lambda *a, **k: None)
    monkeypatch.setattr(legacy_module, "log_activity", lambda *a, **k: None)
    headers = {"Accept": accept} if accept else {}
    app = app_instance()
    FLASHES.clear()
    with app.test_request_context(path, method=method, data=data or {},
                                  headers=headers):
        g.user_id = ADMIN_A
        g.user_role = role
        g.user_school_id = school
        # The chrome reads these as plain attributes, not through `.get`.
        g.user_email = "admin@scan-grade.app"
        g.user_name = "Admin SMP"
        # Set by a before_request hook that does not run in a bare context.
        g.tz_offset = 7
        fn = peel(view) if peel_all else view
        result = fn(**(kwargs or {}))
        FLASHES.extend(get_flashed_messages(with_categories=True))
        return result


#: What the last ``run`` flashed, kept because the request context it flashed
#: into is closed when the assertion happens.
FLASHES: list = []


def flashes():
    return list(FLASHES)


def status_of(result):
    if isinstance(result, tuple):
        return result[1]
    return getattr(result, "status_code", 200)


def json_of(result):
    if isinstance(result, tuple) and hasattr(result[0], "get_json"):
        return result[0].get_json()
    return None


def location_of(result):
    resp = result[0] if isinstance(result, tuple) else result
    return getattr(resp, "headers", {}).get("Location", "")


def body_of(result):
    resp = result[0] if isinstance(result, tuple) else result
    # A view may return a rendered template (a str) or a Response.
    if isinstance(resp, str):
        return resp
    return resp.get_data(as_text=True)


# ── the check lives in the route, before anything reads or writes ───────────

class TestTheRouteAsksWhoseClassItIs:
    def _body(self):
        source = SRC.read_text(encoding="utf-8")
        start = source.index("def promote(")
        end = source.index("@admin_sekolah_bp.route(\"/teachers\")")
        return source[start:end]

    def test_both_ids_are_checked(self):
        body = self._body()
        assert body.count("_class_in_school(") >= 2, (
            "only one of source/target is checked: the other is taken from the "
            "form as if a dropdown proved ownership"
        )

    def test_the_check_precedes_the_preview(self):
        """The preview *is* the read — a foreign class must not be listed."""
        body = self._body()
        assert body.index("_class_in_school(") < body.index(
            "promote_confirm.html"), "the preview reads before the id is checked"

    def test_the_check_precedes_the_write(self):
        body = self._body()
        assert body.index("_class_in_school(") < body.index(
            'update({"class_id": target_class_id})'), (
            "a pupil's class_id is written from an id nobody validated"
        )

    @pytest.mark.parametrize("view", ["promote", "create_class", "edit_class",
                                      "delete_class"])
    def test_every_route_here_carries_the_role_guard(self, view):
        source = SRC.read_text(encoding="utf-8")
        tree = __import__("ast").parse(source)
        node = next(n for n in __import__("ast").walk(tree)
                    if isinstance(n, __import__("ast").FunctionDef) and n.name == view)
        lines = source.splitlines()
        first = min(d.lineno for d in node.decorator_list)
        block = "\n".join(lines[first - 1:node.lineno - 1])
        assert "admin_sekolah_required" in block, (
            f"{view} has no role guard: any signed-in member of the school "
            f"could reach it\n{block}"
        )
        assert "@login_required" not in block, (
            "a session-only guard answers 'is somebody signed in', and every "
            "signed-in member answers yes — a murid included"
        )

    @pytest.mark.parametrize("view", ["edit_class", "delete_class"])
    def test_a_route_that_takes_an_id_also_checks_the_school(self, view):
        source = SRC.read_text(encoding="utf-8")
        tree = __import__("ast").parse(source)
        node = next(n for n in __import__("ast").walk(tree)
                    if isinstance(n, __import__("ast").FunctionDef) and n.name == view)
        lines = source.splitlines()
        first = min(d.lineno for d in node.decorator_list)
        block = "\n".join(lines[first - 1:node.lineno - 1])
        assert 'require_school_access("classes"' in block, (
            f"{view} picks its row by id alone, which reaches every school"
        )


# ── behaviour: the foreign class is refused, and nothing is written ─────────

class TestAPostCannotTouchAnotherSchoolsClass:
    def test_a_foreign_source_is_refused_and_nothing_moves(self, monkeypatch):
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": CLASS_B1, "target_class_id": CLASS_A2,
            "confirmed": "1"}, accept="application/json")
        assert status_of(result) == 403, (
            "another school's class was accepted as the source of a promotion"
        )
        assert "bukan milik sekolah ini" in json_of(result)["error"]
        assert fake.writes_to("students") == [], (
            "a pupil's class_id was rewritten on a refusal"
        )

    def test_a_foreign_target_is_refused(self, monkeypatch):
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": CLASS_A1, "target_class_id": CLASS_B1,
            "confirmed": "1"}, accept="application/json")
        assert status_of(result) == 403
        assert fake.writes_to("students") == []

    def test_the_refusal_reaches_a_browser_too(self, monkeypatch):
        """Not only an API answer: the flash is what the admin actually reads."""
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": CLASS_B1, "target_class_id": CLASS_A2})
        assert status_of(result) == 302
        assert location_of(result) == "/admin-sekolah/promote"
        messages = flashes()
        assert any("bukan milik sekolah" in m for _, m in messages), (
            f"the admin is told nothing; flashed: {messages}"
        )
        assert fake.writes_to("students") == []

    def test_an_unknown_source_is_a_refusal_not_a_crash(self, monkeypatch):
        """`.single()` on an id nobody owns used to answer 500."""
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": "11111111-1111-1111-1111-111111111111",
            "target_class_id": CLASS_A2}, accept="application/json")
        assert status_of(result) == 403

    def test_the_preview_lists_only_the_owners_pupils(self, monkeypatch):
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": CLASS_A1, "target_class_id": CLASS_A2})
        assert status_of(result) == 200
        html = body_of(result)
        assert "Ayu" in html, "our own pupil is missing from the preview"
        assert "RUYIN-SMP-B" not in html, (
            "a school name outside this school appears in the preview"
        )

    def test_a_confirmed_promotion_moves_our_pupils_only(self, monkeypatch):
        fake = FakeSupabase()
        result = run(promote, monkeypatch, fake, data={
            "source_class_id": CLASS_A1, "target_class_id": CLASS_A2,
            "confirmed": "1"})
        assert status_of(result) == 302
        moved = {u["class_id"] for u in fake.writes_to("students")}
        assert moved == {CLASS_A2}, f"pupils moved to: {moved}"
        # Both tables carry the class, so both must move together.
        profile_moves = {u["class_id"] for u in fake.writes_to("profiles")}
        assert profile_moves == {CLASS_A2}, (
            "profiles.class_id is what the roster reads; leaving it behind "
            "puts the pupil in two classes"
        )


# ── the two ids a class form may attach ─────────────────────────────────────

class TestTheRowsAClassMayHangOff:
    @pytest.mark.parametrize("field,value,message", [
        ("wali_kelas_id", TEACHER_B, "Guru tersebut bukan milik sekolah"),
        ("school_year_id", YEAR_B, "Tahun ajaran bukan milik sekolah"),
    ])
    def test_create_refuses_a_foreign_row(self, monkeypatch, field, value,
                                          message):
        fake = FakeSupabase()
        form = {"name": "IX-C", "grade_level": "9", "next": "/admin-sekolah/promote"}
        form[field] = value
        result = run(create_class, monkeypatch, fake,
                     path="/admin-sekolah/classes/create", data=form,
                     accept="application/json")
        assert status_of(result) == 403, f"{field} from another school was stored"
        assert message in json_of(result)["error"]
        assert fake.inserts == []

    @pytest.mark.parametrize("field,value", [
        ("wali_kelas_id", TEACHER_B), ("school_year_id", YEAR_B)])
    def test_edit_refuses_a_foreign_row(self, monkeypatch, field, value):
        fake = FakeSupabase()
        form = {"name": "VII-A", "grade_level": "7",
                "next": "/admin-sekolah/promote", field: value}
        result = run(edit_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/edit",
                     data=form, accept="application/json")
        assert status_of(result) == 403
        assert fake.writes_to("classes") == []

    def test_edit_still_updates_our_own_row(self, monkeypatch):
        fake = FakeSupabase()
        result = run(edit_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/edit",
                     data={"name": "VII-A ", "grade_level": "8",
                           "wali_kelas_id": TEACHER_A, "school_year_id": YEAR_A,
                           "next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 200, body_of(result)
        row = next(r for r in fake.data["classes"] if r["id"] == CLASS_A1)
        assert row["grade_level"] == "8"
        # `school_id` in the update's *filter*, not only in the decorator: the
        # write stays on the row the check saw.
        assert any(dict(f).get("school_id") == SCHOOL_A
                   for t, f in fake.update_filters if t == "classes"), (
            "the update does not filter on school_id"
        )

    def test_edit_refuses_a_second_row_of_the_same_name(self, monkeypatch):
        fake = FakeSupabase()
        result = run(edit_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/edit",
                     data={"name": "VII-B", "grade_level": "7",
                           "school_year_id": YEAR_A,
                           "next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 400
        assert "sudah ada" in json_of(result)["error"]
        assert fake.writes_to("classes") == []


# ── the delete says how many pupils it takes ────────────────────────────────

class TestDeletingAClassIsShownBeforeItHappens:
    def test_an_occupied_class_needs_the_confirmation(self, monkeypatch):
        fake = FakeSupabase()
        result = run(delete_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/delete",
                     data={"next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 409, (
            "a class holding a pupil was deleted by a bare POST"
        )
        body = json_of(result)
        assert body["needs_confirmation"] is True
        assert "1 murid" in body["error"], (
            f"the refusal does not name the count: {body['error']}"
        )
        assert fake.deletes == []
        assert fake.data["students"][0]["class_id"] == CLASS_A1

    def test_a_count_we_cannot_read_is_not_a_count_of_zero(self, monkeypatch):
        """Observed live: the occupant read failed mid-request, reported 0, and
        the delete went ahead for a class holding a pupil. "We could not tell"
        is not "nobody is in it" — the refusal needs a confirm either way."""
        fake = FakeSupabase()
        fake.fail_on = "students"
        result = run(delete_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/delete",
                     data={"next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 409, (
            "an unreadable count was treated as an empty class"
        )
        assert "tidak bisa dibaca" in json_of(result)["error"]
        assert fake.deletes == [], "the class was deleted on a failed read"

    def test_repeating_it_deletes_and_empties_both_tables(self, monkeypatch):
        fake = FakeSupabase()
        result = run(delete_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A1},
                     path=f"/admin-sekolah/classes/{CLASS_A1}/delete",
                     data={"confirm": "1", "next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 200, body_of(result)
        assert "classes" in fake.deletes
        assert fake.data["students"][0]["class_id"] is None, (
            "students.class_id keeps an id pointing at a row that is gone"
        )
        pupil = next(p for p in fake.data["profiles"] if p["id"] == PUPIL_A)
        assert pupil["class_id"] is None, (
            "profiles.class_id is what the roster reads; nulling only the "
            "other table leaves the pupil in a deleted class"
        )

    def test_an_empty_class_deletes_without_a_ceremony(self, monkeypatch):
        fake = FakeSupabase()
        result = run(delete_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A2},
                     path=f"/admin-sekolah/classes/{CLASS_A2}/delete",
                     data={"next": "/admin-sekolah/promote"},
                     accept="application/json")
        assert status_of(result) == 200, body_of(result)
        assert "classes" in fake.deletes

    def test_the_school_check_runs_before_any_of_this(self, monkeypatch):
        """The id picks the row, so the school's answer is the first one.

        The decorator is re-applied here to the peeled view: the role guard in
        front of it answers on a session this test does not have, and the guard
        under test is the school one. It is asserted present *in the source* by
        the test above, so the two together cover the chain.
        """
        from app.decorators.security import require_school_access

        fake = FakeSupabase()
        guarded = require_school_access("classes", "class_id")(peel(delete_class))
        monkeypatch.setattr(mod, "get_supabase", lambda: fake)
        monkeypatch.setattr(auth_module, "get_supabase", lambda: fake)
        app = app_instance()
        with app.test_request_context(
                f"/admin-sekolah/classes/{CLASS_B1}/delete", method="POST",
                data={"confirm": "1"}, headers={"Accept": "application/json"}):
            g.user_id = ADMIN_A
            g.user_role = "admin_sekolah"
            g.user_school_id = SCHOOL_A
            result = guarded(class_id=CLASS_B1)
        assert status_of(result) == 403, (
            "another school's class reached the delete body"
        )
        assert fake.deletes == []

    @pytest.mark.parametrize("next_value,expected", [
        ("/admin-sekolah/promote", "/admin-sekolah/promote"),
        ("/admin-sekolah/classes", "/admin-sekolah/classes"),
        ("/teacher/dashboard", "/admin-sekolah/classes"),
        # The two that keep a *path* the check likes: only the origin check
        # stands between these and an open redirect.
        ("https://evil.example/admin-sekolah/promote",
         "/admin-sekolah/classes"),
        ("//evil.example/admin-sekolah/promote", "/admin-sekolah/classes"),
        ("//evil.example/phish", "/admin-sekolah/classes"),
    ])
    def test_next_cannot_become_an_open_redirect(self, monkeypatch,
                                                 next_value, expected):
        fake = FakeSupabase()
        result = run(delete_class, monkeypatch, fake,
                     kwargs={"class_id": CLASS_A2},
                     path=f"/admin-sekolah/classes/{CLASS_A2}/delete",
                     data={"confirm": "1", "next": next_value})
        assert location_of(result) == expected, (
            f"next={next_value!r} sent the admin to {location_of(result)!r}"
        )


# ── the legacy twin under /admin ────────────────────────────────────────────

class TestTheLegacyClassRoutesAreScopedToo:
    def test_create_takes_its_school_from_the_session_not_the_body(self):
        source = LEGACY_SRC.read_text(encoding="utf-8")
        start = source.index("@admin_bp.route(\"/classes/create\"")
        end = source.index("@admin_bp.route(\"/school/data\")")
        body = source[start:end]
        assert "data.get(\"school_id\"" not in body, (
            "the POST body still decides which school the class lands in — "
            "defaulting to 1, so any admin can plant a row anywhere"
        )
        assert "g.get(\"user_school_id\")" in body

    def test_the_legacy_delete_carries_the_school_check(self):
        source = LEGACY_SRC.read_text(encoding="utf-8")
        start = source.index("@admin_bp.route(\"/classes/<class_id>/delete\"")
        end = source.index("@admin_bp.route(\"/school/data\")")
        block = source[start:end]
        assert 'require_school_access("classes"' in block, (
            "the legacy twin still deletes by id alone, reaching every school"
        )


# ── the forms send what the routes read ─────────────────────────────────────

class TestThePageAndTheRoutesAgree:
    @pytest.mark.parametrize("fragment", [
        '<input type="hidden" name="next" value="/admin-sekolah/promote">',
        '<input type="hidden" name="confirm" value="1">',
        'name="school_year_id"',
        'action="/admin-sekolah/classes/create"',
        'name="wali_kelas_id"',
        'name="source_class_id"',
    ])
    def test_promote_sends_it(self, fragment):
        html = PROMOTE_TEMPLATE.read_text(encoding="utf-8")
        assert fragment in html, (
            f"the promote page does not send {fragment!r}: the route would "
            "answer on another page, refuse a delete it was never asked to "
            "confirm, or drop the year of a class it just created"
        )

    def test_every_form_target_is_a_route_the_app_serves(self, app):
        html = PROMOTE_TEMPLATE.read_text(encoding="utf-8")
        served = {str(r.rule) for r in app.url_map.iter_rules()}
        for path in ("/admin-sekolah/classes/create",
                     f"/admin-sekolah/classes/{CLASS_A1}/edit",
                     f"/admin-sekolah/classes/{CLASS_A1}/delete"):
            prefix = path.replace(CLASS_A1, "<class_id>")
            assert prefix in served or path in served, (
                f"{path} is posted to, but no route answers it"
            )

    def test_every_form_says_where_to_answer(self):
        """Three forms, three `next`s: create, edit and delete each land back
        on the promote page with their flash rather than on the class list."""
        html = PROMOTE_TEMPLATE.read_text(encoding="utf-8")
        assert html.count('name="next" value="/admin-sekolah/promote"') == 3, (
            "one of the three forms no longer says where to answer"
        )

    def test_the_create_form_sends_its_own_year(self):
        """Pinned to *that* form: `name="school_year_id"` also appears in the
        promote form and in the edit row, so a page-wide search would pass while
        the class created here arrived with no year — which is what the blank
        after every class name in the dropdowns used to be."""
        html = PROMOTE_TEMPLATE.read_text(encoding="utf-8")
        block = html.split('action="/admin-sekolah/classes/create"', 1)[1]
        block = block.split("</form>", 1)[0]
        assert 'name="school_year_id"' in block, (
            "the create-class form does not send the year the route stores"
        )
        assert 'name="wali_kelas_id"' in block

    def test_the_other_page_confirms_the_same_way(self):
        html = CLASSES_TEMPLATE.read_text(encoding="utf-8")
        assert 'name="confirm" value="1"' in html or '"confirm": "1"' in html, (
            "the classes page still deletes with a dialog alone, and the "
            "server-side count would refuse every occupied class it opens"
        )

    def test_the_wali_dropdown_reads_the_right_field(self):
        """`profiles` rows carry `full_name`; `name` renders an empty option, so
        the wali kelas of an edited class could not be seen or kept."""""
        html = CLASSES_TEMPLATE.read_text(encoding="utf-8")
        assert "{{ t.name }}" not in html, (
            "the edit form reads a field the teachers list does not have, so "
            "every option in it is blank"
        )
        assert "{{ teacher.full_name }}" in html

    def test_the_page_stays_untranslated_so_it_never_freezes(self):
        """`content_lang = 'id'` pins the page; a pair on a pinned page can
        never render its English half, and `deploy/i18n_coverage.py` rejects a
        page that becomes frozen while the baseline records it as not."""
        html = PROMOTE_TEMPLATE.read_text(encoding="utf-8")
        assert "content_lang = 'id'" in html
        assert "t('" not in html and 't("' not in html, (
            "a bilingual pair on a pinned page: its English half is dead copy "
            "and the i18n gate rejects the page as newly frozen"
        )
