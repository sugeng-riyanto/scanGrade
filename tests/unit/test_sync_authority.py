"""The server, not the browser, owns a sitting's start, end and order.

`POST /api/student/sync-draft` is the endpoint an offline-first paper leans on, and
it is the one write path a paper has besides `submit`. The page's countdown is a
picture of the deadline; the deadline is the server's, and until this file the route
only *reported* the remaining time — it wrote whatever arrived, whenever it arrived.
Three consequences, each a hole this file closes:

* **A sync past the deadline was stored, not refused.** The route computed
  `server_time_left` and then merged and wrote the answers anyway, so a device (or a
  script) kept writing a paper the server had already treated as over. The grace that
  lets a submission arrive late is `exam_window.LATE_GRACE_SECONDS`, and the one
  place that applies it is `attempt_status.expired`; past that, the write is refused
  rather than accepted in silence.
* **The client's claimed `started_at` became the sitting's origin.** On the first
  sync of a paper with no row yet, the route stored the client's own timestamp as
  `started_at` — the very value the deadline is counted from. A device whose clock
  it controls could move its own deadline. The server's clock is now the only origin;
  the client's claim is a signal, never the stored value.
* **A replayed sync overwrote newer answers.** The row carried no order, so an old
  payload resent later (a replay, or two requests reordered) merged over what was
  already there. Each accepted write now carries an `answers._rev`, and a payload
  whose revision is behind the stored one is refused as `stale_rev`.

What must *not* change, and is pinned here too: a sync of a paper already handed in
writes nothing (`already_submitted`), a sync inside the grace is still stored, and a
payload that carries no revision at all is still stored — a client bug or a
last-moment `sendBeacon` must not cost a pupil their answers.

The template sends `rev` and reads the refusals; the server guards here are driven
through the real view with an in-memory PostgREST, the way
`tests/unit/test_sync_eligibility.py` does for the throttle.
"""
from __future__ import annotations

import datetime as dt
import pathlib
from types import SimpleNamespace

import pytest

from app.routes import api as api_module

ROOT = pathlib.Path(__file__).resolve().parents[2]
API_PY = ROOT / "app" / "routes" / "api.py"

#: A fixed instant, so nothing here depends on when the suite runs.
NOW = 1_700_000_000
EXAM_MINUTES = 60
GRACE = 120


def iso(when: int) -> str:
    return dt.datetime.fromtimestamp(when, tz=dt.timezone.utc).isoformat()


EXAM = {
    "id": "exam-1", "duration_minutes": EXAM_MINUTES, "total_questions": 1,
    "answer_key": {}, "question_types": {}, "question_weights": {},
    "max_attempts": 1, "publish_mode": "manual", "is_published": True,
    "status": "active", "start_at": None, "end_at": None,
    "auto_submit_on_window_end": False,
}


# ── an in-memory PostgREST, the subset this route reads ──────────────────────

class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Chainable stand-in for one table. Records every write it is asked to make."""

    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self._mode = "select"
        self._filters = []
        self._payload = None
        self._limit = None
        self._single = False
        self._maybe = False
        self.writes = []

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single, self._maybe = True, True
        return self

    def insert(self, data):
        self._mode, self._payload = "insert", data
        return self

    def update(self, data):
        self._mode, self._payload = "update", data
        return self

    def _match(self):
        return [r for r in self.rows
                if all(r.get(c) == v for c, v in self._filters)]

    def execute(self):
        if self._mode == "select":
            rows = self._match()
            self._filters = []
            if self._limit:
                rows = rows[: self._limit]
            if self._single:
                if not rows:
                    return None if self._maybe else FakeResponse(None)
                return FakeResponse(rows[0])
            return FakeResponse(rows)

        payload = dict(self._payload)
        self.writes.append((self._mode, payload))
        if self._mode == "insert":
            row = dict(payload)
            row.setdefault("id", f"sub-{len(self.rows) + 1}")
            self.rows.append(row)
            self._filters = []
            return FakeResponse([row])

        rows = self._match()
        for r in rows:  # a real UPDATE moves the stored row, stamp included
            r.update(payload)
        self._filters = []
        return FakeResponse(rows)


class FakeSupabase:
    def __init__(self, tables):
        self._tables = dict(tables)

    def table(self, name):
        return self._tables.setdefault(name, FakeTable())


def sitting(*, started, updated=None, answers=None, rev=None, status="draft",
            exam_over=None):
    """One paper on one exam, with the start and the stored revision the test wants."""
    stored_answers = dict(answers or {})
    if rev is not None:
        stored_answers["_rev"] = rev
    row = {
        "id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
        "status": status, "answers": stored_answers,
        "started_at": iso(started),
        "updated_at": iso(updated if updated is not None else started),
    }
    exam = dict(EXAM)
    exam.update(exam_over or {})
    supa = FakeSupabase({"exams": FakeTable([exam]),
                         "submissions": FakeTable([row])})
    return supa, row


def empty_paper(exam_over=None):
    exam = dict(EXAM)
    exam.update(exam_over or {})
    return FakeSupabase({"exams": FakeTable([exam]), "submissions": FakeTable([])})


def writes(supa):
    return supa.table("submissions").writes


# ── the driver: the real view, a pinned clock, no lock and no RBAC ───────────

def sync(supa, monkeypatch, app, *, clock=None, light=True, payload=None):
    """Drive `student_sync_draft` as a logged-in pupil and return (body, status)."""
    from flask import g

    app.extensions["supabase"] = supa
    monkeypatch.setattr(api_module, "_redis_lock", lambda key: object())
    monkeypatch.setattr(api_module, "_release_lock", lambda conn, key: None)
    monkeypatch.setattr(api_module, "exam_sitting_allowed",
                        lambda sb, exam, eid, sid: (True, ""))
    pinned = lambda: (clock or [NOW])[0]
    monkeypatch.setattr(api_module, "time",
                        SimpleNamespace(time=pinned, monotonic=pinned))

    body = {"exam_id": "exam-1", "answers": {"0": "A"}, "light": light}
    body.update(payload or {})
    with app.test_request_context("/api/student/sync-draft", method="POST", json=body):
        g.user_id, g.user_role = "stu-1", "murid"
        out = api_module.student_sync_draft.__wrapped__()
    if isinstance(out, tuple):
        resp, status = out[0], out[1]
    else:
        resp, status = out, out.status_code
    return resp.get_json(), status


# ── 1. the deadline is the server's, and past it the write is refused ────────

class TestTheDeadlineIsTheServers:
    def test_a_sync_after_the_deadline_is_refused_not_stored(self, monkeypatch, app):
        """Started 70 minutes ago on a 60-minute paper: over, and past the grace."""
        supa, _row = sitting(started=NOW - 70 * 60)
        body, status = sync(supa, monkeypatch, app)

        assert status == 409, "a late sync was answered as a success"
        assert body.get("error") == "past_deadline"
        assert writes(supa) == [], (
            "the refused sync still wrote to the paper, so the deadline costs nothing"
        )

    def test_the_refusal_carries_the_clock_so_the_page_can_submit(self, monkeypatch, app):
        """The page auto-submits on `server_time_left <= 0`; a refusal must carry it."""
        supa, _row = sitting(started=NOW - 70 * 60)
        body, _status = sync(supa, monkeypatch, app)
        assert body.get("server_time_left") == 0

    def test_a_sync_inside_the_grace_is_still_stored(self, monkeypatch, app):
        """One minute past the deadline, inside the grace a submission may arrive in."""
        supa, _row = sitting(started=NOW - (EXAM_MINUTES * 60 + 60))
        body, status = sync(supa, monkeypatch, app)

        assert body.get("error") != "past_deadline"
        assert status == 200
        assert len(writes(supa)) == 1, "the grace saved nothing"

    def test_the_grace_boundary_itself_is_not_refused(self, monkeypatch, app):
        """`expired` is `now > deadline + grace`, so the exact edge is still stored."""
        supa, _row = sitting(started=NOW - (EXAM_MINUTES * 60 + GRACE))
        body, _status = sync(supa, monkeypatch, app)
        assert body.get("error") != "past_deadline"
        assert len(writes(supa)) == 1

    def test_the_refusal_does_not_read_the_clock_the_client_claims(self, monkeypatch, app):
        """A pupil insisting it is still early does not move the server's deadline."""
        supa, _row = sitting(started=NOW - 70 * 60)
        body, status = sync(supa, monkeypatch, app,
                            payload={"started_at": (NOW - 60) * 1000})
        assert status == 409
        assert body.get("error") == "past_deadline"

    def test_the_assignment_window_end_ends_the_sitting_too(self, monkeypatch, app):
        """`auto_submit_on_window_end`: the earlier of the two clocks is the end."""
        supa, _row = sitting(
            started=NOW - 60 * 60,
            exam_over={"duration_minutes": 600, "end_at": iso(NOW - 10 * 60),
                       "auto_submit_on_window_end": True},
        )
        body, status = sync(supa, monkeypatch, app)
        assert status == 409
        assert body.get("error") == "past_deadline"


# ── 2. the start time is the server's, whoever asks ──────────────────────────

class TestTheStartIsTheServers:
    def test_a_new_sitting_is_stamped_with_the_servers_clock(self, monkeypatch, app):
        """A claimed start ten thousand seconds ahead must not become the origin."""
        supa = empty_paper()
        sync(supa, monkeypatch, app, payload={"started_at": (NOW + 10_000) * 1000})

        mode, payload = writes(supa)[-1]
        assert mode == "insert"
        assert payload["started_at"] == iso(NOW), (
            "the client's claimed start became the sitting's origin, so a device "
            "that controls its clock controls its own deadline"
        )

    def test_a_claimed_start_in_the_past_does_not_move_the_origin_either(self, monkeypatch, app):
        supa = empty_paper()
        sync(supa, monkeypatch, app, payload={"started_at": (NOW - 10_000) * 1000})

        _mode, payload = writes(supa)[-1]
        assert payload["started_at"] == iso(NOW)

    def test_the_reported_time_left_is_counted_from_the_servers_start(self, monkeypatch, app):
        """A claimed ancient start cannot shorten the sitting to zero."""
        supa = empty_paper()
        body, _status = sync(supa, monkeypatch, app,
                             payload={"started_at": (NOW - 10_000) * 1000})
        assert body.get("server_time_left") == EXAM_MINUTES * 60, (
            "the remaining time was counted from the client's claim"
        )


# ── 3. a replayed or reordered payload is refused by its revision ────────────

class TestReplayIsRefused:
    def test_a_replayed_older_revision_is_refused(self, monkeypatch, app):
        supa, row = sitting(started=NOW - 300, answers={"0": "B"}, rev=5)
        before = dict(supa.table("submissions").rows[0]["answers"])

        body, status = sync(supa, monkeypatch, app,
                            payload={"rev": 3, "answers": {"0": "Z"}})

        assert status == 409, "a stale payload was accepted as a fresh write"
        assert body.get("error") == "stale_rev"
        assert writes(supa) == [], "the replayed sync still wrote"
        assert supa.table("submissions").rows[0]["answers"] == before, (
            "the replayed answers overwrote what the server already held"
        )

    def test_the_refusal_reports_the_revision_to_adopt(self, monkeypatch, app):
        """The page learns the server's revision from the refusal and catches up."""
        supa, _row = sitting(started=NOW - 300, rev=5)
        body, _status = sync(supa, monkeypatch, app, payload={"rev": 3})
        assert body.get("rev") == 5

    def test_the_current_revision_is_accepted_and_advanced(self, monkeypatch, app):
        supa, _row = sitting(started=NOW - 300, rev=5)
        body, status = sync(supa, monkeypatch, app, payload={"rev": 5})

        assert status == 200
        assert body.get("rev") == 6, "the stored revision did not move on"
        _mode, payload = writes(supa)[-1]
        assert payload["answers"]["_rev"] == 6

    def test_a_payload_without_a_revision_is_still_stored(self, monkeypatch, app):
        """A `sendBeacon` or an older client must not lose the last answers."""
        supa, _row = sitting(started=NOW - 300, rev=5)
        body, status = sync(supa, monkeypatch, app)  # no `rev` at all

        assert status == 200, "a revision-less sync was refused, losing the answers"
        assert len(writes(supa)) == 1
        assert body.get("rev") == 6

    def test_the_first_ever_sync_starts_the_sequence(self, monkeypatch, app):
        supa = empty_paper()
        body, _status = sync(supa, monkeypatch, app)

        assert body.get("rev") == 1
        _mode, payload = writes(supa)[-1]
        assert payload["answers"]["_rev"] == 1


# ── 4. a paper already handed in is not a write target ───────────────────────

class TestASubmittedSittingIsNotAWriteTarget:
    def test_a_sync_against_a_submitted_paper_is_not_stored(self, monkeypatch, app):
        supa, _row = sitting(started=NOW - 300, status="submitted")
        body, _status = sync(supa, monkeypatch, app)

        assert body.get("note") == "already_submitted"
        assert writes(supa) == []


# ── 5. one clock owns the sync path (source guards) ──────────────────────────

class TestOneClockOwnsTheSync:
    def test_the_route_asks_the_one_service_for_its_expiry(self):
        """The grace is the app's, not a second copy of `LATE_GRACE_SECONDS`."""
        source = API_PY.read_text(encoding="utf-8-sig")
        assert "attempt_status.expired(" in source, (
            "the sync path does not ask attempt_status whether the sitting is over, "
            "so its grace can drift from the one the submit route and the sweep use"
        )

    def test_the_route_refuses_a_write_past_the_deadline(self):
        source = API_PY.read_text(encoding="utf-8-sig")
        assert "past_deadline" in source, "the sync path has no late refusal"

    def test_the_route_refuses_a_stale_revision(self):
        source = API_PY.read_text(encoding="utf-8-sig")
        assert "stale_rev" in source, "the sync path has no replay guard"


# ── 6. the page carries the order, and reads both refusals ────────────────────

class TestThePageCarriesTheOrder:
    """The server guard is only real if the page sends the number it acts on."""

    SOURCE = (ROOT / "app" / "templates" / "student" / "take_exam.html").read_text(
        encoding="utf-8")

    def test_the_sync_sends_the_revision(self):
        assert "body.rev = sgGetDraftRev();" in self.SOURCE, (
            "the exam page does not send its draft order, so the server's replay "
            "guard never sees one"
        )

    def test_it_adopts_the_servers_revision(self):
        assert "sgSetDraftRev(data.rev)" in self.SOURCE

    def test_it_honours_a_stale_revision(self):
        assert "stale_rev" in self.SOURCE, (
            "the page ignores the server's replay refusal, so it would retry the "
            "same stale payload forever"
        )

    def test_it_submits_when_the_server_says_the_paper_is_over(self):
        assert "past_deadline" in self.SOURCE, (
            "a refused late sync never reaches the page, so the paper is never sent"
        )

    def test_the_unload_beacon_carries_the_revision_too(self):
        assert "rev: sgGetDraftRev()" in self.SOURCE
