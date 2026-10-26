"""The licence-disable route, driven as a response rather than read as text.

PR #40's own note asks a reviewer to validate the HTTP contract on admin-disable
"including LiveKit delete failure and cleanup ledger insert failure". Its test
file is *structural* — it reads the source for `202` and `cleanup_pending` — so it
would keep passing if the branch were rearranged into returning 200, and it cannot
tell a `pending` list from an `unrecorded` one.

This file calls the view and inspects the response it actually produces:

* a failed room deletion is **202**, never 200, and says so in `warning`;
* a deletion debt that could not be written to the queue is reported in
  `cleanup_unrecorded`, separately from `cleanup_pending` — an operator has to
  reconcile the second by hand and the first needs no attention from them;
* a clean revocation is still **200**, so the warning is not a constant.

What this cannot prove, and does not claim: that LiveKit has dropped an
already-connected pupil's camera. A 202 is a warning that deletion is unconfirmed,
not evidence that it happened. That still needs two real browsers against a real
SFU, which is why the ScanMeet flags stay OFF.
"""
import pytest
from flask import Flask, g

from app.routes import super_admin as sa
from app.services import live_cleanup, livekit_gateway

SCHOOL_ID = "school-1"
SESSION = {"id": "s1", "room_name": "room-1", "school_id": SCHOOL_ID,
           "status": "active"}
ENDED = dict(SESSION, status="ended")


class _Answer:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op = "select"

    def select(self, columns):                      # noqa: D401 - chain stub
        self.op = "select"
        return self

    def upsert(self, row, on_conflict=None):
        self.op = "upsert"
        return self

    def update(self, payload):
        self.op = "update"
        return self

    def eq(self, key, value):
        return self

    def limit(self, count):
        return self

    def single(self):
        return self

    def execute(self):
        answer = self.db.answers.get((self.name, self.op), [])
        if isinstance(answer, Exception):
            raise answer
        return _Answer(answer)


class _DB:
    """A table stub keyed by ``(table, operation)``; an Exception raises."""

    def __init__(self, answers):
        self.answers = answers

    def table(self, name):
        return _Query(self, name)


def _db(*, sessions=None, ended=None, sessions_error=None):
    return _DB({
        ("schools", "select"): [{"id": SCHOOL_ID, "npsn": "12345678",
                                 "name": "SMA Negeri 1"}],
        ("scanmeet_school_entitlements", "select"): [],
        ("scanmeet_school_entitlements", "upsert"): [{}],
        ("scan_live_sessions", "select"): (
            sessions_error if sessions_error is not None
            else [SESSION] if sessions is None else sessions),
        ("scan_live_sessions", "update"): (
            [ENDED] if ended is None else ended),
    })


@pytest.fixture
def app():
    return Flask(__name__)


def _disable(monkeypatch, app, db, *, end_room, enqueue):
    monkeypatch.setattr(sa, "get_supabase", lambda: db)
    monkeypatch.setattr(sa, "log_activity", lambda *a, **k: None)

    def fake_end_room(config, room):
        outcome = end_room
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fake_enqueue(db_, session, *, reason="session_ended", error=None, now=None):
        outcome = enqueue
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(livekit_gateway, "end_room", fake_end_room)
    monkeypatch.setattr(live_cleanup, "enqueue", fake_enqueue)

    view = sa.scanmeet_school_licenses
    while getattr(view, "__wrapped__", None) is not None:
        view = view.__wrapped__
    with app.test_request_context("/super-admin/scanmeet-schools?npsn=12345678",
                                  method="POST", json={"enabled": False}):
        g.user_id = "sa-1"
        g.user_role = "super_admin"
        g.user_school_id = None
        return app.make_response(view())


def test_failed_room_deletion_is_202_and_names_what_is_pending(monkeypatch, app):
    response = _disable(monkeypatch, app, _db(),
                        end_room=RuntimeError("livekit unreachable"),
                        enqueue={"id": "job-1"})
    body = response.get_json()

    assert response.status_code == 202, "a deletion that failed must not report success"
    assert body["cleanup_pending"] == ["s1"]
    assert body["cleanup_unrecorded"] == []
    assert body["revoked"] == ["s1"]
    assert body["enabled"] is False
    assert "unconfirmed" in body["warning"].lower()


def test_debt_the_queue_refused_is_reported_apart_from_pending(monkeypatch, app):
    """A queue that raises and a queue that declines are the same fact."""
    for enqueue in (RuntimeError("cleanup table unavailable"), None):
        response = _disable(monkeypatch, app, _db(),
                            end_room=RuntimeError("livekit unreachable"),
                            enqueue=enqueue)
        body = response.get_json()

        assert response.status_code == 202
        assert body["cleanup_unrecorded"] == ["s1"], (
            "unrecorded debt needs an operator; pending needs only a retry")
        assert body["cleanup_pending"] == ["s1"]


def test_successful_revocation_stays_200_without_a_warning(monkeypatch, app):
    response = _disable(monkeypatch, app, _db(), end_room=True,
                        enqueue={"id": "job-1"})
    body = response.get_json()

    assert response.status_code == 200, "the warning must not be a constant"
    assert body["revoked"] == ["s1"]
    assert body["cleanup_pending"] == []
    assert "warning" not in body


def test_a_room_another_worker_already_ended_is_not_claimed(monkeypatch, app):
    """The update is filtered on ``status='active'``; an empty answer means lost."""
    response = _disable(monkeypatch, app, _db(ended=[]), end_room=True,
                        enqueue={"id": "job-1"})
    body = response.get_json()

    assert response.status_code == 200
    assert body["revoked"] == [], "a revocation nobody won must not be reported"


def test_an_unreadable_session_list_is_reconciled_not_silently_200(
        monkeypatch, app):
    response = _disable(monkeypatch, app,
                        _db(sessions_error=RuntimeError("select failed")),
                        end_room=True, enqueue={"id": "job-1"})
    body = response.get_json()

    assert response.status_code == 202
    assert "reconciliation" in body["error"].lower()
