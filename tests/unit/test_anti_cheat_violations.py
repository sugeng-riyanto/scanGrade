"""Anti-cheat violations must actually reach the server, and be charged fairly.

Measured on the real database: ``violation_logs`` was **empty — no row had ever
been written**. Three separate defects kept it that way, and each one made the
exam page's behaviour a lie:

1. The page sent the events with ``navigator.sendBeacon`` as a JSON **array**.
   ``validate_csrf`` reads ``_csrf_token`` out of a JSON *object*, so a bare list
   could never carry one and every event was answered **403 CSRF token invalid**.
   The student watched the penalty ladder climb to "PELANGGARAN #3! -10 poin"
   while nothing was recorded and no penalty ever reached a score.
2. The count that drives the penalty included *every* violation type, so a
   fullscreen exit — which the UI explicitly promises carries no penalty — pushed
   the student's next tab switch up the graduated ladder.
3. The page's counter started at 0 on every load while the ladder lived in the
   database, so a reload handed back warnings already used and moved the
   auto-submit point.

The template guards at the end keep the first and third from coming back.
"""
import pathlib
from types import SimpleNamespace

import pytest

from app.services.anti_cheat_service import (
    calculate_graduated_penalty,
    count_penalized_violations,
)
from app.utils.csrf import validate_csrf

TEMPLATE = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates" / "student" / "take_exam.html"

EXAM = {"anti_cheat_enabled": True, "penalty_per_violation": 5,
        "max_violations": 5, "auto_submit_on_max": True}


# ── fakes ────────────────────────────────────────────────────────

class FakeQuery:
    """Counts rows of one type, honouring the .in_() filter."""

    def __init__(self, rows):
        self._rows = rows
        self._filters = []
        self._in = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, values):
        self._in = (col, list(values))
        return self

    def execute(self):
        rows = [r for r in self._rows if all(r.get(c) == v for c, v in self._filters)]
        if self._in:
            col, values = self._in
            rows = [r for r in rows if r.get(col) in values]
        return SimpleNamespace(count=len(rows), data=rows)


class FakeSupabase:
    def __init__(self, rows, fail=False):
        self._rows = rows
        self._fail = fail

    def table(self, name):
        if self._fail:
            raise RuntimeError("db down")
        assert name == "violation_logs", f"unexpected table {name}"
        return FakeQuery(self._rows)


def log(uid="stu-1", exam="exam-1", vtype="tab_switch"):
    return {"user_id": uid, "exam_id": exam, "violation_type": vtype}


@pytest.fixture
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


# ── the CSRF shape that made every event a no-op ─────────────────

def test_a_json_array_cannot_carry_a_csrf_token(app):
    """This is the exact payload the page used to send."""
    with app.test_request_context(
        "/api/violation/log", method="POST",
        json=[{"exam_id": "e", "violation_type": "tab_switch", "timestamp": 0}],
    ):
        from flask import session
        session["_csrf_token"] = "known-token"
        assert validate_csrf() is False, (
            "a list body can never satisfy the guard — the client must send an object"
        )


def test_a_json_object_with_the_token_passes(app):
    with app.test_request_context(
        "/api/violation/log", method="POST",
        json={"_csrf_token": "known-token", "logs": [{"exam_id": "e"}]},
    ):
        from flask import session
        session["_csrf_token"] = "known-token"
        assert validate_csrf() is True


def test_the_endpoint_rejects_the_old_beacon_payload(app):
    """End to end: the shape the page used to send is refused by the guard."""
    client = app.test_client()
    resp = client.post("/api/violation/log", json=[
        {"exam_id": "e", "violation_type": "tab_switch", "timestamp": 0},
    ])

    assert resp.status_code == 403
    assert "csrf" in resp.get_json()["error"].lower()


def test_the_endpoint_lets_the_new_payload_past_the_guard(app):
    """With a token in the object the guard is satisfied (auth is tested after)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "known-token"

    resp = client.post("/api/violation/log",
                       json={"_csrf_token": "known-token", "logs": [{"exam_id": "e"}]},
                       headers={"Accept": "application/json"})

    assert resp.status_code != 403, resp.data[:200]


# ── which violations are charged ─────────────────────────────────

def test_only_tab_switches_are_counted():
    supa = FakeSupabase([
        log(vtype="tab_switch"), log(vtype="fullscreen_exit"),
        log(vtype="tab_switch"), log(vtype="blur"),
    ])

    assert count_penalized_violations(supa, "stu-1", "exam-1") == 2


def test_other_students_and_exams_are_not_counted():
    supa = FakeSupabase([
        log(), log(uid="stu-2"), log(exam="exam-2"),
    ])

    assert count_penalized_violations(supa, "stu-1", "exam-1") == 1


def test_a_lookup_failure_does_not_invent_a_penalty(app):
    """A DB error must not invent a penalty — and must be logged, not swallowed."""
    with app.app_context():
        assert count_penalized_violations(FakeSupabase([], fail=True), "stu-1", "exam-1") == 0


def test_a_fullscreen_exit_does_not_escalate_the_ladder():
    """The UI promises no penalty for it; the ladder must agree."""
    only_fullscreen = [log(vtype="fullscreen_exit"), log(vtype="fullscreen_exit")]
    supa = FakeSupabase(only_fullscreen)

    count = count_penalized_violations(supa, "stu-1", "exam-1")

    assert count == 0
    assert calculate_graduated_penalty(count, EXAM)["penalty"] == 0


def test_first_tab_switch_after_a_fullscreen_exit_is_still_a_warning():
    """It used to be charged as violation #2 because the exit was counted first."""
    supa = FakeSupabase([log(vtype="fullscreen_exit"), log(vtype="tab_switch")])

    count = count_penalized_violations(supa, "stu-1", "exam-1")

    assert count == 1
    assert calculate_graduated_penalty(count, EXAM)["penalty"] == 0


# ── the endpoint reports what it will charge ─────────────────────

def _call(client, payload, monkeypatch, supabase):
    from app.routes import api as apimod
    monkeypatch.setattr(apimod, "get_supabase", lambda: supabase)
    return apimod, client.post("/api/violation/log", json=payload,
                               headers={"Accept": "application/json"})


def test_count_endpoint_returns_the_penalty(app, monkeypatch):
    from app.routes import api as apimod

    rows = [log(), log(), log(vtype="fullscreen_exit")]
    supa = FakeSupabase(rows)

    class _Exams:
        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(data=EXAM)

    def _table(name):
        if name == "exams":
            return _Exams()
        return FakeQuery(rows)

    monkeypatch.setattr(apimod, "get_supabase", lambda: _TableSupabase(_table))

    with app.test_request_context("/api/violation/count?exam_id=exam-1"):
        from flask import g
        g.user_id, g.user_role = "stu-1", "murid"
        body = apimod.violation_count.__wrapped__().get_json()

    assert body["count"] == 2
    assert body["penalty"] == 5.0          # 2nd violation = -base
    assert body["auto_submit"] is False


class _TableSupabase:
    def __init__(self, table_fn):
        self._table = table_fn

    def table(self, name):
        return self._table(name)


# ── template guards ──────────────────────────────────────────────

def test_page_sends_the_token_in_the_violation_payload():
    src = TEMPLATE.read_text(encoding="utf-8")

    assert "_csrf_token: this._csrfToken()" in src, "the token must travel in the body"
    assert "'X-CSRF-Token': token" in src
    # The beacon-shaped array was the reason nothing was ever recorded.
    assert "new Blob([JSON.stringify([{" not in src


def test_anti_cheat_is_not_armed_on_page_load():
    """Arming before the terms are accepted watched a student who had not started."""
    src = TEMPLATE.read_text(encoding="utf-8")
    init_body = src.split("        init() {", 1)[1].split("        onGoOnline()", 1)[0]

    assert "this.setupAntiCheat()" not in init_body
    assert "this.armAntiCheat()" in init_body

    agree_body = src.split("        agreeExam() {", 1)[1].split("        toggleCalculator()", 1)[0]
    assert "this.armAntiCheat();" in agree_body, "accepting the terms must arm the anti-cheat"


def test_screen_stays_clear_a_background_tab_cannot_start_counting():
    """Only the visible tab enforces, so two tabs cannot charge each other."""
    src = TEMPLATE.read_text(encoding="utf-8")

    assert "if (document.hidden) return;   // a background tab must not take over" in src
    assert "if (!this._isFocusTab || this.submitted) return;" in src
