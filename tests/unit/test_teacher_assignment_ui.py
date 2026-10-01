"""The school admin's many-to-many assignment editor.

`/admin-sekolah/teachers` could only ever show the single `teachers.subject_id`
column, and the only writer of `teacher_assignments` was the exam builder's
one-pair-at-a-time dropdown. Requested: a matrix where one teacher is assigned to
many classes and many subjects at once, for the active year, school-scoped.

The guards here prove the rules a matrix needs and a single-pair write never did:
the diff is a soft close (never a delete), every id must belong to the caller's
school (a manipulated one is refused, not dropped), a pair with a live paper needs
an explicit confirmation, and a pre-045 row with no year still counts as the
active year instead of silently vanishing.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import teacher_assignments as ta


class Q:
    """A chainable Supabase query that filters the fake tables and records writes."""

    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.filters = []
        self.mode = "select"
        self.single_mode = False
        self.payload = None

    def single(self):
        self.single_mode = True
        return self

    maybe_single = single

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, {str(v) for v in values}))
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def upsert(self, rows, on_conflict=None):
        self.mode = "upsert"
        self.payload = (rows, on_conflict)
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = payload
        return self

    def execute(self):
        if self.mode == "upsert":
            self.sb.writes.append(("upsert", self.table, self.payload))
            return SimpleNamespace(data=self.payload[0])
        if self.mode == "update":
            self.sb.writes.append(("update", self.table, self.payload, list(self.filters)))
            return SimpleNamespace(data=[])
        rows = [r for r in self.sb.data.get(self.table, []) if self._match(r)]
        if self.single_mode:
            return SimpleNamespace(data=(rows[0] if rows else None))
        return SimpleNamespace(data=rows)

    def _match(self, row):
        for op, column, value in self.filters:
            if op == "eq" and str(row.get(column)) != str(value):
                return False
            if op == "in" and str(row.get(column)) not in value:
                return False
        return True


class Sb:
    def __init__(self, data=None):
        self.data = data or {}
        self.writes = []

    def table(self, name):
        return Q(self, name)


SCHOOL = "school-1"
TEACHER = "teacher-1"

BASE = {
    "school_years": [{"id": "y1", "name": "2026/2027", "is_active": True,
                       "school_id": SCHOOL}],
    "classes": [{"id": "c1", "school_id": SCHOOL}, {"id": "c2", "school_id": SCHOOL}],
    "subjects": [{"id": "s1", "school_id": SCHOOL}, {"id": "s2", "school_id": SCHOOL}],
    "profiles": [{"id": TEACHER, "school_id": SCHOOL, "role": "guru"}],
    "teachers": [{"id": TEACHER, "school_id": SCHOOL, "employee_id": "T1"}],
    "teacher_assignments": [],
    "exams": [],
}


def _sb(**overrides):
    data = {k: list(v) for k, v in BASE.items()}
    data.update(overrides)
    return Sb(data)


class TestDiff:
    def test_adding_a_pair_upserts_it_active_for_the_year(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        ok, result = ta.save(sb, SCHOOL, TEACHER,
                             [("c1", "s1"), ("c2", "s1")], year_name="2026/2027")
        assert ok, result
        assert result["added_count"] == 1 and result["removed_count"] == 0
        upserts = [w for w in sb.writes if w[0] == "upsert"]
        assert len(upserts) == 1
        rows, on_conflict = upserts[0][2]
        assert rows == [{"teacher_id": TEACHER, "class_id": "c2", "subject_id": "s1",
                         "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"}]
        assert on_conflict == "teacher_id,class_id,subject_id"
        assert not [w for w in sb.writes if w[0] == "update"]

    def test_removing_a_pair_deactivates_it_never_deletes_it(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        ok, result = ta.save(sb, SCHOOL, TEACHER, [], year_name="2026/2027")
        assert ok, result
        assert result["removed_count"] == 1
        updates = [w for w in sb.writes if w[0] == "update"]
        assert len(updates) == 1
        _op, table, payload, filters = updates[0]
        assert table == "teacher_assignments" and payload == {"status": "inactive"}
        assert ("eq", "class_id", "c1") in filters
        assert not [w for w in sb.writes if w[0] == "upsert"]

    def test_a_pre_045_row_with_no_year_is_still_part_of_the_active_year(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": None},
        ])
        # Re-saving the same pair must be a no-op, not a remove-then-add: the old
        # row is the assignment, and hiding it would deactivate it on the next save.
        ok, result = ta.save(sb, SCHOOL, TEACHER, [("c1", "s1")], year_name="2026/2027")
        assert ok, result
        assert result["added_count"] == 0 and result["removed_count"] == 0
        assert sb.writes == []


class TestSchoolScope:
    def test_a_class_from_another_school_is_refused_not_dropped(self):
        sb = _sb()
        ok, result = ta.save(sb, SCHOOL, TEACHER, [("other-class", "s1")])
        assert not ok
        assert result["status"] == 403
        assert sb.writes == [], "an invalid target must write nothing"

    def test_a_subject_from_another_school_is_refused(self):
        sb = _sb()
        ok, result = ta.save(sb, SCHOOL, TEACHER, [("c1", "other-subject")])
        assert not ok and result["status"] == 403

    def test_a_teacher_of_another_school_is_refused(self):
        sb = _sb()
        ok, result = ta.save(sb, SCHOOL, "teacher-elsewhere", [("c1", "s1")])
        assert not ok and result["status"] == 403


class TestRunningExamGuard:
    def _with_live_exam(self):
        return _sb(
            teacher_assignments=[{"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
                                  "school_id": SCHOOL, "status": "active",
                                  "school_year": "2026/2027"}],
            exams=[{"id": "e1", "title": "UTS Matematika", "teacher_id": TEACHER,
                    "subject_id": "s1", "school_id": SCHOOL, "class_ids": ["c1"],
                    "status": "active", "is_published": True}],
        )

    def test_removing_a_pair_with_a_live_paper_needs_confirmation(self):
        sb = self._with_live_exam()
        ok, result = ta.save(sb, SCHOOL, TEACHER, [], year_name="2026/2027")
        assert not ok and result["status"] == 409
        assert result["needs_confirmation"] is True
        assert [e["title"] for e in result["running"]] == ["UTS Matematika"]
        assert sb.writes == [], "the write must wait for the confirmation"

    def test_confirming_applies_the_removal(self):
        sb = self._with_live_exam()
        ok, result = ta.save(sb, SCHOOL, TEACHER, [], year_name="2026/2027",
                             confirm_remove=True)
        assert ok, result
        assert result["removed_count"] == 1

    def test_a_draft_paper_does_not_hold_the_removal(self):
        sb = self._with_live_exam()
        sb.data["exams"][0]["status"] = "draft"
        sb.data["exams"][0]["is_published"] = False
        ok, result = ta.save(sb, SCHOOL, TEACHER, [], year_name="2026/2027")
        assert ok, result and not result.get("needs_confirmation")


class TestRosterCounts:
    def test_counts_classes_and_subjects_once_each(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
            {"teacher_id": TEACHER, "class_id": "c2", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s2",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        counts = ta.school_pairs(sb, SCHOOL, "2026/2027")
        assert counts[TEACHER] == {"classes": 2, "subjects": 2}

    def test_inactive_rows_are_not_counted(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "inactive", "school_year": "2026/2027"},
        ])
        assert ta.school_pairs(sb, SCHOOL, "2026/2027") == {}


# ── The endpoints and the page wiring ─────────────────────────────────────────

from pathlib import Path

from app.routes import admin_sekolah as adm

TEMPLATE = (Path(__file__).resolve().parents[2] / "app" / "templates"
            / "admin_sekolah" / "teachers.html")


def _client(app, monkeypatch, sb):
    from app.utils import auth as authmod

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "admin-1", "email": "a@x", "name": "Admin",
        "role": "admin_sekolah", "school_id": SCHOOL, "status": "active",
    })
    monkeypatch.setattr(adm, "get_supabase", lambda: sb)
    # `require_school_access` imports get_supabase from app.utils.auth itself, so
    # patching only the route module leaves the decorator talking to production.
    monkeypatch.setattr(authmod, "get_supabase", lambda: sb)
    monkeypatch.setattr(adm, "_school_id", lambda: SCHOOL)
    monkeypatch.setattr(adm, "log_activity", lambda *a, **k: None)
    # The write routes carry @subscription_write_required, which would otherwise
    # reach the real billing service; these tests are about the assignment write.
    from app.utils import auth as _auth
    monkeypatch.setattr(_auth, "check_subscription_write", lambda *a, **k: (True, None))
    monkeypatch.setattr(adm, "invalidate_teacher_assignments", lambda *a, **k: None)
    monkeypatch.setattr(adm, "invalidate_school", lambda *a, **k: None)
    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf"
    return client


class TestAssignmentEndpoints:
    def test_get_returns_the_matrix_state(self, app, monkeypatch):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        client = _client(app, monkeypatch, sb)
        r = client.get(f"/admin-sekolah/teachers/{TEACHER}/assignments")
        assert r.status_code == 200, r.status_code
        body = r.get_json()
        assert body["year"]["name"] == "2026/2027"
        assert body["pairs"] == [{"class_id": "c1", "subject_id": "s1"}]
        assert {c["id"] for c in body["classes"]} == {"c1", "c2"}

    def test_another_schools_teacher_is_a_404_not_a_matrix(self, app, monkeypatch):
        client = _client(app, monkeypatch, _sb())
        r = client.get("/admin-sekolah/teachers/teacher-elsewhere/assignments")
        assert r.status_code == 404, r.status_code

    def test_a_class_from_another_school_is_refused_with_403(self, app, monkeypatch):
        sb = _sb()
        client = _client(app, monkeypatch, sb)
        r = client.post(f"/admin-sekolah/teachers/{TEACHER}/assignments",
                        json={"pairs": [{"class_id": "other-class", "subject_id": "s1"}]},
                        headers={"X-CSRF-Token": "csrf"})
        assert r.status_code == 403, r.status_code
        assert sb.writes == []

    def test_a_valid_save_writes_the_pair(self, app, monkeypatch):
        sb = _sb()
        client = _client(app, monkeypatch, sb)
        r = client.post(f"/admin-sekolah/teachers/{TEACHER}/assignments",
                        json={"pairs": [{"class_id": "c1", "subject_id": "s1"}]},
                        headers={"X-CSRF-Token": "csrf"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        assert r.get_json()["added"] == 1
        upserts = [w for w in sb.writes if w[0] == "upsert"]
        assert upserts and upserts[0][2][0][0]["class_id"] == "c1"


class TestPageWiring:
    def test_the_roster_offers_the_matrix_and_the_counts(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "open-assign" in src
        assert "/assignments" in src, "the modal must call the endpoint"
        assert "assignmentMatrix" in src
        assert "kelas, " in src and "mapel" in src
        assert "/teachers/" in src and "/assignments" in src


# ── The create/edit forms: many subjects, each in its own classes ─────────────
#
# Requested: a teacher's assignment should allow more than one subject and can be
# just for certain classes. The matrix did that, but the create/edit forms — where
# an admin actually types a teacher in — still offered one `subject_id` select and
# no class at all. These guards hold the form write to the same rows the matrix
# writes, and hold the roster to showing every subject, not only the legacy one.

from werkzeug.datastructures import MultiDict  # noqa: E402


class TestPairsFromForm:
    def test_each_subject_field_carries_its_own_classes(self):
        form = MultiDict([("name", "Budi"),
                          ("assign_s1", "c1"), ("assign_s1", "c2"),
                          ("assign_s2", "c1")])
        pairs = set(ta.pairs_from_form(form))
        assert pairs == {("c1", "s1"), ("c2", "s1"), ("c1", "s2")}, pairs

    def test_a_form_without_the_section_is_no_pairs(self):
        assert ta.pairs_from_form(MultiDict([("name", "Budi")])) == []

    def test_an_empty_subject_field_is_skipped(self):
        form = MultiDict([("assign_s1", "")])
        assert ta.pairs_from_form(form) == []


class TestSchoolPairsDetail:
    def test_detail_carries_the_ids_the_forms_need(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
            {"teacher_id": TEACHER, "class_id": "c2", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s2",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        d = ta.school_pairs_detail(sb, SCHOOL, "2026/2027")[TEACHER]
        assert d["classes"] == 2 and d["subjects"] == 2
        assert d["subject_ids"] == ["s1", "s2"]
        assert set(d["pairs"]) == {"c1|s1", "c2|s1", "c1|s2"}

    def test_the_counts_helper_keeps_its_old_shape(self):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        assert ta.school_pairs(sb, SCHOOL, "2026/2027") == {TEACHER: {"classes": 1, "subjects": 1}}


class TestCreateFormWritesTheSelection:
    def _stub(self, monkeypatch, sb):
        monkeypatch.setattr(adm, "_get_email_domain", lambda sid: "sekolah.id")

        def fake_create(supabase, **kw):
            sb.data.setdefault("profiles", []).append(
                {"id": "teacher-new", "school_id": SCHOOL, "role": "guru"})
            return "teacher-new"
        monkeypatch.setattr(adm, "create_teacher_account", fake_create)

    def test_two_subjects_in_their_own_classes(self, app, monkeypatch):
        sb = _sb()
        self._stub(monkeypatch, sb)
        client = _client(app, monkeypatch, sb)
        r = client.post("/admin-sekolah/teachers/create",
                        data={"name": "Budi", "assignment_present": "1",
                              "assign_s1": ["c1", "c2"], "assign_s2": ["c1"]},
                        headers={"X-CSRF-Token": "csrf"}, follow_redirects=True)
        assert r.status_code in (200, 301, 302), r.status_code
        upserts = [w for w in sb.writes if w[0] == "upsert"]
        assert upserts, ("the form's assignment was not written: "
                         + r.get_data(as_text=True)[:600])
        rows = upserts[0][2][0]
        assert {(x["class_id"], x["subject_id"]) for x in rows} == {
            ("c1", "s1"), ("c2", "s1"), ("c1", "s2")}
        for x in rows:
            assert x["teacher_id"] == "teacher-new" and x["school_id"] == SCHOOL
            assert x["status"] == "active" and x["school_year"] == "2026/2027"

    def test_no_assignment_section_writes_nothing(self, app, monkeypatch):
        sb = _sb()
        self._stub(monkeypatch, sb)
        client = _client(app, monkeypatch, sb)
        client.post("/admin-sekolah/teachers/create", data={"name": "Siti"},
                    headers={"X-CSRF-Token": "csrf"})
        assert not [w for w in sb.writes if w[0] == "upsert"]


class TestEditFormWritesTheSelection:
    def test_a_second_subject_is_added_and_the_first_kept(self, app, monkeypatch):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        client = _client(app, monkeypatch, sb)
        r = client.post(f"/admin-sekolah/teachers/{TEACHER}/edit",
                        data={"name": "Budi", "assignment_present": "1",
                              "assign_s1": ["c1"], "assign_s2": ["c2"]},
                        headers={"X-CSRF-Token": "csrf"})
        assert r.status_code in (200, 301, 302), r.status_code
        upserts = [w for w in sb.writes if w[0] == "upsert"]
        assert [(x["class_id"], x["subject_id"]) for x in upserts[0][2][0]] == [("c2", "s2")]

    def test_unchecking_everything_is_a_soft_close_not_a_delete(self, app, monkeypatch):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        client = _client(app, monkeypatch, sb)
        client.post(f"/admin-sekolah/teachers/{TEACHER}/edit",
                    data={"name": "Budi", "assignment_present": "1"},
                    headers={"X-CSRF-Token": "csrf"})
        closes = [w for w in sb.writes if w[0] == "update" and w[1] == "teacher_assignments"]
        assert closes and closes[0][2] == {"status": "inactive"}
        assert not [w for w in sb.writes if w[0] == "upsert"], (
            "a removal must be a soft close, never a delete or a re-add")

    def test_an_edit_without_the_section_leaves_assignments_alone(self, app, monkeypatch):
        sb = _sb(teacher_assignments=[
            {"teacher_id": TEACHER, "class_id": "c1", "subject_id": "s1",
             "school_id": SCHOOL, "status": "active", "school_year": "2026/2027"},
        ])
        client = _client(app, monkeypatch, sb)
        client.post(f"/admin-sekolah/teachers/{TEACHER}/edit",
                    data={"name": "Budi"}, headers={"X-CSRF-Token": "csrf"})
        assignment_writes = [w for w in sb.writes if w[1] == "teacher_assignments"]
        assert not assignment_writes, (
            "an unrelated edit must not touch the teacher's assignment")
        legacy = [w for w in sb.writes if w[1] == "teachers"]
        assert not legacy or "subject_id" not in legacy[0][2], (
            "an unrelated edit must not null the legacy subject column")


class TestTemplateWiring:
    def test_the_forms_carry_the_assignment_picker(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "macro assignment_picker" in src
        assert src.count("{{ assignment_picker(") == 2, (
            "both the create and the edit form must offer the picker")
        assert 'name="assignment_present"' in src
        assert "assign_{{ s.id }}" in src

    def test_the_roster_shows_every_assigned_subject(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "t.assign_subject_names" in src, (
            "the roster must list all assigned subjects, not only the legacy one")

    # ── "Semua kelas" vs "kelas tertentu": the all-or-specific shortcut ─────────
    #
    # Requested: "Penugasan guru harusnya bisa lebih dari satu mata pelajaran dan
    # bisa hanya kelas tertentu. Ada pilihan semua, tertentu." Checking a class per
    # subject is the little picture; the whole picture is a teacher who takes one
    # subject in every class (the common case) and another in only two of them. So
    # each subject needs a one-click "Semua kelas" beside the individual classes.
    def test_the_form_picker_offers_all_or_specific_classes(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert 'class="rounded border-indigo-300 sg-assign-all"' in src or \
            "sg-assign-all" in src, "the picker needs a per-subject 'Semua kelas' box"
        assert "data-all-for" in src and "data-class-for" in src
        assert "Semua kelas" in src
        # The shortcut is a client-side convenience over the same explicit class
        # checkboxes the form already submits — the server stays the only
        # authority on what a pair is, so no new field is invented.
        assert "function sgAssignAll(" in src
        assert "function sgAssignSync(" in src, (
            "unchecking a class must clear the 'all' box, or it claims all while one is off")

    def test_the_matrix_offers_all_subjects_for_a_class(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "semua mapel" in src, (
            "a class row needs the mirror shortcut: every subject for that class")
        assert "toggleClassAll(" in src, "the mirror shortcut must be backed by the component"
        assert "toggleSubjectAll(" in src, "the per-subject 'semua kelas' shortcut must stay"
