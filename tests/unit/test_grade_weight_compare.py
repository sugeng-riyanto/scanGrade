"""The weight preview can compare one seeded pupil across several subjects.

`test_grade_weight_seed.py` pinned the seed: a real learner's own component means
fill the sample, so the previewed number is the roster's number. A distribution,
though, is not changed for one subject at a time — the school default covers every
subject that never saved its own row, so the question that decides a save is
whether the change is fair *between the subjects a learner actually sits*.

This file pins that comparison end to end, in the four places it can go wrong:

* **the read** (`grade_weighting.pupil_subject_finals`) — every subject the pupil
  has a *scored* paper in, each with the mark the roster computes for it. A
  subject with no mark is absent rather than 0, an inactive subject is not
  offered, another year's paper does not count, and another school is not read.
* **the arithmetic's input** — each row carries the pupil's own per-component
  means, which is what lets the page re-run the rule under the weights *being
  typed*. The weights are read the **roster's** way, not the pupil's own card's:
  an admin comparing subjects is comparing what the school's tables report.
* **the agreement** — what the page computes from those means under a typed
  distribution must equal what the server computes for the same pupil, which is
  the only thing that makes the comparison trustworthy.
* **the wiring** — the card asks for the comparison once per pupil (it does not
  depend on the selected subject), clearing the seed empties it, and the live
  column refuses to invent a number when the typed weights are not a
  distribution.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import grade_weighting as gw
from app.utils import breadcrumbs
from tests.unit.test_grade_weight_seed import _page_final, needs_node
from tests.unit.test_invigilation import _DB

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

SCHOOL, OTHER = "s1", "s2"
YEAR, LAST_YEAR = "y1", "y0"

#: The school default: Tugas 40%, UTS 60%.
DEFAULT = {"gc1": 40, "gc2": 60}


def _tables() -> dict:
    """A school with a learner, a retired subject, and papers that mislead.

    The marks are chosen so every figure below is checkable by hand:

    * Fisika follows the **school default** (gc1 40, gc2 60): gc1 mean 80 (e1),
      gc2 mean 70 (e2) — 80 × 40% + 70 × 60% = 32 + 42 = **74.0**;
    * Biologi has its **own** row (gc1 100%): 90 × 100% = **90.0**;
    * Kimia is **inactive**, so it is not a subject this page can weight;
    * e5 is the same subject in **last year** (100), e7 is filed under **no
      component** (100), and e9 belongs to another school.
    """
    return {
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Wijaya"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Alumni"},
            {"id": "p9", "school_id": OTHER, "role": "murid", "full_name": "Ana Negeri Lain"},
        ],
        "schools": [{"id": SCHOOL}, {"id": OTHER}],
        "classes": [{"id": "c1", "school_id": SCHOOL, "name": "10A"}],
        "students": [
            {"id": "p1", "school_id": SCHOOL, "class_id": "c1", "status": "active",
             "profiles": {"full_name": "Ana Wijaya"}, "classes": {"name": "10A"}},
            {"id": "p3", "school_id": SCHOOL, "class_id": "c1", "status": "alumni",
             "profiles": {"full_name": "Ana Alumni"}, "classes": {"name": "10A"}},
            {"id": "p9", "school_id": OTHER, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Ana Negeri Lain"}, "classes": {"name": "9Z"}},
        ],
        "subjects": [
            {"id": "su1", "school_id": SCHOOL, "name": "Fisika", "is_active": True},
            {"id": "su2", "school_id": SCHOOL, "name": "Biologi", "is_active": True},
            {"id": "su3", "school_id": SCHOOL, "name": "Kimia", "is_active": False},
            {"id": "su9", "school_id": OTHER, "name": "Fisika", "is_active": True},
        ],
        "grade_component_type": [
            {"id": "gc1", "school_id": SCHOOL, "name": "Tugas", "is_active": True,
             "sort_order": 1, "default_weight": 40},
            {"id": "gc2", "school_id": SCHOOL, "name": "UTS", "is_active": True,
             "sort_order": 2, "default_weight": 60},
        ],
        # Biologi is the one subject with a deliberate override.
        "grade_weight_config": [
            {"id": "w1", "school_id": SCHOOL, "subject_id": "su2", "school_year_id": YEAR,
             "component_id": "gc1", "weight_percent": 100, "is_active": True},
        ],
        "exams": [
            {"id": "e1", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e2", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": YEAR,
             "grade_component_type_id": "gc2"},
            {"id": "e3", "school_id": SCHOOL, "subject_id": "su2", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e4", "school_id": SCHOOL, "subject_id": "su3", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e5", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": LAST_YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "e7", "school_id": SCHOOL, "subject_id": "su1", "school_year_id": YEAR,
             "grade_component_type_id": None},
            {"id": "e9", "school_id": OTHER, "subject_id": "su9", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
        ],
        "submissions": [
            {"id": "b1", "exam_id": "e1", "student_id": "p1", "score": 80,
             "is_published": True, "status": "published"},
            # e2 is marked but NOT released: the roster counts it, the pupil's own
            # card does not, and this page follows the roster.
            {"id": "b2", "exam_id": "e2", "student_id": "p1", "final_score": 70,
             "is_published": False, "status": "submitted"},
            {"id": "b3", "exam_id": "e3", "student_id": "p1", "score": 90,
             "is_published": True, "status": "published"},
            {"id": "b4", "exam_id": "e4", "student_id": "p1", "score": 50,
             "is_published": True, "status": "published"},
            {"id": "b5", "exam_id": "e5", "student_id": "p1", "score": 100,
             "is_published": True, "status": "published"},
            {"id": "b7", "exam_id": "e7", "student_id": "p1", "score": 100,
             "is_published": True, "status": "published"},
            {"id": "b8", "exam_id": "e1", "student_id": "p1"},          # sat, not marked
            {"id": "b9", "exam_id": "e9", "student_id": "p9", "score": 100,
             "is_published": True, "status": "published"},
        ],
    }


def _rows_by_subject(subjects: list[dict]) -> dict:
    return {row["subject_id"]: row for row in subjects}


# ── the read: the learner across their subjects ──────────────────────────────

class TestTheCompareRead:
    def test_it_lists_every_subject_the_pupil_has_a_mark_in(self):
        out = gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)
        assert out is not None and out["pupil"]["name"] == "Ana Wijaya"
        assert out["pupil"]["class_name"] == "10A"
        assert [r["name"] for r in out["subjects"]] == ["Biologi", "Fisika"], (
            "the comparison must list the subjects this learner has marks in, in "
            "name order — it compares, it does not rank")

    def test_each_row_carries_the_mark_the_roster_computes(self):
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        # Fisika follows the default (40/60): 80 × 40% + 70 × 60% = 74.0.
        assert rows["su1"]["final"] == 74.0, "the default distribution was not applied"
        assert rows["su1"]["mode"] == "weighted"
        assert rows["su1"]["total"] == 100
        # Biologi has its own row (gc1 100%): 90 × 100% = 90.0.
        assert rows["su2"]["final"] == 90.0, "a subject's own weights were not used"
        assert rows["su2"]["total"] == 100

    def test_the_mark_is_the_one_finals_for_student_reports_for_the_roster(self):
        """One arithmetic: the comparison cannot disagree with the teacher's table."""
        batch = gw.finals_for_student(_DB(_tables()), SCHOOL, ["su1", "su2"], YEAR, "p1",
                                      released_only=False)
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["final"] == batch["su1"]["final"]
        assert rows["su2"]["final"] == batch["su2"]["final"]

    def test_an_unreleased_paper_counts_here_and_not_on_the_pupils_own_card(self):
        """The deliberate difference, pinned: the roster's numbers, not the pupil's."""
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["final"] == 74.0, (
            "a marked-but-unreleased paper was dropped: this read is the roster's")
        own = gw.finals_for_student(_DB(_tables()), SCHOOL, ["su1"], YEAR, "p1",
                                    released_only=True)
        # Released only: 80 × 40% + 0 (no released UTS) = 32.0.
        assert own["su1"]["final"] == 32.0, (
            "the pupil's own released-only rule changed; the two reads must differ "
            "for the stated reason and no other")

    def test_the_pupils_own_component_means_come_with_the_row(self):
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["marks"] == {"gc1": 80.0, "gc2": 70.0}, (
            "the page cannot re-run the rule under other weights without these")
        assert rows["su2"]["marks"] == {"gc1": 90.0}

    def test_those_means_are_the_ones_the_seed_read_gives(self):
        """Same numbers as the single-subject seed, so the two views agree.

        The **means** must match exactly — that is what lets the page re-run the
        same rule. The paper *count* is deliberately not the same: the seed read
        answers "what can be placed in a component", while this row also counts
        the uncategorised paper it reports in ``untagged``.
        """
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        seed = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "su1", YEAR, "p1")
        assert rows["su1"]["marks"] == seed["marks"]
        assert seed["scored"] == 2, "the seed read places only the tagged papers"
        assert rows["su1"]["scored"] == 3 == seed["scored"] + rows["su1"]["untagged"]

    def test_a_paper_with_no_component_is_reported_not_hidden(self):
        """e7 (100) cannot reach a weighted mark; the row must say so."""
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["untagged"] == 1, (
            "an uncategorised paper was folded silently into the comparison")
        assert rows["su1"]["scored"] == 3

    def test_a_subject_the_pupil_has_no_mark_in_is_absent(self):
        out = gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)
        rows = _rows_by_subject(out["subjects"])
        assert "su3" not in rows, "an inactive subject was offered for comparison"

    def test_a_subject_with_no_scored_paper_is_not_a_row(self):
        tables = _tables()
        tables["submissions"] = [s for s in tables["submissions"]
                                 if s["exam_id"] not in ("e1", "e2", "e7")]
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(tables), SCHOOL, "p1", YEAR)["subjects"])
        assert "su1" not in rows, (
            "a subject with nothing marked came back with an empty row, which reads as a zero")
        assert [r["name"] for r in gw.pupil_subject_finals(_DB(tables), SCHOOL, "p1", YEAR)["subjects"]] \
            == ["Biologi"]

    def test_an_unmarked_sitting_is_not_a_mark(self):
        """b8 (e1, no score) must not drag gc1's mean down to 40."""
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["marks"]["gc1"] == 80.0

    def test_last_years_paper_does_not_count_this_year(self):
        """e5 (100, last year) would make gc1 90 and the final 78."""
        rows = _rows_by_subject(gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)["subjects"])
        assert rows["su1"]["final"] == 74.0, "another year's paper was counted this year"

    def test_another_schools_pupil_is_refused_not_answered_empty(self):
        assert gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p9", YEAR) is None, (
            "a foreign pupil answered as if they were ours")
        assert gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p3", YEAR) is None, (
            "an alumni row was compared for the running year")

    def test_empty_ids_are_never_a_question(self):
        db = _DB(_tables())
        assert gw.pupil_subject_finals(db, "", "p1", YEAR) is None
        assert gw.pupil_subject_finals(db, SCHOOL, "", YEAR) is None

    def test_the_pupil_with_no_year_still_compares(self):
        """No running year widens to the subject's whole history, the caller's call."""
        out = gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", None)
        rows = _rows_by_subject(out["subjects"])
        # gc1 becomes (80 + 100) / 2 = 90 with last year included: 90 × 40% + 70 × 60%
        # = 36 + 42 = 78.0.
        assert rows["su1"]["final"] == 78.0

    def test_the_subject_read_is_bounded_to_this_schools_active_subjects(self):
        src = (ROOT / "app" / "services" / "grade_weighting.py").read_text(encoding="utf-8")
        body = src.split("def pupil_subject_finals(")[1].split("\ndef ")[0]
        assert 'table("subjects")' in body and 'eq("school_id", school)' in body, (
            "the subject list is not bounded by the school")
        assert 'eq("is_active", True)' in body, (
            "a retired subject would be compared against no weights the admin has")
        assert "finals_for_student(" in body and "released_only=False" in body, (
            "the rows must come from the roster's own read, not a second arithmetic")
        assert "compute(" not in body, (
            "a second arithmetic was written instead of reusing finals_for_student")


# ── the agreement: the page's live column is the roster's arithmetic ─────────

class TestTheLiveColumnAgreesWithTheServer:
    """The identity that makes the payload sufficient: a component's mean is the
    single input ``compute`` needs for that component, so the page's rule over
    ``marks`` is the server's rule over the papers — under *any* weights."""

    def _rows(self):
        out = gw.pupil_subject_finals(_DB(_tables()), SCHOOL, "p1", YEAR)
        return _rows_by_subject(out["subjects"])

    @needs_node
    def test_a_changed_default_moves_only_the_subject_that_follows_it(self):
        """The fairness question, with the arithmetic verified by hand."""
        rows = self._rows()
        changed = {"gc1": 60, "gc2": 40}                     # the default, retyped
        typed = {"su1": changed, "su2": {"gc1": 100}}        # Biologi keeps its row
        for sid in ("su1", "su2"):
            server = gw.compute([(cid, m) for cid, m in rows[sid]["marks"].items()],
                                typed[sid])["final"]
            page = _page_final(typed[sid], rows[sid]["marks"])["final"]
            assert page == server, (
                f"the page and the server disagree for {sid}: {page} != {server}")
        # Fisika: 80 × 60% + 70 × 40% = 48 + 28 = 76.0 (was 74.0).
        assert gw.compute([(c, m) for c, m in rows["su1"]["marks"].items()],
                          changed)["final"] == 76.0
        assert _page_final(changed, rows["su1"]["marks"])["final"] == 76.0, (
            "the page does not re-run the roster's rule under the typed default")
        # Biologi: its own row is untouched, so it stays at 90.0.
        assert _page_final({"gc1": 100}, rows["su2"]["marks"])["final"] == 90.0

    @needs_node
    def test_the_saved_figure_is_the_same_rule_under_the_saved_weights(self):
        rows = self._rows()
        saved = {"su1": DEFAULT, "su2": {"gc1": 100}}
        for sid in ("su1", "su2"):
            assert _page_final(saved[sid], rows[sid]["marks"])["final"] == rows[sid]["final"], (
                f"the page cannot reproduce the saved mark for {sid}")

    @needs_node
    def test_a_component_the_pupil_has_no_mark_in_is_still_zero(self):
        """Biologi has no UTS mark: 90 × 40% + 0 × 60% = 36.0, not a renormalised 90."""
        rows = self._rows()
        assert _page_final(DEFAULT, rows["su2"]["marks"])["final"] == 36.0, (
            "the page renormalised a missing component instead of counting it as zero")
        assert gw.compute([(c, m) for c, m in rows["su2"]["marks"].items()],
                          DEFAULT)["final"] == 36.0


# ── the door ────────────────────────────────────────────────────────────────

def _body(name: str) -> str:
    src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]


class TestTheDoor:
    def test_the_compare_door_is_admin_only_and_scopes_by_the_session(self):
        body = _body("admin_grade_pupil_subjects")
        assert "@admin_sekolah_required" in body, "the comparison is not admin-only"
        assert "_school_id()" in body, "the school must come from the session"
        assert "pupil_subject_finals(" in body
        assert 'request.args.get("school' not in body, (
            "the school must never be a value the query can set")
        assert "/grade-weights/pupil-subjects" in ADMIN_SCHOOL.read_text(encoding="utf-8-sig"), (
            "the door is not reachable at the path the page asks for")

    def test_the_compare_door_refuses_a_pupil_that_is_not_this_schools(self):
        body = _body("admin_grade_pupil_subjects")
        assert "if not found" in body and "404" in body, (
            "a foreign pupil must be a refusal, not a quiet empty comparison")

    def test_the_compare_door_is_a_read(self):
        body = _body("admin_grade_pupil_subjects")
        for writer in (".insert(", ".update(", ".delete("):
            assert writer not in body, (
                f"the compare door writes ({writer}): a comparison must never save")

    def test_the_compare_door_uses_the_running_year(self):
        body = _body("admin_grade_pupil_subjects")
        assert "active_school_year(" in body and "year.get(\"id\")" in body, (
            "the comparison would mix every year of the school's papers")

    def test_the_json_readback_is_not_a_page_of_its_own(self):
        assert "pupil-subjects" in breadcrumbs.MACHINE, (
            "the JSON readback would appear in the trail as if it were a page")


# ── the page ─────────────────────────────────────────────────────────────────

def _compare_block() -> str:
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    assert "data-preview-subjects" in html, (
        "the card cannot show the learner across their subjects")
    return html.split("data-preview-subjects", 1)[1].split("</table>", 1)[0]


class TestThePageIsWired:
    def test_the_card_shows_saved_and_typed_side_by_side(self):
        block = _compare_block()
        for pair in ("t('Mapel','Subject')", "t('Tersimpan','Saved')",
                     "t('Dengan bobot ini','With these weights')",
                     "t('Selisih','Change')"):
            assert pair in block, f"the comparison has no {pair} column"
        assert "pupilRowLiveText(row)" in block, "the typed column is not drawn"
        assert "pupilRowDeltaText(row)" in block, "the change is not drawn"
        assert "row.final" in block, "the saved column does not read the server's figure"

    def test_the_change_column_is_coloured_by_direction(self):
        block = _compare_block()
        assert "pupilRowDeltaClass(row)" in block

    def test_the_seed_reads_the_compare_door_and_still_never_posts(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("loadPupilSubjects(studentId) {", 1)[1].split("\n        },", 1)[0]
        assert "/admin-sekolah/grade-weights/pupil-subjects" in body
        assert "student_id=" in body, "the door is asked without naming the pupil"
        assert "post(" not in body, "the comparison writes to the server; it must only read"

    def test_the_comparison_is_read_once_per_pupil_not_per_subject(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        seed = html.split("seedFrom(pupil) {", 1)[1].split("\n        },", 1)[0]
        assert "loadPupilSubjects(pupil.id)" in seed, (
            "seeding a pupil does not load the across-subjects comparison")
        change = html.split("onPreviewSubjectChange() {", 1)[1].split("\n        },", 1)[0]
        assert "loadPupilSubjects(" not in change, (
            "changing the subject re-reads a comparison that does not depend on it")

    def test_clearing_the_seed_empties_the_comparison(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("clearSeed() {", 1)[1].split("\n        },", 1)[0]
        assert "this.pupilSubjects = []" in body and "this.pupilSubjectsNote = ''" in body, (
            "the previous learner's subjects would stay on screen after clearing")

    def test_the_live_column_refuses_a_state_that_is_not_a_distribution(self):
        """No weights = the fallback mean over papers, which this payload cannot
        reproduce; the cell must say nothing rather than the mean of means."""
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("pupilRowLive(row) {", 1)[1].split("\n        },", 1)[0]
        assert "sgPreviewFinal(" in body and "mode === 'weighted'" in body, (
            "the typed column would print the mean of component means as if it were "
            "the roster's fallback mean")
        assert "marks" in body, "the typed column does not use the learner's own means"

    def test_the_typed_column_uses_the_row_s_own_weights_then_the_default(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("pupilRowTyped(row) {", 1)[1].split("\n        },", 1)[0]
        assert "this.weights[row.subject_id]" in body and "this.defaults" in body, (
            "a subject that follows the school default would not follow it here")

    def test_the_rows_keep_the_servers_order(self):
        block = _compare_block()
        assert 'x-for="row in pupilSubjects"' in block, (
            "the comparison does not iterate the rows the server ordered")
        assert ".sort(" not in block, (
            "the page re-sorts the comparison, which turns a comparison into a ranking")

    def test_an_uncategorised_paper_is_flagged_on_its_row(self):
        block = _compare_block()
        assert "row.untagged" in block and "tanpa kategori" in block, (
            "a paper that cannot reach the weighted mark is silently ignored")

    def test_the_comparison_copy_is_bilingual(self):
        block = _compare_block()
        assert re.search(r"t\('[^']+','[^']+'\)", block), (
            "the comparison carries no bilingual pair, so its copy cannot be translated")
        for word in ("Saved", "With these weights", "Change",
                     "This pupil across subjects"):
            assert f"'{word}'" in block, f"the comparison copy lost its English '{word}'"
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        delta = html.split("pupilRowDeltaText(row) {", 1)[1].split("\n        },", 1)[0]
        assert "t('tetap','unchanged')" in delta, (
            "the unchanged cell's copy is not bilingual")
