"""A plan's quota must be enforced where the data is added, not only advertised.

Phase 0 found `@require_subscription` installed on exactly one route
(`create_exam`). The student cap and the AI-grading entitlement were declared in
`TIER_LIMITS`, shown on the pricing page, and checked by nothing: a trial school
could import ten thousand students, and a basic school could run AI grading.

The guards here hold two things:

* the checker itself — a student limit that counts the rows about to be added, not
  just the rows already there, and an `ai_grading` gate that follows the tier;
* the wiring — the routes that *add students* and the routes that *run AI grading*
  actually carry the check, read out of the source so a later refactor that drops
  the decorator fails rather than silently re-opening the door.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import subscription_service as sub

ROOT = Path(__file__).resolve().parents[2]


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _Sb:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _client(tier="basic", students=0):
    return _Sb({
        "school_subscriptions": [{"school_id": "S1", "status": "active",
                                  "plan_id": None, "tier": tier}],
        "subscription_plans": [],
        "students": [{"id": i, "school_id": "S1", "status": "active"}
                     for i in range(students)],
        "exams": [],
    })


def _check(monkeypatch, *, tier="basic", students=0, extra=None):
    monkeypatch.setattr(sub, "get_supabase", lambda: _client(tier, students))
    return sub.check_feature_limit("S1", "add_student", extra=extra)


class TestTheStudentQuota:
    def test_a_school_at_its_cap_cannot_add_another(self, monkeypatch):
        allowed, message = _check(monkeypatch, students=500, extra=1)
        assert not allowed, "a school at its student cap was allowed to add one"
        assert message, "the refusal must explain the quota and the way up"

    def test_a_school_below_its_cap_can(self, monkeypatch):
        assert _check(monkeypatch, students=499, extra=1)[0]

    def test_bulk_import_counts_the_rows_it_is_about_to_add(self, monkeypatch):
        """499 present + 5 incoming on a 500 cap must be refused, not allowed."""
        allowed, _ = _check(monkeypatch, students=499, extra=5)
        assert not allowed, (
            "the import check ignored the incoming rows and would have overshot "
            "the cap by five")

    def test_a_tier_with_no_student_cap_always_passes(self, monkeypatch):
        assert _check(monkeypatch, tier="pro", students=9999, extra=100)[0]


class TestTheAiGradingGate:
    def _ai(self, monkeypatch, tier):
        monkeypatch.setattr(sub, "get_supabase", lambda: _client(tier))
        return sub.check_feature_limit("S1", "ai_grading")

    @pytest.mark.parametrize("tier", ["pro", "enterprise"])
    def test_a_tier_that_includes_it_can(self, monkeypatch, tier):
        assert self._ai(monkeypatch, tier)[0]

    @pytest.mark.parametrize("tier", ["trial", "basic"])
    def test_a_tier_that_does_not_include_it_cannot(self, monkeypatch, tier):
        allowed, message = self._ai(monkeypatch, tier)
        assert not allowed, f"tier {tier} was allowed AI grading"
        assert "AI" in message, "the refusal must name the feature"


def _decorators_above(rel, defname):
    """The decorator lines immediately above a `def`, as one string."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    at = src.index(defname)
    return src[:at].rsplit("\n\n", 1)[-1]


class TestTheRoutesCarryTheCheck:
    def test_adding_a_student_is_gated(self):
        deco = _decorators_above("app/routes/admin_sekolah.py", "def create_student(")
        assert 'require_subscription("add_student")' in deco, (
            "the manual add-student route is not gated on the student quota")

    def test_the_bulk_import_is_gated(self):
        src = (ROOT / "app/routes/admin_sekolah.py").read_text(encoding="utf-8")
        body = src[src.index("def _import_students("):]
        assert "add_student" in body, (
            "the bulk student import is not gated on the student quota")

    def test_ai_grading_is_gated(self):
        for name in ("def ai_grade_essay(", "def ai_grade_bulk("):
            deco = _decorators_above("app/routes/api.py", name)
            assert 'require_subscription("ai_grading")' in deco, (
                f"{name[:-1]} is not gated on the AI-grading entitlement")
