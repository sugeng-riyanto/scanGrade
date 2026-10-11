"""The weight preview can be seeded from a real pupil's record, not only invented.

The preview's arithmetic was already guarded (`test_grade_weight_preview.py`). What
it could not answer is the question an admin actually has while changing a policy:
*what does this change do to a learner I can name?* A sample of 80/90 shows the
mechanism; it does not show the effect on a record, and the two are different
things when a pupil has no mark in one component.

This file pins the seed end to end, in the four places it can go wrong:

* **the read** (`grade_weighting.pupil_component_marks`, `find_pupils`) — one
  pupil's own component means, through this school's exams for this subject and
  year. A paper of another subject, another year, another school, or with no
  component must not be counted, and a component the pupil has no mark in must be
  *absent* rather than 0 — so the preview applies the module's own
  "missing component is zero" policy instead of one this read invented.
* **the scope** — both service reads take the school as a required argument and a
  filter, and the doors in `admin_sekolah.py` check the pupil and the subject
  against the caller's own school before reading anything, so neither id in the
  URL can widen the scope.
* **the default's own seed** (`sgPickSpreadSubject`) — the school default has no
  subject of its own, so the learner's **spread** chooses which subject the sample
  is read from, and the page says which. The choice is weight-aware, because a
  subject whose marks share none of the weights on screen would preview a mark
  built entirely from the "missing component is zero" policy; a tie keeps the
  server's name order so the choice is reproducible.
* **the agreement** — what the page computes from a seeded sample must equal what
  the server computes for that pupil, which is the whole point of the preview.
* **the wiring** — the card can be seeded, seeding is a read (never a POST), a
  hand-edited sample marks the badge *edited* rather than leaving it claiming the
  record, the default's seed reuses the one spread read the comparison table makes,
  and changing the subject re-reads rather than leaving another subject's marks
  under this subject's weights.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import grade_weighting as gw
from tests.unit.test_invigilation import _DB

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

SCHOOL, OTHER = "s1", "s2"
YEAR, LAST_YEAR = "y1", "y0"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own rule")

PREVIEW = re.compile(r"function sgPreviewFinal\([^)]*\) \{(.*?)\r?\n\}", re.S)
SPREAD = re.compile(r"function sgPickSpreadSubject\([^)]*\) \{(.*?)\r?\n\}", re.S)


def _tables() -> dict:
    """One school with three pupils, a teacher, and papers that mislead if read wrong."""
    return {
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Wijaya"},
            {"id": "p2", "school_id": SCHOOL, "role": "murid", "full_name": "Budi Santoso"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "full_name": "Ana Alumni"},
            {"id": "t1", "school_id": SCHOOL, "role": "guru", "full_name": "Ana Guru"},
            {"id": "p9", "school_id": OTHER, "role": "murid", "full_name": "Ana Negeri Lain"},
        ],
        "classes": [{"id": "c1", "school_id": SCHOOL, "name": "7A"}],
        "students": [
            {"id": "p1", "school_id": SCHOOL, "class_id": "c1", "status": "active",
             "profiles": {"full_name": "Ana Wijaya"}, "classes": {"name": "7A"}},
            {"id": "p2", "school_id": SCHOOL, "class_id": "c1", "status": "active",
             "profiles": {"full_name": "Budi Santoso"}, "classes": {"name": "7A"}},
            {"id": "p3", "school_id": SCHOOL, "class_id": "c1", "status": "alumni",
             "profiles": {"full_name": "Ana Alumni"}, "classes": {"name": "7A"}},
            {"id": "p9", "school_id": OTHER, "class_id": "c9", "status": "active",
             "profiles": {"full_name": "Ana Negeri Lain"}, "classes": {"name": "9Z"}},
        ],
        "subjects": [
            {"id": "s1", "school_id": SCHOOL, "name": "Fisika", "is_active": True},
            {"id": "s2", "school_id": SCHOOL, "name": "Biologi", "is_active": True},
            {"id": "s9", "school_id": OTHER, "name": "Fisika", "is_active": True},
        ],
        # ex1/ex2 share gc1 (two papers, one component mean); ex3 is gc2;
        # ex4 is this subject in another year; ex5 is another school; ex6 is the
        # other subject; ex7 is uncategorised.
        "exams": [
            {"id": "ex1", "school_id": SCHOOL, "subject_id": "s1", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "ex2", "school_id": SCHOOL, "subject_id": "s1", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "ex3", "school_id": SCHOOL, "subject_id": "s1", "school_year_id": YEAR,
             "grade_component_type_id": "gc2"},
            {"id": "ex4", "school_id": SCHOOL, "subject_id": "s1", "school_year_id": LAST_YEAR,
             "grade_component_type_id": "gc2"},
            {"id": "ex5", "school_id": OTHER, "subject_id": "s9", "school_year_id": YEAR,
             "grade_component_type_id": "gc1"},
            {"id": "ex6", "school_id": SCHOOL, "subject_id": "s2", "school_year_id": YEAR,
             "grade_component_type_id": "gc3"},
            {"id": "ex7", "school_id": SCHOOL, "subject_id": "s1", "school_year_id": YEAR,
             "grade_component_type_id": None},
        ],
        "submissions": [
            {"id": "b1", "exam_id": "ex1", "student_id": "p1", "score": 80},
            {"id": "b2", "exam_id": "ex2", "student_id": "p1", "final_score": 60},
            {"id": "b3", "exam_id": "ex3", "student_id": "p1", "score": 90},
            {"id": "b4", "exam_id": "ex4", "student_id": "p1", "score": 100},
            {"id": "b5", "exam_id": "ex5", "student_id": "p1", "score": 100},
            {"id": "b6", "exam_id": "ex6", "student_id": "p1", "score": 100},
            {"id": "b7", "exam_id": "ex7", "student_id": "p1", "score": 100},
            {"id": "b8", "exam_id": "ex1", "student_id": "p1"},          # sat, not marked
            {"id": "b10", "exam_id": "ex3", "student_id": "p2", "score": 20},
        ],
    }


# ── the read: one pupil's own marks ──────────────────────────────────────────

class TestTheSeedRead:
    def test_it_reads_the_pupils_own_component_means(self):
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        assert out is not None
        # ex1 80 and ex2 60 are both gc1: the component mean is 70, not the mean of
        # everything. gc2 is 90 from a single paper.
        assert out["marks"] == {"gc1": 70.0, "gc2": 90.0}
        assert out["scored"] == 3, "only this pupil's scored papers may be counted"
        assert out["pupil"]["name"] == "Ana Wijaya"
        assert out["pupil"]["class_name"] == "7A"

    def test_a_paper_of_another_subject_is_not_counted(self):
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        assert "gc3" not in out["marks"], "another subject's paper leaked into this subject"

    def test_a_paper_of_another_year_is_not_counted(self):
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        assert max(out["marks"].values()) == 90.0, "last year's paper was counted this year"

    def test_an_uncategorised_paper_cannot_be_placed(self):
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        assert set(out["marks"]) == {"gc1", "gc2"}, (
            "a paper filed under no component has no component to seed")

    def test_an_unmarked_sitting_is_not_a_zero(self):
        """b8 is a sitting with no score; it must not drag gc1's mean down."""
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        assert out["marks"]["gc1"] == 70.0

    def test_a_component_with_no_mark_stays_out(self):
        """Absent, not 0 — so the preview applies the module's own zero policy."""
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p2")
        assert "gc1" not in out["marks"], (
            "a component the pupil has no mark in must be left out, not filled with 0")
        assert out["marks"] == {"gc2": 20.0}
        assert out["scored"] == 1

    def test_no_year_widens_to_every_year_of_the_subject(self):
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", None, "p1")
        assert out["marks"]["gc2"] == 95.0, (
            "with no running year the subject is read whole; the caller decides the scope")

    def test_a_pupil_of_another_school_is_not_found(self):
        assert gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p9") is None, (
            "another school's pupil answered as if they were ours")

    def test_an_inactive_pupil_is_not_offered(self):
        assert gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p3") is None

    def test_an_empty_id_is_never_a_question(self):
        db = _DB(_tables())
        assert gw.pupil_component_marks(db, "", "s1", YEAR, "p1") is None
        assert gw.pupil_component_marks(db, SCHOOL, "", YEAR, "p1") is None
        assert gw.pupil_component_marks(db, SCHOOL, "s1", YEAR, "") is None

    def test_a_subject_with_no_exams_is_an_empty_sample(self):
        """Not a failure: a subject with no paper yet simply has nothing to seed."""
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s2", YEAR, "p2")
        assert out["marks"] == {} and out["scored"] == 0


# ── the read: the picker ─────────────────────────────────────────────────────

class TestThePupilPickerRead:
    def test_it_finds_this_schools_pupils_by_name(self):
        found = gw.find_pupils(_DB(_tables()), SCHOOL, "Ana")
        names = [p["name"] for p in found]
        assert names == ["Ana Wijaya"], (
            "the search must find our pupil and nothing that is not one")

    def test_a_partial_name_is_enough_and_the_class_comes_along(self):
        found = gw.find_pupils(_DB(_tables()), SCHOOL, "wija")
        assert [(p["name"], p["class_name"]) for p in found] == [("Ana Wijaya", "7A")]

    def test_another_schools_pupil_does_not_leak(self):
        found = gw.find_pupils(_DB(_tables()), SCHOOL, "Ana")
        assert all(p["id"] != "p9" for p in found), (
            "a name that matches another school's pupil returned that pupil")

    def test_a_teacher_is_not_a_pupil(self):
        found = gw.find_pupils(_DB(_tables()), SCHOOL, "Guru")
        assert found == [], "a teacher was offered as a learner to preview against"

    def test_an_inactive_pupil_is_not_offered(self):
        found = gw.find_pupils(_DB(_tables()), SCHOOL, "Alumni")
        assert found == [], "an alumni row was offered for the running year"

    def test_a_short_query_finds_nothing(self):
        db = _DB(_tables())
        assert gw.find_pupils(db, SCHOOL, "") == [], (
            "an empty query answered with the roster: that is a dump, not a lookup")
        assert gw.find_pupils(db, SCHOOL, "a") == []

    def test_the_school_is_a_required_argument(self):
        assert gw.find_pupils(_DB(_tables()), "", "Ana") == []

    def test_the_limit_is_respected(self):
        db = _DB(_tables())
        db.tables["profiles"].append({"id": "p4", "school_id": SCHOOL, "role": "murid",
                                      "full_name": "Ana Wijaya Kedua"})
        db.tables["students"].append({"id": "p4", "school_id": SCHOOL, "status": "active",
                                      "classes": {"name": "7A"}})
        assert len(gw.find_pupils(db, SCHOOL, "Ana", limit=1)) == 1


# ── the agreement: the seeded sample and the server compute the same mark ────

def _page_final(weights: dict, marks: dict) -> dict:
    """Run the page's own preview rule over one sample, through node."""
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    match = PREVIEW.search(html)
    assert match, "the page no longer defines the preview's arithmetic"
    script = (
        "const src = " + json.dumps(match.group(0)) + ";\n"
        "eval(src);\n"
        "console.log(JSON.stringify(sgPreviewFinal(" + json.dumps(weights) + ", "
        + json.dumps(marks) + ")));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


# ── the school default's seed: the learner's own spread picks the subject ────

def _spread_function() -> str:
    """The page's own spread rule, read out of the template."""
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    match = SPREAD.search(html)
    assert match, "the page no longer defines the spread rule"
    return match.group(0)


def _page_spread(weights: dict, subjects: list) -> dict | None:
    """Run the page's own spread rule over one learner's rows, through node."""
    script = (
        "const src = " + json.dumps(_spread_function()) + ";\n"
        "eval(src);\n"
        "console.log(JSON.stringify(sgPickSpreadSubject(" + json.dumps(weights) + ", "
        + json.dumps(subjects) + ")));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _row(name: str, marks: dict, subject_id: str = None) -> dict:
    return {"subject_id": subject_id or name.lower(), "name": name, "marks": marks}


class TestTheSpreadPicksTheSubject:
    """The school default has no subject of its own, so the spread chooses one.

    Marks belong to a subject. Rather than making the admin pick one (which is
    what the page used to demand), the learner's own spread decides — and the
    page then names the subject the sample came from. The rule is deliberately
    *weight-aware*: every row carries the same learner's component means, but the
    weights on screen name component ids, and a subject whose marks share none of
    them would preview a mark built entirely from the "missing component is
    zero" policy rather than from anything the learner actually sat.
    """

    @needs_node
    def test_the_subject_covering_the_most_weighted_components_wins(self):
        weights = {"gc1": 40, "gc2": 60}
        subjects = [_row("Biologi", {"gc3": 70}),
                    _row("Fisika", {"gc1": 80, "gc2": 90})]
        assert _page_spread(weights, subjects)["name"] == "Fisika", (
            "the spread picked a subject whose marks the weights cannot place")

    @needs_node
    def test_a_tie_keeps_the_servers_own_name_order(self):
        weights = {"gc1": 40, "gc2": 60}
        subjects = [_row("Biologi", {"gc1": 70}), _row("Kimia", {"gc2": 80})]
        assert _page_spread(weights, subjects)["name"] == "Biologi", (
            "a tie is decided by something other than the server's ordering, so the "
            "chosen subject is not reproducible")

    @needs_node
    def test_a_row_with_no_mark_is_never_chosen(self):
        weights = {"gc1": 40}
        subjects = [_row("Biologi", {}), _row("Kimia", {"gc1": 50})]
        assert _page_spread(weights, subjects)["name"] == "Kimia", (
            "an empty row was chosen, so the sample is from nowhere")

    @needs_node
    def test_nothing_scored_is_no_subject_at_all(self):
        assert _page_spread({"gc1": 40}, []) is None
        assert _page_spread({"gc1": 40}, [_row("Biologi", {})]) is None

    @needs_node
    def test_a_zero_percent_weight_is_not_a_component(self):
        """Same rule the arithmetic uses: a 0% weight is not on screen."""
        weights = {"gc1": 0, "gc2": 60}
        subjects = [_row("Biologi", {"gc1": 70}), _row("Kimia", {"gc2": 80})]
        assert _page_spread(weights, subjects)["name"] == "Kimia"

    @needs_node
    def test_the_rows_own_marks_are_what_the_pick_hands_back(self):
        """The pick carries the marks, so the seed needs no second read."""
        pick = _page_spread({"gc1": 100}, [_row("Fisika", {"gc1": 80})])
        assert pick["marks"] == {"gc1": 80}
        assert pick["subject_id"] == "fisika"


class TestTheSeedAgreesWithTheServer:
    @needs_node
    def test_a_seeded_sample_carries_the_same_final_mark_as_the_server(self):
        """The point of the seed: the previewed number is the roster's number."""
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p1")
        weights = {"gc1": 40, "gc2": 60}
        server = gw.compute([(cid, mark) for cid, mark in out["marks"].items()], weights)
        page = _page_final(weights, out["marks"])
        # 70 × 40% + 90 × 60% = 28 + 54 = 82, verified by hand from the fixture.
        assert page["final"] == server["final"] == 82.0, (
            "the seeded preview and the server disagree: " + repr((page, server)))
        assert [d["component_id"] for d in page["detail"]] == ["gc1", "gc2"]

    @needs_node
    def test_a_missing_component_still_counts_as_zero_after_seeding(self):
        """The seed leaves the gap alone; the page applies the module's policy."""
        out = gw.pupil_component_marks(_DB(_tables()), SCHOOL, "s1", YEAR, "p2")
        assert set(out["marks"]) == {"gc2"}
        weights = {"gc1": 40, "gc2": 60}
        server = gw.compute([(cid, mark) for cid, mark in out["marks"].items()], weights)
        assert server["final"] == 12.0, "the server's own zero policy changed"
        assert server["detail"][0]["count"] == 0
        assert _page_final(weights, out["marks"])["final"] == 12.0, (
            "the page renormalised a missing component instead of counting it as zero")


# ── the doors ────────────────────────────────────────────────────────────────

def _body(name: str) -> str:
    src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]


class TestTheDoors:
    def test_the_search_door_is_admin_only_and_scopes_by_the_session(self):
        body = _body("admin_grade_pupil_search")
        assert "@admin_sekolah_required" in body, "the picker is not admin-only"
        assert "_school_id()" in body, "the school must come from the session"
        assert "find_pupils(" in body
        assert 'request.args.get("school' not in body, (
            "the school must never be a value the query can set")

    def test_the_marks_door_is_admin_only(self):
        src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
        block = src.split('route("/grade-weights/pupil-marks")')[1].split("\n@admin_sekolah_bp.route")[0]
        assert "@admin_sekolah_required" in block

    def test_the_marks_door_checks_the_subject_belongs_to_the_school(self):
        body = _body("admin_grade_pupil_marks")
        assert 'table("subjects")' in body and 'eq("school_id", sid)' in body, (
            "a subject of another school must be refused, not read")
        assert "404" in body

    def test_the_marks_door_refuses_a_pupil_that_is_not_this_schools(self):
        body = _body("admin_grade_pupil_marks")
        assert "pupil_component_marks(" in body
        assert "if not found" in body and "404" in body, (
            "a foreign pupil must be a refusal, not a quiet empty sample")

    def test_the_doors_are_reads(self):
        for name in ("admin_grade_pupil_search", "admin_grade_pupil_marks"):
            body = _body(name)
            for writer in (".insert(", ".update(", ".delete("):
                assert writer not in body, f"{name} writes: a preview must never save"


# ── the page ─────────────────────────────────────────────────────────────────

def _method(name: str) -> str:
    """One Alpine method's body, read out of the template.

    Anchored at the 8-space indent so a *call* (`this.seedFromSpread(`) can never
    be mistaken for the declaration: the marker has to name the definition, or the
    body read back is the tail of whichever method happened to call it first.
    """
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    marker = f"\n        {name}("
    assert marker in html, f"the page has no {name} method"
    return html.split(marker, 1)[1].split("\n        },", 1)[0]


def _seed_methods() -> str:
    return "\n".join(_method(n) for n in (
        "searchPupils", "seedFrom", "seedFromSpread", "seedFromSubject",
        "applySeed", "loadPupilSubjects", "clearSeed", "onPreviewSubjectChange"))


def _seed_block() -> str:
    html = WEIGHTS_HTML.read_text(encoding="utf-8")
    assert "data-preview-pupil" in html, "there is no way to seed the sample from a pupil"
    return html.split("data-preview-pupil", 1)[1].split("flex flex-wrap gap-3 mb-4", 1)[0]


class TestThePageIsWired:
    def test_the_preview_can_be_seeded_from_a_pupil(self):
        block = _seed_block()
        assert "seedFrom(" in block, "the card names no pupil to seed from"
        assert "searchPupils()" in block, "the picker cannot search"

    def test_the_seed_reads_the_doors_and_never_posts(self):
        body = _seed_methods()
        assert "/admin-sekolah/grade-weights/pupils" in body
        assert "/admin-sekolah/grade-weights/pupil-marks" in body
        assert "post(" not in body, "seeding writes to the server; it must only read"

    def test_the_picker_asks_nothing_for_a_one_character_query(self):
        body = _seed_methods()
        assert "q.length < 2" in body, (
            "the picker searches on a single character, which reads like a dump")

    def test_a_hand_edit_drops_the_attribution(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("setSample(", 1)[1].split("\n        },", 1)[0]
        assert "this.seedIntact = false" in body, (
            "an edited sample would still be labelled as that pupil's marks")

    def test_changing_the_subject_re_seeds_the_same_pupil(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert '@change="onPreviewSubjectChange()"' in html, (
            "the preview can be pointed at another subject while holding the previous "
            "subject's marks")
        body = _seed_methods()
        assert "seedFrom(pupil)" in body, (
            "a subject change must re-read, not leave the old subject's marks under "
            "this subject's weights")

    def test_the_school_default_seeds_from_the_pupils_own_spread(self):
        """The one distribution that covers the whole school can name a learner.

        The page used to answer the school default with "pick a subject first", so
        the policy that applies to every subject that has not been given its own
        row was the single policy that could not be previewed against a real
        learner. Now the learner's own spread decides which subject the sample is
        read from, in the same one read the comparison table already makes.
        """
        body = _seed_methods()
        assert "'__default__'" in body, (
            "the school default is no longer a case the seed knows about")
        assert "seedFromSpread(" in body, (
            "the school default still has nothing to read the sample from")
        assert "sgPickSpreadSubject(" in body, (
            "the spread is not what chooses the subject, so the choice is a coin toss")
        assert "Pilih satu mapel dulu" not in body, (
            "the school default still asks for a subject before it will seed")

    def test_the_spread_is_read_once_for_both_callers(self):
        """The table and the seed want the same rows; the seed does not re-ask."""
        assert ("return this.get('/admin-sekolah/grade-weights/pupil-subjects"
                in _method("loadPupilSubjects")), (
            "loadPupilSubjects does not hand its rows back, so the seed cannot "
            "wait on the read that is already in flight")
        assert "pupil-subjects" not in _method("seedFromSpread"), (
            "the default's seed issues a second read for rows the page already has")

    def test_the_page_says_which_subject_the_sample_came_from(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "data-seed-source" in html, (
            "the school default's sample names no subject, so its numbers cannot be "
            "attributed to anything the admin can check")
        assert "seedSource" in _method("applySeed"), (
            "the seed never records which subject it read")

    def test_the_source_copy_is_bilingual(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "t('dari','from')" in html, (
            "the source label carries no bilingual pair")

    def test_a_failed_spread_read_is_not_reported_as_no_marks(self):
        """"Could not read" and "nothing graded" are different facts."""
        assert "this.pupilSubjectsNote" in _method("seedFromSpread"), (
            "a read that failed would be shown as 'this pupil has no marks', which "
            "is a different fact and hides the failure")

    def test_clearing_and_switching_drop_the_source(self):
        for name in ("clearSeed", "onPreviewSubjectChange"):
            assert "this.seedSource = ''" in _method(name), (
                f"{name} leaves the previous subject's name on the badge")

    def test_clearing_the_seed_empties_the_sample(self):
        body = _seed_methods()
        assert "this.previewScores = {}" in body and "this.seedPupil = null" in body

    def test_the_seed_copy_is_bilingual(self):
        block = _seed_block()
        assert "t('Ambil dari murid','Seed from a pupil')" in block, (
            "the seed control carries no bilingual pair, so its copy cannot be translated")
        assert "t('Cari nama murid…','Search a pupil by name…')" in block
        assert "t('Kosongkan','Clear')" in block
