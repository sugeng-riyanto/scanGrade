"""Managing a school's academic years — the page that had three of four verbs.

`/admin-sekolah/school-years` could create a year, make one current, and delete
one. Nothing on it could rename a year or move its dates, so the only way to fix a
typo was delete-and-recreate — and that is not an edit. The row is referenced with
ON DELETE SET NULL by `classes`, `teacher_assignments`, `exams` and
`student_enrollment`, and with ON DELETE CASCADE by `subject_kkm`, `grade_weights`
and `grade_component_defaults` (051/057/067), so recreating a year silently
detached its classes and wiped the KKM, the weighting and the default
distributions configured for it.

Three things are held here. The refusals the two form handlers share are exercised
as behaviour (`_year_refusal`), because a rule that lives in only one of them is
how a page refuses with one button what it accepted with the other. The page is
*rendered*, twice, with a current year and a past one, because the controls that
went missing were per-row — a grep for "Ubah" in the template would have passed
while the row a school most needs to correct carried none. And the delete of the
running year is refused by the route as well as hidden in the template, since a
POST is a POST.

Run:  python -m pytest tests/unit/test_school_year_crud.py -q
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.routes import admin_sekolah as adm

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "admin_sekolah/school_years.html"
SOURCE = ROOT / "app" / "routes" / "admin_sekolah.py"

# `school_id` is on both rows on purpose: every write this page makes is filtered
# by it, and a fixture without it makes a fake PostgREST match nothing — the
# tests then pass while proving the opposite of what they claim.
RUNNING = {"id": "year-running", "name": "2026/2027", "school_id": "sch-1",
           "start_date": "2026-07-01", "end_date": "2027-06-30", "is_active": True}
PAST = {"id": "year-past", "name": "2025/2026", "school_id": "sch-1",
        "start_date": "2025-07-01", "end_date": "2026-06-30", "is_active": False}


# ── the refusals, as behaviour ────────────────────────────────────────────────

class _Query:
    """Just enough PostgREST to answer the duplicate check, and to record it."""

    def __init__(self, rows: list, log: dict):
        self._rows, self._log = rows, log

    def select(self, *columns):
        self._log["columns"] = columns
        return self

    def eq(self, column, value):
        self._log.setdefault("filters", []).append((column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Db:
    def __init__(self, rows: list):
        self._rows = rows
        self.log: dict = {}

    def table(self, name):
        assert name == "school_years", f"the form reaches {name}"
        return _Query(self._rows, self.log)


class TestTheSharedRefusals:
    def test_an_empty_field_is_refused(self):
        assert adm._year_refusal(_Db([]), "sch-1", "", "2026-07-01", "2027-06-30")
        assert adm._year_refusal(_Db([]), "sch-1", "2026/2027", "", "2027-06-30")
        assert adm._year_refusal(_Db([]), "sch-1", "2026/2027", "2026-07-01", "")

    def test_a_window_that_runs_backwards_is_refused(self):
        refusal = adm._year_refusal(_Db([]), "sch-1", "2026/2027",
                                    "2027-06-30", "2026-07-01")
        assert refusal and "tanggal mulai" in refusal.lower()

    def test_a_name_the_school_already_uses_is_refused(self):
        refusal = adm._year_refusal(_Db([dict(PAST)]), "sch-1", "2025/2026",
                                    "2025-07-01", "2026-06-30")
        assert refusal and "2025/2026" in refusal

    def test_the_name_is_compared_without_case(self):
        assert adm._year_refusal(_Db([dict(PAST)]), "sch-1", "  2025/2026  ",
                                 "2025-07-01", "2026-06-30")

    def test_a_year_keeping_its_own_name_is_not_a_duplicate_of_itself(self):
        """Otherwise *every* edit of a year would be a duplicate of it."""
        assert adm._year_refusal(_Db([dict(PAST)]), "sch-1", "2025/2026",
                                 "2025-07-01", "2026-06-30",
                                 exclude_id="year-past") is None

    def test_a_sound_year_passes(self):
        assert adm._year_refusal(_Db([dict(PAST)]), "sch-1", "2027/2028",
                                 "2027-07-01", "2028-06-30") is None

    def test_the_duplicate_check_reads_one_school(self):
        """The school comes from the session, so the same year name in another
        school is not this school's duplicate — and the query has to say so."""
        db = _Db([])
        adm._year_refusal(db, "sch-1", "2026/2027", "2026-07-01", "2027-06-30")
        assert ("school_id", "sch-1") in db.log.get("filters", [])


# ── the page, rendered ────────────────────────────────────────────────────────

def _render(app, years):
    from flask import g
    with app.test_request_context("/admin-sekolah/school-years"):
        g.user_id, g.user_name, g.user_role = "u-1", "Admin SMP", "admin_sekolah"
        g.user_email, g.tz_offset, g.show = "admin@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", None
        return app.jinja_env.get_template(TEMPLATE).render(years=years)


class TestEveryRowCanBeManaged:
    def test_every_year_can_be_renamed_and_re_dated(self, app):
        page = _render(app, [dict(RUNNING), dict(PAST)])
        for year in (RUNNING, PAST):
            action = f'action="/admin-sekolah/school-years/{year["id"]}/edit"'
            assert page.count(action) == 1, (
                f"{year['name']} carries no edit form — the page offers "
                f"create, activate and delete, which is three verbs of four")
            assert f'value="{year["name"]}"' in page, (
                f"the edit form for {year['name']} is not filled in with the year "
                f"it edits, so saving it would overwrite the row with blanks")

    def test_the_running_year_is_editable_too(self, app):
        """The row a school most often corrects is the one it just created and
        marked current — the buttons used to be hidden on exactly that row."""
        page = _render(app, [dict(RUNNING), dict(PAST)])
        assert f'action="/admin-sekolah/school-years/{RUNNING["id"]}/edit"' in page
        assert 'id="act_year-running" checked' in page, (
            "the edit form must open with the current year's box already ticked")

    def test_an_inactive_year_offers_both_activate_and_delete(self, app):
        page = _render(app, [dict(RUNNING), dict(PAST)])
        assert f'action="/admin-sekolah/school-years/{PAST["id"]}/toggle"' in page
        assert f'action="/admin-sekolah/school-years/{PAST["id"]}/delete"' in page

    def test_the_running_year_is_not_offered_for_deletion_and_says_why(self, app):
        page = _render(app, [dict(RUNNING), dict(PAST)])
        assert f'action="/admin-sekolah/school-years/{RUNNING["id"]}/delete"' not in page
        assert "aktifkan tahun lain dulu" in page, (
            "the delete is gone from the current row with no word about how to "
            "get it back, which is the dead end this page shipped with")


class TestTheDeleteNamesWhatGoesWithTheYear:
    def test_the_dialog_lists_the_cascade(self, app):
        """A year is not a row: its classes and exams detach and its KKM,
        weighting and default distributions cascade away (051/057/067)."""
        page = _render(app, [dict(RUNNING), dict(PAST)])
        # Both halves, named one by one. The confirmation is a `t()` pair, so a
        # check for "the cascade is mentioned" is satisfied by whichever half kept
        # it — measured: the first version of this guard passed a mutation that
        # emptied the half an Indonesian-reading admin reads, because the English
        # one still said it.
        assert "bobot nilai, KKM, serta sebaran nilai bawaan tahun ini ikut terhapus" in page, (
            "the Indonesian half of the delete dialog no longer names what the "
            "cascade takes with the year")
        assert "grade weights, KKM and default distributions go with it" in page, (
            "the English half of the delete dialog no longer names what the "
            "cascade takes with the year")

    def test_no_school_supplied_name_is_interpolated_into_the_handler(self, app):
        """A year named with an apostrophe would end the Alpine expression early;
        the message is static for that reason."""
        page = _render(app, [dict(RUNNING), dict(PAST)])
        assert "t('Hapus tahun ajaran ini?" in page
        assert "{{ y.name }}" not in page.split("confirm(")[1].split(")")[0]


# ── the routes ────────────────────────────────────────────────────────────────

class TestTheEditRoute:
    def _rule(self, app):
        for rule in app.url_map.iter_rules():
            if str(rule) == "/admin-sekolah/school-years/<year_id>/edit":
                return rule
        raise AssertionError("no edit route is registered for a school year")

    def test_it_exists_as_a_post(self, app):
        rule = self._rule(app)
        assert rule.methods - {"HEAD", "OPTIONS"} == {"POST"}, (
            "an edit reachable by GET is a link a crawler or a prefetch follows")
        assert rule.endpoint.endswith("edit_school_year")

    def test_it_is_behind_the_school_guard(self):
        body = SOURCE.read_text(encoding="utf-8-sig")
        head = body.split("def edit_school_year")[0]
        decorators = head.rsplit("\n\n", 1)[-1]
        assert "@require_school_access(\"school_years\", \"year_id\")" in decorators, (
            "the edit resolves a year id from the URL without asking whose it is")
        assert "@admin_sekolah_required" in decorators

    def test_both_writes_are_scoped_to_the_session_school(self):
        body = SOURCE.read_text(encoding="utf-8-sig")
        edit = body.split("def edit_school_year")[1].split("def delete_school_year")[0]
        delete = body.split("def delete_school_year")[1].split("def close_school_year")[0]
        assert '.eq("id", year_id).eq("school_id", sid)' in edit, (
            "the update is addressed by id alone, so a year id from another school "
            "would be written")
        assert 'delete().eq("id", year_id).eq("school_id", sid)' in delete

    def test_one_judgement_serves_both_handlers(self):
        body = SOURCE.read_text(encoding="utf-8-sig")
        create = body.split("def school_years()")[1].split("def ")[0]
        edit = body.split("def edit_school_year")[1].split("def delete_school_year")[0]
        assert "_year_refusal(" in create and "_year_refusal(" in edit, (
            "the two forms read the same fields; a rule in only one of them is a "
            "page that refuses with one button what it accepts with the other")
        assert body.count("Nama, tanggal mulai, dan tanggal berakhir wajib diisi") == 1

    def test_the_edit_does_not_touch_anything_that_hangs_off_the_year(self):
        edit = SOURCE.read_text(encoding="utf-8-sig").split(
            "def edit_school_year")[1].split("def delete_school_year")[0]
        written = {key for chunk in re.findall(r"update\(\{(.*?)\}\)", edit, re.S)
                   for key in re.findall(r'"(\w+)"\s*:', chunk)}
        assert written == {"name", "start_date", "end_date", "is_active"}, (
            f"the edit writes more than the year's own four columns: {sorted(written)}")
        tables = set(re.findall(r'supabase\.table\("(\w+)"\)', edit))
        assert tables == {"school_years"}, (
            f"the edit reaches more than the year it was pointed at: {sorted(tables)}")


# ── the four verbs, driven ────────────────────────────────────────────────────
#
# The page offers four buttons and each one posts a form; what an admin sees is
# the answer to "did my year change", which is a write and not a route. These
# call the handlers the way a submission does — the form fields, the session's
# school, nothing else — against a fake PostgREST that records what was written,
# so "edit writes four columns on that row" is an observation and not a reading
# of the source. `_year_refusal` is exercised above for the same reason.


class _WriteQuery:
    """A PostgREST read/write that answers out of one list of rows.

    Named apart from the `_Query` the refusal tests use: two classes with one
    name in one module is how a change to one silently rewrites the other's
    fixture, and that is exactly what happened the first time these were added.
    """

    def __init__(self, db, table):
        self._db, self._table = db, table
        self.kind, self.payload, self.filters = None, None, []
        db.calls.append(self)

    def select(self, *columns):
        self.kind = self.kind or "select"
        return self

    def insert(self, payload):
        self.kind, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.kind, self.payload = "update", payload
        return self

    def delete(self):
        self.kind = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *a, **k):
        return self

    def _hits(self):
        return [r for r in self._db.rows
                if all(r.get(c) == v for c, v in self.filters)]

    def execute(self):
        if self._table != "school_years":
            # `audit_logs` (and anything else a handler writes on the side) is
            # accepted and ignored: this file is about the year that changed.
            return SimpleNamespace(data=[])
        rows = self._db.rows
        if self.kind == "insert":
            row = dict(self.payload, id=f"new-{len(rows)}")
            rows.append(row)
            return SimpleNamespace(data=[row])
        if self.kind == "update":
            hits = self._hits()
            for r in hits:
                r.update(self.payload)
            return SimpleNamespace(data=hits)
        if self.kind == "delete":
            hits = self._hits()
            for r in hits:
                rows.remove(r)
            return SimpleNamespace(data=hits)
        return SimpleNamespace(data=self._hits())

    def __repr__(self):
        return f"{self.kind} {self._table} {self.payload} where {self.filters}"


class _WriteDb:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return _WriteQuery(self, name)

    def wrote(self, kind, **payload):
        out = [c for c in self.writes(kind)
               if all(c.payload.get(k) == v for k, v in payload.items())]
        assert out, f"no {kind} {payload} among {self.calls}"
        return out

    def writes(self, kind):
        return [c for c in self.calls
                if c.kind == kind and c._table == "school_years"]


_SID = "sch-1"
_MISSING = object()


def _call(app, view, path, form, args=(), rows=None):
    """Post `form` to `view` as this school's admin; answer (flashes, db).

    `view` is the *decorated* function — the one the module exports — and the
    guards around it are peeled here rather than invoked, so a test that passes
    a lambda (which carries no `__wrapped__`, and so peels nothing) would run
    `login_required` and see a refusal with no sign of why. Take the module
    attribute and hand the URL's ids in through `args`.
    """
    import flask
    from flask import g

    db = _WriteDb(rows if rows is not None else [dict(RUNNING), dict(PAST)])
    # The role and login guards are peeled the way the rest of this suite peels
    # them: what is under test is what the form does, not who may post it, and
    # `login_required` wants a signed-in request that this file has no business
    # forging. The decorations themselves are asserted above, by source.
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    with app.test_request_context(path, method="POST", data=form):
        g.user_id, g.user_name, g.user_role = "u-1", "Admin SMP", "admin_sekolah"
        g.user_email, g.tz_offset, g.show = "admin@example.test", 7, {}
        g.user_school_id, g.user_class_id = _SID, None
        # The app is session-scoped and shared, so whatever it held is put back:
        # a test that leaves its fake in `extensions` hands the next one a
        # database that answers out of this test's two rows.
        previous = app.extensions.get("supabase", _MISSING)
        app.extensions["supabase"] = db
        try:
            view(*args)
            return flask.get_flashed_messages(with_categories=True), db
        finally:
            if previous is _MISSING:
                app.extensions.pop("supabase", None)
            else:
                app.extensions["supabase"] = previous


_FORM = {"name": "2027/2028", "start_date": "2027-07-01", "end_date": "2028-06-30"}


class TestCreatingAYear:
    def test_the_row_is_created_under_the_session_school(self, app):
        flashes, db = _call(app, adm.school_years, "/admin-sekolah/school-years",
                            dict(_FORM, is_active="1"))
        assert "audit_logs" in db.tables, (
            "the create is not audited")
        inserted = db.wrote("insert", name="2027/2028")[0]
        assert inserted.payload["school_id"] == _SID, (
            "the year is filed under whatever school_id the form carried, so one "
            "school can create a year inside another")
        assert inserted.payload["start_date"] == "2027-07-01"
        assert inserted.payload["is_active"] is True
        assert any("berhasil" in msg for _cat, msg in flashes), flashes

    def test_making_it_current_clears_the_one_that_was(self, app):
        _flashes, db = _call(app, adm.school_years, "/admin-sekolah/school-years",
                             dict(_FORM, is_active="1"))
        cleared = db.wrote("update", is_active=False)
        assert ("school_id", _SID) in cleared[0].filters, (
            "clearing the running year is not scoped to the school, so one "
            "school's new year switches off every other school's")
        assert db.calls.index(cleared[0]) < db.calls.index(db.wrote("insert")[0]), (
            "the new year is inserted before the old one is cleared, so whichever "
            "order the database applies them in decides which year is running")

    def test_a_duplicate_name_writes_nothing(self, app):
        flashes, db = _call(app, adm.school_years, "/admin-sekolah/school-years",
                            dict(_FORM, name="2025/2026"))
        assert not db.writes("insert"), "a year the school already has was inserted"
        assert any(cat == "error" for cat, _m in flashes), flashes


class TestEditingAYear:
    def test_it_writes_the_four_columns_on_that_row_alone(self, app):
        flashes, db = _call(app, adm.edit_school_year,
                            f"/admin-sekolah/school-years/{PAST['id']}/edit",
                            dict(_FORM), args=(PAST["id"],))
        writes = [c for c in db.writes("update")
                  if c.payload.get("name") == "2027/2028"]
        assert writes, f"the edit wrote no name: {db.calls}"
        assert writes[0].payload == {"name": "2027/2028", "start_date": "2027-07-01",
                                     "end_date": "2028-06-30", "is_active": False}, (
            "an edit that writes more than the year's own fields can overwrite "
            "something that hangs off it")
        assert ("id", PAST["id"]) in writes[0].filters
        assert ("school_id", _SID) in writes[0].filters, (
            "the update is addressed by id alone")
        assert db.rows[1]["name"] == "2027/2028", "the row did not change"
        assert db.rows[0]["name"] == RUNNING["name"], (
            "editing one year rewrote another")
        assert any("berhasil" in msg for _cat, msg in flashes), flashes

    def test_making_it_current_moves_the_flag_off_the_other_year(self, app):
        _flashes, db = _call(app, adm.edit_school_year,
                             f"/admin-sekolah/school-years/{PAST['id']}/edit",
                             dict(_FORM, is_active="1"), args=(PAST["id"],))
        assert db.rows[0]["is_active"] is False, (
            "two years are current at once")
        assert db.rows[1]["is_active"] is True
        cleared = db.wrote("update", is_active=False)
        targeted = [c for c in db.writes("update") if c.payload.get("name")][0]
        assert db.calls.index(cleared[0]) < db.calls.index(targeted), (
            "the new current year is written before the old one is cleared")

    def test_unticking_the_current_year_leaves_the_school_with_none(self, app):
        """Allowed on purpose: it is the only way to make the year that used to
        be current deletable, since the delete route refuses it while it is on."""
        _flashes, db = _call(app, adm.edit_school_year,
                             f"/admin-sekolah/school-years/{RUNNING['id']}/edit",
                             dict(_FORM), args=(RUNNING["id"],))
        assert db.rows[0]["is_active"] is False
        assert all(not r["is_active"] for r in db.rows)

    def test_a_window_that_runs_backwards_is_refused_before_anything_is_written(self, app):
        flashes, db = _call(app, adm.edit_school_year,
                            f"/admin-sekolah/school-years/{PAST['id']}/edit",
                            dict(_FORM, start_date="2028-06-30", end_date="2027-07-01"),
                            args=(PAST["id"],))
        assert not db.writes("update"), "a backwards window was written"
        assert any(cat == "error" for cat, _m in flashes), flashes

    def test_a_year_keeping_its_own_name_still_saves(self, app):
        """The duplicate check excludes the row being edited, end to end."""
        flashes, db = _call(app, adm.edit_school_year,
                            f"/admin-sekolah/school-years/{PAST['id']}/edit",
                            dict(_FORM, name=PAST["name"]), args=(PAST["id"],))
        assert any("berhasil" in msg for _cat, msg in flashes), flashes
        assert db.rows[1]["name"] == PAST["name"]

    def test_another_school_s_year_is_not_written(self, app):
        """The id in the URL is not the authority; the session's school is."""
        _flashes, db = _call(app, adm.edit_school_year,
                             f"/admin-sekolah/school-years/{PAST['id']}/edit",
                             dict(_FORM, name="dijarah"), args=(PAST["id"],))
        every = [c for c in db.writes("update") if c.payload.get("name")]
        assert every and all(("school_id", _SID) in c.filters for c in every), (
            "an update that is not filtered by the school writes another "
            "school's row when the id happens to match")


class TestTogglingAndDeleting:
    def test_activating_clears_the_other_year_first(self, app):
        _flashes, db = _call(app, adm.toggle_school_year,
                             f"/admin-sekolah/school-years/{PAST['id']}/toggle", {},
                             args=(PAST["id"],))
        assert db.rows[0]["is_active"] is False and db.rows[1]["is_active"] is True
        cleared = db.wrote("update", is_active=False)
        enabled = db.wrote("update", is_active=True)
        assert db.calls.index(cleared[0]) < db.calls.index(enabled[0])

    def test_deleting_an_inactive_year_removes_it(self, app):
        flashes, db = _call(app, adm.delete_school_year,
                            f"/admin-sekolah/school-years/{PAST['id']}/delete", {},
                            args=(PAST["id"],))
        assert [r["id"] for r in db.rows] == [RUNNING["id"]], db.rows
        assert (
            ("school_id", _SID) in db.writes("delete")[0].filters), (
            "the delete is addressed by id alone")
        assert any("berhasil" in msg for _cat, msg in flashes), flashes

    def test_deleting_the_running_year_deletes_nothing(self, app):
        flashes, db = _call(app, adm.delete_school_year,
                            f"/admin-sekolah/school-years/{RUNNING['id']}/delete", {},
                            args=(RUNNING["id"],))
        assert not db.writes("delete"), (
            "a direct POST removed the year every class and exam points at")
        assert [r["id"] for r in db.rows] == [RUNNING["id"], PAST["id"]]
        assert any("tidak bisa dihapus" in msg for _cat, msg in flashes), flashes


class TestNoButtonOnThePageIsDead:
    def test_every_form_posts_to_a_route_that_answers_a_post(self, app):
        """Every control on this page is a form, so a form whose target no route
        answers is a button that does nothing — which is what "admin belum bisa
        CRUD" looked like from the outside. The four verbs are asserted
        individually above; this is the sweep that catches the fifth form someone
        adds later, and it is why the create form's empty `action` (the same URL)
        is checked as a path too.
        """
        from werkzeug.exceptions import MethodNotAllowed, NotFound

        page = _render(app, [dict(RUNNING), dict(PAST)])
        actions = sorted(set(re.findall(r'action="(/[^"]*)"', page)))
        assert len(actions) >= 3, f"the page carries almost no forms: {actions}"
        adapter = app.url_map.bind("localhost")
        for action in actions:
            try:
                adapter.match(action, method="POST")
            except NotFound:
                raise AssertionError(f"{action} is a dead button — no route serves it")
            except MethodNotAllowed:
                raise AssertionError(f"{action} answers GET but not POST")


class TestThePageIsBilingual:
    def test_it_follows_the_toggle_rather_than_pinning_itself(self):
        """The page used to declare `content_lang = 'id'` while nine of its strings
        were plain Indonesian, so a school that reads English read none of it.

        The pin is what `deploy/i18n_coverage.py` reads as \"frozen\", and — the
        reason it belongs in this file rather than only in the gate — the page
        carries pairs now, so re-pinning it would leave the pairs dead under
        `lang="id"` while the count above the list still answered the toggle.
        """
        text = (ROOT / "app" / "templates" / TEMPLATE).read_text(encoding="utf-8")
        assert "content_lang" not in text, (
            "the page pins its own language while carrying bilingual copy")
        assert "t('" in text, "the page declares no language at all"


class TestDeletingTheRunningYearIsRefusedByTheServer:
    def test_the_route_reads_is_active_before_it_deletes(self):
        delete = SOURCE.read_text(encoding="utf-8-sig").split(
            "def delete_school_year")[1].split("def close_school_year")[0]
        guard = delete.split("try:")[0]
        assert 'select("is_active")' in guard and "return redirect" in guard, (
            "the running year is only hidden in the template — a direct POST "
            "still removes the year every class and exam points at")
        assert "tidak bisa dihapus" in guard, (
            "the refusal has to say what to do instead, or the admin is stuck")

    def test_the_template_explains_the_same_rule(self, app):
        page = _render(app, [dict(RUNNING)])
        assert "aktifkan tahun lain dulu" in page
