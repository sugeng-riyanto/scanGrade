"""A paper with anti-cheat switched off must record nothing at all.

`exams.anti_cheat_enabled` is the school's own switch (migration 011): a paper that
sets it false is one the school asked *not* to monitor. Two halves of that promise
were already kept — `calculate_graduated_penalty` charges no points for it, and
`resume_code` will not lock the sitting for it — but the record itself was not.
`/api/violation/log` wrote the row into `violation_logs` **before** it had read the
exam at all, so a paper with the switch off still accrued a violation:

* the teacher's report for that paper listed a pupil as having switched tabs or
  left fullscreen, on a paper the school had switched off;
* the count advanced, so the ladder's own state moved even though no penalty was
  charged;
* and the submission was updated with `violations: <count>` — the number the
  results screen reads back.

The fix is ordering plus a refusal: the exam row is read **first**, and when it says
anti-cheat is off, nothing is written. The server is the authority here, not the
page, because the page cannot be trusted with a promise the school made — a
hand-crafted POST must be refused exactly as the page's own is. The page is also
made inert (no banner, no ladder, no auto-submit) so the two halves agree, and the
tests at the end pin that neither half can be dropped alone.

The flag is read as `is False`, exactly as `calculate_graduated_penalty` reads it:
an exam whose row could not be read (or an older row with the column unset) is not
a school asking for silence, and treating it as one would silently disable
anti-cheat for every paper whose row went missing.
"""
from __future__ import annotations

import pathlib
import time

TEMPLATE = (pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "student" / "take_exam.html")

DISABLED = {"id": "exam-1", "anti_cheat_enabled": False,
            "penalty_per_violation": 5, "max_violations": 5,
            "auto_submit_on_max": True}
ENABLED = dict(DISABLED, anti_cheat_enabled=True)
UNSET = {k: v for k, v in DISABLED.items() if k != "anti_cheat_enabled"}


class _Res:
    """What `.execute()` hands back: rows, and the `count` an exact count reads."""

    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else len(data)


class _Q:
    """A fake PostgREST that really filters, and remembers every statement."""

    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.op, self.payload, self._single = [], "select", None, None

    # -- building -----------------------------------------------------------
    def select(self, *cols, **kw):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", dict(payload)
        return self

    def update(self, payload):
        self.op, self.payload = "update", dict(payload)
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def in_(self, column, values):
        self.filters.append((column, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def single(self):
        self._single = True
        return self

    # -- running ------------------------------------------------------------
    def _match(self, row) -> bool:
        for column, want in self.filters:
            got = row.get(column)
            if isinstance(want, set):
                if str(got) not in want:
                    return False
            elif str(got) != want:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        self.db.statements.append((self.op, self.table))
        if self.op == "select":
            hits = [dict(r) for r in rows if self._match(r)]
            # `maybe_single().execute()` answers ``None`` when nothing matched —
            # which `row_or_none` exists to turn into ``None`` rather than a crash.
            if self._single:
                # postgrest answers ``None`` — not a response with `data=None` —
                # when `maybe_single()` matches nothing.
                return _Res(hits[0]) if hits else None
            return _Res(hits, count=len(hits))
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"{self.table}-{len(rows) + 1}")
            rows.append(row)
            self.db.writes.append((self.op, self.table, dict(row)))
            return _Res([dict(row)])
        hits = [r for r in rows if self._match(r)]
        for r in hits:
            r.update(self.payload)
        self.db.writes.append((self.op, self.table, dict(self.payload)))
        return _Res([dict(r) for r in hits])


class _Db:
    def __init__(self, exam):
        self.tables = {
            "exams": [exam] if exam is not None else [],
            "violation_logs": [],
            "submissions": [{"id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
                             "status": "in_progress", "started_at": None,
                             "resume_limit": 3, "resume_count_used": 0,
                             "created_at": "2026-10-06T08:00:00+00:00"}],
        }
        self.statements: list[tuple] = []
        self.writes: list[tuple] = []

    def table(self, name):
        return _Q(self, name)


def _post(app, monkeypatch, db, *, vtype="fullscreen_exit", uid="stu-1"):
    """Drive the real route body with the exam row this fake holds."""
    from app.routes import api as apimod
    monkeypatch.setattr(apimod, "get_supabase", lambda: db)
    monkeypatch.setitem(app.extensions, "supabase", db)   # validate_violation_log
    payload = {"logs": [{"exam_id": "exam-1", "violation_type": vtype,
                         "timestamp": time.time(), "metadata": {}}]}
    with app.test_request_context("/api/violation/log", method="POST", json=payload):
        from flask import g
        g.user_id, g.user_role = uid, "murid"
        return apimod.log_violation.__wrapped__().get_json()


def _inserts(db, table="violation_logs") -> list:
    return [w for w in db.writes if w[0] == "insert" and w[1] == table]


# ── the refusal ──────────────────────────────────────────────────────────────

class TestADisabledPaperRecordsNothing:
    def test_a_disabled_paper_records_no_violation(self, app, monkeypatch):
        """The defect: the row was written before the exam was even read."""
        db = _Db(DISABLED)
        body = _post(app, monkeypatch, db)
        assert db.tables["violation_logs"] == [], (
            "a paper with anti-cheat off still recorded a violation")
        assert body["violations"] == [{"logged": False, "reason": "anti_cheat_disabled"}]

    def test_it_is_refused_for_every_kind_the_page_sends(self, app, monkeypatch):
        """Fullscreen is the one that was measured; the rule is the paper, not the act."""
        for kind in ("fullscreen_exit", "tab_switch", "focus_lost", "orientation_shift"):
            db = _Db(DISABLED)
            _post(app, monkeypatch, db, vtype=kind)
            assert db.tables["violation_logs"] == [], f"{kind} was recorded anyway"

    def test_a_hand_crafted_post_is_refused_too(self, app, monkeypatch):
        """The server is the authority: the page cannot be trusted with the promise."""
        db = _Db(DISABLED)
        _post(app, monkeypatch, db, vtype="tab_switch", uid="stu-9")
        assert db.tables["violation_logs"] == []

    def test_no_penalty_is_synced_onto_the_submission(self, app, monkeypatch):
        """`violations` on the submission is read back by the results screen."""
        db = _Db(DISABLED)
        _post(app, monkeypatch, db)
        assert [w for w in db.writes if w[1] == "submissions"] == [], (
            "a disabled paper still moved the submission's violation count")

    def test_the_exam_row_is_read_before_anything_is_written(self, app, monkeypatch):
        """The ordering *is* the fix — a write first is the bug this pins."""
        db = _Db(DISABLED)
        _post(app, monkeypatch, db)
        assert db.writes == [], "nothing may be written for a disabled paper"
        first_exam = next(i for i, s in enumerate(db.statements)
                          if s == ("select", "exams"))
        assert first_exam == 0, (
            "the exam row must be the first thing read; anything before it is a "
            "decision taken without knowing whether the school asked for silence")


# ── the feature still works ──────────────────────────────────────────────────

class TestTheLadderStillWorks:
    def test_an_enabled_paper_still_records_the_violation(self, app, monkeypatch):
        db = _Db(ENABLED)
        body = _post(app, monkeypatch, db)
        rows = db.tables["violation_logs"]
        assert len(rows) == 1, "the refusal must not have disabled anti-cheat entirely"
        assert rows[0]["violation_type"] == "fullscreen_exit"
        assert rows[0]["user_id"] == "stu-1"
        assert body["violations"][0]["logged"] is True

    def test_an_exam_row_without_the_flag_is_not_read_as_a_refusal(self, app, monkeypatch):
        """A row that could not be read is not a school asking for silence."""
        db = _Db(UNSET)
        body = _post(app, monkeypatch, db)
        assert len(db.tables["violation_logs"]) == 1
        assert body["violations"][0]["logged"] is True


# ── the page is inert too ────────────────────────────────────────────────────

def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _method(name: str) -> str:
    src = _src()
    marker = f"        {name}("
    assert marker in src, f"the page has no {name} method"
    return src.split(marker, 1)[1].split("\n        },", 1)[0]


class TestThePageDoesNotChargeWhenThePaperIsOff:
    def test_the_charge_refuses_before_climbing_the_ladder(self):
        """No banner, no local count, no request: the ladder is not armed."""
        body = _method("handleViolation")
        assert "if (!this.antiCheat.enabled) return;" in body, (
            "a disabled paper still raised a violation banner and posted an event")
        # ...and the guard has to come before the count, or the ladder moved anyway.
        assert body.index("if (!this.antiCheat.enabled) return;") \
            < body.index("this.violationCount++")

    def test_the_charge_still_carries_its_post(self):
        """The guard must not have replaced the reporting the feature needs."""
        body = _method("handleViolation")
        assert "fetch('/api/violation/log'" in body
        assert "violation_type: vtype," in body

    def test_auto_submit_cannot_fire_for_a_disabled_paper(self):
        """The last rung hands the paper in; a paper with the switch off has no rungs."""
        body = _method("_maybeAutoSubmit")
        assert "if (!this.antiCheat.enabled) return;" in body, (
            "a seeded or stale count could still end the paper for a disabled exam")
