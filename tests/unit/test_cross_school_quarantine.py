"""A row that can never be re-pointed still needs a door, or the operator uses SQL.

`cross_school_repair.apply` re-homes a row at the school its people belong to — but
only when there *is* one such school. A `class_subjects` or `teacher_assignments`
row whose class and subject belong to different schools has no single answer
(`two_schools`), and a row whose pointer cannot be read has no answer either
(`target_unknown`). Those are exactly the "truly mis-wired" rows, and until now the
page could only name them and leave the operator to reach for SQL — the page that
cannot do it, again.

This is the second door: **quarantine**. It does not guess a school; it detaches the
row from the live data the way the app itself removes a link (close the offering,
retire the assignment, take the pupil out of the wrong class), so nothing is
destroyed and the row stops feeding a cross-school read.

Two rules make it safe:

* **A reason is mandatory.** The reason is the whole record of *why* a human made a
  link disappear; an empty one turns the audit trail into "somebody clicked".
* **The effect comes from the kind, never from the request.** The form names only
  *which* row; the column and value are the service's table, so a forged form cannot
  write an arbitrary column.
"""
from __future__ import annotations

import re
from pathlib import Path

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


SCH_A, SCH_B, SCH_C = "school-a", "school-b", "school-c"


def _db(**over):
    tables = dict(
        classes=[{"id": "c1", "name": "7A", "school_id": SCH_A}],
        subjects=[{"id": "s1", "name": "Math", "school_id": SCH_A}],
        schools=[{"id": SCH_A, "name": "SMA A"}, {"id": SCH_B, "name": "SMA B"},
                 {"id": SCH_C, "name": "SMA C"}],
        class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                         "school_id": SCH_B, "is_active": True}],
        teacher_assignments=[{"id": "a1", "class_id": "c1", "subject_id": "s1",
                              "teacher_id": "t1", "school_id": SCH_B,
                              "status": "active"}],
        profiles=[{"id": "u1", "class_id": "c1", "school_id": SCH_B}],
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
    return [payload for op, name, payload, _f in db.log
            if op == "update" and (table is None or name == table)]


# ── the reason is mandatory ─────────────────────────────────────────────────

class TestTheReasonIsMandatory:
    def _pair_in_two_schools(self):
        from app.services import cross_school_repair as repair
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        return repair, db

    def test_an_empty_reason_is_refused(self, monkeypatch):
        repair, db = self._pair_in_two_schools()
        _quiet(monkeypatch)
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.REASON_REQUIRED
        assert _writes(db) == [], "a quarantine without a reason touched a row"

    def test_a_whitespace_reason_is_refused(self, monkeypatch):
        repair, db = self._pair_in_two_schools()
        _quiet(monkeypatch)
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "   \t ", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.REASON_REQUIRED
        assert _writes(db) == []

    def test_a_too_short_reason_is_refused(self, monkeypatch):
        """`x` is not a reason; a record nobody can learn from is no record."""
        repair, db = self._pair_in_two_schools()
        _quiet(monkeypatch)
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "x", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.REASON_REQUIRED

    def test_the_reason_key_reaches_the_page(self):
        from app.services import cross_school_repair as repair
        assert repair.reason_key(repair.REASON_REQUIRED) == "repair_reason_required"


# ── the effect is the kind's, never the request's ───────────────────────────

class TestTheEffectComesFromTheKind:
    def test_a_pair_is_closed_not_deleted(self, monkeypatch):
        """The app's own way to remove an offering is `is_active=false` — the row
        survives so a paper under it still points at something."""
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "created under the wrong school", "sa-1")
        assert out["ok"] is True and out["action"] == repair.QUARANTINED
        assert db.tables["class_subjects"][0]["is_active"] is False

    def test_an_assignment_is_retired(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        out = repair.quarantine(db, "assignment_school_mismatch", "a1", "wrong school pair", "sa-1")
        assert out["ok"] is True
        assert db.tables["teacher_assignments"][0]["status"] == "inactive"

    def test_a_pupil_is_taken_out_of_the_wrong_class(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        # A pupil whose class cannot be read has no school to move to.
        db = _db(classes=[])
        out = repair.quarantine(db, "class_pupil_mismatch", "u1", "class no longer exists", "sa-1")
        assert out["ok"] is True
        assert db.tables["profiles"][0]["class_id"] is None

    def test_it_writes_exactly_one_column(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        repair.quarantine(db, "pair_school_mismatch", "p1", "wrong school pair", "sa-1")
        assert _writes(db, "class_subjects")[0] == {"is_active": False}

    def test_an_unknown_kind_never_touches_a_table(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        out = repair.quarantine(db, "something_else", "x", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.UNKNOWN_KIND
        assert _writes(db) == []


# ── refusals that mirror the repair door ────────────────────────────────────

class TestTheRefusals:
    def test_a_missing_row_is_refused(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db()
        db.tables["class_subjects"] = []
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.ROW_NOT_FOUND
        assert _writes(db) == []

    def test_an_already_consistent_row_is_not_quarantined(self, monkeypatch):
        """There is nothing to detach: closing a healthy link is a data loss, not a
        repair, so the door refuses it rather than becoming a delete button."""
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(class_subjects=[{"id": "p1", "class_id": "c1", "subject_id": "s1",
                                  "school_id": SCH_A, "is_active": True}])
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.NOTHING_TO_QUARANTINE
        assert _writes(db) == []

    def test_a_racing_fix_is_reported_rather_than_clobbered(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        original_update = _Query.update

        def update_then_stale(self, payload):
            db.tables["class_subjects"][0]["school_id"] = SCH_A  # the racing fix
            return original_update(self, payload)

        monkeypatch.setattr(_Query, "update", update_then_stale)
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.ROW_CHANGED

    def test_a_failed_read_is_reported_not_raised(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _DB(break_table="class_subjects", **{
            "classes": [{"id": "c1", "name": "7A", "school_id": SCH_A}],
            "subjects": [{"id": "s1", "name": "Math", "school_id": SCH_C}],
            "schools": [],
            "class_subjects": [{"id": "p1", "class_id": "c1", "subject_id": "s1",
                                "school_id": SCH_B, "is_active": True}],
        })
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.READ_FAILED

    def test_a_failed_write_is_reported_not_raised(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        db = _db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}])
        monkeypatch.setattr(_Query, "update",
                            lambda self, payload: (_ for _ in ()).throw(
                                RuntimeError("deadlock")))
        out = repair.quarantine(db, "pair_school_mismatch", "p1", "a reason", "sa-1")
        assert out["ok"] is False and out["reason"] == repair.WRITE_FAILED


# ── the record ──────────────────────────────────────────────────────────────

class TestTheAuditRecord:
    def test_it_names_the_actor_the_row_and_the_reason(self, monkeypatch):
        from app.services import cross_school_repair as repair
        calls = _quiet(monkeypatch)
        repair.quarantine(_db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}]),
                          "pair_school_mismatch", "p1", "created under the wrong school", "sa-1")
        assert len(calls) == 1, "a quarantined row left no record"
        args, kwargs = calls[0]
        assert args[0] == "update" and "class_subjects" in args
        assert kwargs["user_id"] == "sa-1"
        record = kwargs.get("new_data") or {}
        assert "created under the wrong school" in str(record), (
            "the mandatory reason never reached the audit trail")

    def test_it_invalidates_the_reads_it_changed(self, monkeypatch):
        from app.services import cross_school_repair as repair
        _quiet(monkeypatch)
        from app.utils import req_cache
        seen = []
        monkeypatch.setattr(req_cache, "invalidate_class_subjects",
                            lambda cid: seen.append(cid), raising=False)
        repair.quarantine(_db(subjects=[{"id": "s1", "name": "Math", "school_id": SCH_C}]),
                          "pair_school_mismatch", "p1", "wrong school pair", "sa-1")
        assert "c1" in seen, "the class's subject cache was left stale"


# ── the operator's door ─────────────────────────────────────────────────────

class TestTheOperatorQuarantinesARow:
    def _route(self) -> str:
        src = SUPER_ROUTES.read_text(encoding="utf-8")
        at = src.index('@super_bp.route("/integrity/quarantine"')
        nxt = src.find("bp.route(", at + 10)
        end = src.rfind("\n@", at, nxt) if nxt != -1 else len(src)
        return src[at:end if end != -1 else len(src)]

    def test_the_route_exists_and_is_super_admin_only(self):
        block = self._route()
        assert 'methods=["POST"]' in block
        assert "_sa_required" in block, "the quarantine door is not guarded"

    def test_it_calls_the_one_service(self):
        assert "cross_school_repair" in self._route(), (
            "the route re-implements the rule instead of calling the service")

    def test_it_passes_the_reason_through(self):
        assert 'request.form.get("reason"' in self._route(), (
            "the route drops the operator's reason before the service can require it")

    def test_the_effect_is_never_named_by_the_form(self):
        block = self._route()
        for stolen in ('request.form.get("column")', 'request.form.get("value")',
                       'request.form.get("is_active")', 'request.form.get("status")'):
            assert stolen not in block, (
                "the quarantine could be told which column to write by the request")

    def test_it_records_the_act(self):
        assert "log_activity" in self._route(), (
            "a quarantine left no summary in the audit trail")

    def test_the_template_draws_the_quarantine_door(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "/integrity/quarantine" in html, "a refused row has no second door"
        assert 'name="reason"' in html, "the quarantine door has no reason field"
        assert "required" in html.split("/integrity/quarantine", 1)[1][:1500], (
            "the reason field is not required on the page")
        assert re.search(r"t\('", html.split("/integrity/quarantine", 1)[1][:3000]), (
            "the quarantine UI carries no i18n helper")
