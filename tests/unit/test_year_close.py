"""Closing a year must record what happened, not overwrite where pupils were.

The wizard's two halves are pinned here: the *plan* (who moves where, and which
pupils it refuses to guess about) and the *lifecycle* (a closed year is
read-only; a new year starts as a draft and does not become the running one just
by existing).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services import academic_year as ay

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "047_school_year_lifecycle.sql"


class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.op, self.payload, self.conflict = {}, "select", None, None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.conflict = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self.op in ("insert", "upsert", "update"):
            self.db.writes.append({"table": self.table, "op": self.op,
                                   "filters": dict(self.filters), "payload": self.payload})
            return SimpleNamespace(data=[dict(self.payload or {}, id="new")])
        rows = [r for r in self.db.tables.get(self.table, [])
                if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        return SimpleNamespace(data=rows, count=len(rows))


class _Db:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.writes = []

    def table(self, name):
        return _Q(self, name)


def _classes():
    """A middle school: grades 7-9, so **9 is the final grade**."""
    return [
        {"id": "c-8a", "name": "8A", "grade_level": "8", "school_year_id": "y-old"},
        {"id": "c-9a", "name": "9A", "grade_level": "9", "school_year_id": "y-old"},
        {"id": "n-8a", "name": "8A", "grade_level": "8", "school_year_id": "y-new"},
        {"id": "n-9a", "name": "9A", "grade_level": "9", "school_year_id": "y-new"},
    ]


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_the_service_statuses_match_the_database_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"CHECK \(status IN \(([^)]*)\)\)", sql).group(1)
    assert set(re.findall(r"'([a-z]+)'", check)) == set(ay.STATUSES)


def test_a_closed_year_is_not_editable():
    db = _Db({"school_years": [{"id": "y-old", "status": "closed"}]})
    assert ay.editable(db, "y-old") is False
    assert ay.is_closed(db, "y-old") is True


def test_a_draft_and_an_active_year_are_editable():
    for status in ("draft", "active"):
        db = _Db({"school_years": [{"id": "y", "status": status}]})
        assert ay.editable(db, "y") is True, status


def test_a_year_that_cannot_be_read_is_not_editable():
    """Fails closed on the write question, even though it does not claim closed."""
    assert ay.editable(_Db(), "y-missing") is False
    assert ay.editable(_Db(), None) is False


def test_close_year_marks_it_closed_and_stops_it_running():
    db = _Db()
    ay.close_year(db, "sc-1", "y-old")
    write = db.writes[0]
    assert write["payload"] == {"status": "closed", "is_active": False}
    assert write["filters"] == {"id": "y-old", "school_id": "sc-1"}


def test_a_new_year_starts_as_a_draft_not_running():
    db = _Db()
    ay.create_draft_year(db, "sc-1", "2027/2028", "2027-07-01", "2028-06-30")
    assert db.writes[0]["payload"]["status"] == "draft"
    assert db.writes[0]["payload"]["is_active"] is False


def test_activating_a_year_deactivates_the_previous_one_first():
    db = _Db()
    ay.activate_year(db, "sc-1", "y-new")
    first, second = db.writes[0], db.writes[1]
    assert first["payload"]["is_active"] is False, "old year must stop running first"
    assert second["payload"] == {"is_active": True, "status": "active"}


# ── the plan ─────────────────────────────────────────────────────────────────

def _plan(pupils, to_year="y-new"):
    return ay.plan_close(_Db(), "sc-1", "y-old", to_year,
                         classes=_classes(), pupils=pupils)


def test_the_last_grade_graduates_and_others_advance():
    plan = _plan([
        {"id": "st-8", "class_id": "c-8a", "name": "Eight"},
        {"id": "st-9", "class_id": "c-9a", "name": "Nine"},
    ])
    by_id = {r["student_id"]: r for r in plan["rows"]}
    assert by_id["st-8"]["outcome"] == "naik"
    assert by_id["st-8"]["to_class_id"] == "n-9a"
    assert by_id["st-9"]["outcome"] == "lulus"
    assert by_id["st-9"]["to_class_id"] is None


def test_a_pupil_with_no_class_is_reported_not_guessed():
    plan = _plan([{"id": "st-x", "class_id": None, "name": "Nameless"}])
    assert plan["rows"] == []
    assert plan["errors"][0]["student_id"] == "st-x"


def test_a_missing_next_grade_class_is_an_error_not_an_invention():
    """A year with no class at the next grade is a gap the wizard names."""
    classes = [c for c in _classes() if c["id"] != "n-9a"]  # next year has no 9
    plan = ay.plan_close(_Db(), "sc-1", "y-old", "y-new", classes=classes,
                         pupils=[{"id": "st-8", "class_id": "c-8a", "name": "Eight"}])
    assert plan["rows"] == []
    assert plan["errors"] and "grade 9" in plan["errors"][0]["reason"]


def test_planning_without_a_target_year_still_assigns_outcomes():
    plan = _plan([{"id": "st-8", "class_id": "c-8a", "name": "Eight"}], to_year=None)
    assert plan["rows"][0]["outcome"] == "naik"
    assert plan["rows"][0]["to_class_id"] is None


# ── applying it ──────────────────────────────────────────────────────────────

def test_applying_records_the_outcome_and_moves_both_pointers():
    db = _Db()
    plan = ay.plan_close(db, "sc-1", "y-old", "y-new", classes=_classes(),
                         pupils=[{"id": "st-8", "class_id": "c-8a", "name": "Eight"}])
    db.writes.clear()
    report = ay.apply_close(db, "sc-1", plan)
    assert report["moved"] == 1 and report["errors"] == []
    moves = [w for w in db.writes if w["table"] in ("students", "profiles")]
    assert len(moves) == 2, "both class pointers must move together"
    assert all(w["payload"]["class_id"] == "n-9a" for w in moves)
    # the closing year's outcome is written to the OLD year
    outcome = [w for w in db.writes if w["table"] == "student_enrollment"
               and w["payload"].get("status") == "naik"]
    assert outcome and outcome[0]["payload"]["school_year_id"] == "y-old"


def test_a_graduating_pupil_is_not_moved_anywhere():
    db = _Db()
    plan = ay.plan_close(db, "sc-1", "y-old", "y-new", classes=_classes(),
                         pupils=[{"id": "st-9", "class_id": "c-9a", "name": "Nine"}])
    db.writes.clear()
    report = ay.apply_close(db, "sc-1", plan)
    assert report["moved"] == 0
    assert not [w for w in db.writes if w["table"] in ("students", "profiles")]
    assert [w for w in db.writes if w["payload"].get("status") == "lulus"]


def test_an_admin_override_beats_the_default():
    db = _Db()
    plan = ay.plan_close(db, "sc-1", "y-old", "y-new", classes=_classes(),
                         pupils=[{"id": "st-8", "class_id": "c-8a", "name": "Eight"}])
    db.writes.clear()
    ay.apply_close(db, "sc-1", plan, overrides={"st-8": "tinggal_kelas"})
    assert [w for w in db.writes if w["payload"].get("status") == "tinggal_kelas"]
    assert not [w for w in db.writes if w["table"] in ("students", "profiles")], (
        "a pupil staying back is not moved to a new class")


def test_an_error_names_the_pupil():
    db = _Db()
    plan = {"rows": [{"student_id": "st-8", "from_class_id": "c-8a", "outcome": "naik",
                      "to_class_id": None, "from_class": "8A", "to_class": ""}],
            "errors": [], "to_year": "y-new", "from_year": "y-old"}
    report = ay.apply_close(db, "sc-1", plan)
    assert report["errors"] and report["errors"][0]["student_id"] == "st-8"


# ── the migration ────────────────────────────────────────────────────────────

def test_the_migration_is_additive_and_backfills_once():
    sql = MIGRATION.read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", code)
    assert "ADD COLUMN IF NOT EXISTS status" in sql
    # The column is added NULLABLE so the one-time backfill can tell an
    # un-migrated row from a legitimate draft.
    assert "WHERE status IS NULL" in sql
