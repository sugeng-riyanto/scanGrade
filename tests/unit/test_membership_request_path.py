"""The request path: why a closure is total rather than eventual.

`tests/unit/test_membership_closure.py` pins the rules — one canonical status, the
asymmetry, the refusals — at the service. This file pins the *path* those rules
travel on, which is where a closure can leak back in:

* **the school is resolved per request, not cached.** The session cache holds the
  *profile* (whose `school_id` is the home school); the resolved school is computed
  from a fresh membership read every request, so a session cached before a closure
  cannot keep serving the school it names — and the test asserts the profile was
  **not** re-fetched, because a version that simply re-read everything would pass the
  behaviour and lose the cache;
* **a chosen school is only honoured while an active row still names it**, and the
  stale choice is cleared by the read path — the only place that holds both the token
  and the row;
* **a failed read narrows nothing** while a *known* closure narrows. Two opposite
  failures, deliberately handled in opposite directions: an outage must not sign a
  teacher out, and a closure must not be survivable;
* **a closed membership cannot be re-entered** by a fresh request or an approval.
"""
from __future__ import annotations

import pytest

from app.services import school_membership as membership
from app.utils import auth as authmod
from tests.unit.membership_fixtures import FakeDb, membership as mrow, person, school


def session_payload(role="guru", school_id="sc-1", user_id="u-1") -> dict:
    return {"user_id": user_id, "email": "u@example.test", "name": "Uji Guru",
            "role": role, "school_id": school_id, "class_id": None,
            "status": "active", "prefs": {}, "must_change_password": False}


# ── the choice is a preference, not a permission ─────────────────────────────

class TestTheChoiceIsNotAWayBackIn:
    def test_a_chosen_school_that_was_closed_is_dropped(self):
        db = FakeDb(teacher_school_membership=[
            mrow(school_id="sc-1", status="active"),
            mrow(school_id="sc-2", status="closed")])
        assert membership.resolve_active_school(db, "u-1", "sc-1", "sc-2",
                                               "guru") == "sc-1"

    def test_the_stale_choice_is_forgotten_by_the_read_path(self, monkeypatch):
        """Nothing else *can* forget it: only this path holds the token and the row."""
        db = FakeDb(teacher_school_membership=[
            mrow(school_id="sc-1", status="active"),
            mrow(school_id="sc-2", status="active")])
        monkeypatch.setattr(membership, "_client", lambda: db)
        membership.set_active_school("tok-1", "sc-2")
        assert membership.get_active_school("tok-1") == "sc-2"

        db.row("teacher_school_membership", school_id="sc-2")["status"] = "closed"
        assert membership.resolve_for_request("tok-1", "u-1", "sc-1",
                                              "guru") == "sc-1"
        assert membership.get_active_school("tok-1") is None, (
            "the closed choice is still remembered, so it is re-decided forever")


# ── an outage is not a closure ───────────────────────────────────────────────

class TestAFailedReadIsNotAClosure:
    def test_an_unreadable_table_keeps_the_home_school(self, monkeypatch):
        """The pre-064 box is exactly this box, and it must keep working."""
        db = FakeDb(teacher_school_membership=[])
        db.fail.add("teacher_school_membership")
        monkeypatch.setattr(membership, "_client", lambda: db)
        assert membership.resolve_for_request("tok-1", "u-1", "sc-1",
                                              "guru") == "sc-1"

    def test_a_resolver_that_blows_up_keeps_the_session_school(self, app, monkeypatch):
        """The identity path must not be able to sign a whole school out.

        Without the guard in `_resolved_school` the exception reaches
        `login_required`'s handler, which refreshes the token and then refuses — the
        worst outcome available here, and one no closure can hide behind: a closure
        arrives as rows, not as an exception.
        """
        def _boom(*a, **k):
            raise RuntimeError("the resolver is broken")

        monkeypatch.setattr("app.services.school_membership.resolve_for_request", _boom)
        from flask import g
        with app.test_request_context("/"):
            authmod._apply_session(session_payload(role="guru", school_id="sc-1"),
                                   "tok-1")
            assert g.user_school_id == "sc-1"

    def test_a_school_with_no_row_at_all_is_not_closed(self):
        """Every account created after migration 044 has no membership row."""
        db = FakeDb(teacher_school_membership=[mrow(school_id="sc-9", status="closed")])
        assert membership.resolve_active_school(db, "u-1", "sc-new", None,
                                               "guru") == "sc-new"

    def test_a_closed_home_school_falls_to_another_active_membership(self):
        """A closure is not a locked account: the teacher still belongs somewhere."""
        db = FakeDb(teacher_school_membership=[
            mrow(school_id="sc-1", status="closed"),
            mrow(school_id="sc-2", status="active")])
        assert membership.resolve_active_school(db, "u-1", "sc-1", None,
                                               "guru") == "sc-2"

    def test_the_fallback_is_deterministic(self):
        """Sorted, so two requests in the same second cannot disagree."""
        rows = [mrow(school_id="sc-1", status="closed"),
                mrow(school_id="sc-9", status="active"),
                mrow(school_id="sc-2", status="active")]
        for _ in range(3):
            assert membership.resolve_from_memberships("sc-1", None, rows,
                                                      "guru") == "sc-2"


# ── the session applies the answer, and cannot cache it ──────────────────────

class TestTheRequestPath:
    def test_the_membership_is_read_once_per_request(self, monkeypatch):
        db = FakeDb(teacher_school_membership=[mrow(school_id="sc-1")])
        monkeypatch.setattr(membership, "_client", lambda: db)
        app = pytest.importorskip("flask").Flask(__name__)
        with app.test_request_context("/"):
            membership.resolve_for_request("tok-1", "u-1", "sc-1", "guru")
            membership.resolve_for_request("tok-1", "u-1", "sc-1", "guru")
        assert db.reads.count("teacher_school_membership") == 1, (
            "a page that asks twice must not pay twice")

    def test_a_pupil_pays_nothing(self, monkeypatch):
        db = FakeDb(teacher_school_membership=[])
        monkeypatch.setattr(membership, "_client", lambda: db)
        assert membership.resolve_for_request("tok", "u-9", "sc-1", "murid") == "sc-1"
        assert db.reads == []

    def test_the_session_applies_the_resolved_school(self, app, monkeypatch):
        db = FakeDb(teacher_school_membership=[mrow(school_id="sc-2", status="active")])
        monkeypatch.setattr(membership, "_client", lambda: db)
        from flask import g
        with app.test_request_context("/"):
            authmod._apply_session(session_payload(school_id="sc-2"), "tok-1")
            assert g.user_school_id == "sc-2"

        db.row("teacher_school_membership", school_id="sc-2")["status"] = "closed"
        with app.test_request_context("/"):
            authmod._apply_session(session_payload(school_id="sc-2"), "tok-1")
            assert g.user_school_id is None, (
                "a closed membership still resolved to a school")

    def test_a_cached_session_cannot_serve_a_school_a_closure_removed(
            self, app, monkeypatch):
        """The property that makes a closure total rather than eventual.

        The session cache is a *profile* cache, so the second request reuses it — and
        the school still changes under it. The fetch count is asserted so a version
        that re-read the profile every time (passing the change, losing the cache)
        cannot pass as this.
        """
        db = FakeDb(teacher_school_membership=[mrow(school_id="sc-1")])
        monkeypatch.setattr(membership, "_client", lambda: db)
        fetches = []

        def _fetch(token):
            fetches.append(token)
            return session_payload(school_id="sc-1")

        monkeypatch.setattr(authmod, "_fetch_session", _fetch)
        from flask import g
        with app.test_request_context("/"):
            authmod._apply_session(authmod._session_for("tok-1"), "tok-1")
            assert g.user_school_id == "sc-1"

        db.row("teacher_school_membership", school_id="sc-1")["status"] = "closed"
        with app.test_request_context("/"):
            authmod._apply_session(authmod._session_for("tok-1"), "tok-1")
            assert g.user_school_id is None
        assert len(fetches) == 1, (
            "the profile was re-fetched; the cached session is supposed to be reused, "
            "with the school resolved on top of it")

    def test_a_pupil_session_is_untouched(self, app, monkeypatch):
        db = FakeDb(teacher_school_membership=[])
        monkeypatch.setattr(membership, "_client", lambda: db)
        from flask import g
        with app.test_request_context("/"):
            authmod._apply_session(session_payload(role="murid", school_id="sc-1"),
                                   "tok-1")
            assert g.user_school_id == "sc-1"
        assert db.reads == [], "a pupil's request touched the membership table"


def test_no_school_resolves_to_a_refusal_not_a_default(app, monkeypatch):
    """The last link: no school means a refusal, not somebody else's rows."""
    from flask import g
    from app.decorators.security import require_school_access

    @require_school_access("exams")
    def _view(**kwargs):
        return "reached"

    db = FakeDb(teacher_school_membership=[mrow(school_id="sc-1", status="closed")],
                exams=[{"id": "e-1", "school_id": "sc-1"}])
    monkeypatch.setattr(membership, "_client", lambda: db)
    monkeypatch.setattr(authmod, "get_supabase", lambda: db)
    with app.test_request_context("/api/exams/e-1"):
        g.user_id, g.user_role = "u-1", "guru"
        authmod._apply_session(session_payload(school_id="sc-1"), "tok-1")
        response = _view(id="e-1")
    # Flask has not formed a response yet (no request went through the app), so the
    # decorator's `(jsonify(...), 403)` arrives as a tuple.
    status = response[1] if isinstance(response, tuple) else response.status_code
    assert status == 403


# ── the closure cannot be re-entered through the request flow ────────────────

class TestAClosedMembershipCannotBeReEntered:
    def _consent(self):
        return dict(document_version=membership.CONSENT_DOCUMENT_VERSION,
                    document_sha256=membership.consent_sha256())

    def test_a_fresh_request_is_refused(self):
        db = FakeDb(schools=[school("sc-2")],
                    teacher_school_membership=[mrow(school_id="sc-2",
                                                    status="closed")])
        out = membership.create_request(db, "u-1", "sc-2", "guru", **self._consent())
        assert out == {"ok": False, "reason": "membership_closed"}
        assert db.write_payloads("school_membership_request") == []

    def test_an_approval_is_refused_before_anything_is_written(self):
        """Approving a closed membership is a covert reopen, refused while nothing
        has changed yet — an `approved` row promising access nobody has is the one
        state this exists not to create."""
        db = FakeDb(schools=[school("sc-1")],
                    teacher_school_membership=[mrow(school_id="sc-1", status="closed")],
                    school_membership_request=[
                        {"id": "r-1", "teacher_id": "u-1", "target_school_id": "sc-1",
                         "status": "pending"}])
        out = membership.decide_request(db, "sc-1", "r-1", "approved",
                                        actor_id="a-1", actor_role="principal")
        assert out == {"ok": False, "reason": "membership_closed"}
        assert db.writes == [], "a decision was written next to a refusal"
        assert db.row("school_membership_request", id="r-1")["status"] == "pending"
