"""A quarantined row must be able to come back, or the second door is a one-way exit.

The quarantine door (migration-free, `cross_school_repair.quarantine`) detaches a row
that can never be re-pointed — it closes the offering, retires the assignment, or takes
the pupil out of the wrong class — and records *why* in the audit trail. What it did not
have is a way back: once a row was detached, turning the link on again meant SQL, and
the reason the operator typed was written down and never read again.

This is that way back. It is *not* a second repair: the school is still never guessed,
and the door cannot turn on a link that was never proper — it only undoes a specific
quarantine, from its own record:

* **The value restored comes from the audit record, never from the request.** The form
  names only *which* row; the column and the value are the service's, read back from
  what the quarantine itself stored. A pupil goes back into the class the quarantine
  took them out of, not a class the caller named.
* **A row that is not quarantined is refused, not written.** The update is guarded on
  the state the quarantine produced (`is_active=false`, `status='inactive'`,
  `class_id IS NULL`), so a row somebody already restored reports `already_restored`
  instead of being clobbered.
* **A quarantine that changed nothing is not restored.** If the prior value was already
  the detached one, the quarantine was a no-op and there is nothing to undo.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "cross_school_repair.py"
SUPER_ROUTES = ROOT / "app" / "routes" / "super_admin.py"
DASHBOARD = ROOT / "app" / "templates" / "super_admin" / "dashboard.html"

PAIR = "pair_school_mismatch"
ASSIGNMENT = "assignment_school_mismatch"
CLASS_PUPIL = "class_pupil_mismatch"

SCH_A, SCH_B = "school-a", "school-b"


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
        self._is = []
        self._op = "select"
        self._payload = None

    def select(self, columns="*", **kw):
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

    def is_(self, column, value):
        self._is.append((column, value))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, row) -> bool:
        for column, value in self._is:
            if value == "null" and row.get(column) is not None:
                return False
            if value != "null" and str(row.get(column)) != str(value):
                return False
        for column, value in self._filters:
            if isinstance(value, tuple) and value and value[0] == "__in__":
                if str(row.get(column)) not in {str(v) for v in value[1]}:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        self.db.log.append((self._op, self.table, dict(self._payload or {}),
                            list(self._filters), list(self._is)))
        if self.db.break_table == self.table:
            raise RuntimeError("connection reset")
        if self._op == "update":
            hits = [r for r in self.db.tables.get(self.table, []) if self._match(r)]
            for row in hits:
                row.update(self._payload)
            return _Res([dict(r) for r in hits])
        return _Res([dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)])


class _DB:
    def __init__(self, break_table=None, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.log = []
        self.break_table = break_table

    def table(self, name):
        return _Query(self, name)


def _audit(kind, table, row_id, *, prior_col=None, prior=None, reason="wrong school",
           class_id="c1", teacher_id="t1", school_id=SCH_B):
    old = {"school_id": school_id}
    if prior_col:
        old[prior_col] = prior
    new = {"quarantine": "cross_school", "kind": kind, "reason": reason,
           "class_id": class_id, "teacher_id": teacher_id}
    return {"entity_type": table, "entity_id": row_id, "old_data": old,
            "new_data": new, "user_id": "sa-1", "created_at": "2026-10-04T10:00:00Z"}


def _db(**over):
    tables = dict(
        classes=[{"id": "c1", "name": "7A", "school_id": SCH_A}],
        subjects=[{"id": "s1", "name": "Math", "school_id": SCH_A}],
        schools=[{"id": SCH_A, "name": "SMA A"}, {"id": SCH_B, "name": "SMA B"}],
        class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                         "school_id": SCH_B, "is_active": False}],
        teacher_assignments=[{"id": "a1", "class_id": "c1", "subject_id": "s1",
                              "teacher_id": "t1", "school_id": SCH_B,
                              "status": "inactive"}],
        profiles=[{"id": "u1", "class_id": None, "school_id": SCH_B}],
        audit_logs=[_audit(PAIR, "class_subjects", "p1", prior_col="is_active", prior=True)],
    )
    tables.update(over)
    return _DB(**tables)


def _quiet(monkeypatch):
    from app.services import cross_school_repair as repair
    calls = []
    monkeypatch.setattr(repair, "log_activity",
                        lambda *a, **k: calls.append((a, k)), raising=False)
    return calls


def _writes(db, table=None):
    return [payload for op, name, payload, _f, _i in db.log
            if op == "update" and (table is None or name == table)]


# ── reading the record back ─────────────────────────────────────────────────

class TestTheQuarantineRecordCanBeRead:
    def test_it_finds_a_cross_school_quarantine(self):
        from app.services import cross_school_repair as repair
        rows = [_audit(PAIR, "class_subjects", "p1", prior_col="is_active", prior=True)]
        recs = repair.quarantine_records(rows)
        assert len(recs) == 1
        assert recs[0]["kind"] == PAIR and recs[0]["row_id"] == "p1"
        assert recs[0]["table"] == "class_subjects"

    def test_it_ignores_a_repair_or_any_other_record(self):
        from app.services import cross_school_repair as repair
        rows = [
            {"entity_type": "class_subjects", "entity_id": "p1",
             "old_data": {"school_id": SCH_B}, "new_data": {"repair": "cross_school_repoint"}},
            {"entity_type": "school_integrity", "entity_id": "quarantine",
             "old_data": {}, "new_data": {"repaired": 1}},
        ]
        assert repair.quarantine_records(rows) == []

    def test_it_carries_the_reason_the_operator_typed(self):
        from app.services import cross_school_repair as repair
        rows = [_audit(PAIR, "class_subjects", "p1", reason="created under the wrong school")]
        assert repair.quarantine_records(rows)[0]["reason"] == "created under the wrong school"

    def test_it_carries_the_value_the_quarantine_replaced(self):
        """The whole key to restoring a pupil: the class the quarantine removed."""
        from app.services import cross_school_repair as repair
        rows = [_audit(CLASS_PUPIL, "profiles", "u1", prior_col="class_id", prior="c1")]
        rec = repair.quarantine_records(rows)[0]
        assert rec["column"] == "class_id" and rec["prior"] == "c1"


# ── the plan, a pure function of the record ─────────────────────────────────

class TestTheRestorePlan:
    def test_a_pair_turns_back_on(self):
        from app.services import cross_school_repair as repair
        rec = {"kind": PAIR, "row_id": "p1", "table": "class_subjects",
               "column": "is_active", "prior": True, "reason": "x"}
        plan = repair.restore_plan(rec)
        assert plan["ok"] is True and plan["column"] == "is_active"
        assert plan["value"] is True

    def test_an_assignment_is_re_armed(self):
        from app.services import cross_school_repair as repair
        rec = {"kind": ASSIGNMENT, "row_id": "a1", "table": "teacher_assignments",
               "column": "status", "prior": "active", "reason": "x"}
        plan = repair.restore_plan(rec)
        assert plan["ok"] is True and plan["value"] == "active"

    def test_a_pupil_goes_back_to_the_recorded_class(self):
        from app.services import cross_school_repair as repair
        rec = {"kind": CLASS_PUPIL, "row_id": "u1", "table": "profiles",
               "column": "class_id", "prior": "c1", "reason": "x"}
        plan = repair.restore_plan(rec)
        assert plan["ok"] is True and plan["column"] == "class_id"
        assert plan["value"] == "c1"

    def test_a_pupil_record_without_a_class_is_refused_not_guessed(self):
        """An older record, or one damaged, carries no prior — a class is not invented."""
        from app.services import cross_school_repair as repair
        rec = {"kind": CLASS_PUPIL, "row_id": "u1", "table": "profiles",
               "column": "class_id", "prior": None, "reason": "x"}
        plan = repair.restore_plan(rec)
        assert plan["ok"] is False and plan["reason"] == repair.NO_PRIOR_VALUE

    def test_a_quarantine_that_changed_nothing_is_not_restored(self):
        """If the link was already detached, there is nothing to undo."""
        from app.services import cross_school_repair as repair
        rec = {"kind": PAIR, "row_id": "p1", "table": "class_subjects",
               "column": "is_active", "prior": False, "reason": "x"}
        plan = repair.restore_plan(rec)
        assert plan["ok"] is False and plan["reason"] == repair.NOTHING_TO_RESTORE

    def test_an_unknown_kind_is_refused(self):
        from app.services import cross_school_repair as repair
        plan = repair.restore_plan({"kind": "something_else", "row_id": "x",
                                    "table": "profiles", "prior": "c1"})
        assert plan["ok"] is False and plan["reason"] == repair.UNKNOWN_KIND


# ── the door itself ─────────────────────────────────────────────────────────

class TestTheRestoreDoor:
    def test_it_restores_a_pair(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        out = repair.restore(db, PAIR, "p1", "sa-1")
        assert out["ok"] is True and out["action"] == repair.RESTORED
        assert db.tables["class_subjects"][0]["is_active"] is True
        assert _writes(db, "class_subjects")[0] == {"is_active": True}

    def test_it_restores_an_assignment(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(audit_logs=[_audit(ASSIGNMENT, "teacher_assignments", "a1",
                                    prior_col="status", prior="active")])
        out = repair.restore(db, ASSIGNMENT, "a1", "sa-1")
        assert out["ok"] is True
        assert db.tables["teacher_assignments"][0]["status"] == "active"

    def test_it_puts_a_pupil_back_in_their_class(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(audit_logs=[_audit(CLASS_PUPIL, "profiles", "u1",
                                    prior_col="class_id", prior="c1")])
        out = repair.restore(db, CLASS_PUPIL, "u1", "sa-1")
        assert out["ok"] is True
        assert db.tables["profiles"][0]["class_id"] == "c1"

    def test_the_restored_value_is_never_named_by_the_caller(self, monkeypatch):
        """The door takes only a row, and reads the value from the record."""
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(audit_logs=[_audit(CLASS_PUPIL, "profiles", "u1",
                                    prior_col="class_id", prior="c1")])
        repair.restore(db, CLASS_PUPIL, "u1", "sa-1")
        assert _writes(db, "profiles")[0] == {"class_id": "c1"}

    def test_a_row_with_no_quarantine_record_is_refused(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(audit_logs=[])
        out = repair.restore(db, PAIR, "p1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.NOTHING_TO_RESTORE
        assert _writes(db) == []

    def test_a_pupil_record_without_a_prior_is_refused(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(audit_logs=[_audit(CLASS_PUPIL, "profiles", "u1", prior_col="class_id", prior=None)])
        out = repair.restore(db, CLASS_PUPIL, "u1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.NO_PRIOR_VALUE
        assert _writes(db) == []

    def test_an_already_restored_row_is_refused_not_clobbered(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        db.tables["class_subjects"][0]["is_active"] = True  # someone turned it on already
        out = repair.restore(db, PAIR, "p1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.ALREADY_RESTORED

    def test_a_failed_audit_read_is_reported_not_raised(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _DB(break_table="audit_logs",
                 class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                                  "school_id": SCH_B, "is_active": False}])
        out = repair.restore(db, PAIR, "p1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.READ_FAILED
        assert _writes(db) == []

    def test_a_failed_write_is_reported_not_raised(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        monkeypatch.setattr(_Query, "update",
                            lambda self, payload: (_ for _ in ()).throw(RuntimeError("deadlock")))
        out = repair.restore(db, PAIR, "p1", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.WRITE_FAILED

    def test_an_unknown_kind_never_touches_a_table(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        out = repair.restore(db, "something_else", "x", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.UNKNOWN_KIND
        assert _writes(db) == []


# ── the record of the restore ───────────────────────────────────────────────

class TestTheRestoreRecord:
    def test_it_names_the_actor_the_row_and_the_act(self, monkeypatch):
        from app.services import cross_school_repair as repair
        calls = _quiet(monkeypatch)
        repair.restore(_db(), PAIR, "p1", "sa-1")
        assert len(calls) == 1, "a restored row left no record"
        args, kwargs = calls[0]
        assert args[0] == "update" and "class_subjects" in args
        assert kwargs["user_id"] == "sa-1"
        assert "cross_school" in str(kwargs.get("old_data")) + str(kwargs.get("new_data"))

    def test_it_keeps_the_original_reason_readable(self, monkeypatch):
        from app.services import cross_school_repair as repair
        calls = _quiet(monkeypatch)
        db = _db(audit_logs=[_audit(PAIR, "class_subjects", "p1",
                                    prior_col="is_active", prior=True,
                                    reason="created under the wrong school")])
        repair.restore(db, PAIR, "p1", "sa-1")
        assert "created under the wrong school" in str(calls[0][1])

    def test_it_invalidates_the_reads_it_changed(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        from app.utils import req_cache
        seen = []
        monkeypatch.setattr(req_cache, "invalidate_class_subjects",
                            lambda cid: seen.append(cid), raising=False)
        repair.restore(_db(), PAIR, "p1", "sa-1")
        assert "c1" in seen, "the class's subject cache was left stale"


# ── the list an operator reads ──────────────────────────────────────────────

class TestTheQuarantineList:
    def test_it_reads_recent_quarantines_with_their_reason(self, monkeypatch):
        from app.services import cross_school_repair as repair
        db = _db()
        listed = repair.recent_quarantines(db)
        assert len(listed) == 1
        assert listed[0]["row_id"] == "p1"
        assert listed[0]["reason"] == "wrong school"

    def test_it_never_lists_a_row_twice(self):
        from app.services import cross_school_repair as repair
        rows = [
            _audit(PAIR, "class_subjects", "p1", prior_col="is_active", prior=True),
            _audit(PAIR, "class_subjects", "p1", prior_col="is_active", prior=True),
        ]
        assert len(repair.quarantine_records(rows)) == 2
        db = _db(audit_logs=rows)
        assert len(repair.recent_quarantines(db)) == 1

    def test_a_failed_read_is_an_empty_list_not_a_raise(self):
        from app.services import cross_school_repair as repair
        db = _DB(break_table="audit_logs")
        assert repair.recent_quarantines(db) == []


# ── the operator's door ─────────────────────────────────────────────────────

class TestTheOperatorRestoresARow:
    def _route(self) -> str:
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        at = src.index('@super_bp.route("/integrity/restore"')
        nxt = src.find("bp.route(", at + 10)
        end = src.rfind("\n@", at, nxt) if nxt != -1 else len(src)
        return src[at:end if end != -1 else len(src)]

    def test_the_route_exists_and_is_super_admin_only(self):
        block = self._route()
        assert 'methods=["POST"]' in block
        assert "_sa_required" in block, "the restore door is not guarded"

    def test_it_calls_the_one_service(self):
        assert "cross_school_repair" in self._route(), (
            "the route re-implements the rule instead of calling the service")

    def test_the_effect_is_never_named_by_the_form(self):
        block = self._route()
        for stolen in ('request.form.get("column")', 'request.form.get("value")',
                       'request.form.get("class_id")', 'request.form.get("is_active")',
                       'request.form.get("status")'):
            assert stolen not in block, (
                "the restore could be told which column or class to write by the request")

    def test_it_records_the_act(self):
        assert "log_activity" in self._route(), (
            "a restore left no summary in the audit trail")

    def test_the_dashboard_lists_quarantined_rows(self):
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        assert "recent_quarantines" in src, (
            "the dashboard never reads the quarantined rows, so nobody can restore one")


class TestTheTemplateDrawsTheRestoreDoor:
    def test_it_offers_a_restore_action(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "/integrity/restore" in html, "a quarantined row has no way back"

    def test_it_shows_the_reason_the_operator_typed(self):
        """The reason is read back from the audit record beside the row it belongs to."""
        html = DASHBOARD.read_text(encoding="utf-8")
        block = html.split('id="cross-school-quarantines"', 1)[1][:2500]
        assert "q.reason" in block, "the restore UI never shows why the row was quarantined"
        assert "/integrity/restore" in block

    def test_it_is_bilingual(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        block = html.split('id="cross-school-quarantines"', 1)[1][:3000]
        assert re.search(r"t\('", block), "the restore UI carries no i18n helper"

    def test_the_outcome_banner_reads_the_redirect(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "restored" in html, "the restore outcome never reaches the page"
