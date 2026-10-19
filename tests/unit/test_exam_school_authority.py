"""A paper is filed under the school this request is acting in — never another.

`exams.school_id` is the one column that decides which school weights a paper, which
school's pupils may sit it, whose marks come back under it and whose reports count
it. It was written in four places from four different answers: the builder took
`g.user_school_id` (the *profile's* school, so a teacher working at a second NPSN
filed their paper into their home school), and `POST /exam/` took the value straight
out of the request body — `filtered.get("school_id") or g.get("user_school_id")` —
so a caller could name **any** school at all, member or not.

What is pinned here:

* **the resolution** (`resolve_for_request`) — the school a request is for, verified
  against an active membership in that same request, dropping a chosen school whose
  row is gone, and reading nothing at all for a pupil or a school admin;
* **the write side** (`filing_school`) — the same question asked by a *write*, and it
  is deliberately not the read's answer: an unreadable membership narrows nothing on a
  page but refuses a paper, because a paper filed in an unconfirmed school is the
  leak this exists to stop;
* **the doors** — every route that can create or re-file a paper asks that authority,
  and the body may no longer name the school.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import g

from app.services import school_membership as membership

ROOT = Path(__file__).resolve().parents[2]
EXAM_ROUTE = ROOT / "app" / "routes" / "exam.py"
TEACHER_ROUTE = ROOT / "app" / "routes" / "teacher.py"
AUTH = ROOT / "app" / "utils" / "auth.py"


# ── fakes ────────────────────────────────────────────────────────────────────

class _PgError(Exception):
    """The shape postgrest-py raises for a table that is not in the schema cache."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


#: What a database says when the table itself is not there (migration 044 unrun).
MISSING_TABLE = _PgError("PGRST205", "Could not find the table "
                         "'public.teacher_school_membership' in the schema cache")


class _Query:
    """One `teacher_school_membership` read, filterable the way PostgREST is."""

    def __init__(self, rows, fail=False):
        self.rows = list(rows)
        self.fail = fail
        self.selects = []

    def _boom(self):
        if not self.fail:
            return
        raise self.fail if isinstance(self.fail, BaseException) else RuntimeError("no such table")

    def select(self, cols, *a, **k):
        self._boom()
        self.selects.append(cols)
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self._boom()
        return SimpleNamespace(data=self.rows)


class _Sb:
    """A Supabase stand-in that refuses any table but the membership one.

    `fail` is either a plain flag (a generic failure: the read could not be
    answered) or the exception to raise, so a test can hand it the *table is
    missing* shape the app is supposed to tell apart from an outage.
    """

    def __init__(self, rows=None, fail=False):
        self.rows = list(rows or [])
        self.fail = fail
        self.reads = []

    def table(self, name):
        assert name == "teacher_school_membership", name
        self.last = _Query(list(self.rows), fail=self.fail)
        self.reads.append(self.last)
        return self.last


def _rows(*pairs):
    """`("sc-A", "active"), ("sc-B", "closed")` → membership rows."""
    return [{"user_id": "u-1", "school_id": school, "status": status}
            for school, status in pairs]


def _a_choice(monkeypatch, chosen):
    """Stand in for the server-side chosen school, and record whether it is dropped."""
    dropped = []
    monkeypatch.setattr(membership, "get_active_school", lambda token: chosen)
    monkeypatch.setattr(membership, "clear_active_school", lambda token: dropped.append(token))
    return dropped


# ── reading: which school is this request for? ───────────────────────────────

def test_a_pupil_or_admin_never_consults_the_membership_table(monkeypatch):
    """`profiles.school_id` is the whole answer for a single-school role."""
    monkeypatch.setattr(membership, "resolve_for_request",
                        lambda *a, **k: pytest.fail("must not be asked"))
    sb = _Sb(_rows(("sc-A", "active")))
    for role in ("murid", "admin_sekolah"):
        assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-A", role) == "sc-home"
    assert sb.reads == []


def test_a_cross_school_teacher_is_resolved_into_the_active_npsn(monkeypatch):
    """The headline: the active membership wins over the profile's school."""
    _a_choice(monkeypatch, "sc-B")
    sb = _Sb(_rows(("sc-A", "active"), ("sc-B", "active")))
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru", supabase=sb) == "sc-B"


def test_a_chosen_school_whose_row_is_gone_is_dropped_and_forgotten(monkeypatch):
    """A closure may not be outlived by the choice made before it."""
    dropped = _a_choice(monkeypatch, "sc-B")
    sb = _Sb(_rows(("sc-A", "active"), ("sc-B", "closed")))
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru", supabase=sb) == "sc-A"
    assert dropped == ["tok"]


def test_the_home_school_with_no_row_at_all_is_kept(monkeypatch):
    """A teacher created after migration 044 has no row — that is not a closure."""
    _a_choice(monkeypatch, None)
    sb = _Sb([])
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru", supabase=sb) == "sc-A"


def test_a_closed_home_school_falls_to_another_active_membership(monkeypatch):
    """Sorted, so two requests cannot disagree about which school that teacher is in."""
    _a_choice(monkeypatch, None)
    sb = _Sb(_rows(("sc-A", "closed"), ("sc-C", "active"), ("sc-B", "active")))
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru", supabase=sb) == "sc-B"


def test_an_unreadable_membership_narrows_nothing_on_a_read(monkeypatch):
    _a_choice(monkeypatch, "sc-B")
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru",
                                          supabase=_Sb(fail=True)) == "sc-A"


def test_membership_rows_returns_closed_rows_too():
    """`[]` for a closure and `None` for an outage cannot be told apart without them."""
    sb = _Sb(_rows(("sc-A", "active"), ("sc-B", "closed")))
    rows = membership.membership_rows(sb, "u-1")
    assert {r["school_id"] for r in rows} == {"sc-A", "sc-B"}
    assert sb.last.selects == ["school_id,status"]
    assert membership.membership_rows(_Sb(fail=True), "u-1") is None


# ── writing: the school a paper is filed under ───────────────────────────────

def _file(*, role="guru", active, home=None, requested=None, sb=None):
    return membership.filing_school(role=role, user_id="u-1",
                                    active_school_id=active, home_school_id=home,
                                    requested_school_id=requested,
                                    supabase=sb if sb is not None else _Sb(_rows((active, "active"))))


def test_a_paper_is_filed_under_the_active_npsn_not_the_profile_school():
    school, error = _file(active="sc-B", home="sc-A",
                          sb=_Sb(_rows(("sc-A", "active"), ("sc-B", "active"))))
    assert (school, error) == ("sc-B", None)


def test_a_body_that_names_a_different_school_is_refused_not_obeyed():
    """The whole point for `POST /exam/`: a name in the body is not an authority."""
    sb = _Sb(_rows(("sc-A", "active"), ("sc-B", "active")))
    assert _file(active="sc-B", home="sc-B", requested="sc-A", sb=sb) == (
        None, membership.FILING_OTHER_SCHOOL)
    # …and the same school as the one in force is accepted, so an echoing client works.
    assert _file(active="sc-B", home="sc-B", requested="sc-B", sb=sb) == ("sc-B", None)


def test_no_school_at_all_is_a_refusal():
    """A paper with no school is one every school's pupils may sit — worse than a no."""
    assert _file(active=None, home=None) == (None, membership.FILING_NO_SCHOOL)


def test_an_unconfirmed_membership_refuses_the_paper(monkeypatch):
    """The leak this closes: an outage must not silently become the home school."""
    school, error = membership.filing_school(
        role="guru", user_id="u-1", active_school_id="sc-A", home_school_id="sc-A",
        supabase=_Sb(fail=True))
    assert school is None and error == membership.FILING_UNVERIFIED
    # …and a read that could not be *answered* stays unreadable, whatever the client
    # put in the message: a timeout is not a missing table.
    timed_out = _Sb(fail=TimeoutError("connection reset by peer"))
    assert membership.membership_rows(timed_out, "u-1") is None
    assert membership.filing_school(
        role="guru", user_id="u-1", active_school_id="sc-A", home_school_id="sc-A",
        supabase=timed_out) == (None, membership.FILING_UNVERIFIED)


def test_a_box_without_the_membership_table_keeps_saving_exams():
    """A missing table is not an outage: every teacher there is single-school.

    Migration 044 may not have run on a box, and that state has no memberships at
    all — so `profiles.school_id` is the whole truth and a write may proceed. Reading
    it as an unreadable read would refuse every exam save on a database that never
    had the feature, which is the widest possible version of this change's blast
    radius. The two states are told apart by the error, and both are pinned here.
    """
    sb = _Sb(fail=MISSING_TABLE)
    assert membership.membership_rows(sb, "u-1") == []
    assert membership.resolve_for_request("tok", "u-1", "sc-A", "guru", supabase=sb) == "sc-A"
    assert membership.filing_school(
        role="guru", user_id="u-1", active_school_id="sc-A", home_school_id="sc-A",
        supabase=sb) == ("sc-A", None)
    # Postgres' own wording, and its code in the other attribute.
    for exc in (Exception("relation \"public.teacher_school_membership\" does not exist"),
                _PgError("42P01", "undefined table"),
                _PgError("PGRST205", "nope")):
        assert membership.membership_rows(_Sb(fail=exc), "u-1") == [], exc
    # A failure with no such marker is an outage, not a missing table.
    assert membership.membership_rows(_Sb(fail=Exception("gateway timeout")), "u-1") is None


def test_a_school_whose_membership_was_closed_is_refused():
    """The active school is re-derived from the rows read in this same request.

    A closure that landed after the session resolved the school still stops the
    write: the row says `closed`, so the school the page was drawn for is not one
    this request may file into any more.
    """
    sb = _Sb(_rows(("sc-B", "closed"), ("sc-C", "active")))
    assert membership.filing_school(
        role="guru", user_id="u-1", active_school_id="sc-B", home_school_id="sc-B",
        supabase=sb) == (None, membership.FILING_UNVERIFIED)


def test_a_home_school_with_no_row_at_all_may_still_be_filed_into():
    """Not a closure: an account created after migration 044 has no row to close."""
    sb = _Sb(_rows(("sc-C", "active")))
    assert membership.filing_school(
        role="guru", user_id="u-1", active_school_id="sc-B", home_school_id="sc-B",
        supabase=sb) == ("sc-B", None)


def test_a_pupils_or_admins_school_is_the_profiles_and_needs_no_membership_row():
    sb = _Sb([])
    assert _file(role="murid", active="sc-A", home="sc-A", sb=sb) == ("sc-A", None)
    assert _file(role="admin_sekolah", active="sc-A", home="sc-A", sb=sb) == ("sc-A", None)
    assert sb.reads == [], "a single-school role must not read the membership table"


def test_the_wrapper_hands_the_rule_the_home_school_and_not_the_resolved_one(app, monkeypatch):
    """`g.user_home_school_id` and `g.user_school_id` are different facts.

    Passing the resolved school where the home school belongs would make the
    resolution's step 4 (\"the home school, when it has no row\") unreachable, and a
    closed home membership would then resolve back to itself forever.
    """
    from app.utils import auth
    seen = {}
    monkeypatch.setattr(membership, "_rows_for_request",
                        lambda user_id, supabase=None: _rows(("sc-B", "active")))

    def _spy(**kwargs):
        seen.update(kwargs)
        return kwargs["active_school_id"], None

    monkeypatch.setattr(membership, "filing_school", _spy)
    with app.test_request_context("/teacher/exams/new", method="POST"):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_home_school_id, g.user_school_id = "sc-A", "sc-B"
        assert auth.filing_school_for_request(requested_school_id="sc-Z") == ("sc-B", None)
    assert seen["home_school_id"] == "sc-A"
    assert seen["active_school_id"] == "sc-B"
    assert seen["requested_school_id"] == "sc-Z"
    assert seen["role"] == "guru" and seen["user_id"] == "u-1"


# ── the session: the resolved school is what every page and write reads ──────

def test_the_session_keeps_both_the_profile_school_and_the_resolved_one(app, monkeypatch):
    from app.utils import auth
    monkeypatch.setattr(auth, "_resolved_school", lambda token, data: "sc-B")
    with app.test_request_context("/teacher/dashboard"):
        auth._apply_session({"user_id": "u-1", "role": "guru", "school_id": "sc-A"}, "tok")
        assert g.user_school_id == "sc-B"
        assert g.user_home_school_id == "sc-A"


def test_the_chosen_school_moves_to_the_new_token_on_a_refresh(app, monkeypatch):
    """Otherwise an hourly refresh silently returns the teacher to their home school."""
    from app.utils import auth
    stored = {}
    monkeypatch.setattr(membership, "get_active_school", lambda t: "sc-B" if t == "old" else None)
    monkeypatch.setattr(membership, "set_active_school", lambda t, s: stored.__setitem__(t, s))
    monkeypatch.setattr(membership, "clear_active_school", lambda t: stored.pop(t, None))
    with app.test_request_context("/"):
        auth._carry_active_school("old", "new")
        assert stored == {"new": "sc-B"}
        # A no-op when there is nothing to carry, or no new token to carry it to.
        auth._carry_active_school("old", "old")
        auth._carry_active_school(None, "new")
    assert stored == {"new": "sc-B"}


def test_a_pupil_is_never_asked_the_membership_question(app, monkeypatch):
    from app.utils import auth
    monkeypatch.setattr(membership, "resolve_for_request",
                        lambda *a, **k: pytest.fail("membership table must not be read"))
    with app.test_request_context("/student/dashboard"):
        assert auth._resolved_school("tok", {"user_id": "u-1", "role": "murid",
                                             "school_id": "sc-A"}) == "sc-A"


def test_a_resolution_that_raises_keeps_the_profile_school(app, monkeypatch):
    """An unexpected failure must not end every teacher's session."""
    from app.utils import auth

    def _boom(*a, **k):
        raise RuntimeError("nobody could answer")

    monkeypatch.setattr(membership, "resolve_for_request", _boom)
    with app.test_request_context("/teacher/dashboard"):
        assert auth._resolved_school("tok", {"user_id": "u-1", "role": "guru",
                                            "school_id": "sc-A"}) == "sc-A"


def test_a_refresh_resolves_the_school_again(app, monkeypatch):
    from app.utils import auth
    seen = []
    monkeypatch.setattr(membership, "resolve_for_request",
                        lambda token, uid, home, role: seen.append((token, home)) or "sc-B")
    with app.test_request_context("/teacher/dashboard"):
        assert auth._resolved_school("new-tok", {"user_id": "u-1", "role": "guru",
                                                "school_id": "sc-A"}) == "sc-B"
    assert seen == [("new-tok", "sc-A")]


def test_the_token_refresh_asks_the_same_question_instead_of_trusting_the_profile():
    """A refresh must not be a way back into a school a closure removed.

    The refresh path reads the profile itself and used to take `profiles.school_id`
    straight from it, so an hourly token refresh quietly returned a cross-school
    teacher to the school their profile names — and that is the school their next
    paper would have been filed in.
    """
    body = _function_bodies(AUTH.read_text(encoding="utf-8"), ("_refresh_token",))[0]
    # Two branches read a profile (the pd one and the metadata fallback), so both must
    # resolve — counting them is what stops one branch quietly keeping the old rule.
    assert body.count("g.user_school_id = _resolved_school(token, {") == 2
    assert "g.user_school_id = g.user_home_school_id" not in body
    assert 'g.user_home_school_id = pd.get("school_id")' in body
    # …and the choice is re-keyed onto the new token *before* the first resolution,
    # or the refresh request itself resolves the teacher into the school their
    # profile names — the school a save on that request would be filed under.
    assert body.index("_carry_active_school(previous_token, token)") < body.index(
        "g.user_school_id = _resolved_school(token, {")


# ── the doors ────────────────────────────────────────────────────────────────

def test_the_json_door_files_under_the_authority_and_not_the_body():
    src = EXAM_ROUTE.read_text(encoding="utf-8")
    writable = re.search(r"WRITABLE_EXAM_FIELDS = \{(.*?)\}", src, re.S)
    assert writable, "the writable-field set must exist"
    assert "school_id" not in writable.group(1), (
        "the body must not be able to name the school a paper is filed under")
    assert 'filtered["school_id"] = filtered.get("school_id")' not in src, (
        "the body's school_id was the door's answer — it is the server's now")
    assert 'filtered["school_id"] = school_id' in src, (
        "the column is written from the authority's answer, not from the body")
    # The refusal has to come before the write, or the row is minted (or moved) and
    # then argued about. One call site per door: create and update, each asking with
    # the body's own value — the assignment itself, so a call whose answer is thrown
    # away (or a branch that never runs) does not count as asking.
    create, update = _function_bodies(src, ("create_exam", "update_exam"))
    ask = "school_id, error = filing_school_for_request(requested_school_id=data.get(\"school_id\"))"
    assert ask in create and ask in update
    assert create.index('jsonify({"error": error})') < create.index(".insert(filtered)")
    assert update.index('jsonify({"error": error})') < update.index(".update(filtered)")
    assert create.index('filtered["school_id"] = school_id') < create.index(".insert(filtered)")


def test_update_cannot_move_a_paper_between_schools():
    src = EXAM_ROUTE.read_text(encoding="utf-8")
    body = _function_bodies(src, ("update_exam",))[0]
    assert "school_id" not in re.search(r"WRITABLE_EXAM_FIELDS = \{(.*?)\}", src, re.S).group(1)
    assert 'data.get("school_id")' in body, "a body asking for another school is answered"
    assert ".update(filtered)" in body, "only the filtered, school-free fields are written"


def test_the_list_read_is_scoped_to_one_school_and_strips_keys_it_may_not_show():
    src = EXAM_ROUTE.read_text(encoding="utf-8")
    body = _function_bodies(src, ("list_exams",))[0]
    assert 'query.eq("school_id", school_id)' in body
    assert 'g.get("user_role") != "super_admin"' in body
    assert "can_read_exam(" in body
    assert 'row.pop("answer_key", None)' in body


def test_the_builder_create_asks_the_authority_before_it_mints_the_row():
    src = TEACHER_ROUTE.read_text(encoding="utf-8")
    body = _function_bodies(src, ("exam_form",))[0]
    assert 'requested_school_id=request.form.get("school_id")' in body, (
        "a hand-crafted builder post may not name a school either")
    assert "return _exam_save_refused(autosave, _school_error)" in body
    assert '"school_id": school_id,' in body
    assert body.index("filing_school_for_request(") < body.index('"school_id": school_id,')


def test_the_builder_edit_never_restates_the_school():
    src = TEACHER_ROUTE.read_text(encoding="utf-8")
    body = _function_bodies(src, ("exam_detail",))[0]
    assert '"school_id": g.get("user_school_id"),' not in src, (
        "no door may restate the caller's school as the paper's")
    assert 'if not exam_row.get("school_id"):' in body, (
        "only a row that has no school is repaired")
    assert 'data["school_id"] = filing_school_id' in body
    assert body.index('if not exam_row.get("school_id"):') < body.index('data["school_id"] = filing_school_id')
    assert "filing_school_id, _school_error = filing_school_for_request()" in body, (
        "the repair is a write, so it goes through the verified authority")
    assert "filing_school_id = g.get" not in body, (
        "the repair may not fall back to whatever school the session holds")


def test_a_duplicate_and_a_remedial_inherit_the_source_papers_school():
    src = TEACHER_ROUTE.read_text(encoding="utf-8")
    dup = _function_bodies(src, ("duplicate_exam",))[0]
    assert 'new_data = {k: v for k, v in exam.items()' in dup, (
        "the copy is built from the guarded source row, so its school travels with it")
    assert 'new_data["school_id"]' not in dup, "a copy is not re-filed"
    remedial = _function_bodies(src, ("generate_remedial",))[0]
    assert '"school_id": exam.get("school_id") or g.get("user_school_id"),' in remedial


def test_the_refusals_are_named_so_nothing_can_be_grepped_into_a_second_spelling():
    """Every refusal a teacher can read comes from the service's own constants."""
    constants = (membership.FILING_NO_SCHOOL, membership.FILING_UNVERIFIED,
                 membership.FILING_OTHER_SCHOOL)
    assert all(c.strip() == c and c for c in constants)
    assert len(set(constants)) == 3, "three situations, three sentences"
    for path in (EXAM_ROUTE, TEACHER_ROUTE):
        src = path.read_text(encoding="utf-8")
        for text in constants:
            assert text not in src, (
                f"the sentence belongs to the service constant, not typed at a door: {path.name}")


# ── the door, executed: the paper the database is actually handed ────────────
#
# The source assertions above say the route asks the authority. These run the route
# and read the row it hands the database, because "asks the authority" and "the row
# carries the answer" are different claims and only the second one is the guarantee.

class _Table:
    """One table read/write against `_Db`, filtered the way PostgREST is."""

    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = {}
        self.not_equal = []
        self.one_of = []
        self.payload = None
        self.verb = None
        self.one_row = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def neq(self, col, val):
        self.not_equal.append((col, val))
        return self

    def in_(self, col, values):
        self.one_of.append((col, {str(v) for v in values}))
        return self

    # Date windows and null checks are answers this fake does not model; the doors
    # under test are asking *which school*, and the rows handed back are the fixture's.
    def is_(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def lte(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def single(self):
        # PostgREST answers `.single()` with the row itself, not a one-row list: a
        # fake that hands back a list is a fake whose reader gets a `list.get`.
        self.one_row = True
        return self

    def maybe_single(self):
        return self.single()

    def insert(self, payload):
        self.payload, self.verb = dict(payload), "insert"
        return self

    def update(self, payload, *, returning="representation"):
        self.payload, self.verb = dict(payload), "update"
        return self

    def upsert(self, payload, **k):
        self.payload, self.verb = dict(payload), "upsert"
        return self

    def delete(self):
        self.payload, self.verb = None, "delete"
        return self

    def execute(self):
        if self.db.fail is not None and self.name == "teacher_school_membership":
            raise self.db.fail
        if self.verb is not None:
            self.db.writes.append((self.name, self.verb, self.payload))
            if self.verb == "delete":
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[dict(self.payload, id=self.db.insert_id)])
        rows = [r for r in self.db.rows.get(self.name, [])
                if all(str(r.get(c)) == str(v) for c, v in self.filters.items())
                and all(str(r.get(c)) != str(v) for c, v in self.not_equal)
                and all(str(r.get(c)) in wanted for c, wanted in self.one_of)]
        if self.one_row:
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


class _Db:
    """A Supabase stand-in with per-table rows, recording every write."""

    def __init__(self, rows=None, fail=None, insert_id="e-new"):
        self.rows = rows or {}
        self.fail = fail
        self.insert_id = insert_id
        self.writes = []

    def table(self, name):
        return _Table(self, name)

    @property
    def inserts(self):
        return [(name, payload) for name, verb, payload in self.writes if verb == "insert"]

    @property
    def updates(self):
        return [(name, payload) for name, verb, payload in self.writes
                if verb in ("update", "upsert")]


TWO_SCHOOLS = {
    "teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "active"},
        {"user_id": "u-1", "school_id": "sc-B", "status": "active"},
    ],
    # The running year of sc-B, and none for sc-A: the year the paper is dated to is
    # read for the school it is filed under, so this doubles as a witness that the
    # whole create is scoped to the active NPSN.
    "school_years": [{"id": "y-1", "name": "2026/2027", "is_active": True,
                      "school_id": "sc-B"}],
}


def _call_create(app, monkeypatch, db, body):
    """Run `POST /exam/`'s view as a guru acting in sc-B, home school sc-A."""
    from app.routes import exam as exam_routes
    from app.utils import auth as auth_utils
    monkeypatch.setattr(membership, "get_active_school", lambda token: "sc-B")
    monkeypatch.setattr(membership, "clear_active_school", lambda token: None)
    monkeypatch.setattr(auth_utils, "get_supabase", lambda: db)
    monkeypatch.setattr(exam_routes, "get_supabase", lambda: db)
    with app.test_request_context("/exam/", method="POST", json=body):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_home_school_id, g.user_school_id = "sc-A", "sc-B"
        # `inspect.unwrap` past `teacher_required`/`login_required`: this test is about
        # what the door *writes*, and the decorators' own refusals have their guards.
        return inspect.unwrap(exam_routes.create_exam)()


def test_the_json_create_door_writes_the_resolved_school_into_the_row(app, monkeypatch):
    """The guarantee, executed: a cross-school teacher's paper lands in sc-B."""
    db = _Db(TWO_SCHOOLS)
    response, status = _call_create(app, monkeypatch, db, {"title": "UH 1"})
    assert status == 201, response
    assert db.inserts == [("exams", db.inserts[0][1])] and len(db.inserts) == 1
    row = db.inserts[0][1]
    assert row["school_id"] == "sc-B", "the row must carry the active NPSN, not sc-A"
    assert row["teacher_id"] == "u-1"
    assert row["school_year_id"] == "y-1", "dated to the active NPSN's running year"


def test_the_json_create_door_refuses_a_body_that_names_another_school(app, monkeypatch):
    db = _Db(TWO_SCHOOLS)
    response, status = _call_create(app, monkeypatch, db,
                                    {"title": "UH 1", "school_id": "sc-A"})
    assert status == 400, response
    assert response.get_json()["error"] == membership.FILING_OTHER_SCHOOL
    assert db.inserts == [], "a refused create must not mint a row anywhere"


def test_the_json_create_door_refuses_when_the_membership_cannot_be_read(app, monkeypatch):
    """The leak this whole change is about: the home school must not be a fallback."""
    db = _Db(TWO_SCHOOLS, fail=TimeoutError("connection reset by peer"))
    response, status = _call_create(app, monkeypatch, db, {"title": "UH 1"})
    assert status == 400, response
    assert response.get_json()["error"] == membership.FILING_UNVERIFIED
    assert db.inserts == [], "an unconfirmed school may not host the paper"


def test_the_same_door_still_saves_on_a_box_without_the_membership_table(app, monkeypatch):
    """Migration 044 unrun: no memberships exist, so the profile's school is the truth."""
    years = [dict(y, school_id="sc-A") for y in TWO_SCHOOLS["school_years"]]
    db = _Db({"school_years": years}, fail=MISSING_TABLE)
    from app.routes import exam as exam_routes
    from app.utils import auth as auth_utils
    monkeypatch.setattr(auth_utils, "get_supabase", lambda: db)
    monkeypatch.setattr(exam_routes, "get_supabase", lambda: db)
    with app.test_request_context("/exam/", method="POST", json={"title": "UH 1"}):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_home_school_id, g.user_school_id = "sc-A", "sc-A"
        response, status = inspect.unwrap(exam_routes.create_exam)()
    assert status == 201, response
    assert db.inserts[0][1]["school_id"] == "sc-A"


# ── the *builder's* door, executed ───────────────────────────────────────────
#
# The source assertion above says `exam_form` asks the authority before it mints a
# row. The builder is where a paper is actually created — `POST /exam/` is the JSON
# door — so the guarantee is only held if a hand-run save through the form lands in
# the active NPSN, and these run it. Home school sc-A, acting school sc-B.

#: The smallest body the builder's save accepts; the door reads these fields.
BUILDER_FORM = {
    "action": "save_draft",
    "title": "UH 1",
    "subject": "Matematika",
    "total_questions": "1",
    "duration_minutes": "60",
    "passing_score": "70",
    "question_types": json.dumps({"0": "mcq"}),
    "answer_key": json.dumps({"0": "A"}),
    "question_weights": json.dumps({"0": 100}),
}

#: A paper already filed in sc-B, as the edit door reads it back.
BUILDER_PAPER = {"id": "e-1", "teacher_id": "u-1", "school_id": "sc-B",
                 "title": "UH 1", "status": "draft", "is_published": False}


def _call_builder(app, monkeypatch, db, form=None, *, exam_id=None):
    """Run the builder's save as a guru working in sc-B whose profile names sc-A.

    With `exam_id` this is the edit door, without it the create door. `inspect.unwrap`
    steps past `teacher_or_admin_required`/`subscription_write_required`: what is under
    test is the school the row is written into, and those decorators have their own
    guards.
    """
    from app.routes import teacher as teacher_routes
    from app.utils import auth as auth_utils
    monkeypatch.setattr(membership, "get_active_school", lambda token: "sc-B")
    monkeypatch.setattr(membership, "clear_active_school", lambda token: None)
    monkeypatch.setattr(auth_utils, "get_supabase", lambda: db)
    monkeypatch.setattr(teacher_routes, "get_supabase", lambda: db)
    url = f"/teacher/exams/{exam_id}" if exam_id else "/teacher/exams/new"
    with app.test_request_context(url, method="POST",
                                  data=dict(BUILDER_FORM, **(form or {}))):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_home_school_id, g.user_school_id = "sc-A", "sc-B"
        g.tz_offset = 7
        if exam_id:
            return inspect.unwrap(teacher_routes.exam_detail)(exam_id)
        return inspect.unwrap(teacher_routes.exam_form)()


def test_the_builder_create_writes_the_active_npsn_into_the_row(app, monkeypatch):
    """The guarantee, executed through the door a teacher actually saves from.

    The row must carry the active NPSN and be dated to *its* running year, which is
    what ties the paper's weighting and its results to the school the teacher is
    working in. Worth saying out loud: at this door the authority's answer and the
    session's own resolved school are the same string — `filing_school` answers with
    the school it was asked about whenever it answers at all — so the assertion below
    pins which school that is, and the *leak* on a save is closed by the refusal the
    two tests after this one execute (the door returns before the row is built).
    """
    db = _Db(TWO_SCHOOLS)
    _call_builder(app, monkeypatch, db)
    rows = [payload for name, payload in db.inserts if name == "exams"]
    assert len(rows) == 1, db.writes
    assert rows[0]["school_id"] == "sc-B", "the builder filed the paper in the home school"
    assert rows[0]["teacher_id"] == "u-1"
    assert rows[0]["school_year_id"] == "y-1", "dated to the active NPSN's running year"


def test_the_builder_create_refuses_a_post_that_names_another_school(app, monkeypatch):
    """A hidden field is still a body: naming sc-A must be a refusal, not an order."""
    db = _Db(TWO_SCHOOLS)
    response, status = _call_builder(app, monkeypatch, db,
                                     {"school_id": "sc-A", "action": "autosave"})
    assert status == 400, response
    assert response.get_json()["error"] == membership.FILING_OTHER_SCHOOL
    assert db.inserts == [], "a refused save may not mint a row anywhere"


def test_the_builder_create_refuses_when_the_membership_cannot_be_read(app, monkeypatch):
    """The leak itself: an outage must not become the home school on a save."""
    db = _Db(TWO_SCHOOLS, fail=TimeoutError("connection reset by peer"))
    response, status = _call_builder(app, monkeypatch, db, {"action": "autosave"})
    assert status == 400, response
    assert response.get_json()["error"] == membership.FILING_UNVERIFIED
    assert db.inserts == [], "an unconfirmed school may not host the paper"


def test_the_builder_edit_cannot_move_a_paper_between_schools(app, monkeypatch):
    """A save may not restate the school: the row keeps the one it was filed under."""
    db = _Db(dict(TWO_SCHOOLS, exams=[dict(BUILDER_PAPER)]))
    _call_builder(app, monkeypatch, db, {"action": "autosave"}, exam_id="e-1")
    written = [payload for name, payload in db.updates if name == "exams"]
    assert written, db.writes
    assert all("school_id" not in payload for payload in written), (
        "the school this paper is weighted and reported under is not an edit's to change")


# ── helpers ──────────────────────────────────────────────────────────────────

def _function_bodies(src: str, names) -> list[str]:
    """The body of each named function, from its `def` to the next top-level one."""
    out = []
    for name in names:
        match = re.search(rf"^def {name}\(", src, re.M)
        assert match, f"{name} not found"
        rest = src[match.end():]
        nxt = re.search(r"^(?:@|def )", rest, re.M)
        out.append(src[match.start():match.end() + (nxt.start() if nxt else len(rest))])
    return out
