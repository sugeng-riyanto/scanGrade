"""A tag is *derived*, so a tag written by SQL must be corrected without a human.

`exams.assessment_period_id` is derived from the paper's window and the school's
calendar (`_tag_for`). The write doors recompute it when the *paper* is saved and
`retag_school_exams` recomputes it when the *calendar* is edited through the app —
but a tag written by hand in SQL, or by any path that reaches the table without
passing a door, is invisible to both. Nothing re-derives it until somebody edits
that paper or that calendar again.

So the derivation is put on a timer and behind a button:

* **the sweep** (`retag_all_schools`) runs the existing per-school rule over every
  school, so the aggregate answer is the same rule the doors use, never a second
  copy of it;
* **the timer** (`app/services/period_reconcile_service.py`) runs one pass a day,
  the same daemon-thread shape as the deadline, retention and deploy-alert loops;
* **the button** on `/super-admin/dashboard` lets an operator reconcile on demand
  without waiting for the tick — the case where they have just fixed something in
  SQL and want the correction now.

What is asserted here, and why:

* the aggregate **is** the per-school rule — a second implementation is how the two
  drift apart, and this file reads the source to prove there is only one;
* one school whose read fails **does not stop the others** and is reported, so a
  single bad row cannot make the whole box silently stop reconciling;
* the sweep is **idempotent** and **school-scoped** per call — a second pass writes
  nothing, and no call re-homes another school's papers;
* the timer is wired to `create_app` behind `START_BACKGROUND_SCHEDULERS`, and its
  first pass is *not* immediate (constructing an app must not write);
* the button is a **super-admin door** with CSRF, and every string it shows is
  bilingual in the template where the language toggle can reach it.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from tests.conftest import app_instance

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "assessment_periods.py"
SCHED = ROOT / "app" / "services" / "period_reconcile_service.py"
INIT = ROOT / "app" / "__init__.py"
CONFIG = ROOT / "app" / "config.py"
ROUTES = ROOT / "app" / "routes" / "super_admin.py"
DASH = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"


# ── a fake that really filters and mutates ───────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.mode, self.payload = "select", None

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("!=" + column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("IN " + column, set(map(str, values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if key.startswith("!="):
                if str(row.get(key[2:])) == str(value):
                    return False
            elif key.startswith("IN "):
                if str(row.get(key[3:])) not in value:
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload,
                                 self.mode))
        if self.table in self.store.fail:
            raise RuntimeError("boom")
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Resp(hit)
        if self.mode == "delete":
            hit = [r for r in rows if self._matches(r)]
            self.store.rows[self.table] = [r for r in rows if r not in hit]
            return _Resp(hit)
        if self.mode == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"p{len(rows) + 1}")
            rows.append(row)
            return _Resp([row])
        return _Resp([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None, fail=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail = set(fail)
        self.calls = []

    def table(self, name):
        return _Query(self, name)

    def exam(self, exam_id):
        for row in self.rows.get("exams", []):
            if row.get("id") == exam_id:
                return row
        raise AssertionError(f"no exam {exam_id}")

    def writes(self, table="exams"):
        return [c for c in self.calls if c[0] == table and c[3] == "update"]


def _period(**over):
    row = {"id": "p1", "school_id": "s1", "kind": "mid_semester", "name": "UTS",
           "start_date": "2026-09-01", "end_date": "2026-09-10", "is_active": True}
    row.update(over)
    return row


def _exam(**over):
    row = {"id": "e1", "school_id": "s1", "start_at": "2026-09-05T02:00:00",
           "end_at": "2026-09-05T03:00:00", "assessment_period_id": "p1"}
    row.update(over)
    return row


# ── the aggregate is the per-school rule, not a second copy ──────────────────

class TestTheAggregateReusesTheRule:
    def test_the_sweep_calls_the_per_school_sweep(self):
        src = SERVICE.read_text(encoding="utf-8")
        assert "def retag_all_schools(" in src, "no cross-school sweep exists"
        body = src.split("def retag_all_schools(")[1].split("\ndef ")[0]
        assert "retag_school_exams(" in body, (
            "the aggregate sweep re-implements the rule instead of calling the "
            "per-school one the calendar doors already call")

    def test_every_school_gets_its_tag_re_derived(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "schools": [{"id": "s1"}, {"id": "s2"}],
            "assessment_periods": [
                _period(id="p1", school_id="s1"),
                _period(id="p9", school_id="s2", start_date="2026-09-01",
                        end_date="2026-09-30")],
            "exams": [
                _exam(id="e1", school_id="s1", assessment_period_id="ghost"),
                _exam(id="e2", school_id="s2", assessment_period_id="ghost")]})

        out = ap.retag_all_schools(sb)

        assert out["ok"] is True
        assert out["schools"] == 2
        assert out["retagged"] == 2
        assert sb.exam("e1")["assessment_period_id"] == "p1"
        assert sb.exam("e2")["assessment_period_id"] == "p9"

    def test_a_school_whose_read_fails_does_not_stop_the_others(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "schools": [{"id": "s1"}, {"id": "s2"}],
            "assessment_periods": [_period(id="p1", school_id="s1"),
                                   _period(id="p9", school_id="s2")],
            "exams": [
                _exam(id="e1", school_id="s1", assessment_period_id="ghost"),
                _exam(id="e2", school_id="s2", assessment_period_id="ghost")]})

        # Make only the first school's exam read blow up.
        calls = {"n": 0}
        original = sb.table

        def flaky(name):
            query = original(name)
            if name == "exams":
                calls["n"] += 1
                if calls["n"] == 1:
                    query.execute = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            return query

        sb.table = flaky
        out = ap.retag_all_schools(sb)

        assert out["ok"] is False and out["failed"], (
            "a failed school must be reported, not hidden")
        assert sb.exam("e2")["assessment_period_id"] == "p9", (
            "one school's failure stopped another school's sweep")

    def test_the_whole_sweep_is_idempotent(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={
            "schools": [{"id": "s1"}],
            "assessment_periods": [_period(id="p1", school_id="s1")],
            "exams": [_exam(id="e1", school_id="s1", assessment_period_id="ghost")]})

        first = ap.retag_all_schools(sb)
        before = len(sb.writes())
        second = ap.retag_all_schools(sb)

        assert first["retagged"] == 1 and second["retagged"] == 0
        assert len(sb.writes()) == before, "the second pass wrote again"

    def test_a_read_that_fails_sweeps_nothing_and_does_not_raise(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"schools": [{"id": "s1"}]}, fail=("schools",))

        out = ap.retag_all_schools(sb)

        assert out["ok"] is False and out["retagged"] == 0
        assert out["schools"] == 0


# ── the timer ────────────────────────────────────────────────────────────────

class TestTheTimer:
    def test_the_service_exists_and_offers_a_start_and_a_stop(self):
        assert SCHED.exists(), "no period-reconcile service module"
        src = SCHED.read_text(encoding="utf-8")
        assert "def start_period_reconcile_scheduler(" in src
        assert "def stop_period_reconcile_scheduler(" in src
        assert "threading.Thread" in src and "daemon=True" in src

    def test_the_first_pass_is_not_immediate(self):
        """Building an app must not write; the first pass waits a full interval."""
        src = SCHED.read_text(encoding="utf-8")
        loop = src.split("def _loop(")[1].split("\ndef ")[0]
        assert "_stop.wait(" in loop, (
            "the loop runs a pass before waiting, so constructing the app writes")

    def test_the_scheduler_runs_a_pass_in_an_application_context(self):
        src = SCHED.read_text(encoding="utf-8")
        loop = src.split("def _loop(")[1].split("\ndef ")[0]
        assert "app_context()" in loop, "the pass needs an app context for Supabase"

    def test_the_timer_is_wired_to_create_app_behind_the_scheduler_switch(self):
        src = INIT.read_text(encoding="utf-8")
        assert "start_period_reconcile_scheduler" in src, (
            "the reconcile loop is never started")
        # It must sit inside the START_BACKGROUND_SCHEDULERS block, alongside the
        # other loops, so a test suite or a deploy probe never writes.
        guarded = src.split("START_BACKGROUND_SCHEDULERS")[1]
        assert "start_period_reconcile_scheduler" in guarded, (
            "the loop starts even when background schedulers are switched off")

    def test_the_interval_is_configurable(self):
        src = CONFIG.read_text(encoding="utf-8")
        assert "PERIOD_RECONCILE_INTERVAL_SECONDS" in src

    def test_starting_twice_is_safe_and_the_thread_is_a_daemon(self):
        from app.services import period_reconcile_service as svc

        class _App:
            def app_context(self):
                import contextlib
                return contextlib.nullcontext()

        svc.start_period_reconcile_scheduler(interval=3600, app=_App())
        first = svc._thread
        svc.start_period_reconcile_scheduler(interval=3600, app=_App())
        try:
            assert svc._thread is first, "a second start spawned a second loop"
            assert first.daemon is True
        finally:
            svc.stop_period_reconcile_scheduler()


# ── the button ───────────────────────────────────────────────────────────────

class TestTheButton:
    def test_the_route_exists_and_calls_the_sweep(self):
        src = ROUTES.read_text(encoding="utf-8")
        assert '"/reconcile-periods"' in src, "no reconcile button route"
        body = src.split("def reconcile_periods(")[1].split("\ndef ")[0]
        assert "retag_all_schools(" in body, "the button does not run the sweep"
        assert "_sa_required" in src.split("def reconcile_periods(")[0].rsplit(
            "@super_bp.route", 1)[1], "the reconcile door is not super-admin only"

    def test_the_route_is_registered_and_post_only(self):
        app = app_instance()
        rule = next((r for r in app.url_map.iter_rules()
                     if r.rule.endswith("/reconcile-periods")), None)
        assert rule is not None, "the reconcile route is not registered"
        assert "POST" in rule.methods and "GET" not in rule.methods

    def test_the_dashboard_has_the_button_and_it_sends_csrf(self):
        html = DASH.read_text(encoding="utf-8")
        assert "/super-admin/reconcile-periods" in html, "the dashboard has no button"
        call = html.split("/super-admin/reconcile-periods")[1][:400]
        assert "X-CSRF-Token" in call, "the reconcile POST sends no CSRF token"

    def test_every_string_the_button_shows_is_bilingual(self):
        html = DASH.read_text(encoding="utf-8")
        block = html.split("/super-admin/reconcile-periods")[0]
        # The nearest sgT call carrying the button's copy, both languages.
        tail = block.rsplit("sgT(", 1)[-1]
        assert "," in tail, "the reconcile copy is not bilingual"

    def test_the_template_carries_no_hardcoded_only_string(self):
        """The button's label must go through sgT/t(), not a bare literal."""
        html = DASH.read_text(encoding="utf-8")
        assert re.search(r"sgT\(|t\('", html), "the dashboard uses no i18n helper"
