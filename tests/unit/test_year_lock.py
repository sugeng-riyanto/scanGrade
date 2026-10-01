"""A closed year is read-only — at every door, and the doors are enumerated.

The rule lives in `app/decorators/year_lock.py`, but a rule applied by hand to
forty routes is a rule that will be forgotten on the forty-first. So the main
guard here is a *sweep*: it reads every mutating teacher route out of the source
and requires each one either to carry `@open_year_required` or to appear in an
explicit exemption list with a reason. A new write route therefore fails this
file until it is classified.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.decorators import year_lock
from app.services import academic_year

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"

#: Mutating routes that do NOT touch a year's papers, marks or scores. Each one
#: needs a reason, because "it was easier" is not one — this list is the only way
#: a write route avoids the lock, so an unjustified entry is a hole.
EXEMPT = {
    "/exams/parse-pdf": "reads an upload into a preview; writes no exam yet",
    "/exams/generate-key": "generates answer-key text for a form; writes nothing",
    "/exams/parse-pdf/markdown": "parses text in memory; writes nothing",
    "/exams/export-scan": "exports a file; writes nothing",
    "/reset-password": "the teacher's own account, not a year's data",
    "/profile/update": "the teacher's own account, not a year's data",
    "/assignments": "an assignment is per class-subject, not per year's marks",
    "/assignments/<assignment_id>": "same as /assignments",
    "/api/ai/test-demo": "a test call to the AI provider; writes nothing",
    "/ai-settings/add-key": "the teacher's own settings",
    "/ai-settings/<key_id>/toggle": "the teacher's own settings",
    "/ai-settings/<key_id>/delete": "the teacher's own settings",
    "/ai-settings/save-prompt": "the teacher's own settings",
    "/ai-settings/reset-prompt": "the teacher's own settings",
    "/subjects/new": "a school subject, not a year's marks",
    "/subjects/<subject_id>/delete": "a school subject, not a year's marks",
    "/analysis/<exam_id>/share": "a share link, not the year's data",
    "/analysis/<exam_id>/share/revoke": "a share link, not the year's data",
    "/analysis/<exam_id>/report/student/<student_id>/share":
        "a share link exposes an archived paper read-only; it does not change it",
    "/analysis/<exam_id>/report/student/<student_id>/share/revoke":
        "revoking that same read-only link",
    "/exams/new":
        "creates a NEW paper, which belongs to the running year; there is nothing "
        "to date yet, so no closed year can be its target",
    "/retake-requests/<request_id>/decide":
        "authorises a fresh sitting at a new year's door; a closed year's own rows "
        "are not touched",
}

#: Routes whose id key the decorator must name, so the sweep can check the key
#: against the route pattern rather than trusting a convention.
EXPECTED_KEYS = {
    "exam_id": ("exam_id",),
    "submission_id": ("submission_id",),
}

MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def _routes():
    """[(path, methods, decorator_block, function_name)] for every teacher route."""
    src = TEACHER.read_text(encoding="utf-8")
    out = []
    pattern = re.compile(
        r'@teacher_bp\.route\(\s*"([^"]+)"(?:\s*,\s*methods=\[([^\]]*)\])?\s*\)\n((?:@[^\n]*\n)*)def (\w+)\(')
    for match in pattern.finditer(src):
        path, methods_raw, decos, name = match.groups()
        methods = {m.strip().strip('"') for m in (methods_raw or '"GET"').split(",")}
        out.append((path, methods, decos, name))
    return out


def test_the_sweep_finds_the_write_routes_at_all():
    found = [(p, m) for p, m, _, _ in _routes() if m & set(MUTATING)]
    assert len(found) > 25, f"the sweep only found {len(found)} mutating routes"


def test_every_write_route_is_locked_or_excused_with_a_reason():
    unclassified = []
    for path, methods, decos, name in _routes():
        if not (methods & set(MUTATING)):
            continue
        if "@open_year_required" in decos:
            continue
        if path in EXEMPT:
            assert EXEMPT[path].strip(), f"{path} is excused with no reason"
            continue
        unclassified.append(f"{path} ({name})")
    assert not unclassified, (
        "these mutating routes can change a closed year's data without asking:\n  "
        + "\n  ".join(unclassified)
        + "\n\nAdd @open_year_required(...) or an entry in EXEMPT with a reason.")


def test_every_exemption_still_exists():
    """An exemption for a route that is gone hides the next route that takes it."""
    paths = {p for p, m, _, _ in _routes() if m & set(MUTATING)}
    stale = sorted(set(EXEMPT) - paths)
    assert not stale, f"EXEMPT names routes that no longer exist: {stale}"


def test_the_decorator_names_an_id_key_the_route_actually_has():
    """`@open_year_required('exam_id')` on a route with no such key is dead code."""
    wrong = []
    for path, methods, decos, name in _routes():
        if "@open_year_required" not in decos or not (methods & set(MUTATING)):
            continue
        keys = re.findall(r'open_year_required\(\s*"([^"]+)"', decos)
        assert keys, f"{path} uses @open_year_required without naming a key"
        for key in keys:
            if "<%s>" % key not in path:
                wrong.append(f"{path} names {key}")
    assert not wrong, "the decorator names a URL key the route does not carry: " + str(wrong)


# ── the rule itself ──────────────────────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = list(vals)
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = self.db.tables.get(self.table, [])
        out = []
        for r in rows:
            ok = True
            for k, v in self.filters.items():
                if isinstance(v, list):
                    if r.get(k) not in v:
                        ok = False
                elif str(r.get(k)) != str(v):
                    ok = False
            if ok:
                out.append(r)
        return SimpleNamespace(data=out)


class _Db:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self, name)


def test_a_paper_carries_its_own_year():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_exam(db, "e-1") == "y-1"


def test_an_old_paper_is_dated_by_its_class():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": None, "class_ids": ["c-1"]}],
              "classes": [{"id": "c-1", "school_year_id": "y-1"}]})
    assert academic_year.year_of_exam(db, "e-1") == "y-1"


def test_a_paper_spanning_years_has_no_single_year():
    db = _Db({"exams": [{"id": "e-1", "school_year_id": None,
                         "class_ids": ["c-1", "c-2"]}],
              "classes": [{"id": "c-1", "school_year_id": "y-1"},
                          {"id": "c-2", "school_year_id": "y-2"}]})
    assert academic_year.year_of_exam(db, "e-1") is None


def test_a_submission_reaches_its_year_through_its_exam():
    db = _Db({"submissions": [{"id": "s-1", "exam_id": "e-1"}],
              "exams": [{"id": "e-1", "school_year_id": "y-1", "class_ids": []}]})
    assert academic_year.year_of_submission(db, "s-1") == "y-1"


def test_only_a_closed_year_refuses_a_write():
    closed = _Db({"school_years": [{"id": "y-1", "status": "closed"}]})
    open_ = _Db({"school_years": [{"id": "y-1", "status": "active"}]})
    draft = _Db({"school_years": [{"id": "y-1", "status": "draft"}]})
    assert academic_year.write_refusal(closed, "y-1")
    assert academic_year.write_refusal(open_, "y-1") is None
    assert academic_year.write_refusal(draft, "y-1") is None


def test_an_unknown_year_does_not_refuse_every_write():
    """A read blip must not take the whole grading screen down."""
    assert academic_year.write_refusal(_Db({}), "y-missing") is None
    assert academic_year.write_refusal(_Db({}), None) is None


def test_a_closed_year_is_still_not_editable():
    closed = _Db({"school_years": [{"id": "y-1", "status": "closed"}]})
    assert academic_year.editable(closed, "y-1") is False


def test_the_message_is_stated_once():
    assert "ditutup" in year_lock.CLOSED_YEAR_MESSAGE


def test_promotion_into_a_closed_year_is_refused_too():
    """The school admin's promote door is the same rule, not a separate one."""
    src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
    promote = src.split("def promote(")[1].split("\n# \u2500\u2500\u2500")[0]
    assert "academic_year.write_refusal(" in promote, (
        "promotion can still push pupils into a year whose marks are history")


# ── the decorator, actually refusing ────────────────────────────────────────

def _decorated(monkeypatch, year_id, refusal):
    from app.services import academic_year as ay
    from app.utils import auth as auth_mod

    monkeypatch.setattr(ay, "year_of_exam", lambda sb, i: year_id)
    monkeypatch.setattr(ay, "year_of_submission", lambda sb, i: year_id)
    monkeypatch.setattr(ay, "write_refusal", lambda sb, y: refusal)
    monkeypatch.setattr(auth_mod, "get_supabase", lambda: object())

    @year_lock.open_year_required("exam_id")
    def view(exam_id):
        return "wrote"

    return view


def test_a_closed_year_answers_403_to_an_api_write(app, monkeypatch):
    view = _decorated(monkeypatch, "y-closed", "closed")
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        resp = view(exam_id="e-1")
    body, status = (resp[0], resp[1]) if isinstance(resp, tuple) else (resp, 200)
    assert status == 403, f"a closed year answered {status}"


def test_an_open_year_lets_the_write_through(app, monkeypatch):
    view = _decorated(monkeypatch, "y-open", None)
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        assert view(exam_id="e-1") == "wrote"


def test_a_paper_with_no_year_is_not_refused(app, monkeypatch):
    """An exam written before the column existed must still be gradeable."""
    view = _decorated(monkeypatch, None, None)
    with app.test_request_context("/api/grade-question/e-1/0/save", method="POST",
                                  headers={"Accept": "application/json"}):
        assert view(exam_id="e-1") == "wrote"


def test_a_form_write_into_a_closed_year_is_redirected_with_a_message(app, monkeypatch):
    view = _decorated(monkeypatch, "y-closed", "closed")
    with app.test_request_context("/teacher/exams/e-1/recalculate", method="POST"):
        resp = view(exam_id="e-1")
    assert getattr(resp, "status_code", None) in (301, 302), resp
