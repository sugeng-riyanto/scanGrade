"""Every transition of the ScanMeet microphone-approval state machine, offline.

The machine has one row per pupil per room and six statuses:

    (none) --raise--> pending --approve--> approving --ok--> approved
                        |                       `--LiveKit fails--> pending
                        |--reject--> rejected
                        `--reconcile (repair)--> pending

    approved --close--> closing --ok--> closed
                            `--LiveKit fails--> approved
                            `--reconcile (repair)--> closed

Three things make this a machine rather than a set of handlers, and each is
tested here rather than assumed:

* **Compare-and-swap.** Every write carries `.eq("status", …)` for the state the
  request believes it saw, so two requests cannot both win. The fixture flips a
  status between the read and the write (`db.race`), which is what the loser
  sees, and the loser must answer 409 — not report a state it did not set.
* **Fail closed.** Any failure between the transitional write and the final one
  must end with the microphone **off** and the row still repairable. A "maybe
  approved" is the one outcome that cannot be expressed here.
* **One open request per pupil.** Migration 070's partial unique index is
  modelled, so a raise that would create a second open row raises 23505 instead
  of quietly succeeding — including while the row is `closing`.

Nothing here touches a database, a socket, or the LiveKit SDK.
"""
import pytest

from tests.unit.live_fixtures import RAISE, SESSION, door, stage

STATES = ("pending", "approving", "approved", "closing", "rejected", "closed")

#: (from, action) -> (status code, resulting status, microphone call or None).
#: Written as one table so a missing cell is visible: every state meets every
#: action, and the answer is the route's, not this docstring's.
MATRIX = {
    ("pending", "approve"): (200, "approved", True),
    ("pending", "reject"): (200, "rejected", None),
    ("pending", "close"): (409, "pending", None),
    ("approving", "approve"): (409, "approving", None),
    ("approving", "reject"): (409, "approving", None),
    ("approving", "close"): (409, "approving", None),
    ("approved", "approve"): (409, "approved", None),
    ("approved", "reject"): (409, "approved", None),
    ("approved", "close"): (200, "closed", False),
    ("closing", "approve"): (409, "closing", None),
    ("closing", "reject"): (409, "closing", None),
    ("closing", "close"): (409, "closing", None),
    ("rejected", "approve"): (409, "rejected", None),
    ("rejected", "reject"): (409, "rejected", None),
    ("rejected", "close"): (409, "rejected", None),
    ("closed", "approve"): (409, "closed", None),
    ("closed", "reject"): (409, "closed", None),
    ("closed", "close"): (409, "closed", None),
}


def _room(status):
    return {
        "scan_live_sessions": [dict(SESSION)],
        "scan_live_hand_raises": [dict(RAISE, status=status)],
    }


@pytest.mark.parametrize("state,action", sorted(MATRIX))
def test_every_state_action_pair_answers_the_table(monkeypatch, state, action):
    code, result, mic = MATRIX[(state, action)]
    app, db, calls = stage(monkeypatch, _room(state))
    response = door(app, "moderate", "room-id", "raise-1", action)
    assert response.status_code == code
    assert db.statuses() == [result]
    if mic is None:
        assert calls == [], "a refused or stored decision must not touch LiveKit"
    else:
        assert calls == [("room-id", "sg_room", "student-x", mic)]
    if code == 200:
        assert response.get_json() == {"status": result}


@pytest.mark.parametrize("state", STATES)
def test_an_unknown_action_is_refused_before_any_state_is_read(monkeypatch, state):
    app, db, calls = stage(monkeypatch, _room(state))
    response = door(app, "moderate", "room-id", "raise-1", "promote")
    assert response.status_code == 400
    assert db.statuses() == [state]
    assert calls == []


@pytest.mark.parametrize("state", STATES)
def test_an_unknown_request_id_is_not_distinguishable_from_another_room(monkeypatch, state):
    app, _db, calls = stage(monkeypatch, _room(state))
    response = door(app, "moderate", "room-id", "raise-other", "approve")
    assert response.status_code == 403
    assert calls == []


# ── the pupil's side: raise, re-raise, and the index ────────────────────────

@pytest.mark.parametrize("state", ["pending", "approving", "approved", "closing"])
def test_a_pupil_with_an_open_request_gets_it_back_instead_of_a_second_row(
        monkeypatch, state):
    app, db, _ = stage(monkeypatch, _room(state))
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 200
    assert response.get_json() == {"id": "raise-1", "status": state}
    assert db.statuses() == [state], "the pupil's row must not be duplicated or moved"


@pytest.mark.parametrize("state", ["rejected", "closed"])
def test_a_decided_row_does_not_block_the_next_raise(monkeypatch, state):
    app, db, _ = stage(monkeypatch, _room(state))
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 201
    assert response.get_json()["status"] == "pending"
    assert db.statuses() == [state, "pending"]


def test_a_pupil_with_no_row_opens_one(monkeypatch):
    app, db, _ = stage(monkeypatch, {
        "scan_live_sessions": [dict(SESSION)], "scan_live_hand_raises": []})
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 201
    assert db.statuses() == ["pending"]


def test_the_pupils_open_set_is_the_one_the_index_enforces(monkeypatch):
    """If the route's list and the index disagree, the loser is a 503.

    The fixture raises 23505 for any second open row, so a state the route reads
    as closed and the index reads as open fails here as a 503 rather than a
    silent duplicate. This is the test that caught ``closing``.
    """
    for state in ("pending", "approving", "approved", "closing"):
        app, db, _ = stage(monkeypatch, _room(state))
        response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
        assert response.status_code == 200, (state, response.get_json())
        assert len(db.raises()) == 1, state


def test_a_pupil_without_a_sitting_is_refused(monkeypatch):
    app, db, _ = stage(monkeypatch, _room("pending"), sitting_allowed=False)
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 403
    assert db.statuses() == ["pending"]


def test_a_teacher_cannot_raise_a_hand(monkeypatch):
    app, _db, _ = stage(monkeypatch, _room("pending"))
    response = door(app, "hand_raise", "room-id")
    assert response.status_code == 403


def test_a_pupil_of_another_school_sees_nothing(monkeypatch):
    app, _db, _ = stage(monkeypatch, {
        "scan_live_sessions": [dict(SESSION, school_id="school-2")],
        "scan_live_hand_raises": [dict(RAISE, status="pending")],
    })
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 403


def test_a_storage_failure_is_reported_as_retryable(monkeypatch):
    app, db, _ = stage(monkeypatch, {
        "scan_live_sessions": [dict(SESSION)], "scan_live_hand_raises": []})
    db.fail_insert = True
    response = door(app, "hand_raise", "room-id", role="murid", user="student-x")
    assert response.status_code == 503
    assert db.statuses() == []


# ── failures, retries and races ─────────────────────────────────────────────

def test_a_failed_permission_change_reverts_and_a_retry_succeeds(monkeypatch):
    app, db, calls = stage(monkeypatch, _room("pending"),
                           mic_errors=[RuntimeError("LiveKit down")])
    first = door(app, "moderate", "room-id", "raise-1", "approve")
    assert first.status_code == 503
    assert db.statuses() == ["pending"], "the teacher must be able to retry"
    assert [c[3] for c in calls] == [True, False], "the retry is only safe after a mute"

    second = door(app, "moderate", "room-id", "raise-1", "approve")
    assert second.status_code == 200
    assert db.statuses() == ["approved"]
    assert [c[3] for c in calls] == [True, False, True]


def test_a_failed_close_reverts_to_approved_and_a_retry_succeeds(monkeypatch):
    app, db, calls = stage(monkeypatch, _room("approved"),
                           mic_errors=[RuntimeError("LiveKit down")])
    assert door(app, "moderate", "room-id", "raise-1", "close").status_code == 503
    assert db.statuses() == ["approved"]
    assert door(app, "moderate", "room-id", "raise-1", "close").status_code == 200
    assert db.statuses() == ["closed"]
    assert [c[3] for c in calls] == [False, False]


def test_two_moderators_cannot_both_approve(monkeypatch):
    app, db, calls = stage(monkeypatch, _room("pending"))
    assert door(app, "moderate", "room-id", "raise-1", "approve").status_code == 200
    second = door(app, "moderate", "room-id", "raise-1", "approve")
    assert second.status_code == 409
    assert db.statuses() == ["approved"]
    assert len(calls) == 1, "the loser must not touch the microphone"


def test_a_decision_that_loses_the_transitional_race_reports_it(monkeypatch):
    """The first write is conditional too: a row moved meanwhile is not renamed."""
    app, db, calls = stage(monkeypatch, _room("pending"))
    db.race = "rejected"
    response = door(app, "moderate", "room-id", "raise-1", "approve")
    assert response.status_code == 409
    assert db.statuses() == ["rejected"]
    assert calls == [], "no permission may be granted for a request that was rejected"


def test_a_row_moved_after_the_permission_was_applied_is_muted_again(monkeypatch):
    """Aim the race at the *second* write: approve already turned the mic on.

    Reporting 200 here would claim the row this request no longer owns, and
    leave a live microphone behind. The route has to notice, mute, and say 409.
    """
    app, db, calls = stage(monkeypatch, _room("pending"))
    db.race_after = 1
    db.race = "closed"
    response = door(app, "moderate", "room-id", "raise-1", "approve")
    assert response.status_code == 409
    assert db.statuses() == ["closed"]
    assert [c[3] for c in calls] == [True, False], "the microphone must end off"


def test_the_loser_of_a_close_race_leaves_the_row_alone(monkeypatch):
    app, db, calls = stage(monkeypatch, _room("approved"))
    db.race_after = 1
    db.race = "approved"
    response = door(app, "moderate", "room-id", "raise-1", "close")
    assert response.status_code == 409
    assert db.statuses() == ["approved"]
    assert [c[3] for c in calls] == [False], "close already had the microphone off"


# ── the repair, against the same table ─────────────────────────────────────

REPAIR = {
    "pending": (409, "pending"),
    "approving": (200, "pending"),
    "approved": (409, "approved"),
    "closing": (200, "closed"),
    "rejected": (409, "rejected"),
    "closed": (409, "closed"),
}


@pytest.mark.parametrize("state", STATES)
def test_repair_only_acts_on_a_parked_grant(monkeypatch, state):
    code, result = REPAIR[state]
    app, db, calls = stage(monkeypatch, _room(state))
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == code
    assert db.statuses() == [result]
    if code == 200:
        assert [c[3] for c in calls] == [False]
    else:
        assert calls == []


def test_repair_is_idempotent_by_refusal(monkeypatch):
    app, db, _ = stage(monkeypatch, _room("approving"))
    assert door(app, "reconcile", "room-id", "raise-1").status_code == 200
    assert db.statuses() == ["pending"]
    again = door(app, "reconcile", "room-id", "raise-1")
    assert again.status_code == 409, "a repaired row is not repairable twice"
    assert db.statuses() == ["pending"]


def test_repair_then_approve_walks_the_whole_path(monkeypatch):
    """A stuck approval, repaired, then decided, then closed: one row, no gaps."""
    app, db, calls = stage(monkeypatch, _room("approving"))
    assert door(app, "reconcile", "room-id", "raise-1").status_code == 200
    assert door(app, "moderate", "room-id", "raise-1", "approve").status_code == 200
    assert door(app, "moderate", "room-id", "raise-1", "close").status_code == 200
    assert db.statuses() == ["closed"]
    assert [c[3] for c in calls] == [False, True, False]


# ── the session is the outer door ──────────────────────────────────────────

@pytest.mark.parametrize("endpoint,args", [
    ("hand_raise", ("room-id",)),
    ("moderate", ("room-id", "raise-1", "approve")),
    ("reconcile", ("room-id", "raise-1")),
    ("hand_raise_queue", ("room-id",)),
    ("end_session", ("room-id",)),
])
def test_an_ended_session_closes_every_door(monkeypatch, endpoint, args):
    rows = {
        "scan_live_sessions": [dict(SESSION, status="ended")],
        "scan_live_hand_raises": [dict(RAISE, status="pending")],
    }
    app, db, calls = stage(monkeypatch, rows)
    kwargs = {"role": "murid", "user": "student-x"} if endpoint == "hand_raise" else {}
    response = door(app, endpoint, *args, **kwargs)
    assert response.status_code == 403
    assert db.statuses() == ["pending"]
    assert calls == []


@pytest.mark.parametrize("endpoint,args", [
    ("moderate", ("room-id", "raise-1", "approve")),
    ("reconcile", ("room-id", "raise-1")),
    ("hand_raise_queue", ("room-id",)),
    ("end_session", ("room-id",)),
])
def test_a_second_teacher_cannot_take_over_the_room(monkeypatch, endpoint, args):
    app, db, calls = stage(monkeypatch, _room("approving"))
    response = door(app, endpoint, *args, user="teacher-b")
    assert response.status_code == 403
    assert db.statuses() == ["approving"]
    assert calls == []


def test_the_disabled_switch_answers_every_door_with_404(monkeypatch):
    for endpoint, args in (("hand_raise", ("room-id",)),
                           ("moderate", ("room-id", "raise-1", "approve")),
                           ("reconcile", ("room-id", "raise-1")),
                           ("end_session", ("room-id",))):
        app, db, calls = stage(monkeypatch, _room("pending"), live_on=False)
        assert door(app, endpoint, *args).status_code == 404, endpoint
        assert calls == []
        assert db.statuses() == ["pending"]
