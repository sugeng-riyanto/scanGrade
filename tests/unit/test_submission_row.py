"""A re-sit is written into the row the unique constraint already keys by.

`submissions` is unique on `(student_id, exam_id)` for **every** status, but the
submit route used to decide between UPDATE and INSERT by asking whether a *draft*
row existed. A `retracted` row is neither a draft nor a live attempt — and
`max_attempts` excludes it on purpose, which is why the exam returns to the exam
list — so the INSERT went ahead against a key that was already taken:

    duplicate key value violates unique constraint "submissions_student_exam_unique"

The student saw that sentence in an alert and could not submit at all. These tests
pin the write the crash needs: a target row exists ⇒ PATCH it, never POST, and the
only INSERT happens when this student really has no row.
"""
import json
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from supabase import create_client

from app.services.submission_service import (
    LIVE_STATUSES,
    finish_sitting,
    open_sitting,
    sitting_target,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
STUDENT = "d6574fb8-234f-4f53-86cd-9fc6702766a9"
EXAM = "5f357840-1fb2-45c0-a822-2493124aacbb"
ROW = "7414ed48-6918-40bf-8b08-0abab3360024"


def row(status, **extra):
    """One submissions row, keyed the way the constraint keys it."""
    return {
        "id": ROW,
        "exam_id": EXAM,
        "student_id": STUDENT,
        "status": status,
        "started_at": "2026-09-13T06:22:17+00:00",
        **extra,
    }


# ── a fake PostgREST, in memory ───────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, name, calls, pending=None):
        self.store = store
        self.name = name
        self.calls = calls
        self.filters = []
        self.payload = pending

    def select(self, *cols, **kwargs):
        self.calls.append(("select", self.name))
        return self

    def update(self, payload):
        self.calls.append(("update", self.name))
        self.payload = payload
        return self

    def insert(self, payload):
        self.calls.append(("insert", self.name))
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        kind = self.calls[-1][0]
        if kind == "select":
            return _Result([r for r in rows
                            if all(r.get(c) == v for c, v in self.filters)])
        if kind == "update":
            changed = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
            for r in changed:
                r.update(self.payload)
            return _Result([dict(r) for r in changed])
        if isinstance(self.payload, list):
            rows.extend(self.payload)
            return _Result(list(self.payload))
        created = {"id": f"new-{len(rows) + 1}", **self.payload}
        rows.append(created)
        return _Result([created])


class _FakeSupabase:
    def __init__(self, rows=None):
        self.store = {"submissions": list(rows or [])}
        self.calls = []

    def table(self, name):
        return _Query(self.store, name, self.calls)


def writes(log):
    return [c for c in log if c[0] != "select"]


# ── which row a finished attempt is written into ──────────────────────────────

class TestWhichRowOwnsTheAttempt:
    def test_no_row_means_nothing_to_target(self):
        assert sitting_target([]) is None

    def test_a_draft_is_the_ordinary_target(self):
        assert sitting_target([row("draft")])["status"] == "draft"

    def test_a_retracted_row_is_a_target_not_an_obstacle(self):
        """The defect: this row exists, so an INSERT collides."""
        assert sitting_target([row("retracted")])["status"] == "retracted"

    @pytest.mark.parametrize("status", LIVE_STATUSES)
    def test_a_live_attempt_is_never_a_target(self, status):
        assert sitting_target([row(status)]) is None

    def test_a_draft_wins_over_a_retracted_row(self):
        draft = row("draft")
        assert sitting_target([row("retracted"), draft]) is draft


# ── opening a sitting ─────────────────────────────────────────────────────────

class TestOpeningASitting:
    def test_creates_a_draft_when_the_student_has_no_row(self):
        sb = _FakeSupabase()
        created, opened = open_sitting(sb, EXAM, STUDENT)
        assert opened is True
        assert created["status"] == "draft"
        assert created["started_at"]
        assert writes(sb.calls) == [("insert", "submissions")]

    def test_an_open_draft_keeps_its_stamp_and_is_not_rewritten(self):
        """The timer survives a refresh — which needs no write at all."""
        sb = _FakeSupabase([row("draft")])
        sitting, opened = open_sitting(sb, EXAM, STUDENT)
        assert opened is False
        assert sitting["started_at"] == "2026-09-13T06:22:17+00:00"
        assert writes(sb.calls) == []

    def test_a_voided_attempt_is_reopened_as_a_draft(self):
        sb = _FakeSupabase([row("retracted")])
        sitting, opened = open_sitting(sb, EXAM, STUDENT)
        assert opened is True
        assert sitting["status"] == "draft"
        assert sitting["answers"] == {}
        assert writes(sb.calls) == [("update", "submissions")]
        assert sb.store["submissions"][0]["status"] == "draft"

    def test_a_draft_without_a_stamp_gets_one(self):
        sb = _FakeSupabase([row("draft", started_at=None)])
        _sitting, opened = open_sitting(sb, EXAM, STUDENT)
        assert opened is True
        assert sb.store["submissions"][0]["started_at"]

    @pytest.mark.parametrize("status", LIVE_STATUSES)
    def test_a_live_attempt_is_left_alone(self, status):
        sb = _FakeSupabase([row(status)])
        sitting, opened = open_sitting(sb, EXAM, STUDENT)
        assert opened is False
        assert sitting["status"] == status
        assert writes(sb.calls) == []

    def test_an_answer_without_a_representation_is_read_back(self):
        """supabase-py can answer an insert with no body; the row still exists."""
        sb = _FakeSupabase()
        original = _Query.execute

        def no_body(self):
            if self.calls[-1][0] == "insert":
                sb.store["submissions"].append(row("draft"))
                return _Result(None)
            return original(self)

        _Query.execute = no_body
        try:
            sitting, opened = open_sitting(sb, EXAM, STUDENT)
        finally:
            _Query.execute = original
        assert opened is True
        assert sitting["id"] == ROW


# ── finishing a sitting ───────────────────────────────────────────────────────

class TestFinishingASitting:
    def test_a_retracted_row_is_updated_not_inserted(self):
        """Exactly the production failure: a PATCH, and no POST anywhere."""
        sb = _FakeSupabase([row("retracted")])
        written = finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"}, None)
        assert written == ROW
        assert writes(sb.calls) == [("update", "submissions")]
        assert sb.store["submissions"][0]["status"] == "submitted"

    def test_the_rows_the_route_already_read_cost_no_extra_query(self):
        sb = _FakeSupabase([row("retracted")])
        finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"}, [row("retracted")])
        assert sb.calls == [("update", "submissions")]

    def test_no_row_means_insert(self):
        sb = _FakeSupabase()
        finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"}, [])
        assert writes(sb.calls) == [("insert", "submissions")]

    @pytest.mark.parametrize("status", LIVE_STATUSES)
    def test_a_live_attempt_is_reported_not_overwritten(self, status):
        sb = _FakeSupabase([row(status)])
        assert finish_sitting(sb, EXAM, STUDENT, {"score": 0}, None) == "already_submitted"
        assert writes(sb.calls) == []

    def test_a_collision_from_the_other_tab_becomes_an_update(self):
        """Two tabs submit at once: the loser writes into the winner's row."""
        sb = _FakeSupabase()
        original = _Query.execute

        def collide(self):
            if self.calls[-1][0] == "insert":
                # What PostgREST answers when the key is taken.
                self.store["submissions"].append(row("draft"))
                raise RuntimeError(
                    "{'code': '23505', 'message': 'duplicate key value violates "
                    "unique constraint \"submissions_student_exam_unique\"'}"
                )
            return original(self)

        _Query.execute = collide
        try:
            written = finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"}, [])
        finally:
            _Query.execute = original
        assert written == ROW
        assert sb.store["submissions"][0]["status"] == "submitted"

    def test_another_failure_is_not_swallowed(self):
        sb = _FakeSupabase()
        original = _Query.execute

        def broken(self):
            if self.calls[-1][0] == "insert":
                raise RuntimeError("permission denied for table submissions")
            return original(self)

        _Query.execute = broken
        try:
            with pytest.raises(RuntimeError):
                finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"}, [])
        finally:
            _Query.execute = original


# ── the same thing over HTTP, through the real client ────────────────────────

class _Recorder(BaseHTTPRequestHandler):
    """A stand-in for PostgREST that answers a GET and remembers every write."""

    rows: list = []
    seen: list = []

    def _answer(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                              # noqa: N802
        _Recorder.seen.append(("GET", self.path))
        self._answer(_Recorder.rows)

    def do_POST(self):                                             # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"[]")
        _Recorder.seen.append(("POST", self.path))
        self._answer(payload if isinstance(payload, list) else [payload], 201)

    def do_PATCH(self):                                            # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Recorder.seen.append(("PATCH", self.path))
        self._answer([{**payload, "id": ROW}])

    def log_message(self, *args):                                  # noqa: D102
        pass


def _client():
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # supabase-py rejects any key not shaped like a JWT.
    return server, create_client(f"http://127.0.0.1:{server.server_port}", "dummy.jwt.value")


def test_a_retracted_row_produces_a_patch_and_no_insert():
    _Recorder.seen = []
    _Recorder.rows = [{"id": ROW, "status": "retracted"}]
    server, sb = _client()
    try:
        written = finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"})
    finally:
        server.shutdown()
        server.server_close()
    assert written == ROW
    verbs = [v for v, _p in _Recorder.seen]
    assert "PATCH" in verbs, _Recorder.seen
    assert "POST" not in verbs, f"an INSERT was attempted anyway: {_Recorder.seen}"
    patch = [p for v, p in _Recorder.seen if v == "PATCH"][0]
    assert f"id=eq.{ROW}" in patch, patch


def test_no_row_at_all_produces_an_insert():
    _Recorder.seen = []
    _Recorder.rows = []
    server, sb = _client()
    try:
        finish_sitting(sb, EXAM, STUDENT, {"status": "submitted"})
    finally:
        server.shutdown()
        server.server_close()
    assert [v for v, _p in _Recorder.seen if v != "GET"] == ["POST"]


# ── the rule lives in one place ───────────────────────────────────────────────

class TestOnePlaceOwnsTheWrite:
    ROUTES = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")

    def test_the_submit_route_does_not_write_submissions_itself(self):
        """A future edit must not reintroduce the INSERT this fixed."""
        body = re.search(
            r"def submit_exam\(exam_id\):(.*?)\n@student_bp", self.ROUTES, re.S
        ).group(1)
        offenders = re.findall(
            r'table\("submissions"\)[\s\S]{0,80}?\.(insert|update)\(', body
        )
        assert not offenders, (
            "submit_exam writes the submissions row itself again "
            f"({offenders}) — the INSERT collides on a retracted row"
        )
        assert "finish_sitting(" in body

    def test_the_exam_page_does_not_write_submissions_itself(self):
        body = re.search(
            r"def take_exam\(exam_id\):(.*?)\n@student_bp", self.ROUTES, re.S
        ).group(1)
        offenders = re.findall(
            r'table\("submissions"\)[\s\S]{0,80}?\.(insert|update)\(', body
        )
        assert not offenders, f"take_exam writes the row itself again ({offenders})"
        assert "open_sitting(" in body

    def test_the_student_is_not_shown_the_database_error(self):
        body = re.search(
            r"def submit_exam\(exam_id\):(.*?)\n@student_bp", self.ROUTES, re.S
        ).group(1)
        assert 'jsonify({"error": str(e)})' not in body, (
            "the raw exception is echoed to the student again — that is where "
            "a constraint name turned into a user-visible alert"
        )
