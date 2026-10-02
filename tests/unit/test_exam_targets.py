"""Per-pupil exam targets: the roster, the diff, and the door.

An exam's audience was its classes. This feature adds the one missing sentence —
"everyone in these classes, except these pupils" — and these guards pin the three
things that would silently ruin it:

* the **diff** must not delete a row (a pupil excluded after submitting keeps the
  attempt their row is the link to), and a pupil who is not on the roster must be
  left alone;
* the **roster** must fall back to `profiles.class_id` while a school's
  enrollment table is still empty, or the feature would show nobody on day one;
* the **door** must consult the target list per request when the paper carries
  one, and not at all when it does not (every exam written before migration 048).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import exam_targets as et
from app.utils.exam_access import exam_sitting_allowed

SCHOOL = "school-1"
YEAR = "year-1"
EXAM = "exam-1"


# ── a tiny query fake, enough for the reads this service makes ────────────────

class _Q:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append(("in", col, {str(v) for v in values}))
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, payload, on_conflict=None):
        self.sb.upserts.append((self.table, payload, on_conflict))
        return self

    def update(self, payload):
        self.sb.updates.append((self.table, payload))
        return self

    def execute(self):
        rows = self.sb.data.get(self.table, [])
        out = []
        for r in rows:
            ok = True
            for op, col, val in self.filters:
                if op == "eq" and str(r.get(col)) != val:
                    ok = False
                if op == "in" and str(r.get(col)) not in val:
                    ok = False
            if ok:
                out.append(r)
        return SimpleNamespace(data=out)


class _Sb:
    def __init__(self, data=None):
        self.data = data or {}
        self.upserts = []
        self.updates = []

    def table(self, name):
        return _Q(self, name)


# ── the diff ─────────────────────────────────────────────────────────────────

def _roster(*pairs):
    return [{"student_id": s, "class_id": c, "full_name": s.upper()} for s, c in pairs]


class TestTheDiff:
    def test_an_unchecked_pupil_becomes_excluded_with_their_reason(self):
        """Once there is an exception the *whole* roster is written, so the list
        is complete and a later reader never has to guess an absent pupil."""
        rows = et.plan_diff(_roster(("a", "c1"), ("b", "c1")), {}, {"a"},
                            {"b": "izin"})
        by_id = {r["student_id"]: r for r in rows}
        assert by_id["a"]["status"] == "included" and by_id["a"]["reason"] is None
        assert by_id["b"] == {"student_id": "b", "class_id": "c1",
                              "status": "excluded", "reason": "izin"}

    def test_a_re_ticked_pupil_drops_the_reason(self):
        rows = et.plan_diff(_roster(("a", "c1")),
                            {"a": {"status": "excluded", "class_id": "c1",
                                   "reason": "izin"}},
                            {"a"}, {})
        assert rows == [{"student_id": "a", "class_id": "c1", "status": "included",
                         "reason": None}]

    def test_an_unchanged_pupil_is_not_rewritten(self):
        existing = {"a": {"status": "excluded", "class_id": "c1", "reason": "sakit"}}
        assert et.plan_diff(_roster(("a", "c1")), existing, set(), {"a": "sakit"}) == []

    def test_a_pupil_no_longer_on_the_roster_is_left_alone(self):
        """They belong to another class (or year); the diff is not their keeper."""
        existing = {"gone": {"status": "included", "class_id": "c9", "reason": None}}
        rows = et.plan_diff(_roster(("a", "c1")), existing, {"a"}, {})
        assert "gone" not in {r["student_id"] for r in rows}


# ── the roster ───────────────────────────────────────────────────────────────

class TestTheRoster:
    def test_enrollment_is_preferred_when_it_has_rows(self):
        sb = _Sb({
            "student_enrollment": [
                {"student_id": "s1", "class_id": "c1", "school_id": SCHOOL,
                 "status": "aktif", "school_year_id": YEAR}],
            "profiles": [{"id": "s1", "full_name": "Ani", "role": "murid",
                          "school_id": SCHOOL, "class_id": "c1"}],
        })
        got = et.roster_for_classes(sb, SCHOOL, ["c1"], YEAR)
        assert [r["student_id"] for r in got] == ["s1"]
        assert got[0]["full_name"] == "Ani"

    def test_it_falls_back_to_profile_class_when_enrollment_is_empty(self):
        sb = _Sb({"student_enrollment": [], "profiles": [
            {"id": "s1", "full_name": "Ani", "role": "murid", "school_id": SCHOOL,
             "class_id": "c1"}]})
        got = et.roster_for_classes(sb, SCHOOL, ["c1"], YEAR)
        assert [r["student_id"] for r in got] == ["s1"]

    def test_no_classes_returns_nobody(self):
        assert et.roster_for_classes(_Sb({}), SCHOOL, [], YEAR) == []


# ── the write ────────────────────────────────────────────────────────────────

class _Form(dict):
    def getlist(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else ([v] if v else [])


class TestSyncFromForm:
    def _sb(self):
        return _Sb({
            "profiles": [
                {"id": "s1", "full_name": "Ani", "role": "murid",
                 "school_id": SCHOOL, "class_id": "c1"},
                {"id": "s2", "full_name": "Budi", "role": "murid",
                 "school_id": SCHOOL, "class_id": "c1"},
            ],
            "exam_target_student": [],
        })

    def test_no_markers_means_untouched(self):
        sb = self._sb()
        out = et.sync_from_form(sb, EXAM, SCHOOL, ["c1"], _Form({}))
        assert out["skipped"] is True and sb.upserts == []

    def test_a_roster_that_did_not_load_is_not_read_as_all_excluded(self):
        sb = self._sb()
        out = et.sync_from_form(sb, EXAM, SCHOOL, ["c1"],
                                _Form({"target_present": "1"}))
        assert out["skipped"] is True and sb.upserts == []

    def test_everyone_ticked_stays_in_class_mode(self):
        sb = self._sb()
        out = et.sync_from_form(sb, EXAM, SCHOOL, ["c1"],
                                _Form({"target_present": "1", "target_loaded": "1",
                                       "target_students": ["s1", "s2"]}))
        assert out["excluded"] == 0 and sb.upserts == [] and sb.updates == []

    def test_one_unchecked_writes_the_exception_and_flips_the_mode(self):
        sb = self._sb()
        out = et.sync_from_form(sb, EXAM, SCHOOL, ["c1"],
                                _Form({"target_present": "1", "target_loaded": "1",
                                       "target_students": ["s1"],
                                       "target_reason_s2": "izin"}))
        assert out["written"] == 2          # the whole roster, once there is an exception
        table, payload, _ = sb.upserts[0]
        assert table == "exam_target_student"
        by_id = {p["student_id"]: p for p in payload}
        assert by_id["s2"]["status"] == "excluded" and by_id["s2"]["reason"] == "izin"
        assert by_id["s1"]["status"] == "included"
        assert ("exams", {"target_mode": "students"}) in sb.updates

    def test_an_injected_student_id_is_ignored(self):
        """A hand-written post cannot target a pupil outside the roster."""
        sb = self._sb()
        et.sync_from_form(sb, EXAM, SCHOOL, ["c1"],
                          _Form({"target_present": "1", "target_loaded": "1",
                                 "target_students": ["s1", "s2", "not-on-roster"]}))
        assert sb.upserts == []           # everyone on the roster is included


# ── the door ─────────────────────────────────────────────────────────────────

def _exam(**over):
    base = {"id": EXAM, "school_id": SCHOOL, "teacher_id": "t1",
            "is_published": True, "status": "active", "class_ids": ["c1"]}
    base.update(over)
    return base


class _DoorSb:
    """The two reads the door makes: the pupil's profile, and the target row."""

    def __init__(self, target_status=None):
        self.target_status = target_status

    def table(self, name):
        return _DoorQ(self, name)


class _DoorQ:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self.table == "profiles":
            return SimpleNamespace(data={"school_id": SCHOOL, "class_id": "c1"})
        if self.table == "exam_target_student":
            if self.sb.target_status is None:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{"status": self.sb.target_status}])
        return SimpleNamespace(data=[])


def test_a_class_mode_paper_ignores_targets():
    exam = _exam(target_mode="class")
    allowed, _ = exam_sitting_allowed(_DoorSb(None), exam, EXAM, "s1")
    assert allowed is True


def test_a_target_mode_paper_is_refused_when_the_pupil_is_excluded():
    exam = _exam(target_mode="students")
    allowed, reason = exam_sitting_allowed(_DoorSb("excluded"), exam, EXAM, "s1")
    assert allowed is False and "peserta" in reason


def test_a_target_mode_paper_admits_an_included_pupil():
    exam = _exam(target_mode="students")
    allowed, _ = exam_sitting_allowed(_DoorSb("included"), exam, EXAM, "s1")
    assert allowed is True


def test_a_target_mode_paper_refuses_a_pupil_with_no_row_at_all():
    exam = _exam(target_mode="students")
    allowed, _ = exam_sitting_allowed(_DoorSb(None), exam, EXAM, "s1")
    assert allowed is False
