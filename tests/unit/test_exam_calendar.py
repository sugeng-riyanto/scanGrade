"""An exam's window must sit inside the assessment period the school is running.

The calendar (`assessment_periods`, migration 050) names the date range a period
runs; `exams.start_at`/`end_at` say when a paper may be sat. Nothing joined them,
so a teacher could file a Mid Semester paper whose window lands in July — outside
the period the school opened — and every page would still call it "Mid Semester",
because nothing ever compared the two.

The rule enforced here, and the one thing it deliberately does *not* do
---------------------------------------------------------------------
The window's first day must be on or after the running period's start and its last
day on or before the period's end. Comparison is by the **school's civil date**,
not the raw UTC instant: the form converts local time to UTC, so an 02:00 Jakarta
start is the previous UTC day, and comparing UTC dates would refuse a window that
is plainly inside.

Two cases stay unchanged on purpose:

* **No period is running** — a school that does not use the calendar keeps the
  behaviour it had. "Refuse an exam outside the running period" has nothing to
  enforce against when there is no running period.
* **A window with no dates** — the form's own rule is that a paper published
  without dates may be sat, so it cannot "fall outside" anything.

The refusal is the teacher-form shape its neighbour already uses: a flash with the
period named, and a redirect back to the builder.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"


# ── a fake Supabase that serves the running period, and nothing else ─────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.mode, self.payload = [], "select", None

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for k, v in self.filters)

    def execute(self):
        self.store.calls.append((self.table, self.mode))
        rows = self.store.rows.get(self.table, [])
        if self.mode == "insert":
            self.store.inserted.append((self.table, self.payload))
            row = dict(self.payload)
            row.setdefault("id", "new-1")
            return _Resp([row])
        return _Resp([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.inserted = []
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _period(**over):
    row = {"id": "p1", "school_id": "s1", "kind": "mid_semester",
           "name": "UTS Ganjil", "start_date": "2026-09-01",
           "end_date": "2026-09-10", "is_active": True}
    row.update(over)
    return row


def _sb(active=True, **over):
    return FakeSupabase(rows={"assessment_periods": [_period(**over)] if active else []})


# ── the decision itself ──────────────────────────────────────────────────────

class TestTheWindowAgainstTheRunningPeriod:
    def test_a_window_inside_the_period_is_allowed(self):
        from app.services import assessment_periods as ap

        period, outside = ap.window_outside_running_period(
            _sb(), "s1", "2026-09-03T02:00:00", "2026-09-05T02:00:00")
        assert period and period["kind"] == "mid_semester"
        assert outside is False

    def test_a_window_starting_before_the_period_is_refused(self):
        from app.services import assessment_periods as ap

        _period_row, outside = ap.window_outside_running_period(
            _sb(), "s1", "2026-08-20T02:00:00", "2026-08-21T02:00:00")
        assert outside is True

    def test_a_window_ending_after_the_period_is_refused(self):
        from app.services import assessment_periods as ap

        _period_row, outside = ap.window_outside_running_period(
            _sb(), "s1", "2026-09-09T02:00:00", "2026-09-20T02:00:00")
        assert outside is True

    def test_the_period_boundaries_are_inclusive(self):
        """A window that exactly touches both ends is inside, not outside."""
        from app.services import assessment_periods as ap

        _period_row, outside = ap.window_outside_running_period(
            _sb(), "s1", "2026-09-01T09:00:00", "2026-09-10T09:00:00")
        assert outside is False

    def test_no_running_period_changes_nothing(self):
        from app.services import assessment_periods as ap

        period, outside = ap.window_outside_running_period(
            _sb(active=False), "s1", "2026-08-01T02:00:00", "2026-08-02T02:00:00")
        assert period is None and outside is False, (
            "a school with no running period must keep the behaviour it had")

    def test_a_window_without_dates_cannot_escape(self):
        from app.services import assessment_periods as ap

        for start, end in ((None, None), ("", ""), ("2026-09-03T02:00:00", None)):
            _period_row, outside = ap.window_outside_running_period(
                _sb(), "s1", start, end)
            assert outside is False, (start, end)

    def test_the_date_is_the_schools_local_day_not_utc(self):
        """02:00 Jakarta on the 1st is 19:00 UTC on the 31st — still the 1st here.

        Comparing UTC dates would put this window a day before the period and
        refuse a paper that is plainly inside it.
        """
        from app.services import assessment_periods as ap

        _period_row, outside = ap.window_outside_running_period(
            _sb(), "s1", "2026-08-31T19:00:00", "2026-09-01T19:00:00",
            tz_offset_hours=7)
        assert outside is False, "the window was read on the UTC day, not the school's"

    def test_a_failed_period_read_refuses_nothing(self):
        """`active_period` answers None on a hiccup, which must not block a paper."""
        from app.services import assessment_periods as ap

        class Boom(FakeSupabase):
            def table(self, name):
                raise RuntimeError("down")

        period, outside = ap.window_outside_running_period(
            Boom(), "s1", "2026-08-01T02:00:00", "2026-08-02T02:00:00")
        assert period is None and outside is False


# ── the sentence the teacher reads ───────────────────────────────────────────

class TestTheRefusalNamesThePeriod:
    def test_the_message_carries_the_period_name_and_dates(self):
        from app.routes import teacher

        message = teacher._off_calendar_message(
            {"name": "UTS Ganjil", "start_date": "2026-09-01", "end_date": "2026-09-10"})
        assert "UTS Ganjil" in message
        assert "2026-09-01" in message and "2026-09-10" in message

    def test_the_message_survives_a_missing_period(self):
        from app.routes import teacher

        assert teacher._off_calendar_message(None).strip()


# ── both write doors ask ─────────────────────────────────────────────────────

def _call(src: str, name: str) -> str:
    return src.split(f"def {name}(")[1].split("\ndef ")[0]


class TestBothDoorsEnforceTheCalendar:
    def test_the_new_exam_write_asks_the_calendar(self):
        call = _call(TEACHER.read_text(encoding="utf-8"), "exam_form")
        assert "window_outside_running_period(" in call, (
            "POST /teacher/exams/new accepts a window outside the running period")
        assert "assessment_periods" in call, "the calendar module was never reached"

    def test_the_edit_write_asks_the_calendar(self):
        call = _call(TEACHER.read_text(encoding="utf-8"), "exam_detail")
        assert "window_outside_running_period(" in call, (
            "POST /teacher/exams/<id> accepts a window outside the running period")

    def test_the_refusal_is_a_flash_then_a_redirect(self):
        """The shape its neighbour (the end<=start check) already uses.

        Asked in two parts, because both doors now hand their refusals to one
        helper: the helper answers a browser post with the flash-and-redirect it
        always gave, and JSON to the builder's background save, which must never
        navigate. The door has to reach for it, and the helper has to keep the
        browser answer — otherwise a refusal would stop telling the teacher why.
        """
        src = TEACHER.read_text(encoding="utf-8")
        for name in ("exam_form", "exam_detail"):
            call = _call(src, name)
            guard = call.split("window_outside_running_period(")[1][:400]
            assert "_exam_save_refused(" in guard or "flash(" in guard, (
                f"{name} refuses without saying why")
        helper = _call(src, "_exam_save_refused")
        assert "flash(" in helper, "a refused browser post is told nothing"
        assert "redirect(" in helper, "a refused browser post is sent nowhere"
        assert "jsonify(" in helper, "the background save is left to navigate on a 400"


# ── driving the create door: a refusal touches no exam ───────────────────────

def _raw(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


class TestTheCreateDoorRefusesAnOutsideWindow:
    def _post(self, app, monkeypatch, start_at, end_at, *, school="s1"):
        from flask import g
        from app.routes import teacher
        from app.services import assessment_periods as ap

        sb = _sb()
        monkeypatch.setattr(teacher, "get_supabase", lambda: sb)
        with app.test_request_context(
                "/teacher/exams/new", method="POST",
                data={"title": "UTS", "subject": "MTK", "total_questions": "5",
                      "start_at": start_at, "end_at": end_at, "action": "save_draft"}):
            g.user_id = "t-1"
            g.user_role = "admin_sekolah"
            g.user_school_id = school
            g.tz_offset = 7
            g.show = {}
            response = _raw(teacher.exam_form)()
        return sb, response

    def test_an_outside_window_is_refused_and_no_exam_is_written(self, app, monkeypatch):
        sb, response = self._post(app, monkeypatch,
                                  "2026-08-20T09:00", "2026-08-21T09:00")
        assert response.status_code in (301, 302, 303), (
            "the paper was accepted despite its window escaping the period")
        assert not [c for c in sb.inserted if c[0] == "exams"], (
            "a refused paper still reached the exams table")

    def test_an_inside_window_is_not_blocked_by_the_calendar(self, app, monkeypatch):
        """The refusal must be the calendar's, not a blanket stop on every write.

        The insert may fail later for reasons this test does not stage; what it
        pins is that the *calendar* did not send the teacher back.
        """
        from app.routes import teacher
        from app.services import assessment_periods as ap

        seen = {}
        monkeypatch.setattr(
            ap, "window_outside_running_period",
            lambda *a, **k: (seen.setdefault("called", {"id": "p1", "name": "UTS"}),
                             False))
        monkeypatch.setattr(teacher, "assessment_periods", ap)
        sb, response = self._post(app, monkeypatch,
                                  "2026-09-03T09:00", "2026-09-04T09:00")
        assert seen.get("called"), "the calendar was never asked"
        # It got past the calendar: the exams table was reached (and the fake
        # answered the insert), so the redirect is not the calendar refusal.
        assert [c for c in sb.inserted if c[0] == "exams"], (
            "the calendar blocked an inside window instead of letting it through")
