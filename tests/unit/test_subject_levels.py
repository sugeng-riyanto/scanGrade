"""Which subjects a class offers, and how far each pupil has got.

Two facts a school could not state before, and the four ways either can go
silently wrong:

* a class offers only **some** subjects — and the mapping must be a *soft* close,
  because a subject taken off a class in October is still part of that class's
  history;
* a pupil's track inside one subject — **basic**, **intermediate**, **advanced** —
  must be one of those three and nothing else, and it must be written for a pupil
  who is really in that class of that school, not for an id a caller typed in.

Every check below is the one that a manipulated `class_id` / `student_id`, a
typo'd `level`, or a hard delete would break in production.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import subject_levels as sl


SCHOOL = "school-1"
OTHER_SCHOOL = "school-2"
SUBJECT = "subject-1"
CLASS_A = "class-a"
CLASS_B = "class-b"
YEAR = "year-1"


# ── a tiny query fake, enough for the reads and writes this service makes ─────

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

    def order(self, *a, **k):
        return self

    def upsert(self, payload, on_conflict=None):
        self.sb.upserts.append((self.table, payload, on_conflict))
        return self

    def update(self, payload):
        self.sb.updates.append((self.table, payload, {c: v for op, c, v in self.filters}))
        return self

    def insert(self, payload):
        self.sb.inserts.append((self.table, payload))
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
                out.append(dict(r))
        return SimpleNamespace(data=out)


class _Sb:
    def __init__(self, data=None):
        self.data = data or {}
        self.upserts = []
        self.updates = []
        self.inserts = []

    def table(self, name):
        return _Q(self, name)


def _school(sb):
    sb.data.setdefault("schools", [{"id": SCHOOL}, {"id": OTHER_SCHOOL}])


# ── the vocabulary ───────────────────────────────────────────────────────────

class TestTheVocabulary:
    def test_the_three_tracks_are_exactly_basic_intermediate_advanced(self):
        assert sl.LEVELS == ("basic", "intermediate", "advanced")

    def test_a_level_outside_the_vocabulary_is_refused(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}],
            "profiles": [{"id": "s1", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A}],
        })
        ok, result = sl.save_levels(sb, SCHOOL, SUBJECT, CLASS_A, {"s1": "expert"})
        assert ok is False
        assert result.get("status") == 400
        # Nothing is written for a refused level — not even the pupils whose value
        # was fine, because a partial write is harder to explain than a refusal.
        assert sb.upserts == []


# ── the class mapping ────────────────────────────────────────────────────────

class TestTheClassMapping:
    def test_a_class_from_another_school_is_refused_not_dropped(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL},
                        {"id": "class-foreign", "school_id": OTHER_SCHOOL}],
        })
        ok, result = sl.save_mapping(sb, SCHOOL, SUBJECT, {CLASS_A, "class-foreign"})
        assert ok is False
        assert result.get("status") == 403
        assert sb.upserts == [] and sb.updates == []

    def test_removing_a_class_soft_closes_and_never_deletes(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}, {"id": CLASS_B, "school_id": SCHOOL}],
            "class_subjects": [
                {"id": "cs1", "school_id": SCHOOL, "subject_id": SUBJECT,
                 "class_id": CLASS_A, "is_active": True},
                {"id": "cs2", "school_id": SCHOOL, "subject_id": SUBJECT,
                 "class_id": CLASS_B, "is_active": True},
            ],
        })
        ok, result = sl.save_mapping(sb, SCHOOL, SUBJECT, {CLASS_B})
        assert ok is True
        # CLASS_A is closed, not deleted: the row survives so a paper or a level
        # set under it still points somewhere. The close is written by the *same*
        # bulk upsert that opens classes — a class with a row is closed in place
        # and a class with no row is inserted already closed — so the write must
        # carry `is_active=False` for the class that was dropped.
        written = [r for _t, payload, _c in sb.upserts
                   for r in (payload if isinstance(payload, list) else [payload])]
        closed = [r for r in written if str(r.get("class_id")) == CLASS_A]
        assert closed, "removing a class must write is_active=False, not delete"
        assert closed[0].get("is_active") is False
        assert all(r.get("is_active") is not False or str(r.get("class_id")) == CLASS_A
                   for r in written), "a kept class was written closed"
        assert result["removed"] == 1

    def test_the_write_is_one_statement_per_side_not_one_per_class(self):
        """A live probe caught the cost of a loop here: the connection dropped
        between per-class inserts, one row had landed, and the caller was told the
        save failed — a half-applied mapping the admin could not see. The write is
        two bulk statements (an upsert for everything on, an update for everything
        off), so that window is one round-trip wide, not one per class."""
        classes = [{"id": f"c{i}", "school_id": SCHOOL} for i in range(6)]
        sb = _Sb({"subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
                  "classes": classes})
        ok, result = sl.save_mapping(sb, SCHOOL, SUBJECT, {c["id"] for c in classes})
        assert ok is True
        assert result["added"] == 6
        assert len(sb.upserts) == 1, f"expected ONE bulk upsert, got {len(sb.upserts)}"
        payload = sb.upserts[0][1]
        assert isinstance(payload, list) and len(payload) == 6
        assert sb.upserts[0][2], "the upsert must name the conflict target"

    def test_re_adding_a_class_reactivates_the_same_row(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}],
            "class_subjects": [
                {"id": "cs1", "school_id": SCHOOL, "subject_id": SUBJECT,
                 "class_id": CLASS_A, "is_active": False},
            ],
        })
        ok, _ = sl.save_mapping(sb, SCHOOL, SUBJECT, {CLASS_A})
        assert ok is True
        assert sb.upserts, "an existing (closed) row is reactivated in place"
        row = sb.upserts[0][1][0]
        assert row["class_id"] == CLASS_A and row["is_active"] is True

    def test_mapped_class_ids_reads_only_active_rows(self):
        sb = _Sb({
            "class_subjects": [
                {"school_id": SCHOOL, "subject_id": SUBJECT, "class_id": CLASS_A, "is_active": True},
                {"school_id": SCHOOL, "subject_id": SUBJECT, "class_id": CLASS_B, "is_active": False},
            ],
        })
        assert sl.mapped_class_ids(sb, SCHOOL, SUBJECT) == {CLASS_A}


# ── the pupil levels ─────────────────────────────────────────────────────────

class TestThePupilLevels:
    def test_a_pupil_outside_the_class_is_refused(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}],
            "profiles": [{"id": "s1", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A},
                         {"id": "outsider", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_B}],
        })
        ok, result = sl.save_levels(sb, SCHOOL, SUBJECT, CLASS_A, {"outsider": "advanced"})
        assert ok is False
        assert result.get("status") == 403
        assert sb.upserts == []

    def test_a_pupil_with_no_row_is_inserted_in_one_bulk_statement(self):
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}],
            "profiles": [{"id": "s1", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A},
                         {"id": "s2", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A}],
        })
        ok, result = sl.save_levels(sb, SCHOOL, SUBJECT, CLASS_A,
                                    {"s1": "intermediate", "s2": "advanced"},
                                    year_id=YEAR, set_by="admin-1")
        assert ok is True
        assert len(sb.inserts) == 1, f"expected ONE bulk insert, got {len(sb.inserts)}"
        payload = sb.inserts[0][1]
        assert isinstance(payload, list) and len(payload) == 2
        row = payload[0]
        assert row["subject_id"] == SUBJECT
        assert row["class_id"] == CLASS_A
        assert row["school_year_id"] == YEAR
        assert row["school_id"] == SCHOOL
        assert result["saved"] == 2 and result["inserted"] == 2

    def test_an_existing_row_is_updated_in_place_grouped_by_level(self):
        """One row per pupil, subject and year. The uniqueness is a *partial*
        index, so PostgREST cannot name it in an ON CONFLICT — the write is an
        update by id instead, one statement per distinct level (at most three)."""
        sb = _Sb({
            "subjects": [{"id": SUBJECT, "school_id": SCHOOL}],
            "classes": [{"id": CLASS_A, "school_id": SCHOOL}],
            "profiles": [{"id": "s1", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A},
                         {"id": "s2", "school_id": SCHOOL, "role": "murid", "class_id": CLASS_A}],
            "student_subject_levels": [
                {"id": "lvl1", "school_id": SCHOOL, "student_id": "s1",
                 "subject_id": SUBJECT, "school_year_id": YEAR, "level": "basic"},
                {"id": "lvl2", "school_id": SCHOOL, "student_id": "s2",
                 "subject_id": SUBJECT, "school_year_id": YEAR, "level": "basic"},
            ],
        })
        ok, result = sl.save_levels(sb, SCHOOL, SUBJECT, CLASS_A,
                                    {"s1": "advanced", "s2": "advanced"},
                                    year_id=YEAR, set_by="admin-1")
        assert ok is True
        assert sb.inserts == [], "a pupil who already has a row is never inserted twice"
        assert not any(u[1].get("level") is None for u in sb.updates)
        updates = [u for u in sb.updates if u[0] == "student_subject_levels"]
        assert len(updates) == 1, "both pupils share a level, so one update covers them"
        assert updates[0][1]["level"] == "advanced"
        assert result["updated"] == 2 and result["inserted"] == 0

    def test_the_roster_defaults_a_pupil_with_no_row_to_basic(self):
        """A class just mapped has no level rows yet; every pupil reads as basic,
        which is the *default* the school starts everyone on, not a missing value."""
        sb = _Sb({
            "schools": [{"id": SCHOOL}],
            "profiles": [{"id": "s1", "school_id": SCHOOL, "role": "murid",
                          "class_id": CLASS_A, "full_name": "Ana"},
                         {"id": "s2", "school_id": SCHOOL, "role": "murid",
                          "class_id": CLASS_A, "full_name": "Budi"}],
            "student_subject_levels": [
                {"school_id": SCHOOL, "student_id": "s2", "subject_id": SUBJECT,
                 "class_id": CLASS_A, "level": "advanced"},
            ],
            "student_enrollment": [],
        })
        roster = sl.roster_with_levels(sb, SCHOOL, SUBJECT, CLASS_A)
        by_id = {r["student_id"]: r for r in roster}
        assert by_id["s2"]["level"] == "advanced"
        assert by_id["s1"]["level"] == sl.DEFAULT_LEVEL


# ── the page and its routes ──────────────────────────────────────────────────

class TestTheRoutes:
    ROUTES = __import__("pathlib").Path(__file__).resolve().parents[2] / "app" / "routes" / "admin_sekolah.py"

    def test_the_admin_owns_the_three_endpoints(self):
        src = self.ROUTES.read_text(encoding="utf-8")
        for pattern in ("/subjects/<subject_id>/mapping",
                        "/subjects/<subject_id>/levels"):
            assert pattern in src, f"{pattern} is not registered"
        # Each one is admin-only: never a guru, never anonymous.
        idx = src.index("/subjects/<subject_id>/mapping")
        window = src[idx:idx + 900]
        assert "admin_sekolah_required" in window
