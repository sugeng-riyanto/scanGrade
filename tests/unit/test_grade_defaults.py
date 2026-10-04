"""A school states its weights once; every subject follows until it is overridden.

Migration 057 gave weights *per subject per year*, so a school with one policy had
to type it into every subject, and a subject nobody touched fell back to the simple
mean even though the school had declared its weights. Migration 058 adds a
**school default** on the component itself.

This file pins the precedence — custom subject config wins, else the default, else
the simple mean — and the rule that makes a default a *policy* rather than a
number: it counts only when it sums to 100.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "app" / "routes" / "admin_sekolah.py"
TEACHER = ROOT / "app" / "routes" / "teacher.py"
SERVICE = ROOT / "app" / "services" / "grade_weighting.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"
MIGRATION = ROOT / "supabase" / "migrations" / "058_grade_component_defaults.sql"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.op, self.payload = [], "select", None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append((col, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self.op == "update":
            self.store.setdefault("_updates", []).append((self.table, self.payload, list(self.filters)))
        return _Resp(self.store.get(self.table, []))


class _Fake:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables, name)


def _components(**defaults):
    out = []
    for cid, weight in defaults.items():
        out.append({"id": cid, "name": cid, "school_id": "s1",
                    "is_active": True, "default_weight": weight})
    return out


class TestTheDefaultIsAPolicy:
    def test_a_default_that_does_not_reach_100_is_not_a_policy(self):
        fake = _Fake({"grade_component_type": _components(c1=30, c2=30)})
        assert gw.default_config(fake, "s1") == {}, (
            "a half-filled default must not be read as a distribution")

    def test_a_default_that_reaches_100_is_used(self):
        fake = _Fake({"grade_component_type": _components(c1=30, c2=70)})
        assert gw.default_config(fake, "s1") == {"c1": 30, "c2": 70}

    def test_a_zero_default_is_not_a_component_of_the_policy(self):
        fake = _Fake({"grade_component_type": _components(c1=100, c2=0)})
        assert gw.default_config(fake, "s1") == {"c1": 100}


class TestPrecedence:
    def test_a_custom_subject_config_wins_over_the_default(self):
        fake = _Fake({
            "grade_component_type": _components(c1=30, c2=70),
            "grade_weight_config": [
                {"component_id": "c1", "weight_percent": 100, "is_active": True,
                 "school_id": "s1", "subject_id": "sub1", "school_year_id": "y1"}],
        })
        assert gw.effective_config(fake, "s1", "sub1", "y1") == {"c1": 100}

    def test_an_untouched_subject_follows_the_default(self):
        fake = _Fake({
            "grade_component_type": _components(c1=30, c2=70),
            "grade_weight_config": [],
        })
        assert gw.effective_config(fake, "s1", "sub1", "y1") == {"c1": 30, "c2": 70}

    def test_no_default_and_no_config_is_the_simple_mean(self):
        fake = _Fake({"grade_component_type": _components(c1=50),
                      "grade_weight_config": []})
        assert gw.effective_config(fake, "s1", "sub1", "y1") == {}


class TestSavingTheDefault:
    def test_a_total_other_than_100_is_refused(self):
        fake = _Fake({"grade_component_type": _components(c1=0, c2=0)})
        ok, out = gw.save_defaults(fake, "s1", {"c1": 40, "c2": 40})
        assert not ok and out["status"] == 400 and "100" in out["error"]

    def test_a_foreign_component_is_refused(self):
        fake = _Fake({"grade_component_type": _components(c1=0)})
        ok, out = gw.save_defaults(fake, "s1", {"c-other": 100})
        assert not ok and out["status"] == 403

    def test_an_empty_set_clears_every_default(self):
        fake = _Fake({"grade_component_type": _components(c1=30, c2=70)})
        ok, out = gw.save_defaults(fake, "s1", {})
        assert ok and out["cleared"] is True
        writes = fake.tables.get("_updates", [])
        assert len(writes) == 2, "every component must be reset, not only the named ones"
        assert all(w[1].get("default_weight") == 0 for w in writes)

    def test_the_default_only_writes_columns_the_table_has(self):
        """`grade_component_type` (057) has no `updated_by`; a write that names it
        would fail on the live schema."""
        fake = _Fake({"grade_component_type": _components(c1=100)})
        gw.save_defaults(fake, "s1", {"c1": 100}, actor_id="u1")
        writes = fake.tables.get("_updates", [])
        assert writes and all("updated_by" not in w[1] for w in writes)


class TestTheWiring:
    def test_the_defaults_route_is_admin_only(self):
        src = ADMIN.read_text(encoding="utf-8")
        assert 'route("/grade-weights/defaults", methods=["POST"])' in src
        block = src.split('route("/grade-weights/defaults", methods=["POST"])')[1].split("\n@admin_sekolah_bp.route")[0]
        assert "@admin_sekolah_required" in block

    def test_the_page_sends_the_default_and_the_custom_rows(self):
        src = ADMIN.read_text(encoding="utf-8")
        body = src.split("def admin_grade_weights(")[1].split("\ndef ")[0]
        assert "default_config(" in body and "custom_subjects" in body
        assert "defaults=defaults" in body

    def test_the_service_uses_the_effective_config_where_a_mark_is_computed(self):
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def subject_finals(")[1].split("\ndef ")[0]
        assert "effective_config(" in body, (
            "the weighted mark must honour the default, or an untouched subject "
            "still averages simply")

    def test_the_subject_table_uses_the_effective_config(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_table(")[1].split("\ndef ")[0]
        assert "effective_config(" in body

    def test_the_exam_dropdown_offers_components_for_every_subject(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_components_by_subject(")[1].split("\ndef ")[0]
        assert "default_config(" in body, (
            "a subject the admin never touched must still offer its categories")
        assert 'select("id")' in body, "the picker must cover every school subject"

    def test_the_page_has_a_default_editor(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "saveDefaults()" in html and "defaultTotal()" in html
        assert "School Default Weights" in html

    def test_the_matrix_marks_subjects_that_follow_the_default(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "isCustom(" in html and "followDefault(" in html


class TestTheMigration:
    def test_it_adds_the_default_column_idempotently(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS default_weight" in sql
        assert "BETWEEN 0 AND 100" in sql
        code = "\n".join(line for line in sql.splitlines()
                         if not line.strip().startswith("--"))
        assert "BEGIN;" not in code and "COMMIT;" not in code, (
            "the migration must leave the transaction to apply_migration.py")
