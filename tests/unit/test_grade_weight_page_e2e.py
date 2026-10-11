"""The weight page end to end: sign in, seed a learner, check the table's numbers.

The pieces are each guarded somewhere else — `test_grade_weight_seed.py` pins the
seed read and the page's wiring, `test_grade_weight_compare.py` the comparison's
service rule, `test_grade_weight_doors.py` the two preview doors as *requests*, and
`test_grade_weighting.py` the arithmetic. What none of them does is walk the chain
the way a school admin does: sign in at the door, open the page, name a learner, and
then read the number the page will print beside each of that learner's subjects.

So this file drives the real things and fakes only the two network boundaries:

* the **credential check** (`_sign_in_with_retry`) and the **session resolution**
  (`auth.get_user`) are the two GoTrue calls; everything between them is the app's
  own — the CSRF check, the cookie the door sets, `login_required`, the profile row
  it reads for the role and the school, the admin gate, the template, the doors;
* the database is the suite's PostgREST stand-in, extended in one place
  (`_SingleDB`) because `_fetch_session` reads `.single().execute().data` and does
  `pd.get(...)` on it — the shared fake answers a *list* there, which sends the
  session through its "the newest column is missing" retry, drains the
  process-wide registry, and answers an empty profile (an admin with no role, so
  the page refuses). PostgREST answers one row for `.single()`; the fake has to as
  well, or this test would be exercising a code path the box does not have.

## The fixture, and the numbers by hand

One school, one learner (`Rina Melati`, 7A) with marks in three of four subjects.
Two component types (UTS, UAS); the school default is **50/50**, and Fisika has
saved its **own** 40/60 so the table exercises both "has its own row" and "follows
the default":

| Subject | UTS | UAS | policy | final |
|---|---|---|---|---|
| Fisika | 80 | 90 | own 40/60 | 80×0.4 + 90×0.6 = **86.0** |
| Kimia | 60 | 70 | default 50/50 | 60×0.5 + 70×0.5 = **65.0** |
| Biologi | 55 | — | default 50/50 | 55×0.5 + **0**×0.5 = **27.5** |
| Sejarah | — | — | default 50/50 | **not a row** — nothing graded |

Biologi's `27.5` is deliberate: the missing component is *zero*, the module's own
policy, and the page shows that gap rather than renormalising the weights. Sejarah
is absent rather than `0.0` because a mark nobody has earned would read as a fail.

Order is subject-name order — the comparison is a comparison, not a ranking.

## The other page, and why it is here

The comparison answers a policy for one learner across subjects. The same policy
is printed for a teacher as one subject across a class (`/teacher/students`), and
that page is a *different* read — `grade_weighting.subject_finals` where the
comparison uses `pupil_subject_finals`. Both end in the same `compute` on the same
effective config, so the two must agree digit for digit; the last class signs the
teacher in as the account that owns Fisika and asserts exactly that, through the
two rendered pages rather than through the service they share. The teacher's
scoping is asserted beside it: a guru sees their assigned classes only, and asking
the roster for a subject they do not teach falls back rather than answering with
another subject's class.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import auth as auth_routes
from app.services import grade_weighting as gw
from tests.unit.test_invigilation import _DB, _Query, _Res

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own rule")

SCHOOL = "s1"
FOREIGN = "s2"
YEAR = "y1"
ADMIN = "u-admin"
TEACHER = "u-ani"
PUPIL = "p-rina"
OTHER_PUPIL = "p-budi"
ADMIN_EMAIL = "admin@example.id"
TEACHER_EMAIL = "ani@example.id"
CLASS_7A = "c-7a"
CLASS_7B = "c-7b"
CITRA = "p-citra"
#: A fresh token per sign-in, because the session cache is keyed on the token and
#: this file signs in more than once. Reusing one string made the *pupil's* test read
#: the admin session an earlier test had cached under it — the app's cache answering
#: what it was asked, with a token no two real sign-ins ever share.
TOKEN_PREFIX = "e2e-access-token"

FISIKA, KIMIA, BIOLOGI, SEJARAH = "sub-fis", "sub-kim", "sub-bio", "sub-sej"
UTS, UAS = "gc-uts", "gc-uas"

PAGE = "/admin-sekolah/grade-weights"
SEARCH = f"{PAGE}/pupils"
COMPARE = f"{PAGE}/pupil-subjects"

#: Subject name → the final the table must print, computed by hand from the
#: fixture below (and asserted against the roster read as well as the page).
BY_HAND = {"Fisika": 86.0, "Kimia": 65.0, "Biologi": 27.5}


def _tables() -> dict:
    """Two schools; one learner with three subjects; one subject he never sat."""
    return {
        "schools": [{"id": SCHOOL, "name": "SMA Satu", "npsn": "11111111"}],
        "profiles": [
            {"id": ADMIN, "school_id": SCHOOL, "role": "admin_sekolah",
             "full_name": "Admin Sekolah", "status": "active",
             "email": ADMIN_EMAIL},
            {"id": PUPIL, "school_id": SCHOOL, "role": "murid",
             "full_name": "Rina Melati", "status": "active", "class_id": CLASS_7A},
            # The same name at another school: a search that lost its school filter
            # offers this one instead, and every assertion below would move.
            {"id": FOREIGN + "-pupil", "school_id": FOREIGN, "role": "murid",
             "full_name": "Rina Melati", "status": "active"},
            # A same-school pupil in a class this teacher does **not** hold. The
            # roster's class scoping is only measurable if the school has a pupil
            # outside it, or "assigned classes only" and "the whole school" answer
            # the same list and the test proves nothing.
            {"id": CITRA, "school_id": SCHOOL, "role": "murid",
             "full_name": "Citra Dewi", "status": "active", "class_id": CLASS_7B},
            # The teacher who owns Fisika. Signing in as this account is what makes
            # the roster below *their* roster: the subjects and classes come from
            # the assignment rows, not from anything the test hands the page.
            {"id": TEACHER, "school_id": SCHOOL, "role": "guru",
             "full_name": "Ani Wijaya", "status": "active",
             "email": TEACHER_EMAIL},
        ],
        # Only Fisika is assigned to this teacher; his roster must not show Kimia.
        "teacher_assignments": [
            {"id": "ta-1", "school_id": SCHOOL, "teacher_id": TEACHER,
             "class_id": CLASS_7A, "subject_id": FISIKA, "status": "active",
             "school_year": "2026/2027"},
        ],
        "students": [
            {"id": PUPIL, "school_id": SCHOOL, "status": "active",
             "profiles": {"full_name": "Rina Melati"}, "classes": {"name": "7A"}},
            {"id": OTHER_PUPIL, "school_id": SCHOOL, "status": "active",
             "profiles": {"full_name": "Budi Santoso"}, "classes": {"name": "7A"}},
            {"id": FOREIGN + "-pupil", "school_id": FOREIGN, "status": "active",
             "profiles": {"full_name": "Rina Melati"}, "classes": {"name": "7B"}},
            {"id": CITRA, "school_id": SCHOOL, "status": "active",
             "profiles": {"full_name": "Citra Dewi"}, "classes": {"name": "7B"}},
        ],
        "classes": [
            {"id": CLASS_7A, "school_id": SCHOOL, "name": "7A", "grade_level": "7"},
            {"id": CLASS_7B, "school_id": SCHOOL, "name": "7B", "grade_level": "7"},
        ],
        "subjects": [
            {"id": FISIKA, "school_id": SCHOOL, "name": "Fisika", "is_active": True},
            {"id": KIMIA, "school_id": SCHOOL, "name": "Kimia", "is_active": True},
            {"id": BIOLOGI, "school_id": SCHOOL, "name": "Biologi", "is_active": True},
            {"id": SEJARAH, "school_id": SCHOOL, "name": "Sejarah", "is_active": True},
        ],
        "school_years": [
            {"id": YEAR, "school_id": SCHOOL, "name": "2026/2027", "is_active": True,
             "status": "open"},
        ],
        # The school default: 50/50, read from the components' own `default_weight`.
        "grade_component_type": [
            {"id": UTS, "school_id": SCHOOL, "name": "UTS", "is_active": True,
             "default_weight": 50, "sort_order": 1},
            {"id": UAS, "school_id": SCHOOL, "name": "UAS", "is_active": True,
             "default_weight": 50, "sort_order": 2},
        ],
        # ...and Fisika's own row, so one subject does not follow it.
        "grade_weight_config": [
            {"id": "cfg-1", "school_id": SCHOOL, "subject_id": FISIKA,
             "school_year_id": YEAR, "component_id": UTS, "weight_percent": 40,
             "is_active": True},
            {"id": "cfg-2", "school_id": SCHOOL, "subject_id": FISIKA,
             "school_year_id": YEAR, "component_id": UAS, "weight_percent": 60,
             "is_active": True},
        ],
        "exams": [
            {"id": "ex-1", "school_id": SCHOOL, "subject_id": FISIKA,
             "school_year_id": YEAR, "grade_component_type_id": UTS},
            {"id": "ex-2", "school_id": SCHOOL, "subject_id": FISIKA,
             "school_year_id": YEAR, "grade_component_type_id": UAS},
            {"id": "ex-3", "school_id": SCHOOL, "subject_id": KIMIA,
             "school_year_id": YEAR, "grade_component_type_id": UTS},
            {"id": "ex-4", "school_id": SCHOOL, "subject_id": KIMIA,
             "school_year_id": YEAR, "grade_component_type_id": UAS},
            {"id": "ex-5", "school_id": SCHOOL, "subject_id": BIOLOGI,
             "school_year_id": YEAR, "grade_component_type_id": UTS},
        ],
        "submissions": [
            {"exam_id": "ex-1", "student_id": PUPIL, "score": 80, "final_score": None},
            {"exam_id": "ex-2", "student_id": PUPIL, "score": 82, "final_score": 90},
            {"exam_id": "ex-3", "student_id": PUPIL, "score": 60, "final_score": None},
            {"exam_id": "ex-4", "student_id": PUPIL, "score": 70, "final_score": None},
            {"exam_id": "ex-5", "student_id": PUPIL, "score": 55, "final_score": None},
            # A classmate's paper: it must never move this learner's means.
            {"exam_id": "ex-1", "student_id": OTHER_PUPIL, "score": 100,
             "final_score": None},
        ],
    }


# ── the database, answering `.single()` the way PostgREST does ──────────────


class _SingleQuery(_Query):
    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        self._one = True
        return self

    def execute(self):
        res = super().execute()
        if getattr(self, "_one", False) and self._op == "select":
            rows = res.data if isinstance(res.data, list) else [res.data]
            return _Res(dict(rows[0]) if rows else {})
        return res


class _SingleDB(_DB):
    """`_DB`, with `.single()` answering a row — see the module docstring."""

    def table(self, name):
        return _SingleQuery(self, name)


# ── the two network boundaries, and nothing else ───────────────────────────


#: Who a sign-in is for. The role is *not* here: it is read from the account's
#: profile row when the request resolves, which is the point of signing in.
ACCOUNTS = {
    ADMIN: (ADMIN_EMAIL, "Admin Sekolah"),
    TEACHER: (TEACHER_EMAIL, "Ani Wijaya"),
}


def _user(user_id: str):
    email, name = ACCOUNTS[user_id]
    return SimpleNamespace(id=user_id, email=email,
                           user_metadata={"full_name": name})


def _login_result(token: str, user_id: str = ADMIN):
    """What GoTrue answers a correct password with."""
    return SimpleNamespace(
        user=_user(user_id),
        session=SimpleNamespace(access_token=token, refresh_token="e2e-refresh"),
    )


class _Doors:
    """The two GoTrue boundaries, standing in for **several** signed-in readers.

    One token per sign-in, kept in a map, because one test can need two readers at
    once — the admin whose comparison is the answer and the teacher whose roster
    has to print it — and the app resolves the session from the cookie on every
    request. The first shape of this registered a single token, so the second
    sign-in replaced the first and whichever client asked second carried a token
    the fake no longer knew: the app's own refusal, with a fixture's bug behind it.
    """

    def __init__(self, app, db, monkeypatch):
        self.app = app
        self.tokens: dict[str, str] = {}
        app.extensions["supabase"] = db
        app.extensions["supabase_auth"] = SimpleNamespace(auth=SimpleNamespace(
            get_user=self._get_user,
            admin=SimpleNamespace(list_users=lambda: []),
        ))
        monkeypatch.setattr(auth_routes, "_sign_in_with_retry", self._attempt)

    def _get_user(self, presented):
        if presented not in self.tokens:
            raise AssertionError(f"a request carried a token nobody issued: {presented!r}")
        return SimpleNamespace(user=_user(self.tokens[presented]))

    def _attempt(self, client, email, password):
        user_id = next(uid for uid, (addr, _n) in ACCOUNTS.items() if addr == email)
        token = f"{TOKEN_PREFIX}-{uuid.uuid4().hex}"
        self.tokens[token] = user_id
        return _login_result(token, user_id)

    def sign_in(self, user_id: str = ADMIN):
        """Sign one reader in through `/auth/sign-in`; `(client, response)`."""
        email, _name = ACCOUNTS[user_id]
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "csrf"
        response = client.post("/auth/sign-in", data={
            "email": email, "password": "the-password", "_csrf_token": "csrf"})
        return client, response


@pytest.fixture
def db() -> _SingleDB:
    return _SingleDB(_tables())


@pytest.fixture
def doors(app, db, monkeypatch):
    """The one place sessions are faked, so a test can sign in two readers."""
    return _Doors(app, db, monkeypatch)


def _sign_in(app, db, monkeypatch, user_id: str = ADMIN):
    """Sign in through the door; return ``(client, response)``."""
    return _Doors(app, db, monkeypatch).sign_in(user_id)


def _landed(client, response, home: str, who: str):
    assert response.status_code == 302 and response.headers["Location"] == home, (
        f"the sign-in door did not land the {who} on {home}: "
        f"{response.status_code} {response.headers.get('Location')}")
    return client


@pytest.fixture
def as_admin(doors):
    """A client that has actually signed in, so the page is the admin's own."""
    return _landed(*doors.sign_in(ADMIN), "/admin-sekolah/dashboard", "school admin")


@pytest.fixture
def as_guru(doors):
    """The teacher who owns Fisika, signed in at the same door.

    The role is not passed in — it is the profile row's, read back when the first
    request resolves — so the landing page is the check that the account is a guru
    and not the admin the sibling fixture signs in as.
    """
    return _landed(*doors.sign_in(TEACHER), "/teacher/dashboard", "teacher")


def _get_json(client, path: str, **params):
    return client.get(path, query_string=params, headers={"Accept": "application/json"})


# ── the page's own JSON, read out of what it renders ───────────────────────


def _json_literal(html: str, key: str):
    """The value the page hands Alpine as `key` (`weights`, `defaults`, `rows`).

    The teacher's roster hands over a *list* (`rows: [...]`) where the weights page
    hands over objects, so the scanner finds whichever of the two brackets comes
    first and balances on that one. Jinja's `tojson` escapes `<`, `>` and `&`, and
    the data here is names and numbers, so a bracket inside a string is not a case
    this has to survive.
    """
    at = html.index(f"{key}: ")
    opens = [i for i in (html.find("[", at), html.find("{", at)) if i != -1]
    assert opens, f"the page rendered nothing for {key}"
    start = min(opens)
    open_ch = html[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    for i in range(start, len(html)):
        if html[i] == open_ch:
            depth += 1
        elif html[i] == close_ch:
            depth -= 1
            if depth == 0:
                return json.loads(html[start:i + 1])
    raise AssertionError(f"the page rendered no complete JSON value for {key}")


PREVIEW = re.compile(r"function sgPreviewFinal\([^)]*\) \{(.*?)\r?\n\}", re.S)


def _page_rule(cases: list[tuple[dict, dict]]) -> list[dict]:
    """Run the page's own arithmetic over every row, in one interpreter."""
    match = PREVIEW.search(WEIGHTS_HTML.read_text(encoding="utf-8"))
    assert match, "the page no longer defines the preview's arithmetic"
    rows = [{"weights": w, "marks": m} for w, m in cases]
    script = (
        "const src = " + json.dumps(match.group(0)) + ";\n"
        "eval(src);\n"
        "const rows = " + json.dumps(rows) + ";\n"
        "console.log(JSON.stringify(rows.map(r => sgPreviewFinal(r.weights, r.marks))));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


# ── 1. the sign-in ─────────────────────────────────────────────────────────


class TestTheAdminSignsInThroughTheDoor:

    def test_the_credentials_are_accepted_and_land_on_the_admin_home(self, app, db, monkeypatch):
        client, response = _sign_in(app, db, monkeypatch)
        assert response.status_code == 302, (
            "the sign-in door refused a valid school admin: " + response.get_data(as_text=True)[:400])
        assert response.headers["Location"] == "/admin-sekolah/dashboard"
        assert any("access_token" in cookie
                   for cookie in response.headers.getlist("Set-Cookie")), (
            "the door did not issue a session cookie, so nothing after this is "
            "signed in")

    def test_the_session_the_door_issued_is_the_admin_the_page_reads(self, app, db, monkeypatch):
        """No `_session_for` stub: the profile row decides the role and the school.

        This is what makes the page that follows *the admin's own* page rather than
        a page that trusts a dict this test handed it.
        """
        client, _ = _sign_in(app, db, monkeypatch)
        page = client.get(PAGE)
        assert page.status_code == 200, (
            "a signed-in school admin was refused their own weights page: "
            f"{page.status_code}")

    def test_a_pupil_signing_in_the_same_way_is_refused_the_admin_page(self, app, db, monkeypatch):
        """The door is the same door; the page is the admin's.

        The learner's own role row is what refuses this, not the sign-in: the merged
        door has no "wrong door" left to refuse, so a pupil signing in is a success
        that lands on their own dashboard — and this page must not open for them.
        """
        db.tables["profiles"][0] = dict(db.tables["profiles"][0],
                                        role="murid", full_name="Rina Melati")
        client, response = _sign_in(app, db, monkeypatch)
        assert response.status_code == 302
        assert response.headers["Location"] == "/student/dashboard"
        refused = client.get(PAGE)
        assert refused.status_code != 200, (
            "a pupil reached the school admin's weights page")
        assert "/student/dashboard" in refused.headers.get("Location", ""), (
            "the refusal must send the reader to their own home, not to the sign-in "
            "page they are already signed in at")


# ── 2. the page, and what it hands its own JavaScript ──────────────────────


class TestThePageRendersForTheAdmin:

    def test_the_preview_and_the_comparison_are_on_the_page(self, as_admin):
        page = as_admin.get(PAGE)
        html = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "data-preview-pupil" in html, "no way to name a learner on the page"
        assert "data-preview-subjects" in html, "the page has no across-subjects table"

    def test_the_weights_it_shows_are_the_ones_the_school_saved(self, as_admin):
        """The table's live column is computed from *these*, so they are asserted.

        Fisika has its own row (40/60) while Kimia and Biologi follow the school
        default (50/50) — so the page and the server agreeing row by row below is
        not the weaker claim that one policy was applied to everything.
        """
        html = as_admin.get(PAGE).get_data(as_text=True)
        matrix = _json_literal(html, "weights")
        defaults = _json_literal(html, "defaults")
        assert defaults == {UTS: 50, UAS: 50}, (
            "the school default the page shows is not the one the components carry")
        assert matrix[FISIKA] == {UTS: 40, UAS: 60}, (
            "Fisika's own distribution is not the one the page renders")
        for subject in (KIMIA, BIOLOGI):
            assert matrix[subject] == defaults, (
                f"{subject} should follow the school default until it is given its own row")


# ── 3. seeding a learner: the two doors the page reads ─────────────────────


class TestSeedingALearner:

    def test_the_search_finds_this_schools_pupil_and_no_other(self, as_admin):
        body = _get_json(as_admin, SEARCH, q="Rina").get_json()
        assert body["success"] is True
        assert [p["id"] for p in body["pupils"]] == [PUPIL], (
            "the picker offered a learner from another school, or the wrong one")
        assert body["pupils"][0]["name"] == "Rina Melati"
        assert body["pupils"][0]["class_name"] == "7A"

    def test_the_table_lists_every_subject_the_learner_has_a_mark_in(self, as_admin):
        body = _get_json(as_admin, COMPARE, student_id=PUPIL).get_json()
        assert body["success"] is True
        assert body["pupil"] == {"id": PUPIL, "name": "Rina Melati", "class_name": "7A"}
        assert [row["name"] for row in body["subjects"]] == ["Biologi", "Fisika", "Kimia"], (
            "the comparison must be the learner's own subjects, in name order — "
            "and not rank them")

    def test_a_subject_with_nothing_graded_is_not_a_row(self, as_admin):
        """Sejarah is offered by the school and the learner has sat nothing in it.

        A row would print `0.0` — the weighted branch answers 0 for a subject with
        no scored paper — and a hard zero beside a subject nobody has sat is a
        failed subject the learner has not failed.
        """
        rows = _get_json(as_admin, COMPARE, student_id=PUPIL).get_json()["subjects"]
        assert SEJARAH not in [row["subject_id"] for row in rows]
        assert all(row["scored"] > 0 for row in rows)

    def test_a_classmates_paper_does_not_move_this_learners_marks(self, as_admin):
        """Budi scored 100 on ex-1; Rina's UTS mean must stay 80."""
        rows = _get_json(as_admin, COMPARE, student_id=PUPIL).get_json()["subjects"]
        fisika = next(r for r in rows if r["subject_id"] == FISIKA)
        assert fisika["marks"] == {UTS: 80.0, UAS: 90.0}


# ── 4. the table's numbers, against the server ─────────────────────────────


class TestTheTablesNumbers:

    def _rows(self, as_admin) -> list[dict]:
        return _get_json(as_admin, COMPARE, student_id=PUPIL).get_json()["subjects"]

    def test_every_final_is_the_one_computed_by_hand_from_the_fixture(self, as_admin):
        got = {row["name"]: row["final"] for row in self._rows(as_admin)}
        assert got == BY_HAND, (
            "the comparison's finals do not match the fixture's own arithmetic — "
            "the weights are read from the wrong place (a subject's own row vs the "
            "school default), or a missing component is being renormalised instead "
            "of counting as zero")
        for row in self._rows(as_admin):
            assert row["mode"] == "weighted" and row["total"] == 100, (
                f"{row['name']} is not being computed under a full weighted policy")

    def test_every_final_is_the_number_the_teachers_roster_reports(self, as_admin, db):
        """The other read of the same fact: the table cannot disagree with the roster.

        `subject_finals` is what the teacher's grade table and both exports are built
        from — one subject, every pupil — while the comparison reads one pupil across
        subjects. Same `compute`, same weights, same scoping, two entry points: a
        divergence here is a policy that reads one way on the admin page and another
        way on the page a teacher prints.
        """
        for row in self._rows(as_admin):
            roster = gw.subject_finals(db, SCHOOL, row["subject_id"], YEAR, [PUPIL])
            assert roster[PUPIL]["final"] == row["final"], (
                f"{row['name']}: the comparison says {row['final']} and the roster "
                f"says {roster[PUPIL]['final']} for the same learner")
            assert roster[PUPIL]["mode"] == row["mode"]

    @needs_node
    def test_the_pages_own_rule_agrees_with_the_server_for_every_row(self, as_admin):
        """"With these weights" must equal "Saved" before anything is typed.

        The saved policy is what the page loads with, so the live column and the
        server's number are the same question — and the page computes its column with
        its own copy of the rule (`sgPreviewFinal`). This is the assertion that the
        copy is still the server's rule, made against the numbers the doors actually
        returned rather than against a fixture written for the arithmetic.
        """
        html = as_admin.get(PAGE).get_data(as_text=True)
        matrix, defaults = _json_literal(html, "weights"), _json_literal(html, "defaults")
        rows = self._rows(as_admin)
        cases = [(matrix.get(row["subject_id"]) or defaults, row["marks"]) for row in rows]
        live = _page_rule(cases)
        for row, preview in zip(rows, live):
            assert preview["mode"] == "weighted", (
                f"{row['name']}: the page fell back to the simple mean, so its live "
                "column prints a dash while the server prints a weighted mark")
            assert preview["final"] == row["final"], (
                f"{row['name']}: the page would print {preview['final']} where the "
                f"server says {row['final']}")

    def test_the_doors_the_page_reads_are_reads(self, as_admin, db):
        """A preview that can save is the defect these doors exist to avoid."""
        before = [entry for entry in db.log if entry[0] != "select"]
        _get_json(as_admin, SEARCH, q="Rina")
        _get_json(as_admin, COMPARE, student_id=PUPIL)
        as_admin.get(PAGE)
        after = [entry for entry in db.log if entry[0] != "select"]
        assert after == before, (
            "the page or its doors wrote to the database: " + repr(after[len(before):]))


# ── 5. the same number on the page a teacher prints ────────────────────────

ROSTER = "/teacher/students"


def _roster(client, subject_id: str) -> list[dict]:
    """The rows the teacher's grade table hands its own JavaScript.

    The page computes nothing: `students: {{ students|tojson }}` is the server's
    rows, final mark and all, so reading them here is reading what the teacher
    sees — not a re-run of the service behind it.
    """
    page = client.get(ROSTER, query_string={"subject_id": subject_id})
    assert page.status_code == 200, (
        f"the teacher was refused their own roster: {page.status_code}")
    return _json_literal(page.get_data(as_text=True), "rows")


def _row_for(rows: list[dict], pupil_id: str) -> dict:
    row = next((r for r in rows if r["id"] == pupil_id), None)
    assert row, f"the teacher's roster does not contain {pupil_id}"
    return row


class TestTheTeacherRosterAgreesWithTheComparison:
    """One policy, two pages: the admin's comparison and the teacher's roster.

    They are not the same function. The comparison transposes — one pupil across
    subjects — while the roster reads one subject across a class. Both end in
    `grade_weighting.compute` on the same effective config, and this is the only
    place the two are made to print the *same digits* for one learner, through the
    two real pages rather than through the service they share. A policy that reads
    one way on the page a school decides on and another way on the page a teacher
    prints is the divergence nothing else in the suite would catch.
    """

    def test_a_guru_cannot_open_the_admin_weights_page(self, as_guru):
        refused = as_guru.get(PAGE)
        assert refused.status_code != 200, (
            "a guru reached the school admin's weights page")
        assert "/teacher/dashboard" in refused.headers.get("Location", ""), (
            "a signed-in guru refused the admin page must land on their own home")

    def test_the_roster_number_is_the_admin_comparisons_number(self, as_admin, as_guru):
        """The one assertion this file exists for: two doors, one number.

        Rina's Fisika final is 86.0 under the subject's own 40/60. The comparison
        names it; the roster must name the same number, for the same learner, with
        no re-typing of the policy in between.
        """
        admin_rows = _get_json(as_admin, COMPARE, student_id=PUPIL).get_json()["subjects"]
        admin_fisika = next(r for r in admin_rows if r["subject_id"] == FISIKA)
        assert admin_fisika["final"] == 86.0

        row = _row_for(_roster(as_guru, FISIKA), PUPIL)
        assert row["final"] == admin_fisika["final"], (
            f"the teacher's roster prints {row['final']} where the admin's comparison "
            f"prints {admin_fisika['final']} for the same learner in the same subject")
        assert row["mode"] == admin_fisika["mode"] == "weighted"
        assert row["full_name"] == "Rina Melati"
        assert row["class_name"] == "7A"

    def test_a_subject_the_teacher_does_not_teach_is_not_their_roster(self, as_guru):
        """Kimia is not this teacher's. Asking for it must not hand over its roster.

        A guru's subjects come from their active assignments, and a requested
        subject they do not hold falls back to their own first subject rather than
        answering with the other subject's class. Kimia's number for the same
        learner under the school default is 65.0; Fisika's is 86.0, so the two are
        distinguishable in the one place it matters.
        """
        asked = _row_for(_roster(as_guru, KIMIA), PUPIL)
        owned = _row_for(_roster(as_guru, FISIKA), PUPIL)
        assert asked["final"] == owned["final"] == 86.0, (
            "requesting a subject the teacher does not teach returned that subject's "
            "roster instead of falling back to the teacher's own")

    def test_the_roster_holds_only_the_class_the_teacher_is_assigned(self, as_guru):
        """Citra is in 7B, which this teacher does not hold; Rina is in 7A, which
        they do. A whole-school roster would list both, so this is the assertion
        that separates "assigned classes" from "everyone" rather than the weaker
        one that happens to be true either way."""
        rows = _roster(as_guru, FISIKA)
        assert [r["id"] for r in rows] == [PUPIL], (
            "the roster shows a pupil outside the classes this teacher is assigned")

    def test_the_roster_allocates_nothing(self, as_guru, db):
        """A teacher opening their own grade table is a read, like the admin's preview."""
        before = [entry for entry in db.log if entry[0] != "select"]
        _roster(as_guru, FISIKA)
        after = [entry for entry in db.log if entry[0] != "select"]
        assert after == before, (
            "reading the teacher's roster wrote to the database: " + repr(after[len(before):]))
