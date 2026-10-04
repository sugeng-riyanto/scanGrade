"""The door the cross-school sweep was missing: fix the row, not just find it.

`school_integrity.cross_school_findings` reports rows whose `school_id` disagrees
with the school of the people they point at. It hands an operator a table of ids and
says, in effect, "go and set that `school_id` by hand" — through SQL, from a page
that cannot do it. So the finding is found and then left, which is the same shape of
failure as a check nobody opens.

`app/services/cross_school_repair.py` is the door. Two rules make it safe:

* **The target comes from the row, never from the request.** The only thing the
  request carries is *which* row (its kind and id); the school it should hold is
  re-derived from that row's own `class_id`/`subject_id` on every apply. A forged
  form cannot re-home a row into a school of the attacker's choosing.
* **An ambiguous row is refused, not guessed.** A `class_subjects` or
  `teacher_assignments` row points at *two* things; if the class and the subject
  belong to different schools there is no single answer, and the repair says so
  (`two_schools`) instead of picking one and breaking the other.

The write is guarded on the value the plan was made against, so a repair racing a
concurrent fix reports `row_changed` rather than clobbering it, and every applied
repair writes an audit record.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "cross_school_repair.py"
SUPER_ROUTES = ROOT / "app" / "routes" / "super_admin.py"
DASHBOARD = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"


# ── a PostgREST stand-in that records reads and applies writes ──────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._select = "*"
        self._payload = None

    def select(self, columns="*", **kw):
        self._select = columns
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, ("__in__", list(values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, row) -> bool:
        for column, value in self._filters:
            if isinstance(value, tuple) and value and value[0] == "__in__":
                if str(row.get(column)) not in {str(v) for v in value[1]}:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        self.db.log.append((self._op, self.table, dict(self._payload or {}),
                            list(self._filters)))
        if self._op == "update":
            hits = [r for r in self.db.tables.get(self.table, []) if self._match(r)]
            for row in hits:
                row.update(self._payload)
            return _Res([dict(r) for r in hits])
        rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
        if self.db.break_table == self.table:
            raise RuntimeError("connection reset")
        return _Res(rows)


class _DB:
    def __init__(self, break_table=None, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.log = []
        self.break_table = break_table

    def table(self, name):
        return _Query(self, name)


SCH_A, SCH_B = "school-a", "school-b"


def _db(**over):
    tables = dict(
        classes=[{"id": "c1", "name": "7A", "school_id": SCH_A}],
        subjects=[{"id": "s1", "name": "Math", "school_id": SCH_A}],
        schools=[{"id": SCH_A, "name": "SMA A"}, {"id": SCH_B, "name": "SMA B"}],
        class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                         "school_id": SCH_B, "is_active": True}],
        teacher_assignments=[{"id": "a1", "class_id": "c1", "subject_id": "s1",
                              "teacher_id": "t1", "school_id": SCH_B,
                              "status": "active"}],
        profiles=[{"id": "u1", "class_id": "c1", "school_id": SCH_B}],
    )
    tables.update(over)
    return _DB(**tables)


def _quiet_audit(monkeypatch):
    """Record audit calls without an app context (see sitting_unlock's guards)."""
    from app.services import cross_school_repair as repair
    calls = []
    monkeypatch.setattr(repair, "log_activity",
                        lambda *a, **k: calls.append((a, k)), raising=False)
    return calls


def _writes(db, table=None):
    return [payload for op, name, payload, _f in db.log
            if op == "update" and (table is None or name == table)]


# ── the plan, a pure function of the row ────────────────────────────────────

class TestThePlan:
    def _classes(self):
        return {"c1": {"id": "c1", "school_id": SCH_A}}

    def _subjects(self):
        return {"s1": {"id": "s1", "school_id": SCH_A}}

    def test_a_pupil_is_repointed_to_their_class_school(self):
        from app.services import cross_school_repair as repair
        row = {"id": "u1", "class_id": "c1", "school_id": SCH_B}
        plan = repair.plan("class_pupil_mismatch", row, self._classes(), self._subjects())
        assert plan["ok"] is True
        assert plan["table"] == "profiles"
        assert plan["row_id"] == "u1"
        assert plan["from_school_id"] == SCH_B
        assert plan["to_school_id"] == SCH_A

    def test_a_pair_is_repointed_to_the_school_its_class_and_subject_share(self):
        from app.services import cross_school_repair as repair
        row = {"id": "p1", "class_id": "c1", "subject_id": "s1", "school_id": SCH_B}
        plan = repair.plan("pair_school_mismatch", row, self._classes(), self._subjects())
        assert plan["ok"] is True
        assert plan["table"] == "class_subjects"
        assert plan["to_school_id"] == SCH_A

    def test_a_pair_whose_owners_disagree_is_refused_not_guessed(self):
        """No single right answer: re-pointing would satisfy one and break the
        other, so the door must say so instead of picking."""
        from app.services import cross_school_repair as repair
        row = {"id": "p1", "class_id": "c1", "subject_id": "s1", "school_id": SCH_B}
        subjects = {"s1": {"id": "s1", "school_id": "school-c"}}
        plan = repair.plan("pair_school_mismatch", row, self._classes(), subjects)
        assert plan["ok"] is False
        assert plan["reason"] == repair.TWO_SCHOOLS

    def test_an_unreadable_pointer_is_refused(self):
        from app.services import cross_school_repair as repair
        row = {"id": "p1", "class_id": "gone", "subject_id": "s1", "school_id": SCH_B}
        plan = repair.plan("pair_school_mismatch", row, self._classes(), self._subjects())
        assert plan["ok"] is False
        assert plan["reason"] == repair.TARGET_UNKNOWN

    def test_an_already_consistent_row_is_not_a_repair(self):
        from app.services import cross_school_repair as repair
        row = {"id": "u1", "class_id": "c1", "school_id": SCH_A}
        plan = repair.plan("class_pupil_mismatch", row, self._classes(), self._subjects())
        assert plan["ok"] is False
        assert plan["reason"] == repair.ALREADY_CONSISTENT

    def test_a_missing_row_is_refused(self):
        from app.services import cross_school_repair as repair
        plan = repair.plan("class_pupil_mismatch", None, self._classes(), self._subjects())
        assert plan["ok"] is False
        assert plan["reason"] == repair.ROW_NOT_FOUND

    def test_an_unknown_kind_is_refused(self):
        from app.services import cross_school_repair as repair
        plan = repair.plan("something_else", {"id": "x"}, {}, {})
        assert plan["ok"] is False
        assert plan["reason"] == repair.UNKNOWN_KIND


# ── the preview: read-only, and it can name both schools ────────────────────

def _finding(kind, **kw):
    base = {"kind": kind}
    base.update(kw)
    return base


class TestThePreview:
    def test_it_plans_a_repair_for_every_finding(self):
        from app.services import cross_school_repair as repair
        findings = [
            _finding("pair_school_mismatch", pair_id="p1", class_id="c1",
                     subject_id="s1", pair_school_id=SCH_B),
            _finding("class_pupil_mismatch", pupil_id="u1", class_id="c1",
                     class_school_id=SCH_A, pupil_school_id=SCH_B),
        ]
        plans = repair.preview(_db(), findings)
        assert [p["ok"] for p in plans] == [True, True]
        assert {p["row_id"] for p in plans} == {"p1", "u1"}

    def test_it_names_the_schools_so_the_operator_knows_where_it_goes(self):
        from app.services import cross_school_repair as repair
        findings = [_finding("class_pupil_mismatch", pupil_id="u1", class_id="c1",
                             class_school_id=SCH_A, pupil_school_id=SCH_B)]
        plan = repair.preview(_db(), findings)[0]
        assert plan["from_school_name"] == "SMA B"
        assert plan["to_school_name"] == "SMA A"

    def test_it_writes_nothing(self):
        from app.services import cross_school_repair as repair
        db = _db()
        repair.preview(db, [_finding("pair_school_mismatch", pair_id="p1",
                                     class_id="c1", subject_id="s1",
                                     pair_school_id=SCH_B)])
        assert _writes(db) == [], "a preview changed data"
        assert all(op == "select" for op, _t, _p, _f in db.log)


# ── the apply: re-derived, guarded, audited ─────────────────────────────────

class TestTheApply:
    def test_it_repoints_the_row_at_the_school_it_re_derives(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db()
        out = repair.apply(db, "class_pupil_mismatch", "u1", "sa-1")
        assert out["ok"] is True and out["action"] == "repointed"
        assert db.tables["profiles"][0]["school_id"] == SCH_A

    def test_the_target_is_never_taken_from_the_caller(self, monkeypatch):
        """The row is re-read; only its own pointers decide the school."""
        _quiet_audit(monkeypatch)
        from app.services import cross_school_repair as repair
        db = _db()
        repair.apply(db, "pair_school_mismatch", "p1", "sa-1")
        assert db.tables["class_subjects"][0]["school_id"] == SCH_A
        # and the update was aimed at the one row, with the planned value
        payload = _writes(db, "class_subjects")[0]
        assert payload == {"school_id": SCH_A}

    def test_it_refuses_an_ambiguous_row_without_writing(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": "school-c"}])
        out = repair.apply(db, "pair_school_mismatch", "p1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.TWO_SCHOOLS
        assert db.tables["class_subjects"][0]["school_id"] == SCH_B

    def test_it_refuses_a_row_that_is_no_longer_there(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db()
        db.tables["profiles"] = []
        out = repair.apply(db, "class_pupil_mismatch", "u1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.ROW_NOT_FOUND
        assert _writes(db) == []

    def test_a_racing_fix_is_reported_rather_than_clobbered(self, monkeypatch):
        """The update is guarded on the school the plan was made against, so a row
        somebody already fixed reports `row_changed` instead of being overwritten."""
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db()
        # Plan is made, then the row is fixed by somebody else before the write.
        original_update = _Query.update

        def update_then_stale(self, payload):
            db.tables["profiles"][0]["school_id"] = SCH_A   # the racing fix lands
            return original_update(self, payload)

        monkeypatch.setattr(_Query, "update", update_then_stale)
        out = repair.apply(db, "class_pupil_mismatch", "u1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.ROW_CHANGED

    def test_every_applied_repair_is_audited(self, monkeypatch):
        from app.services import cross_school_repair as repair
        calls = _quiet_audit(monkeypatch)
        repair.apply(_db(), "class_pupil_mismatch", "u1", "sa-1")
        assert len(calls) == 1, "a repair that re-homes a row left no audit record"
        args, kwargs = calls[0]
        assert args[0] == "update" and "profiles" in args
        assert kwargs["user_id"] == "sa-1"

    def test_an_unknown_kind_never_touches_a_table(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db()
        out = repair.apply(db, "teacher_assignments", "a1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.UNKNOWN_KIND
        assert _writes(db) == []

    def test_it_invalidates_the_reads_it_changed(self, monkeypatch):
        """A cached class-offering would keep serving the old school for its TTL."""
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        from app.utils import req_cache
        seen = []
        monkeypatch.setattr(req_cache, "invalidate_class_subjects",
                            lambda cid: seen.append(cid), raising=False)
        repair.apply(_db(), "pair_school_mismatch", "p1", "sa-1")
        assert "c1" in seen, "the class's subject cache was left stale"

    def test_a_failed_write_is_reported_not_raised(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet_audit(monkeypatch)
        db = _db()
        monkeypatch.setattr(_Query, "update",
                            lambda self, payload: (_ for _ in ()).throw(
                                RuntimeError("deadlock")))
        out = repair.apply(db, "class_pupil_mismatch", "u1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.WRITE_FAILED


# ── the operator's door ─────────────────────────────────────────────────────

class TestTheOperatorRepairsARow:
    def _route(self) -> str:
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        at = src.index('@super_bp.route("/integrity/repair"')
        nxt = src.find("bp.route(", at + 10)
        end = src.rfind("\n@", at, nxt) if nxt != -1 else len(src)
        return src[at:end if end != -1 else len(src)]

    def test_the_route_exists_and_is_super_admin_only(self):
        block = self._route()
        assert "methods=[\"POST\"]" in block
        assert "_sa_required" in block, "the repair door is not guarded"

    def test_it_calls_the_one_service(self):
        assert "cross_school_repair" in self._route(), (
            "the route re-implements the rule instead of calling the service")

    def test_the_school_is_never_read_from_the_form(self):
        block = self._route()
        for stolen in ('request.form.get("school_id")', 'request.args.get("school_id")',
                       'request.form.get("to_school_id")'):
            assert stolen not in block, (
                "the repair takes its target school from the request")

    def test_it_records_the_batch(self):
        assert "log_activity" in self._route(), (
            "a batch of repairs left no summary in the audit trail")

    def test_the_dashboard_previews_each_finding(self):
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        body = src.split("def dashboard(", 1)[1].split("\ndef ", 1)[0]
        assert "repair_plans" in body, (
            "the page lists findings with no preview of where a repair would move them")

    def test_the_template_draws_the_repair_door(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "/integrity/repair" in html, "the finding has no action on the page"
        assert "repair_plans" in html, "the card does not show the proposed school"
        assert re.search(r"t\('", html.split("integrity", 1)[1][:6000]), (
            "the repair UI carries no i18n helper")
