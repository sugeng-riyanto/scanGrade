"""The two preview doors, driven through the Flask client — not read off the source.

`tests/unit/test_grade_weight_seed.py` already holds `admin_grade_pupil_search` and
`admin_grade_pupil_marks` to the decorators and the `_school_id()` call they *name*.
That is a claim about the source, and it is exactly the kind of claim that stays true
while the door is open: a decorator can be present and the route still read a foreign
row, and `if not found: 404` can be written and never reached because the service
above it answered something truthy. What cannot be argued with is a request.

So these tests make the requests. The suite's own PostgREST stand-in
(`tests.unit.test_invigilation._DB`) holds **two schools'** rows — the caller's and a
foreign one — and *honours* every filter, so a route that dropped the school from its
query answers with the other school's pupil instead of an empty list, and the failure
names the leak rather than a missing line. Measured here:

* **a guru is refused, before a row is read** — the refusal is the decorator, not a
  filter, and the store records no read at all;
* **another school's pupil is a 404** on the marks door and on the compare door;
* **another school's subject is a 404** on the marks door;
* **the search never answers with a foreign pupil** — the foreign row is given the
  *same name*, so a search that lost its school filter returns it and the test fails;
* and, in the other direction, **a real pupil is answered with their real record**.

That last one is not decoration. A door that always refused would pass every refusal
test in this file, so the happy path is what makes the refusals mean something.

Read-only is asserted too: the store's log records writes, and the preview family
must not have any — a preview that can save is the defect these doors exist to avoid.

A fast way to check the guards bite rather than the app having a lucky shape: drop
`.eq("school_id", …)` from `find_pupils`, or the `subjects` ownership read in the
marks door, and the matching test below fails naming the leak.
"""
from __future__ import annotations

import pytest

from app.utils import auth as authmod
from app.utils.auth import dashboard_for
from tests.unit.test_invigilation import _DB

SCHOOL = "school-1"
FOREIGN = "school-2"

LOCAL_PUPIL = "murid-1"
FOREIGN_PUPIL = "murid-9"
SAME_NAME = "Rina Melati"

SUBJECT = "sub-1"
FOREIGN_SUBJECT = "sub-foreign"

YEAR = "year-1"
EXAM = "exam-1"
COMPONENT = "comp-1"

SEARCH = "/admin-sekolah/grade-weights/pupils"
MARKS = "/admin-sekolah/grade-weights/pupil-marks"
COMPARE = "/admin-sekolah/grade-weights/pupil-subjects"


def _tables() -> dict:
    """Two schools, with a pupil of each carrying the **same name**.

    The shared name is deliberate: it is what makes "the search never returns a
    foreign pupil" a real assertion instead of a coincidence of the fixture. The
    foreign pupil is also given a mark, so a door that forgets the school answers
    with a *number* rather than with nothing.
    """
    return {
        "profiles": [
            {"id": LOCAL_PUPIL, "full_name": SAME_NAME, "school_id": SCHOOL,
             "role": "murid"},
            {"id": FOREIGN_PUPIL, "full_name": SAME_NAME, "school_id": FOREIGN,
             "role": "murid"},
        ],
        "students": [
            {"id": LOCAL_PUPIL, "school_id": SCHOOL, "status": "active",
             "profiles": {"full_name": SAME_NAME}, "classes": {"name": "7A"}},
            {"id": FOREIGN_PUPIL, "school_id": FOREIGN, "status": "active",
             "profiles": {"full_name": SAME_NAME}, "classes": {"name": "7B"}},
        ],
        "subjects": [
            {"id": SUBJECT, "name": "Fisika", "school_id": SCHOOL, "is_active": True},
            {"id": "sub-2", "name": "Kimia", "school_id": SCHOOL, "is_active": True},
            {"id": FOREIGN_SUBJECT, "name": "Biologi", "school_id": FOREIGN,
             "is_active": True},
        ],
        "school_years": [
            {"id": YEAR, "name": "2026/2027", "school_id": SCHOOL, "is_active": True},
        ],
        "exams": [
            {"id": EXAM, "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_component_type_id": COMPONENT},
            {"id": "foreign-exam", "school_id": FOREIGN, "subject_id": FOREIGN_SUBJECT,
             "school_year_id": "foreign-year", "grade_component_type_id": "foreign-comp"},
        ],
        "submissions": [
            {"exam_id": EXAM, "student_id": LOCAL_PUPIL, "score": 80,
             "final_score": 80.0},
            # The foreign pupil has a mark too: if the school filter is lost, the
            # answer is a number that looks like a real preview.
            {"exam_id": "foreign-exam", "student_id": FOREIGN_PUPIL, "score": 95,
             "final_score": 95.0},
        ],
    }


# ── reading back what the door asked the database ───────────────────────────


def _reads(store) -> list[tuple]:
    return [entry for entry in store.log if entry[0] == "select"]


def _writes(store) -> list[tuple]:
    return [entry for entry in store.log if entry[0] != "select"]


def _filters(store, table: str) -> list[tuple]:
    """Every filter ``table`` was read with, across the whole request."""
    return [f for op, name, _payload, filters in store.log
            if op == "select" and name == table for f in filters]


# ── the request, as the app serves it ───────────────────────────────────────


def _session(role: str, school_id: str | None = SCHOOL) -> dict:
    """The session the decorators read — the same shape ``_apply_session`` maps."""
    return {"user_id": "u-1", "email": "admin@example.id", "name": "Admin",
            "role": role, "school_id": school_id, "class_id": None,
            "status": "active", "prefs": {}, "must_change_password": False}


@pytest.fixture
def store() -> _DB:
    return _DB(_tables())


@pytest.fixture
def client(app, monkeypatch):
    """A client whose database and session are answered locally.

    ``_session_for`` is replaced rather than a real token minted: the suite must
    not dial Supabase, and the decorators' own behaviour (role, status, timeouts)
    is part of what is under test here, so the session they resolve is handed to
    them. The token itself is still presented, because ``login_required`` reads
    one before it resolves anything.
    """
    src = app.test_client()
    monkeypatch.setattr("app.routes.admin_sekolah.get_supabase", lambda: src.store)
    monkeypatch.setattr(authmod, "_session_for", lambda token: src.session)
    return src


@pytest.fixture
def as_admin(client, store):
    """Signed in as the school's own admin, holding the fixture's rows."""
    _as(client, store, "admin_sekolah", SCHOOL)
    return client


def _as(client, store, role: str, school_id: str | None = SCHOOL):
    client.session = _session(role, school_id)
    client.store = store
    return client


def _get(client, path: str, **params):
    """One JSON request, with the session that was set up for it."""
    return client.get(path, query_string=params,
                      headers={"Authorization": "Bearer test-token",
                               "Accept": "application/json"})


# ── 1. a guru does not get in, and not after a row is read ──────────────────


class TestAGuruIsRefused:
    """``@admin_sekolah_required`` is the gate, and the gate is not a filter.

    A refusal that read the school's rows first and *then* decided would still
    answer 403 here, so the store is asserted untouched: the decorator runs inside
    ``login_required`` and before the body, and the only way to see that from
    outside is that nothing was asked.
    """

    @pytest.mark.parametrize("path,params", [
        (SEARCH, {"q": SAME_NAME}),
        (MARKS, {"student_id": LOCAL_PUPIL, "subject_id": SUBJECT}),
        (COMPARE, {"student_id": LOCAL_PUPIL}),
    ])
    def test_the_door_is_a_403_for_a_guru(self, client, store, path, params):
        _as(client, store, "guru")
        response = _get(client, path, **params)
        assert response.status_code == 403, (
            f"{path} answered {response.status_code} to a guru — the preview family "
            "is the school admin's, and a guru is not that")
        assert _reads(store) == [], "a refused caller had rows read for them"
        assert _writes(store) == []

    def test_a_guru_asking_for_a_page_is_sent_to_their_own_home(self, client, store):
        """The browser half of the same refusal: a redirect, not a 403 page."""
        _as(client, store, "guru")
        response = client.get(SEARCH, query_string={"q": SAME_NAME},
                              headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 302
        assert response.headers["Location"] == dashboard_for("guru"), (
            "a signed-in guru was not sent to the guru's own dashboard")
        assert _reads(store) == []

    def test_an_admin_of_no_school_is_not_offered_anything(self, client, store):
        """No school in the session means no pupils — not every pupil."""
        _as(client, store, "admin_sekolah", None)
        response = _get(client, SEARCH, q=SAME_NAME)
        assert response.status_code == 200
        assert response.get_json()["pupils"] == []
        assert _filters(store, "profiles") == [], (
            "a search with no school to scope it read the pupil table anyway")


# ── 2. the other school's pupil is not this school's ────────────────────────


class TestAnotherSchoolsPupil:
    """The pupil id is in the URL, so it is the one value a caller can forge."""

    def test_the_marks_door_is_a_404_for_a_foreign_pupil(self, as_admin):
        response = _get(as_admin, MARKS, student_id=FOREIGN_PUPIL, subject_id=SUBJECT)
        assert response.status_code == 404, (
            "a pupil of another school was answered by the marks door — a forged id "
            "must be a refusal, not an empty sample that reads like 'nothing graded'")

    def test_the_compare_door_is_a_404_for_a_foreign_pupil(self, as_admin):
        response = _get(as_admin, COMPARE, student_id=FOREIGN_PUPIL)
        assert response.status_code == 404

    def test_the_foreign_pupil_is_not_even_asked_about(self, as_admin):
        """A refusal that read the foreign rows first is a leak with a 404 on top."""
        _get(as_admin, COMPARE, student_id=FOREIGN_PUPIL)
        for op, table, _payload, filters in as_admin.store.log:
            if op != "select":
                continue
            for kind, column, value in filters:
                if kind == "in" and column in ("student_id", "id"):
                    assert FOREIGN_PUPIL not in [str(v) for v in value], (
                        f"the foreign pupil was read from {table} anyway, so the "
                        "refusal came after their rows were in the process")

    def test_the_local_pupil_is_answered(self, as_admin):
        """The same door, the same subject, the caller's own pupil — a real mark."""
        response = _get(as_admin, COMPARE, student_id=LOCAL_PUPIL)
        assert response.status_code == 200


class TestAnotherSchoolsSubject:
    """The subject is a filter the route applies itself, before any mark is read."""

    def test_the_marks_door_is_a_404_for_a_foreign_subject(self, as_admin):
        response = _get(as_admin, MARKS, student_id=LOCAL_PUPIL,
                        subject_id=FOREIGN_SUBJECT)
        assert response.status_code == 404, (
            "a subject of another school was accepted, so its weights and its papers "
            "can be previewed from this school's page")

    def test_the_subject_is_asked_for_with_the_school_on_it(self, as_admin):
        _get(as_admin, MARKS, student_id=LOCAL_PUPIL, subject_id=FOREIGN_SUBJECT)
        assert ("eq", "school_id", SCHOOL) in _filters(as_admin.store, "subjects"), (
            "the subject read did not carry the school, so ownership is being "
            "decided by something the id in the URL can change")


# ── 3. the picker can only ever offer this school's pupils ──────────────────


class TestTheSearchNeverReturnsAForeignPupil:
    """Both schools hold a pupil with the **same name**, on purpose.

    A fixture where the two names differ cannot tell a search that filters by
    school from one that got lucky with the string, so the foreign pupil is named
    ``Rina Melati`` as well — the answer is the local one, and only the local one.
    """

    def test_the_answer_holds_only_this_schools_pupil(self, as_admin):
        body = _get(as_admin, SEARCH, q=SAME_NAME).get_json()
        assert body["success"] is True
        ids = [p["id"] for p in body["pupils"]]
        assert ids == [LOCAL_PUPIL], (
            f"the picker offered {ids} for one name — the school is not a filter")

    def test_the_read_carried_both_the_school_and_the_role(self, as_admin):
        _get(as_admin, SEARCH, q=SAME_NAME)
        filters = _filters(as_admin.store, "profiles")
        assert ("eq", "school_id", SCHOOL) in filters, (
            "the name search is not scoped to the caller's school")
        assert ("eq", "role", "murid") in filters, (
            "the picker can offer staff, which is not a pupil to preview")

    def test_the_class_comes_with_the_row(self, as_admin):
        """Two learners can share a name; the class is how they are told apart."""
        pupils = _get(as_admin, SEARCH, q=SAME_NAME).get_json()["pupils"]
        assert pupils[0]["name"] == SAME_NAME
        assert pupils[0]["class_name"] == "7A"

    @pytest.mark.parametrize("q", ["", "R", "  ", "x"])
    def test_a_query_too_short_is_answered_with_nothing(self, as_admin, q):
        assert _get(as_admin, SEARCH, q=q).get_json()["pupils"] == []

    def test_the_search_cannot_be_pointed_at_another_school(self, as_admin):
        """``?school_id=`` is not a parameter — the session is the only source."""
        body = _get(as_admin, SEARCH, q=SAME_NAME, school_id=FOREIGN).get_json()
        assert [p["id"] for p in body["pupils"]] == [LOCAL_PUPIL]
        assert ("eq", "school_id", FOREIGN) not in _filters(as_admin.store, "profiles")


# ── 4. the doors open, which is what makes the refusals mean something ──────


class TestTheHappyPath:
    """A door that always refused would pass every test above."""

    def test_the_marks_door_answers_the_pupils_own_record(self, as_admin):
        body = _get(as_admin, MARKS, student_id=LOCAL_PUPIL,
                    subject_id=SUBJECT).get_json()
        assert body["success"] is True
        assert body["pupil"] == {"id": LOCAL_PUPIL, "name": SAME_NAME,
                                 "class_name": "7A"}
        assert body["marks"] == {COMPONENT: 80.0}
        assert body["scored"] == 1

    def test_the_compare_door_lists_only_the_subjects_with_a_mark(self, as_admin):
        body = _get(as_admin, COMPARE, student_id=LOCAL_PUPIL).get_json()
        assert body["success"] is True
        names = [s["name"] for s in body["subjects"]]
        assert names == ["Fisika"], (
            "a subject with no mark was listed, which reads as a zero")
        assert body["subjects"][0]["final"] == 80.0
        assert body["subjects"][0]["marks"] == {COMPONENT: 80.0}

    @pytest.mark.parametrize("path,params", [
        (MARKS, {}),
        (MARKS, {"student_id": LOCAL_PUPIL}),
        (MARKS, {"subject_id": SUBJECT}),
        (COMPARE, {}),
    ])
    def test_a_missing_id_is_named_rather_than_guessed(self, as_admin, path, params):
        response = _get(as_admin, path, **params)
        assert response.status_code == 400
        assert response.get_json()["error"]


# ── 5. a preview never saves ────────────────────────────────────────────────


class TestTheDoorsAreReads:
    """The whole family is a preview, so it must not be able to write."""

    @pytest.mark.parametrize("path,params", [
        (SEARCH, {"q": SAME_NAME}),
        (MARKS, {"student_id": LOCAL_PUPIL, "subject_id": SUBJECT}),
        (COMPARE, {"student_id": LOCAL_PUPIL}),
    ])
    def test_no_door_writes(self, as_admin, path, params):
        assert _get(as_admin, path, **params).status_code in (200, 400, 404)
        assert _writes(as_admin.store) == [], f"{path} wrote to the database"
