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
   fullscreen exit — which the UI then promised carried no penalty — pushed the
   student's next tab switch up the graduated ladder.
3. The page's counter started at 0 on every load while the ladder lived in the
   database, so a reload handed back warnings already used and moved the
   auto-submit point.

The template guards at the end keep the first and third from coming back.

**The fullscreen policy changed deliberately since (2).** Fullscreen is now
mandatory for the whole exam and leaving it is charged like a tab switch, because
the school asked for it: the exam page blocks behind an overlay until fullscreen
is regained, and *an act the page blocks cannot be an act that carries no
penalty* — that combination is what let a restored or minimised window go
unnoticed during an exam. The tests below encode the new policy, so reverting it
has to be deliberate.
"""
import pathlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.anti_cheat_service import (
    PENALIZED_VIOLATION_TYPES,
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

    # Ordering and limiting do not change a count, and the debounce read only
    # needs the newest row out of whatever it is handed.
    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
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

def test_the_acts_that_count_are_tab_switch_and_leaving_fullscreen():
    """Everything the student can be *charged* for, and nothing they cannot."""
    supa = FakeSupabase([
        log(vtype="tab_switch"), log(vtype="fullscreen_exit"),
        log(vtype="tab_switch"), log(vtype="blur"),
    ])

    assert count_penalized_violations(supa, "stu-1", "exam-1") == 3


def test_the_penalized_set_is_exactly_these_three():
    """Pinned so a fourth type cannot be charged by accident — and so a kind the
    page only records (a right-click, a copy) cannot start costing points.

    ``focus_lost`` is the third, and it is the one that had to be measured to be
    believed: a windowed exam beside another window leaves ``document.hidden``
    false, so the visibility path never fired and the window-blur handler logged a
    console line and recorded nothing. The three are one act to a school — the
    assessment stopped being the thing on screen — so they share the ladder.
    """
    assert set(PENALIZED_VIOLATION_TYPES) == {
        "tab_switch", "fullscreen_exit", "focus_lost",
    }


def test_other_students_and_exams_are_not_counted():
    supa = FakeSupabase([
        log(), log(uid="stu-2"), log(exam="exam-2"),
    ])

    assert count_penalized_violations(supa, "stu-1", "exam-1") == 1


def test_a_lookup_failure_does_not_invent_a_penalty(app):
    """A DB error must not invent a penalty — and must be logged, not swallowed."""
    with app.app_context():
        assert count_penalized_violations(FakeSupabase([], fail=True), "stu-1", "exam-1") == 0


def test_leaving_fullscreen_climbs_the_same_ladder_as_a_tab_switch():
    """The first offence of either kind is the warning; the penalty starts at the
    second. Leaving fullscreen is not a lesser act than switching tabs."""
    supa = FakeSupabase([log(vtype="fullscreen_exit")])
    first = count_penalized_violations(supa, "stu-1", "exam-1")
    assert first == 1
    assert calculate_graduated_penalty(first, EXAM) == {
        "penalty": 0, "warning": True, "auto_submit": False,
        "current_penalty_this_violation": 0,
    }

    supa = FakeSupabase([log(vtype="fullscreen_exit"), log(vtype="fullscreen_exit")])
    second = count_penalized_violations(supa, "stu-1", "exam-1")
    assert second == 2
    assert calculate_graduated_penalty(second, EXAM)["penalty"] == float(
        EXAM["penalty_per_violation"])


def test_the_two_kinds_share_one_counter():
    """A tab switch after a fullscreen exit is the student's second offence, not
    their first — the ladder counts acts, not per-type totals."""
    supa = FakeSupabase([log(vtype="fullscreen_exit"), log(vtype="tab_switch")])

    count = count_penalized_violations(supa, "stu-1", "exam-1")

    assert count == 2
    assert calculate_graduated_penalty(count, EXAM)["penalty"] == 5.0


# ── the debounce, on a server that is not on UTC ─────────────────

class _LogsOnlySupabase:
    """Only the one read ``validate_violation_log`` performs."""

    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "violation_logs", f"unexpected table {name}"
        return FakeQuery(self._rows)


def _recent_row(value):
    # The keys the query filters on have to be present, or FakeQuery filters the
    # row away and the read looks like "no previous event".
    return _LogsOnlySupabase([{"user_id": "stu-1", "exam_id": "exam-1",
                               "created_at": value}])


def test_one_instant_parses_to_one_epoch_on_any_machine():
    """The debounce is the only thing between a broken client and an
    auto-submitted exam, and it silently did nothing. See below.

    Checked as an identity, not against the clock: comparing to `time.time()`
    would only expose the defect on a machine whose local timezone is not UTC,
    which is exactly why it survived — development is UTC and the server is WIB.
    Two spellings of one instant must parse to one epoch wherever this runs.
    """
    from app.services import anti_cheat_service as svc

    utc = svc._as_utc_epoch("2026-09-13T13:32:54+00:00")
    wib = svc._as_utc_epoch("2026-09-13T20:32:54+07:00")
    zulu = svc._as_utc_epoch("2026-09-13T13:32:54Z")
    naive = svc._as_utc_epoch("2026-09-13T13:32:54")

    assert utc == wib, "the offset was thrown away, so WIB read as a later instant"
    assert utc == zulu
    assert utc == naive, "a column with no offset is UTC here, as it is written"
    # ...and the value has to be the instant it names, not a local reading of it.
    assert utc == datetime(2026, 9, 13, 13, 32, 54, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("spelling", ["offset", "zulu", "naive"])
def test_a_second_event_in_the_same_instant_is_rejected(app, spelling):
    """The same rule through the function that uses it."""
    from app.services import anti_cheat_service as svc

    now = datetime.now(timezone.utc)
    value = {
        "offset": now.isoformat(),
        "zulu": now.isoformat().replace("+00:00", "Z"),
        "naive": now.replace(tzinfo=None).isoformat(),
    }[spelling]

    with app.app_context():
        app.extensions["supabase"] = _recent_row(value)
        out = svc.validate_violation_log("stu-1", "exam-1", time.time())

    assert out == {"valid": False, "reason": "rate_limited"}


def test_an_event_outside_the_window_is_accepted(app):
    """The other half: the window must not become a wall. A genuine second act,
    a minute later, has to be recorded."""
    from app.services import anti_cheat_service as svc

    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    with app.app_context():
        app.extensions["supabase"] = _recent_row(old)
        out = svc.validate_violation_log("stu-1", "exam-1", time.time())

    assert out["valid"] is True


def test_an_unreadable_timestamp_does_not_wedge_the_endpoint(app):
    """A row that cannot be parsed must not crash the log route, and must not be
    treated as 'just now' either — that would swallow every later event."""
    from app.services import anti_cheat_service as svc

    with app.app_context():
        app.extensions["supabase"] = _recent_row("not-a-date")
        out = svc.validate_violation_log("stu-1", "exam-1", time.time())

    assert out["valid"] is True


# ── the endpoint reports what it will charge ─────────────────────

def _call(client, payload, monkeypatch, supabase):
    from app.routes import api as apimod
    monkeypatch.setattr(apimod, "get_supabase", lambda: supabase)
    return apimod, client.post("/api/violation/log", json=payload,
                               headers={"Accept": "application/json"})


def test_count_endpoint_returns_the_penalty(app, monkeypatch):
    from app.routes import api as apimod

    # One tab switch plus two fullscreen exits: all three are charged now.
    rows = [log(), log(vtype="fullscreen_exit"), log(vtype="fullscreen_exit")]
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

    assert body["count"] == 3
    # `penalty` is the running total (0 + base + 2×base); the ladder the student
    # reads on screen quotes the per-violation figure, which for the third is
    # 2×base. Keeping the two apart is what the numbers here hold.
    assert body["penalty"] == 15.0
    assert body["current_penalty_this_violation"] == 10.0
    assert body["auto_submit"] is False


class _TableSupabase:
    def __init__(self, table_fn):
        self._table = table_fn

    def table(self, name):
        return self._table(name)


# ── template guards ──────────────────────────────────────────────

def test_page_sends_the_token_in_the_violation_payload():
    src = TEMPLATE.read_text(encoding="utf-8")

    # Both halves of the request have to carry it. The body is what makes a
    # `sendBeacon`-shaped call work and the header is what a `fetch` is checked
    # against first; the original bug was a body that could not hold either.
    assert "const token = this._csrfToken();" in src
    assert "_csrf_token: token" in src, "the token must travel in the body"
    assert "'X-CSRF-Token': token" in src, "and in the header"
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


# ── fullscreen is enforced, not merely announced ─────────────────

def _src():
    return TEMPLATE.read_text(encoding="utf-8")


def test_the_fullscreen_state_is_sampled_not_only_listened_for():
    """The defect this whole change is about.

    Restoring a window down and minimising it are the acts a student uses to put
    something else on screen, and neither reliably fires `fullscreenchange` or
    even `resize` — so a listener-only check reported nothing and the exam looked
    clean. The state has to be sampled as well.
    """
    src = _src()

    assert "document.addEventListener('fullscreenchange', check);" in src
    assert "window.addEventListener('resize'" in src, "a resize must re-check"
    assert "this._fsWatch = setInterval(check, 2000);" in src, \
        "the state must also be sampled — no event covers every way out"
    assert "this.watchFullscreen();" in src, "and the watcher has to be armed"


def test_losing_fullscreen_covers_the_exam_until_it_comes_back():
    """A page cannot maximise a window, so blocking is what 'not allowed' means.

    It also has to be recoverable in one click: the overlay is the only thing on
    screen, and the click that dismisses it is the user gesture the browser
    requires before it will grant fullscreen again.
    """
    src = _src()

    assert 'x-show="fullscreenBlocked && !submitted"' in src, \
        "the overlay must disappear with the exam, not outlive the submission"
    assert "resumeFullscreen()" in src, "and offer the way back"
    assert "this.requestExamFullscreen();" in src
    assert "this.fullscreenBlocked = false;" in src, \
        "only regaining fullscreen may clear it"


def test_a_device_without_the_fullscreen_api_is_not_failed_for_it():
    """iPhone Safari has no Fullscreen API, and a permissions policy can switch it
    off. Neither is a student's doing, and neither can be fixed by them — so the
    check stands down rather than blocking an exam they cannot start."""
    src = _src()

    assert "_fsSupported()" in src
    assert "document.fullscreenEnabled !== false" in src
    assert "!this.antiCheat.fullscreen_required || !this._fsSupported()" in src, \
        "the guard has to be in the check itself, not only where it is armed"


def test_one_minimise_is_charged_once():
    """A minimise makes the document hidden, which the visibility handler already
    charges. Sampling the fullscreen state while hidden would bill the same act a
    second time, and an over-charged student is as wrong as an uncaught one.
    """
    src = _src()

    assert "// A hidden document is a minimise or a tab switch" in src
    assert "this._hiddenAbsenceCharged = true;" in src
    assert "if (this._hiddenAbsenceCharged) {" in src, \
        "the overlay must be able to go up without charging again"


def test_starting_the_exam_is_not_itself_a_violation():
    """agreeExam() asks for fullscreen on the click that arms the watcher, and the
    request resolves a moment later. Without a grace window the student's first
    act of starting is charged."""
    src = _src()

    assert "_fsGraceMs" in src
    assert "!this._sawFullscreen && (Date.now() - this._fsArmedAt) < this._fsGraceMs" in src


def test_a_reload_does_not_bill_the_browser_dropping_fullscreen():
    """Measured in the browser: Chrome keeps fullscreen across a same-origin
    reload, so the new document's first sample is already *in* fullscreen — and
    the browser then drops it on its own. Treating that as the student having
    established fullscreen and then left it charged a reload, which is the very
    action the page supports for crash recovery.

    So arriving in fullscreen must not arm the charge; only a fullscreen reached
    from outside may.
    """
    src = _src()

    assert "_fsWasAbsent: false," in src
    assert "if (this._fsWasAbsent) this._sawFullscreen = true;" in src, \
        "an inherited fullscreen must not count as one the student established"
    assert "this._fsWasAbsent = true;" in src, \
        "and reaching fullscreen later has to be recognised as such"


def test_the_violation_is_recorded_as_the_act_it_was():
    """The teacher's report can only tell a fullscreen exit from a tab switch if
    the client names the right type, so the type travels with the request — and
    so does how long the student was gone, which is the number the countdown
    makes worth reading and the evidence a countdown existed at all."""
    src = _src()

    assert "handleViolation(vtype = 'tab_switch', trigger = null, awaySeconds = null)" in src
    assert "this.handleViolation('fullscreen_exit');" in src
    assert "this.startAwayGrace('focus_lost', 'window_blur', gone);" in src, \
        "the window-blur path has to name its own act, or the report cannot"
    assert "this.startAwayGrace('tab_switch', 'visibilitychange', gone);" in src, \
        "and the hidden path must not borrow the other one's name"
    assert "violation_type: vtype," in src, "the type must reach the server"
    assert "trigger: trigger, away_seconds: awaySeconds" in src, \
        "the act the page saw and the seconds it measured both travel with it"
    assert "this.handleViolation(kind || 'tab_switch', trigger, away)" in src, \
        "and the charge carries the very absence the countdown was opened for"


def test_the_terms_say_fullscreen_is_required():
    """The reason the old behaviour was indefensible: the page charged nothing for
    something it also never asked for. The agreement now says what is required.
    """
    src = _src()

    assert "Wajib layar penuh." in src
    assert "menghentikan ujian" in src
    assert "keluar dari layar penuh" in src, \
        "and the ladder must name the acts that are counted"


def test_the_visible_window_that_loses_focus_blurs_the_paper_under_the_same_guards():
    """The case the page could not see at all.

    A restored-down browser beside another window leaves ``document.hidden``
    false: the visibility path never ran, the fullscreen check never ran, and the
    window-blur handler's whole body was a console line. So a windowed exam could
    be read off the screen for the length of a sitting with no record that it
    happened. It now raises the same panel under the same guards and after the
    same debounce — and, since the second chance, opens the countdown instead of
    charging on the spot.
    """
    src = _src()
    handler = src.split("window.addEventListener('blur'", 1)[1] \
               .split("window.addEventListener('focus'", 1)[0]

    assert "if (document.hidden) return;" in handler, \
        "a hidden document belongs to the visibility handler: one absence, one charge"
    assert "if (!this._isFocusTab || this.submitted || document.hidden) return;" in handler, \
        "raised after the guard, never before it"
    assert "if (this.fullscreenBlocked) return;" in handler, \
        "an absence the fullscreen overlay already charged is not billed twice"
    assert "}, 1500);" in handler, \
        "the same debounce as the hidden path, so a moment's inattention is nothing"
    assert "this.startAwayGrace('focus_lost', 'window_blur', gone);" in handler, \
        "the panel goes up and the countdown starts in the same breath"
    assert "this.handleViolation(" not in handler, \
        "and the charge waits for the countdown, so a phone call is not a penalty"


# ── the paper is blurred, not merely dimmed ──────────────────────
#
# A window restored down and a new tab are the same act from the student's side:
# something else on screen. The paper behind is still there to be read, which is
# exactly why it stops being readable — so it is blurred behind the panel, both
# for losing fullscreen and for leaving the exam.

def test_a_restored_window_blurs_the_paper_rather_than_hiding_it():
    """Leaving fullscreen already covered the paper; a 95% scrim *hides* it.

    The ask was a blur: the exam is visibly still there and unreadable. A
    `backdrop-*` utility is what does that, and it has to be on the fullscreen
    panel — the one that answers a restore-down — not on some other element.
    """
    src = _src()

    fullscreen_panel = src.split('x-show="fullscreenBlocked && !submitted"', 1)[1]
    fullscreen_panel = fullscreen_panel.split('>', 1)[0]
    assert "backdrop-blur" in fullscreen_panel, \
        "the fullscreen panel has to blur the paper, not cover it"
    assert "bg-surface-900/95 backdrop-blur-sm" not in src, \
        "a 95% scrim is a hide, which is what a blur replaced"


def test_leaving_the_exam_blurs_the_paper_too():
    """Switching to another tab left the paper fully readable when the student
    came back. It is now blurred behind its own panel, with one click back.
    """
    src = _src()

    assert 'x-show="awayBlurred && !submitted"' in src, \
        "the away panel must disappear with the exam, not outlive the submission"
    panel = src.split('x-show="awayBlurred && !submitted"', 1)[1].split('>', 1)[0]
    assert "backdrop-blur" in panel, "and it has to be the blur, not a plain scrim"
    assert '@click="returnToExam()"' in src, "with one deliberate way back"


def test_the_away_blur_cannot_fire_before_the_terms_are_accepted():
    """Arming on page load watched a student who had not started, and a
    middle-clicked link in a background tab was counted as leaving. The blur is
    raised in exactly one place — the funnel every armed handler calls — so no
    path can put the panel up while skipping the guards beside it.
    """
    src = _src()

    assert "awayBlurred: false," in src, "it starts clear"
    assert src.count("this.awayBlurred = true;") == 1, \
        "one site for both ways the screen is taken away, so the guards cannot " \
        "be true of one and false of the other"
    funnel = src.split("startAwayGrace(kind, trigger, since) {", 1)[1] \
               .split("\n        },", 1)[0]
    assert "this.awayBlurred = true;" in funnel, "and that site is the funnel"
    assert "if (this.submitted || !this.antiCheat.enabled) return;" in funnel
    assert "if (!this._isFocusTab) return;" in funnel, \
        "the tab in front is the only one that may blur its own paper"
    armed = src.split("armAntiCheat() {", 1)[1]
    assert "this.setupAntiCheat()" in armed, \
        "the handlers live in what arming installs"
    assert armed.count("this.startAwayGrace(") == 2, \
        "both of them, so neither can be reached from somewhere that is not armed"


def test_the_second_tab_of_the_same_exam_does_not_blur_the_tab_in_front():
    """Two tabs of one exam each counting the other is the defect this ladder was
    fixed for. The countdown obeys the same rule as the charge beside it: a tab
    another tab has taken over from stays silent, so the paper a student is
    actually working is never blurred by the tab they are not looking at.
    """
    src = _src()

    hidden = src.split("if (document.hidden) {", 1)[1].split("} else {", 1)[0]
    assert "if (!this._isFocusTab || this.submitted) return;" in hidden, \
        "the hidden path still refuses a tab another tab took over from"
    assert "this.startAwayGrace('tab_switch', 'visibilitychange', gone);" in hidden, \
        "and hands the absence to the funnel, which applies the rule a second time"
    assert "this.awayBlurred = true;" not in hidden, \
        "so the blur is never raised outside the guarded funnel"


def test_one_click_clears_the_blur_and_re_enters_fullscreen():
    """Leaving the exam usually drops fullscreen as well, so clearing the blur on
    its own would meet the fullscreen panel on the next click. One gesture has to
    do both — and that gesture is the one the browser requires.
    """
    src = _src()

    body = src.split("async returnToExam() {", 1)[1].split("\n        },", 1)[0]
    assert "this.awayBlurred = false;" in body, "the panel has to come down"
    assert "await this.resumeFullscreen();" in body, \
        "and the same click re-enters fullscreen when that was lost too"
