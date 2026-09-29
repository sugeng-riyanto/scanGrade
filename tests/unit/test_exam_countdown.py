"""The exam clock is the server's, and the page may not invent one of its own.

The countdown on a sitting is stored, not remembered: `submissions.started_at` is
written once, when the student first opens the paper, and every later reading —
the page's own render, the periodic sync, the sweep that closes an expired paper —
computes "how long is left" from that stamp and the present. So leaving the exam
and coming back cannot restart anything; the arithmetic has nothing else to read.

Two ways the page could break that rule itself, both measured on the running code
before this file existed:

1. **It invented a deadline for a paper that has none.** The teacher's form offers
   ``duration_minutes = 0``, labelled *Tak terbatas / Unlimited*, and
   `exam_window.deadline()` reads it as "nothing enforces an end". The student's
   page did not: `const EXAM_DURATION = (exam.duration_minutes or 120) * 60` turned
   that same 0 into **two hours**, and whenever the server answered with no
   deadline the page counted down from it and called ``submitExam(true)`` at zero.
   A teacher who chose *Unlimited* got a paper that submitted itself, and the
   countdown a student watched was one nobody set.
2. **It read "no answer" as "no time left".** The sync API omits
   ``server_time_left`` when nothing enforces an end, but two of its branches send
   it as JSON ``null``. In JavaScript ``null <= 0`` is **true**, so a null signal
   was read as an expired clock: the page jumped to zero and force-submitted the
   paper. The same coercion made ``null < timeLeft - 5`` true, so the display could
   be set to ``null`` — "NaN:NaN" on screen.

What is checked here, and how:

* the *behaviour* of the tick and of the countdown rule, run under Node against
  the shipped template text, because "does it submit?" is not a question a regex
  can answer;
* the route's own decision — that the time a returning student is handed is a
  function of the stored stamp and the present, and that neither visiting nor
  revisiting rewrites that stamp.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import student as studentmod
from app.utils import exam_window

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own rule")

EXAM_ID = "exam-1"
PUPIL = "stu-1"
SCHOOL = "school-A"
CLASS = "class-7A"
START = datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)


def _exam(**over):
    row = {
        "id": EXAM_ID, "title": "Latihan Ujian", "subject": "Fisika",
        "school_id": SCHOOL, "class_ids": [CLASS], "teacher_id": "guru-1",
        "is_published": True, "status": "active",
        "duration_minutes": 60, "total_questions": 0,
        "question_types": {}, "answer_key": {}, "question_weights": {},
        "start_at": None, "end_at": None, "auto_submit_on_window_end": False,
        "pdf_page_urls": [],
    }
    row.update(over)
    return row


# ── a postgrest stand-in that really stores ─────────────────────────────────
#
# `open_sitting` inserts or updates; the property under test is that a second
# visit does *not* rewrite the stamp. A fake that returned the row it was given
# without storing it could not tell those apart, so this one keeps state.

class FakeQuery:
    def __init__(self, store, name, log):
        self._store, self._name, self._log = store, name, log
        self._filters, self._one, self._pending = [], False, None

    # ── reading ─────────────────────────────────────────────────────────────
    def select(self, *columns, **kwargs):
        self._log.append(columns[0] if columns else "")
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def neq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        # postgrest answers `maybe_single()` with a row or None, never a list:
        # `row_or_none()` and every guard that calls `.get()` depend on that.
        self._one = True
        return self

    # ── writing ─────────────────────────────────────────────────────────────
    def insert(self, payload):
        self._pending = ("insert", dict(payload))
        return self

    def update(self, patch):
        self._pending = ("update", dict(patch))
        return self

    def _matches(self):
        out = []
        for row in self._store.setdefault(self._name, []):
            if all(row.get(col) in want if isinstance(want, list) else row.get(col) == want
                   for col, want in self._filters):
                out.append(row)
        return out

    def execute(self):
        if self._pending:
            kind, payload = self._pending
            self._pending = None
            if kind == "insert":
                self._store.setdefault(self._name, []).append(payload)
                return SimpleNamespace(data=[payload], count=1)
            rows = self._matches()
            for row in rows:
                row.update(payload)
            return SimpleNamespace(data=rows, count=len(rows))
        rows = self._matches()
        if self._one:
            return SimpleNamespace(data=rows[0] if rows else None, count=len(rows))
        return SimpleNamespace(data=rows, count=len(rows))


class FakeSupabase:
    def __init__(self, tables=None):
        self._tables = tables or {}
        self.selects = {}

    def table(self, name):
        return FakeQuery(self._tables, name,
                         self.selects.setdefault(name, []))

    def rows(self, name):
        return self._tables.get(name, [])


def _seeded(sitting_start, **exam_over):
    """The world as the database would hold it: one pupil, one exam, one sitting."""
    return FakeSupabase({
        "exams": [_exam(**exam_over)],
        "profiles": [{"id": PUPIL, "full_name": "Ahmad", "class_id": CLASS,
                      "school_id": SCHOOL, "role": "murid", "status": "active"}],
        "classes": [{"id": CLASS, "name": "7A", "grade_level": "7", "school_id": SCHOOL}],
        "submissions": [{
            "id": "sub-1", "exam_id": EXAM_ID, "student_id": PUPIL,
            "status": "draft", "answers": {},
            "started_at": sitting_start.isoformat(),
            "updated_at": sitting_start.isoformat(),
        }],
    })


def _visit(app, monkeypatch, supa):
    """Open the exam page the way the route does, capturing what it hands over."""
    from flask import g

    captured = {}

    def _capture(name, **kw):
        captured["template"], captured["ctx"] = name, kw
        return ""

    monkeypatch.setattr(studentmod, "render_template", _capture)
    monkeypatch.setattr(studentmod, "ensure_page_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(studentmod, "issue_code", lambda *a, **k: "123456")
    app.extensions["supabase"] = supa

    with app.test_request_context(f"/student/exams/{EXAM_ID}"):
        g.user_id, g.user_role, g.user_name = PUPIL, "murid", "Ahmad"
        g.user_email, g.user_school_id = "", SCHOOL
        g.user_class_id, g.user_status = CLASS, "active"
        g.tz_offset, g.show = 7, {}
        studentmod.take_exam.__wrapped__(EXAM_ID)

    assert captured.get("template") == "student/take_exam.html"
    return captured["ctx"]


def _stamp_of(supa):
    return supa.rows("submissions")[0].get("started_at")


# ── the clock belongs to the server ─────────────────────────────────────────

class TestTheCountdownIsTheServers:
    def test_a_returning_student_is_handed_the_time_that_is_left(self, app, monkeypatch):
        """The defect a restart would produce is a *fresh hour*, so that is what
        this refuses: the page is given the remainder, not the duration."""
        started = datetime.now(timezone.utc) - timedelta(minutes=10)
        ctx = _visit(app, monkeypatch, _seeded(started))

        expected = exam_window.seconds_left(_exam(), started.isoformat())
        assert expected is not None
        assert abs(ctx["seconds_left"] - expected) <= 5, (
            f"the page was handed {ctx['seconds_left']}s, not the {expected}s that remain"
        )
        assert ctx["seconds_left"] < 60 * 60 - 500, (
            "ten minutes into a one-hour paper the page still offers most of the hour"
        )

    def test_the_start_it_is_counting_from_is_the_stored_one(self, app, monkeypatch):
        started = datetime.now(timezone.utc) - timedelta(minutes=10)
        supa = _seeded(started)
        ctx = _visit(app, monkeypatch, supa)
        assert exam_window.parse_dt(ctx["exam_started_at"]) == \
            exam_window.parse_dt(started.isoformat())

    def test_visiting_again_does_not_rewrite_the_start(self, app, monkeypatch):
        """Leaving and coming back is the whole question, so the stamp is read
        after both visits: a second stamp is a restarted exam."""
        started = datetime.now(timezone.utc) - timedelta(minutes=20)
        supa = _seeded(started)
        before = _stamp_of(supa)

        first = _visit(app, monkeypatch, supa)
        second = _visit(app, monkeypatch, supa)

        assert _stamp_of(supa) == before, (
            "opening the page again moved `started_at`, which restarts the clock"
        )
        assert first["exam_started_at"] == second["exam_started_at"]
        assert second["seconds_left"] <= first["seconds_left"], (
            "the second visit was handed more time than the first"
        )
        assert len(supa.rows("submissions")) == 1, (
            "a second sitting row was created for the same exam"
        )

    def test_the_same_stamp_keeps_counting_while_the_student_is_away(self):
        """Twenty minutes later, from the *same* stamp: the remainder shrinks and
        the duration is never offered again. This is the arithmetic every later
        reading shares — the page's render, the sync, and the sweep."""
        started = START
        exam = _exam()
        left_at_start = exam_window.seconds_left(exam, started, started)
        left_after_20 = exam_window.seconds_left(exam, started, started + timedelta(minutes=20))

        assert left_at_start == 60 * 60
        assert left_after_20 == 60 * 60 - 20 * 60, (
            "time spent away did not come off the clock"
        )

    def test_the_window_end_still_caps_what_is_left(self):
        """The other clock: a paper that must stop at the assignment's end is not
        given its duration just because it has one."""
        exam = _exam(duration_minutes=120, auto_submit_on_window_end=True,
                     end_at=(START + timedelta(minutes=30)).isoformat())
        left = exam_window.seconds_left(exam, START, START + timedelta(minutes=10))
        assert left == 20 * 60


# ── an exam with no deadline has no countdown ───────────────────────────────

def _render_page(app, **over):
    """The exam page with only the context its own copy needs (as its route sends)."""
    from flask import g

    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": 1,
                 "duration_minutes": 30, "question_types": {}, "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": START.isoformat(), "recovery_code": "",
        "question_options": {}, "deadline": (START + timedelta(minutes=30)).isoformat(),
        "deadline_reason": "duration", "seconds_left": 1800, "window_end": None,
        "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    for key, value in over.items():
        if key in ctx["exam"]:
            ctx["exam"][key] = value
        else:
            ctx[key] = value
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


TICK = re.compile(r"this\.timer = setInterval\(\(\) => \{(.*?)\n\s*\}, 1000\);", re.S)
COUNTDOWN = re.compile(r"function sgCountdown\([^)]*\) \{\r?\n(.*?)\r?\n\}", re.S)
INITIAL = re.compile(r"\n\s*timeLeft: (.+?),(?:\r?\n)", re.S)
ALERT = re.compile(r"_deadlineAlert\(\) \{\r?\n(.*?)\r?\n        \},", re.S)


def _alert(body: str, cases: list) -> list:
    """Run the page's own deadline-warning method over (timeLeft, hidden) pairs."""
    script = (
        "const body = " + json.dumps(body) + ";\n"
        "globalThis.document = {hidden: false};\n"
        "const warn = new Function('return function(){' + body + '}')();\n"
        "console.log(JSON.stringify(" + json.dumps(cases) + ".map(function (c) {\n"
        "  const self = {submitted: false, showExamAgreement: false, timeLeft: c[0],\n"
        "    _alerted: {}, beeps: [], beep(n) { this.beeps.push(n); }};\n"
        "  document.hidden = c[1];\n"
        "  warn.call(self);\n"
        "  return self.beeps;\n"
        "})));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _evaluate(html: str, expression: str, inputs: list) -> list:
    """Evaluate one expression from the page, with the page's own sgCountdown."""
    rule = COUNTDOWN.search(html)
    assert rule, "the page no longer defines the rule that reads the server's number"
    script = (
        "eval(" + json.dumps(rule.group(0)) + ");\n"
        "const ask = new Function('EXAM_SECONDS_LEFT', 'return (' + "
        + json.dumps(expression) + " + ');');\n"
        "console.log(JSON.stringify(" + json.dumps(inputs) + ".map(function (v) {\n"
        "  const out = ask(v);\n"
        "  return (typeof out === 'number' && isFinite(out)) ? out : (out === null ? null : 'not-a-number');\n"
        "})));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _tick(html: str, states: list) -> list:
    """Run the page's own timer callback, three times per starting state."""
    match = TICK.search(html)
    assert match, "the page no longer has a one-second timer to run"
    body = match.group(1)
    script = (
        "const body = " + json.dumps(body) + ";\n"
        "const tick = new Function('return function(){' + body + '}')();\n"
        "const states = " + json.dumps(states) + ";\n"
        "console.log(JSON.stringify(states.map(function (s) {\n"
        "  const c = {timeLeft: s, submitted: false, alerts: 0,\n"
        "    submitExam(f) { this.submitted = true; this.forced = f; },\n"
        "    _deadlineAlert() { this.alerts++; }};\n"
        "  for (let i = 0; i < 3; i++) tick.call(c);\n"
        "  return {out: c.timeLeft, submitted: c.submitted, forced: !!c.forced, alerts: c.alerts};\n"
        "})));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _countdown(html: str, values: list) -> list:
    match = COUNTDOWN.search(html)
    assert match, "the page no longer defines the rule that reads the server's number"
    assert len(match.group(0).splitlines()) == 3, (
        f"the countdown rule is not the three lines this reads: {match.group(0)!r}"
    )
    script = (
        "const src = " + json.dumps(match.group(0)) + ";\n"
        "eval(src);\n"
        "console.log(JSON.stringify(" + json.dumps(values) + ".map(function (v) {\n"
        "  const out = sgCountdown(v);\n"
        "  return (typeof out === 'number' && isFinite(out)) ? out : (out === null ? null : 'not-null');\n"
        "})));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


class TestAPaperWithNoDeadlineHasNoCountdown:
    def test_the_page_has_no_duration_of_its_own(self):
        """``duration_minutes`` of 0 is *Tak terbatas*, not two hours. The page
        must not turn it into a countdown, so the constant it used to is gone."""
        source = PAGE.read_text(encoding="utf-8")
        assert "EXAM_DURATION" not in source, (
            "the page still derives a countdown from the exam's configured duration"
        )

    @needs_node
    def test_the_countdown_is_the_servers_number_and_nothing_else(self, app):
        """Run the initializer the page actually ships. A fallback of any shape —
        ``|| 7200``, ``Math.max(0, duration - elapsed)`` — shows up here as a
        number where the server said there is none."""
        html = _render_page(app)
        expr = INITIAL.search(html)
        assert expr, "the page no longer starts its countdown somewhere this can read"
        got = _evaluate(html, expr.group(1), [None, 1800])
        assert got == [None, 1800], (
            f"the countdown's starting value is not the server's number: {got}"
        )

    def test_a_rendered_unlimited_exam_says_so(self, app):
        html = _render_page(app, duration_minutes=0, deadline=None,
                            deadline_reason="duration", seconds_left=None)
        assert "const EXAM_SECONDS_LEFT = null" in html, (
            "the route's `no deadline` was not rendered as `null`"
        )
        assert "EXAM_DURATION" not in html

    @needs_node
    def test_a_null_clock_neither_counts_nor_submits(self, app):
        """The defect in one line: with no deadline the tick used to walk the
        countdown down to zero and submit the paper."""
        html = _render_page(app, duration_minutes=0, deadline=None,
                            deadline_reason="duration", seconds_left=None)
        got = _tick(html, [None])
        assert got[0]["out"] is None, "a countdown ran on a paper with no deadline"
        assert got[0]["submitted"] is False, "an exam with no time limit submitted itself"
        assert got[0]["alerts"] == 0, "the deadline warnings fired on a paper with no deadline"

    @needs_node
    def test_a_real_clock_still_counts_down_and_then_submits(self, app):
        """The other direction, so the guard cannot be satisfied by a timer that
        never runs."""
        html = _render_page(app)
        got = _tick(html, [2, 0])
        assert got[0] == {"out": 0, "submitted": True, "forced": True, "alerts": 2}, got[0]
        assert got[1]["submitted"] is True, (
            "a paper whose clock had already run out was not submitted"
        )


# ── a time signal is a number ───────────────────────────────────────────────

class TestATimeSignalMustBeANumber:
    @needs_node
    def test_only_a_finite_number_is_a_clock(self, app):
        html = _render_page(app)
        asked = [None, 0, 3000, 120.5, "300", "", True, False, [], {}, "NaN", "Infinity"]
        got = _countdown(html, asked)
        assert got[:4] == [None, 0, 3000, 120.5], (
            "`null` or a real number was misread as a clock"
        )
        assert got[4:] == [None] * (len(asked) - 4), (
            f"something that is not a number was accepted as time left: {got[4:]}"
        )

    def test_the_sync_cannot_read_a_null_for_no_time_left(self):
        """``null <= 0`` is true in JavaScript, so a null from the sync used to
        force-submit the paper. The reconciliation has to read the signal through
        the same rule as everything else."""
        source = PAGE.read_text(encoding="utf-8")
        block = source[source.index("Timer reconciliation"):]
        block = block[:block.index("syncCanvasToServer")]
        assert "sgCountdown(data.server_time_left)" in block, (
            "the sync's `server_time_left` is compared without being read as a number"
        )
        assert "data.server_time_left <= 0" not in block, (
            "the raw signal is still compared against zero — a JSON null passes that test"
        )

    @needs_node
    def test_the_warning_needs_a_clock_to_warn_about(self, app):
        """The deadline beeps read ``timeLeft <= 60``, and ``null <= 60`` is true:
        without this guard a paper with no limit would announce that one minute was
        left. Run against the page's own method, with the tab both visible and
        hidden, because ``document.hidden`` already silences it for other reasons."""
        html = _render_page(app)
        body = ALERT.search(html)
        assert body, "the deadline warning is no longer where this can read it"
        got = _alert(body.group(1), [(None, False), (30, False), (30, True),
                                     (180, False), (400, False)])
        assert got[0] == [], f"a paper with no deadline announced a deadline: {got[0]}"
        assert got[1] == [3], f"the one-minute warning did not fire: {got[1]}"
        assert got[2] == [], f"a hidden tab was announced to: {got[2]}"
        assert got[3] == [2], f"the five-minute warning did not fire: {got[3]}"
        assert got[4] == [], f"a warning fired far from the mark: {got[4]}"

    def test_no_display_formats_a_clock_it_does_not_have(self):
        """`formatTime(null)` renders ``NaN:NaN``, and `:class=\"null < 60\"` is
        true, so the timer would paint red on a paper with no limit."""
        source = PAGE.read_text(encoding="utf-8")
        printed = list(re.finditer(r"formatTime\(timeLeft\)", source))
        assert len(printed) == 2, "the countdown is printed somewhere new"
        for value in printed:
            line = source[source.rindex("\n", 0, value.start()):value.end()]
            assert "timeLeft === null" in line, (
                f"a countdown is printed without checking it exists: {line.strip()[:120]}"
            )
        assert source.count("timeLeft !== null && timeLeft < 60") == 2, (
            "a timer colour is chosen from a null countdown"
        )


# ── the terms screen states the limit the teacher chose ────────────────────

class TestTheTermsScreenStatesTheLimit:
    def test_an_unlimited_paper_does_not_read_as_zero_minutes(self, app):
        """The first place a student reads what they are agreeing to. `0` there
        means *Tak terbatas*, and **the page is told which branch it is in** rather
        than deciding for itself: the rule lives in `exam_window.duration_facts`, so
        the list, the dashboard, the builder and this page cannot answer differently
        (see `test_duration_rule`)."""
        html = _render_page(app, duration_minutes=0)
        assert "durationMinutes: 0" in html
        assert "t('Tak terbatas','Unlimited')" in html, (
            "the duration line cannot say 'Unlimited' in either language"
        )
        assert "durationUnlimited: true" in html, (
            "the duration line is not told the server's answer for a zero duration"
        )
        assert "durationUnlimited ? t('Tak terbatas','Unlimited')" in html, (
            "the duration line does not read the server's branch"
        )

    def test_the_duration_line_reads_the_exam_row(self, app):
        for minutes, unlimited in ((45, "false"), (0, "true")):
            html = _render_page(app, duration_minutes=minutes)
            assert f"durationMinutes: {minutes}" in html
            assert f"durationUnlimited: {unlimited}" in html
